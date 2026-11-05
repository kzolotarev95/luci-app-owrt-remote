param(
    [ValidateNotNullOrEmpty()][string]$Router = 'root@192.168.2.1',
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$UploadName = 'owrt-xray-source-' + [guid]::NewGuid().ToString('N')
$StageRoot = Join-Path ([System.IO.Path]::GetTempPath()) $UploadName
$Utf8 = [System.Text.UTF8Encoding]::new($false)
$RemoteScript = @'
#!/bin/sh
set -eu
stage=/tmp/__UPLOAD_NAME__
target=/usr/sbin/owrt-remote
backup_dir=/root/owrt-remote-backup-before-xray-source-v121
runtime=/tmp/owrt-xray/xray
changed=0
phase=preflight
restore_runtime() {
    if [ -s "$backup_dir/xray" ]; then
        mkdir -p "$(dirname "$runtime")"
        cp -p "$backup_dir/xray" "$runtime.new-__UPLOAD_NAME__" && chmod 755 "$runtime.new-__UPLOAD_NAME__" && mv -f "$runtime.new-__UPLOAD_NAME__" "$runtime"
        uci set owrtremote.main.xray_bin=/tmp/owrt-xray/xray >/dev/null 2>&1 || true
        uci commit owrtremote >/dev/null 2>&1 || true
    fi
}
cleanup() {
    code=$?
    trap - EXIT
    if [ "$code" -ne 0 ]; then
        printf 'Failed during %s (rc=%s).\n' "$phase" "$code" >&2
        if [ "$changed" = 1 ] && [ -s "$stage/previous" ]; then
            echo 'Restoring the previous agent. The working Xray/configuration is retained.' >&2
            cp -p "$stage/previous" "$target.new-__UPLOAD_NAME__" && mv -f "$target.new-__UPLOAD_NAME__" "$target" || true
            restore_runtime || true
        fi
    fi
    rm -f "$target.new-__UPLOAD_NAME__"
    rm -rf "$stage"
    exit "$code"
}
trap cleanup EXIT
trap 'exit 1' INT TERM

test -s "$target"
if [ '__ACTION__' = Install ]; then
    test -s "$stage/agent"
    sh -n "$stage/agent"
    grep -Fq 'https://github.com/yichya/openwrt-xray' "$stage/agent"
    grep -Fq 'xray_package_extract_binary()' "$stage/agent"
    if [ ! -e "$backup_dir" ]; then
        mkdir -p "$stage/retained"
        chmod 700 "$stage/retained"
        cp -p "$target" "$stage/retained/agent"
        if [ -s "$runtime" ]; then cp -p "$runtime" "$stage/retained/xray"; fi
        : >"$stage/retained/complete"
        mv "$stage/retained" "$backup_dir"
    fi
    test -f "$backup_dir/complete" && test -s "$backup_dir/agent" || { echo 'Incomplete backup. Nothing changed.' >&2; exit 1; }
    source_file=$stage/agent
else
    test -f "$backup_dir/complete" && test -s "$backup_dir/agent" || { echo "No complete backup: $backup_dir. Nothing changed." >&2; exit 1; }
    sh -n "$backup_dir/agent"
    source_file=$backup_dir/agent
fi

cp -p "$target" "$stage/previous"
phase=install
changed=1
cp "$source_file" "$target.new-__UPLOAD_NAME__"
chmod 755 "$target.new-__UPLOAD_NAME__"
mv -f "$target.new-__UPLOAD_NAME__" "$target"
phase=verify
sh -n "$target"
cmp "$source_file" "$target"
if [ '__ACTION__' = Install ]; then
    phase=xray-package
    # Force one controlled download from yichya/openwrt-xray now. The package
    # is validated before /tmp/owrt-xray/xray and UCI are changed.
    "$target" install-xray-tmp
else
    restore_runtime
fi
printf 'Backup: %s\n' "$backup_dir"
printf '\nXRAY_SOURCE_ROUTER_%s_OK\n' '__ACTION__'
'@
$RemoteScript = $RemoteScript.Replace('__UPLOAD_NAME__', $UploadName).Replace('__ACTION__', $Action)
try {
    [System.IO.Directory]::CreateDirectory($StageRoot) | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $StageRoot 'run.sh'), $RemoteScript.Replace("`r`n", "`n") + "`n", $Utf8)
    if ($Action -eq 'Install') {
        $Content = [System.IO.File]::ReadAllText((Join-Path $RepoRoot 'files\usr\sbin\owrt-remote')).Replace("`r`n", "`n")
        [System.IO.File]::WriteAllText((Join-Path $StageRoot 'agent'), $Content, $Utf8)
    }
    if ($PrepareOnly) { Write-Host "Prepared only: $StageRoot"; return }
    & scp -O -r -- $StageRoot "${Router}:/tmp/"
    if ($LASTEXITCODE -ne 0) { throw 'Upload failed. The installation has not started.' }
    & ssh -T -o ConnectTimeout=10 $Router "sh /tmp/$UploadName/run.sh"
    if ($LASTEXITCODE -ne 0) { throw "$Action failed. See the output above; success was not confirmed." }
    Write-Host "Xray source fix $Action finished successfully."
} finally {
    if (-not $PrepareOnly) {
        $Resolved = [System.IO.Path]::GetFullPath($StageRoot)
        $Expected = [System.IO.Path]::GetFullPath((Join-Path ([System.IO.Path]::GetTempPath()) $UploadName))
        if ($Resolved -ne $Expected -or (Split-Path -Leaf $Resolved) -ne $UploadName) { throw 'Unsafe staging cleanup path.' }
        if (Test-Path -LiteralPath $Resolved) { Remove-Item -LiteralPath $Resolved -Recurse -Force }
    }
}
