param(
    [ValidateNotNullOrEmpty()][string]$Router = 'root@192.168.2.1',
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$UploadName = 'owrt-xray-reverse-' + [guid]::NewGuid().ToString('N')
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
backup_dir=/root/owrt-remote-backup-before-xray-reverse-v121
changed=0
restart_attempted=0
phase=preflight
config=$(uci -q get owrtremote.main.xray_config || true)
[ -n "$config" ] || config=/etc/xray/owrt-remote-client.json
case "$config" in /*) ;; *) echo 'xray_config must be absolute.' >&2; exit 1 ;; esac
item_path() {
    case "$1" in
        cgi) printf /www/cgi-bin/owrt-remote ;;
        agent) printf /usr/sbin/owrt-remote ;;
    esac
}
complete_backup() {
    [ -f "$1/complete" ] && [ -s "$1/cgi" ] && [ -s "$1/agent" ] &&
        [ -s "$1/config-path" ] && { [ -s "$1/config.json" ] || [ -f "$1/config-missing" ]; }
}
save_config() {
    printf '%s\n' "$config" >"$1/config-path"
    if [ -f "$config" ]; then cp -p "$config" "$1/config.json"; else touch "$1/config-missing"; fi
}
restore_config() {
    [ "$(cat "$1/config-path")" = "$config" ] || return 1
    if [ -f "$1/config-missing" ]; then
        rm -f "$config"
    else
        cp -p "$1/config.json" "$config.new-__UPLOAD_NAME__" || return 1
        mv -f "$config.new-__UPLOAD_NAME__" "$config" || return 1
    fi
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
    restore_config "$dir"
}
cleanup() {
    code=$?
    trap - EXIT
    if [ "$code" -ne 0 ]; then
        printf 'Failed during %s (rc=%s).\n' "$phase" "$code" >&2
        if [ "$changed" = 1 ]; then
            echo 'Restoring the agent and CGI from this attempt.' >&2
            restore_files "$stage/previous" || echo 'Restore failed; retained backup is available.' >&2
            if [ "$restart_attempted" = 1 ] && [ -s "$config" ]; then /etc/init.d/owrt-remote restart >&2 || true; fi
        fi
    fi
    for item in cgi agent; do path=$(item_path "$item"); rm -f "$path.new-__UPLOAD_NAME__"; done
    rm -f "$config.new-__UPLOAD_NAME__"
    rm -rf "$stage"
    exit "$code"
}
trap cleanup EXIT
trap 'exit 1' INT TERM
for item in cgi agent; do
    path=$(item_path "$item")
    test -s "$path"
done
if [ '__ACTION__' = Install ]; then
    for item in cgi agent; do test -s "$stage/$item"; sh -n "$stage/$item"; done
    grep -F -q 'xray_validate_transport_binary()' "$stage/agent"
    grep -F -q '26.6.27' "$stage/agent"
    grep -F -q '"finalRules"' "$stage/agent"
    grep -F -q 'OWRT_REMOTE_APPLY_TRACE' "$stage/cgi"
    if [ ! -e "$backup_dir" ]; then
        mkdir -p "$stage/retained-backup"
        chmod 700 "$stage/retained-backup"
        for item in cgi agent; do path=$(item_path "$item"); cp -p "$path" "$stage/retained-backup/$item"; done
        save_config "$stage/retained-backup"
        touch "$stage/retained-backup/complete"
        mv "$stage/retained-backup" "$backup_dir"
    fi
    complete_backup "$backup_dir" || { echo "Incomplete backup: $backup_dir. Nothing changed." >&2; exit 1; }
    source_dir=$stage
else
    complete_backup "$backup_dir" || { echo "No complete backup: $backup_dir. Nothing changed." >&2; exit 1; }
    [ "$(cat "$backup_dir/config-path")" = "$config" ] || { echo 'xray_config changed since backup. Nothing changed.' >&2; exit 1; }
    for item in cgi agent; do sh -n "$backup_dir/$item"; done
    source_dir=$backup_dir
fi
printf 'Backup: %s\n' "$backup_dir"
mkdir -p "$stage/previous"
chmod 700 "$stage/previous"
for item in cgi agent; do path=$(item_path "$item"); cp -p "$path" "$stage/previous/$item"; done
save_config "$stage/previous"
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
if [ '__ACTION__' = Install ]; then
    phase=validate-client-config
    # render-client validates the candidate with Xray before atomic replacement.
    /usr/sbin/owrt-remote render-client
    grep -F -q '"finalRules"' "$config"
else
    phase=restore-client-config
    restore_config "$backup_dir"
fi
phase=restart-tunnel
restart_attempted=1
/etc/init.d/owrt-remote restart
# Keep the compatible core and its boot cache on rollback; restore the saved
# agent, CGI and JSON. The VPS server binary does not need a downgrade.
printf '\nXRAY_REVERSE_ROUTER_%s_OK\n' '__ACTION__'

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
    Write-Host "Xray reverse fix $Action finished successfully."
} finally {
    if (-not $PrepareOnly) {
        $Resolved = [System.IO.Path]::GetFullPath($StageRoot)
        $Expected = [System.IO.Path]::GetFullPath((Join-Path ([System.IO.Path]::GetTempPath()) $UploadName))
        if ($Resolved -ne $Expected -or (Split-Path -Leaf $Resolved) -ne $UploadName) { throw 'Unsafe staging cleanup path.' }
        if (Test-Path -LiteralPath $Resolved) { Remove-Item -LiteralPath $Resolved -Recurse -Force }
    }
}
