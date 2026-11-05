param(
    [ValidateNotNullOrEmpty()][string]$Router = 'root@192.168.2.1',
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$UploadName = 'owrt-xray-validation-' + [guid]::NewGuid().ToString('N')
$StageRoot = Join-Path ([System.IO.Path]::GetTempPath()) $UploadName
$Utf8 = [System.Text.UTF8Encoding]::new($false)
$Payloads = @{
    'cgi' = 'files\www\cgi-bin\owrt-remote'
    'agent' = 'files\usr\sbin\owrt-remote'
}
$RemoteScript = @'
#!/bin/sh
set -eu
stage=/tmp/__UPLOAD_NAME__
backup_dir=/root/owrt-remote-backup-before-xray-validation-v121
changed=0
phase=preflight
item_path() {
    case "$1" in
        cgi) printf /www/cgi-bin/owrt-remote ;;
        agent) printf /usr/sbin/owrt-remote ;;
    esac
}
complete_backup() {
    [ -f "$1/complete" ] && [ -s "$1/cgi" ] && [ -s "$1/agent" ]
}
restore_files() {
    local dir item path
    dir=$1
    complete_backup "$dir" || return 1
    for item in cgi agent; do
        path=$(item_path "$item")
        cp -p "$dir/$item" "$path.new-__UPLOAD_NAME__" || return 1
        mv -f "$path.new-__UPLOAD_NAME__" "$path" || return 1
    done
}
cleanup() {
    code=$?
    trap - EXIT
    if [ "$code" -ne 0 ]; then
        printf 'Failed during %s (rc=%s).\n' "$phase" "$code" >&2
        if [ "$changed" = 1 ]; then
            echo 'Restoring the agent and CGI from this attempt.' >&2
            restore_files "$stage/previous" || echo 'Restore failed; retained backup is available.' >&2
        fi
    fi
    for item in cgi agent; do path=$(item_path "$item"); rm -f "$path.new-__UPLOAD_NAME__"; done
    rm -rf "$stage"
    exit "$code"
}
trap cleanup EXIT
for item in cgi agent; do
    path=$(item_path "$item")
    test -s "$path"
done
if [ '__ACTION__' = Install ]; then
    for item in cgi agent; do test -s "$stage/$item"; sh -n "$stage/$item"; done
    grep -F -q 'validate_xray_config()' "$stage/agent"
    grep -F -q 'unset http_proxy https_proxy' "$stage/agent"
    grep -F -q 'OWRT_REMOTE_APPLY_TRACE' "$stage/cgi"
    if [ ! -e "$backup_dir" ]; then
        mkdir -p "$stage/retained-backup"
        chmod 700 "$stage/retained-backup"
        for item in cgi agent; do path=$(item_path "$item"); cp -p "$path" "$stage/retained-backup/$item"; done
        touch "$stage/retained-backup/complete"
        mv "$stage/retained-backup" "$backup_dir"
    fi
    complete_backup "$backup_dir" || { echo "Incomplete backup: $backup_dir. Nothing changed." >&2; exit 1; }
    source_dir=$stage
else
    complete_backup "$backup_dir" || { echo "No complete backup: $backup_dir. Nothing changed." >&2; exit 1; }
    for item in cgi agent; do sh -n "$backup_dir/$item"; done
    source_dir=$backup_dir
fi
printf 'Backup: %s\n' "$backup_dir"
mkdir -p "$stage/previous"
for item in cgi agent; do path=$(item_path "$item"); cp -p "$path" "$stage/previous/$item"; done
touch "$stage/previous/complete"
phase=install
changed=1
for item in cgi agent; do
    path=$(item_path "$item")
    cp "$source_dir/$item" "$path.new-__UPLOAD_NAME__"
    chmod 755 "$path.new-__UPLOAD_NAME__"
    mv -f "$path.new-__UPLOAD_NAME__" "$path"
done
phase=verify
for item in cgi agent; do
    path=$(item_path "$item")
    sh -n "$path"
    cmp "$source_dir/$item" "$path"
done
# Deploy only code. Do not regenerate config or restart a remote tunnel here.
printf '\nXRAY_VALIDATION_ROUTER_%s_OK\n' '__ACTION__'

'@
$RemoteScript = $RemoteScript.Replace('__UPLOAD_NAME__', $UploadName).Replace('__ACTION__', $Action)
try {
    [System.IO.Directory]::CreateDirectory($StageRoot) | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $StageRoot 'run.sh'), $RemoteScript.Replace("`r`n", "`n") + "`n", $Utf8)
    if ($Action -eq 'Install') {
        foreach ($Item in $Payloads.GetEnumerator()) {
            $Content = [System.IO.File]::ReadAllText((Join-Path $RepoRoot $Item.Value)).Replace("`r`n", "`n")
            [System.IO.File]::WriteAllText((Join-Path $StageRoot $Item.Key), $Content, $Utf8)
        }
    }
    if ($PrepareOnly) { Write-Host "Prepared only: $StageRoot"; return }
    & scp -O -r -- $StageRoot "${Router}:/tmp/"
    if ($LASTEXITCODE -ne 0) { throw 'Upload failed. The installation has not started.' }
    & ssh -T -o ConnectTimeout=10 $Router "sh /tmp/$UploadName/run.sh"
    if ($LASTEXITCODE -ne 0) { throw "$Action failed. See the output above; success was not confirmed." }
    Write-Host "Xray validation fix $Action finished successfully."
} finally {
    if (-not $PrepareOnly) {
        $Resolved = [System.IO.Path]::GetFullPath($StageRoot)
        $Expected = [System.IO.Path]::GetFullPath((Join-Path ([System.IO.Path]::GetTempPath()) $UploadName))
        if ($Resolved -ne $Expected -or (Split-Path -Leaf $Resolved) -ne $UploadName) { throw 'Unsafe staging cleanup path.' }
        if (Test-Path -LiteralPath $Resolved) { Remove-Item -LiteralPath $Resolved -Recurse -Force }
    }
}
