param(
    [ValidateNotNullOrEmpty()][string]$Router = 'root@192.168.2.1',
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$UploadName = 'owrt-wan-recovery-' + [guid]::NewGuid().ToString('N')
$StageRoot = Join-Path ([System.IO.Path]::GetTempPath()) $UploadName
$Utf8 = [System.Text.UTF8Encoding]::new($false)
$Payloads = @{
    'wan.sh' = 'files\usr\lib\owrt-remote\wan.sh'
    'agent' = 'files\usr\sbin\owrt-remote'
}
$RemoteScript = @'
#!/bin/sh
set -eu
stage=/tmp/__UPLOAD_NAME__
backup_dir=/root/owrt-remote-backup-before-wan-recovery-v121
changed=0
phase=preflight
item_path() {
    case "$1" in
        wan.sh) printf /usr/lib/owrt-remote/wan.sh ;;
        agent) printf /usr/sbin/owrt-remote ;;
    esac
}
complete_backup() {
    [ -f "$1/complete" ] && [ -s "$1/wan.sh" ] && [ -s "$1/agent" ]
}
restore_files() {
    local dir item path
    dir=$1
    complete_backup "$dir" || return 1
    for item in agent wan.sh; do
        path=$(item_path "$item")
        cp -p "$dir/$item" "$path.new-__UPLOAD_NAME__" || return 1
        mv -f "$path.new-__UPLOAD_NAME__" "$path" || return 1
    done
}
heartbeat_pid() {
    local state
    state=$(ubus call service list '{"name":"owrt-remote"}') || return 1
    printf '%s' "$state" | jsonfilter -e '@["owrt-remote"].instances.heartbeat.pid' 2>/dev/null || true
}
refresh_heartbeat() {
    local old_pid new_pid attempt
    old_pid=$(heartbeat_pid) || return 1
    case "$old_pid" in ''|0) echo 'Heartbeat loop is stopped; files will be used at its next start.'; return 0 ;; *[!0-9]*) return 1 ;; esac
    # Signal only the supervised heartbeat shell; procd respawns it from the
    # new agent. A healthy Xray instance stays running; a crashed one can recover.
    ubus call service signal '{"name":"owrt-remote","instance":"heartbeat","signal":9}' >/dev/null || return 1
    attempt=0
    while [ "$attempt" -lt 20 ]; do
        sleep 1
        new_pid=$(heartbeat_pid) || return 1
        case "$new_pid" in ''|0|*[!0-9]*) ;; *)
            [ "$new_pid" = "$old_pid" ] || { echo 'Heartbeat loop reloaded; WAN recovery is active for enabled tunnels.'; return 0; } ;;
        esac
        attempt=$((attempt+1))
    done
    echo 'Heartbeat loop did not respawn. Code will be restored.' >&2
    return 1
}
cleanup() {
    code=$?
    trap - EXIT
    if [ "$code" -ne 0 ]; then
        printf 'Failed during %s (rc=%s).\n' "$phase" "$code" >&2
        if [ "$changed" = 1 ]; then
            echo 'Restoring the agent and WAN helper from this attempt.' >&2
            restore_files "$stage/previous" && refresh_heartbeat || echo 'Restore failed; retained backup is available.' >&2
        fi
    fi
    for item in agent wan.sh; do path=$(item_path "$item"); rm -f "$path.new-__UPLOAD_NAME__"; done
    rm -rf "$stage"
    exit "$code"
}
trap cleanup EXIT
for item in agent wan.sh; do
    path=$(item_path "$item")
    test -s "$path"
done
heartbeat_pid >/dev/null
if [ '__ACTION__' = Install ]; then
    for item in agent wan.sh; do test -s "$stage/$item"; sh -n "$stage/$item"; done
    grep -F -q 'wan_recover_xray()' "$stage/agent"
    grep -F -q 'wait_for_selected_wan()' "$stage/agent"
    grep -F -q 'reason=https_required' "$stage/agent"
    grep -F -q 'remote_curl_post' "$stage/wan.sh"
    if [ ! -e "$backup_dir" ]; then
        mkdir -p "$stage/retained-backup"
        chmod 700 "$stage/retained-backup"
        for item in agent wan.sh; do path=$(item_path "$item"); cp -p "$path" "$stage/retained-backup/$item"; done
        touch "$stage/retained-backup/complete"
        mv "$stage/retained-backup" "$backup_dir"
    fi
    complete_backup "$backup_dir" || { echo "Incomplete backup: $backup_dir. Nothing changed." >&2; exit 1; }
    source_dir=$stage
else
    complete_backup "$backup_dir" || { echo "No complete backup: $backup_dir. Nothing changed." >&2; exit 1; }
    for item in agent wan.sh; do sh -n "$backup_dir/$item"; done
    source_dir=$backup_dir
fi
printf 'Backup: %s\n' "$backup_dir"
mkdir -p "$stage/previous"
for item in agent wan.sh; do path=$(item_path "$item"); cp -p "$path" "$stage/previous/$item"; done
touch "$stage/previous/complete"
phase=install
changed=1
for item in agent wan.sh; do
    path=$(item_path "$item")
    cp "$source_dir/$item" "$path.new-__UPLOAD_NAME__"
    chmod 755 "$path.new-__UPLOAD_NAME__"
    mv -f "$path.new-__UPLOAD_NAME__" "$path"
done
phase=verify
for item in agent wan.sh; do
    path=$(item_path "$item")
    sh -n "$path"
    cmp "$source_dir/$item" "$path"
done
phase=heartbeat-reload
refresh_heartbeat
# The reloaded heartbeat recovers an enabled, crashed Xray instance when WAN
# returns. A healthy tunnel is not restarted by this deployment.
printf '\nWAN_RECOVERY_ROUTER_%s_OK\n' '__ACTION__'

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
    Write-Host "WAN recovery fix $Action finished successfully."
} finally {
    if (-not $PrepareOnly) {
        $Resolved = [System.IO.Path]::GetFullPath($StageRoot)
        $Expected = [System.IO.Path]::GetFullPath((Join-Path ([System.IO.Path]::GetTempPath()) $UploadName))
        if ($Resolved -ne $Expected -or (Split-Path -Leaf $Resolved) -ne $UploadName) { throw 'Unsafe staging cleanup path.' }
        if (Test-Path -LiteralPath $Resolved) { Remove-Item -LiteralPath $Resolved -Recurse -Force }
    }
}
