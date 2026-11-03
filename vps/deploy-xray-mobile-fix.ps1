param(
    [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$Router,
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$UploadName = 'owrt-xray-mobile-' + [guid]::NewGuid().ToString('N')
$StageRoot = Join-Path ([System.IO.Path]::GetTempPath()) $UploadName
$Utf8 = [System.Text.UTF8Encoding]::new($false)
$RemoteScript = @'
#!/bin/sh
set -eu
stage=/tmp/__UPLOAD_NAME__
target=/www/cgi-bin/owrt-remote
backup_dir=/root/owrt-remote-backup-before-xray-mobile-v120
backup=$backup_dir/owrt-remote
candidate=$target.new-__UPLOAD_NAME__
changed=0
phase=preflight
cleanup() {
    code=$?
    trap - EXIT
    if [ "$code" -ne 0 ]; then
        printf 'Failed during %s (rc=%s).\n' "$phase" "$code" >&2
        if [ "$changed" = 1 ]; then
            echo 'Restoring the CGI from this attempt.' >&2
            cp -p "$stage/previous" "$candidate"
            mv -f "$candidate" "$target"
        fi
    fi
    rm -f "$candidate"
    rm -rf "$stage"
    exit "$code"
}
trap cleanup EXIT
test -s "$target"
if [ '__ACTION__' = Install ]; then
    test -s "$stage/cgi"
    sh -n "$stage/cgi"
    grep -F -q 'normalize_xray_settings()' "$stage/cgi"
    grep -F -q 'autocorrect="off" autocapitalize="off"' "$stage/cgi"
    phase=backup
    mkdir -p "$backup_dir"
    chmod 700 "$backup_dir"
    if [ ! -e "$backup" ]; then cp -p "$target" "$backup"; fi
    test -s "$backup"
    source_file=$stage/cgi
else
    if [ ! -s "$backup" ]; then
        printf 'No backup on this router: %s. Nothing changed.\n' "$backup" >&2
        exit 1
    fi
    sh -n "$backup"
    source_file=$backup
fi
printf 'Backup: %s\n' "$backup"
cp -p "$target" "$stage/previous"
phase=install
cp "$source_file" "$candidate"
chmod 755 "$candidate"
changed=1
mv -f "$candidate" "$target"
phase=verify
sh -n "$target"
cmp "$source_file" "$target"
printf '\nXRAY_MOBILE_ROUTER_%s_OK\n' '__ACTION__'
'@
$RemoteScript = $RemoteScript.Replace('__UPLOAD_NAME__', $UploadName).Replace('__ACTION__', $Action)
try {
    [System.IO.Directory]::CreateDirectory($StageRoot) | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $StageRoot 'run.sh'), $RemoteScript.Replace("`r`n", "`n") + "`n", $Utf8)
    if ($Action -eq 'Install') {
        $Content = [System.IO.File]::ReadAllText((Join-Path $RepoRoot 'files\www\cgi-bin\owrt-remote')).Replace("`r`n", "`n")
        [System.IO.File]::WriteAllText((Join-Path $StageRoot 'cgi'), $Content, $Utf8)
    }
    if ($PrepareOnly) { Write-Host "Prepared only: $StageRoot"; return }
    & scp -O -r -- $StageRoot "${Router}:/tmp/"
    if ($LASTEXITCODE -ne 0) { throw 'Upload failed. The installation has not started.' }
    & ssh -T -o ConnectTimeout=10 $Router "sh /tmp/$UploadName/run.sh"
    if ($LASTEXITCODE -ne 0) { throw "$Action failed. See the output above; success was not confirmed." }
    Write-Host "Router CGI $Action finished successfully."
} finally {
    if (-not $PrepareOnly) {
        $Resolved = [System.IO.Path]::GetFullPath($StageRoot)
        $Expected = [System.IO.Path]::GetFullPath((Join-Path ([System.IO.Path]::GetTempPath()) $UploadName))
        if ($Resolved -ne $Expected -or (Split-Path -Leaf $Resolved) -ne $UploadName) { throw 'Unsafe staging cleanup path.' }
        if (Test-Path -LiteralPath $Resolved) { Remove-Item -LiteralPath $Resolved -Recurse -Force }
    }
}
