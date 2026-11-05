param(
    [ValidateNotNullOrEmpty()][string]$Router = 'root@192.168.2.1',
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$UploadName = 'owrt-xray-boot-' + [guid]::NewGuid().ToString('N')
$StageRoot = Join-Path ([System.IO.Path]::GetTempPath()) $UploadName
$Utf8 = [System.Text.UTF8Encoding]::new($false)
$RemoteScript = @'
#!/bin/sh
set -eu
stage=/tmp/__UPLOAD_NAME__
target=/usr/sbin/owrt-remote
backup_dir=/root/owrt-remote-backup-before-xray-boot-v121
changed=0
phase=preflight
heartbeat_pid() {
    local state
    state=$(ubus call service list '{"name":"owrt-remote"}') || return 1
    printf '%s' "$state" | jsonfilter -e '@["owrt-remote"].instances.heartbeat.pid' || true
}
refresh_heartbeat() {
    local old_pid new_pid attempt
    old_pid=$(heartbeat_pid) || return 1
    case "$old_pid" in ''|0) echo 'Heartbeat stopped; it stays stopped.'; return 0 ;; *[!0-9]*) return 1 ;; esac
    ubus call service signal '{"name":"owrt-remote","instance":"heartbeat","signal":9}' >/dev/null || return 1
    attempt=0
    while [ "$attempt" -lt 20 ]; do
        sleep 1
        new_pid=$(heartbeat_pid) || return 1
        case "$new_pid" in ''|0|*[!0-9]*) ;; *) [ "$new_pid" = "$old_pid" ] || return 0 ;; esac
        attempt=$((attempt+1))
    done
    return 1
}
recover_stopped_xray() {
    local state running heartbeat enabled
    enabled=$(uci -q get owrtremote.main.enabled || true)
    case "$enabled" in 1|yes|true|on|enabled) ;; *) return 0 ;; esac
    state=$(ubus call service list '{"name":"owrt-remote"}') || return 1
    running=$(printf '%s' "$state" | jsonfilter -e '@["owrt-remote"].instances.xray.running' || true)
    heartbeat=$(printf '%s' "$state" | jsonfilter -e '@["owrt-remote"].instances.heartbeat.running' || true)
    [ "$running" != true ] && [ "$heartbeat" = true ] || return 0
    ubus call service add '{"name":"owrt-remote","instances":{"xray":{"command":["/usr/sbin/owrt-remote","run"],"env":{"OWRT_REMOTE_SERVICE_LOG":"1"},"respawn":["3600","5","5"],"stdout":true,"stderr":true}}}' >/dev/null
}
cleanup() {
    code=$?
    trap - EXIT
    if [ "$code" -ne 0 ]; then
        printf 'Failed during %s (rc=%s).\n' "$phase" "$code" >&2
        if [ "$changed" = 1 ]; then
            echo 'Restoring the agent from this attempt. Working Xray/configuration retained.' >&2
            if cp -p "$stage/previous" "$target.new-__UPLOAD_NAME__" && mv -f "$target.new-__UPLOAD_NAME__" "$target"; then
                refresh_heartbeat || echo 'Heartbeat reload failed; retained backup remains available.' >&2
            fi
        fi
    fi
    rm -f "$target.new-__UPLOAD_NAME__"
    rm -rf "$stage"
    exit "$code"
}
trap cleanup EXIT
trap 'exit 1' INT TERM
test -s "$target"
command -v ubus >/dev/null
command -v jsonfilter >/dev/null
heartbeat_pid >/dev/null
if [ '__ACTION__' = Install ]; then
    test -s "$stage/agent"
    sh -n "$stage/agent"
    grep -Fq 'xray_cache_restore()' "$stage/agent"
    grep -Fq 'xray_bootstrap_lock()' "$stage/agent"
    command -v gzip >/dev/null
    command -v sha256sum >/dev/null
    if [ ! -e "$backup_dir" ]; then
        mkdir -p "$stage/retained"
        chmod 700 "$stage/retained"
        cp -p "$target" "$stage/retained/agent"
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
    phase=prepare-xray
    # Seed the boot cache now, while an existing RAM executable is still here.
    # Never restart a healthy tunnel or replace the user's client JSON.
    "$target" prepare-xray
fi
phase=heartbeat-reload
refresh_heartbeat
if [ '__ACTION__' = Install ]; then
    phase=xray-recovery
    recover_stopped_xray
fi
printf 'Backup: %s\n' "$backup_dir"
echo 'Agent installed. Tunnel readiness must be checked in the VPS card/logs.'
printf '\nXRAY_BOOT_ROUTER_%s_OK\n' '__ACTION__'
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
    Write-Host "Xray boot fix $Action finished successfully."
} finally {
    if (-not $PrepareOnly) {
        $Resolved = [System.IO.Path]::GetFullPath($StageRoot)
        $Expected = [System.IO.Path]::GetFullPath((Join-Path ([System.IO.Path]::GetTempPath()) $UploadName))
        if ($Resolved -ne $Expected -or (Split-Path -Leaf $Resolved) -ne $UploadName) { throw 'Unsafe staging cleanup path.' }
        if (Test-Path -LiteralPath $Resolved) { Remove-Item -LiteralPath $Resolved -Recurse -Force }
    }
}
