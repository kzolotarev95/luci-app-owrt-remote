param(
    [ValidateNotNullOrEmpty()][string]$Router = 'root@192.168.2.1',
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$UploadName = 'owrt-xray-apk-' + [guid]::NewGuid().ToString('N')
$StageRoot = Join-Path ([System.IO.Path]::GetTempPath()) $UploadName
$Utf8 = [System.Text.UTF8Encoding]::new($false)
$Payloads = @{
    'agent' = 'files\usr\sbin\owrt-remote'
}
$RemoteScript = @'
#!/bin/sh
set -eu
stage=/tmp/__UPLOAD_NAME__
backup_dir=/root/owrt-remote-backup-before-xray-apk-v120
key_file=/etc/owrt-remote/web.key
key_candidate=$key_file.new-__UPLOAD_NAME__
changed=0
phase=preflight
item_path() {
    case "$1" in
        agent) printf /usr/sbin/owrt-remote ;;
    esac
}
complete_backup() {
    [ -f "$1/complete" ] && [ -s "$1/agent" ]
}
restore_files() {
    local dir item path
    dir=$1
    complete_backup "$dir" || return 1
    for item in agent; do
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
            echo 'Restoring the previous agent from this attempt.' >&2
            restore_files "$stage/previous" || echo 'Restore failed; retained backup is available.' >&2
        fi
    fi
    for item in agent; do path=$(item_path "$item"); rm -f "$path.new-__UPLOAD_NAME__"; done
    rm -f "$key_candidate"
    rm -rf "$stage"
    exit "$code"
}
trap cleanup EXIT
trap 'exit 1' INT TERM
for item in agent; do
    path=$(item_path "$item")
    test -s "$path"
done
if [ '__ACTION__' = Install ]; then
    for item in agent; do test -s "$stage/$item"; sh -n "$stage/$item"; done
    grep -F -q 'xray_validate_transport_binary()' "$stage/agent"
    grep -F -q '26.6.27' "$stage/agent"
    grep -F -q 'apk extract --allow-untrusted --no-chown --destination' "$stage/agent"
    grep -F -q 'xray_package_extract_binary "$package" "$dir/xray.new" "$asset"' "$stage/agent"
    if [ ! -e "$backup_dir" ]; then
        mkdir -p "$stage/retained-backup"
        chmod 700 "$stage/retained-backup"
        for item in agent; do path=$(item_path "$item"); cp -p "$path" "$stage/retained-backup/$item"; done
        touch "$stage/retained-backup/complete"
        mv "$stage/retained-backup" "$backup_dir"
    fi
    complete_backup "$backup_dir" || { echo "Incomplete backup: $backup_dir. Nothing changed." >&2; exit 1; }
    source_dir=$stage
else
    complete_backup "$backup_dir" || { echo "No complete backup: $backup_dir. Nothing changed." >&2; exit 1; }
    for item in agent; do sh -n "$backup_dir/$item"; done
    source_dir=$backup_dir
fi
printf 'Backup: %s\n' "$backup_dir"
mkdir -p "$stage/previous"
for item in agent; do path=$(item_path "$item"); cp -p "$path" "$stage/previous/$item"; done
touch "$stage/previous/complete"
phase=install
changed=1
for item in agent; do
    path=$(item_path "$item")
    cp "$source_dir/$item" "$path.new-__UPLOAD_NAME__"
    chmod 755 "$path.new-__UPLOAD_NAME__"
    mv -f "$path.new-__UPLOAD_NAME__" "$path"
done
phase=verify
for item in agent; do
    path=$(item_path "$item")
    sh -n "$path"
    cmp "$source_dir/$item" "$path"
done
if [ '__ACTION__' = Install ]; then
    phase=prepare-xray-apk
    # Replaces a known incompatible core, or restores/downloads a validated
    # compatible one. Does not render JSON or restart an existing tunnel.
    /usr/sbin/owrt-remote prepare-xray
    # install.sh creates the web key after Xray preparation. Recover that last
    # step if the first installation was interrupted by the APK defect.
    if [ ! -s "$key_file" ]; then
        phase=finish-first-install
        mkdir -p "$(dirname "$key_file")"
        if command -v hexdump >/dev/null 2>&1; then
            (umask 077; dd if=/dev/urandom bs=16 count=1 2>/dev/null | hexdump -v -e '16/1 "%02x"' >"$key_candidate")
        else
            (umask 077; dd if=/dev/urandom bs=16 count=1 2>/dev/null | od -An -v -tx1 | tr -d ' \n' >"$key_candidate")
        fi
        [ "$(wc -c <"$key_candidate")" -eq 32 ] || { echo 'Web key generation failed.' >&2; exit 1; }
        chmod 600 "$key_candidate"
        mv -f "$key_candidate" "$key_file"
        echo 'Web key created for the interrupted first installation.'
    fi
fi
# Rollback restores agent code only; retain the validated compatible core,
# its boot cache, and all router settings. Never remove a working executable.
printf '\nXRAY_APK_ROUTER_%s_OK\n' '__ACTION__'

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
    Write-Host "Xray APK fix $Action finished successfully."
} finally {
    if (-not $PrepareOnly) {
        $Resolved = [System.IO.Path]::GetFullPath($StageRoot)
        $Expected = [System.IO.Path]::GetFullPath((Join-Path ([System.IO.Path]::GetTempPath()) $UploadName))
        if ($Resolved -ne $Expected -or (Split-Path -Leaf $Resolved) -ne $UploadName) { throw 'Unsafe staging cleanup path.' }
        if (Test-Path -LiteralPath $Resolved) { Remove-Item -LiteralPath $Resolved -Recurse -Force }
    }
}
