param(
    [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$Router,
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$UploadName = 'owrt-global-logs-' + [guid]::NewGuid().ToString('N')
$StageRoot = Join-Path ([System.IO.Path]::GetTempPath()) $UploadName
$Utf8 = [System.Text.UTF8Encoding]::new($false)
$RemoteScript = @'
#!/bin/sh
set -eu
stage=/tmp/__UPLOAD_NAME__
backup=/root/owrt-remote-backup-before-global-logs-stop-v120
items='cgi agent init logs.sh logs-init luci'
changed=0
phase=preflight
item_path() {
    case "$1" in
        cgi) echo /www/cgi-bin/owrt-remote ;;
        agent) echo /usr/sbin/owrt-remote ;;
        init) echo /etc/init.d/owrt-remote ;;
        logs.sh) echo /usr/lib/owrt-remote/logs.sh ;;
        logs-init) echo /etc/init.d/owrt-remote-logs ;;
        luci) echo /www/luci-static/resources/view/owrt_remote.js ;;
    esac
}
refresh_router_ui() {
    rm -rf /tmp/luci-indexcache /tmp/luci-modulecache
    if [ -x /etc/init.d/rpcd ]; then /etc/init.d/rpcd restart || true; fi
    if [ -x /etc/init.d/uhttpd ]; then /etc/init.d/uhttpd reload || true; fi
}
save_files() {
    local dir item path
    dir=$1
    mkdir -p "$dir"
    chmod 700 "$dir"
    for item in $items; do
        path=$(item_path "$item")
        if [ -e "$path" ]; then cp -p "$path" "$dir/$item"; else : >"$dir/$item.absent"; fi
    done
    if [ -x /etc/init.d/owrt-remote-logs ] && /etc/init.d/owrt-remote-logs enabled; then : >"$dir/logs-enabled"; fi
    if ubus call service list '{"name":"owrt-remote-logs"}' 2>/dev/null | grep -q '"running": true'; then : >"$dir/logs-running"; fi
    if /usr/sbin/owrt-remote status 2>/dev/null | grep -q '^xray_state: running'; then : >"$dir/tunnel-running"; fi
    : >"$dir/complete"
}
validate_backup() {
    local dir item
    dir=$1
    [ -f "$dir/complete" ] || return 1
    for item in $items; do
        [ -s "$dir/$item" ] || [ -f "$dir/$item.absent" ] || return 1
    done
}
restore_files() {
    local dir item path
    dir=$1
    validate_backup "$dir" || { echo "Incomplete backup: $dir" >&2; return 1; }
    if [ -x /etc/init.d/owrt-remote-logs ]; then
        /etc/init.d/owrt-remote-logs stop || true
        /etc/init.d/owrt-remote-logs disable || true
    fi
    for item in $items; do
        path=$(item_path "$item")
        if [ -f "$dir/$item.absent" ]; then
            rm -f "$path" || return 1
        else
            cp -p "$dir/$item" "$path" || return 1
        fi
    done
    if [ -x /etc/init.d/owrt-remote-logs ]; then
        [ ! -f "$dir/logs-enabled" ] || /etc/init.d/owrt-remote-logs enable
        [ ! -f "$dir/logs-running" ] || /etc/init.d/owrt-remote-logs start
    else
        rm -rf /tmp/owrt-remote-logs
    fi
    if [ -f "$stage/previous/tunnel-running" ]; then /etc/init.d/owrt-remote restart; fi
    refresh_router_ui
}
cleanup() {
    code=$?
    trap - EXIT
    if [ "$code" -ne 0 ]; then
        printf 'Failed during %s (rc=%s).\n' "$phase" "$code" >&2
        if [ "$changed" = 1 ]; then
            echo 'Restoring files from this attempt.' >&2
            restore_files "$stage/previous" || echo 'Restore failed; retained backup is available.' >&2
        fi
    fi
    for item in $items; do path=$(item_path "$item"); rm -f "$path.new-__UPLOAD_NAME__"; done
    rm -rf "$stage"
    exit "$code"
}
trap cleanup EXIT
for item in cgi agent init; do path=$(item_path "$item"); test -s "$path"; done
if [ '__ACTION__' = Install ]; then
    for item in $items; do test -s "$stage/$item"; done
    for item in cgi agent init logs.sh logs-init; do sh -n "$stage/$item"; done
    command -v logread >/dev/null
    command -v ubus >/dev/null
    command -v mkfifo >/dev/null
    command -v base64 >/dev/null
    command -v jsonfilter >/dev/null
    grep -F -q 'globalLogsToggle' "$stage/cgi"
    for item in cgi luci; do grep -F -q 'v120' "$stage/$item"; done
    grep -F -q 'LOG_KEEP_SECONDS=86400' "$stage/logs.sh"
    grep -F -q 'log_sync_loop' "$stage/logs.sh"
    grep -F -q 'sync_global_logs' "$stage/agent"
    phase=backup
    if [ ! -e "$backup" ]; then save_files "$stage/retained"; mv "$stage/retained" "$backup"; fi
    validate_backup "$backup" || { echo "Incomplete backup: $backup. Nothing changed." >&2; exit 1; }
    save_files "$stage/previous"
    phase=install
    changed=1
    if [ -x /etc/init.d/owrt-remote-logs ]; then /etc/init.d/owrt-remote-logs stop; fi
    mkdir -p /usr/lib/owrt-remote /www/luci-static/resources/view
    for item in $items; do
        path=$(item_path "$item")
        cp "$stage/$item" "$path.new-__UPLOAD_NAME__"
        case "$item" in logs.sh|luci) chmod 644 "$path.new-__UPLOAD_NAME__" ;; *) chmod 755 "$path.new-__UPLOAD_NAME__" ;; esac
        mv -f "$path.new-__UPLOAD_NAME__" "$path"
    done
    /etc/init.d/owrt-remote-logs enable
    /etc/init.d/owrt-remote-logs start
    phase=verify
    ready=0
    for attempt in 1 2 3 4 5; do
        if ubus call service list '{"name":"owrt-remote-logs"}' | grep -q '"running": true'; then ready=1; break; fi
        sleep 1
    done
    if [ "$ready" != 1 ]; then echo 'Log collector did not start.' >&2; logread -e owrt-remote | tail -n 30 >&2; exit 1; fi
    # Generate UTF-8 on the router; Windows PowerShell 5.1 reads scripts as ANSI.
    marker="$(printf '\320\223\320\273\320\276\320\261\320\260\320\273\040\320\273\320\276\320\263\320\270\072') collector-__UPLOAD_NAME__"
    ready=0
    for attempt in 1 2 3 4 5; do
        logger -t owrt-remote "$marker"
        sleep 1
        if sh /usr/lib/owrt-remote/logs.sh read | grep -F -q "$marker"; then ready=1; break; fi
    done
    if [ "$ready" != 1 ]; then echo 'Log collector is running but did not capture the test event.' >&2; exit 1; fi
    for item in $items; do path=$(item_path "$item"); cmp "$stage/$item" "$path"; done
    if [ -f "$stage/previous/tunnel-running" ]; then
        phase=tunnel-restart
        /etc/init.d/owrt-remote restart
        ready=0
        for attempt in 1 2 3 4 5 6 7 8 9 10; do
            if /usr/sbin/owrt-remote status | grep -q '^xray_state: running'; then ready=1; break; fi
            sleep 1
        done
        if [ "$ready" != 1 ]; then echo 'Tunnel did not restart.' >&2; logread -e owrt-remote | tail -n 30 >&2; exit 1; fi
    fi
    refresh_router_ui
    echo 'Log collector: running. Router settings preserved.'
else
    validate_backup "$backup" || { echo "No complete backup: $backup. Nothing changed." >&2; exit 1; }
    save_files "$stage/previous"
    changed=1
    phase=rollback
    restore_files "$backup"
fi
printf 'Backup: %s\n' "$backup"
printf '\nGLOBAL_LOGS_ROUTER_%s_OK\n' '__ACTION__'
'@
$RemoteScript = $RemoteScript.Replace('__UPLOAD_NAME__', $UploadName).Replace('__ACTION__', $Action)
$Files = @{
    'cgi' = 'files\www\cgi-bin\owrt-remote'
    'agent' = 'files\usr\sbin\owrt-remote'
    'init' = 'files\etc\init.d\owrt-remote'
    'logs.sh' = 'files\usr\lib\owrt-remote\logs.sh'
    'logs-init' = 'files\etc\init.d\owrt-remote-logs'
    'luci' = 'files\www\luci-static\resources\view\owrt_remote.js'
}
try {
    [System.IO.Directory]::CreateDirectory($StageRoot) | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $StageRoot 'run.sh'), $RemoteScript.Replace("`r`n", "`n") + "`n", $Utf8)
    if ($Action -eq 'Install') {
        foreach ($Item in $Files.GetEnumerator()) {
            $Content = [System.IO.File]::ReadAllText((Join-Path $RepoRoot $Item.Value)).Replace("`r`n", "`n")
            [System.IO.File]::WriteAllText((Join-Path $StageRoot $Item.Key), $Content, $Utf8)
        }
    }
    if ($PrepareOnly) { Write-Host "Prepared only: $StageRoot"; return }
    & scp -O -r -- $StageRoot "${Router}:/tmp/"
    if ($LASTEXITCODE -ne 0) { throw 'Upload failed. The installation has not started.' }
    & ssh -T -o ConnectTimeout=10 $Router "sh /tmp/$UploadName/run.sh"
    if ($LASTEXITCODE -ne 0) { throw "$Action failed. See the output above; success was not confirmed." }
    Write-Host "Global logs $Action finished successfully. Refresh the router panel with Ctrl+F5."
} finally {
    if (-not $PrepareOnly) {
        $Resolved = [System.IO.Path]::GetFullPath($StageRoot)
        $Expected = [System.IO.Path]::GetFullPath((Join-Path ([System.IO.Path]::GetTempPath()) $UploadName))
        if ($Resolved -ne $Expected -or (Split-Path -Leaf $Resolved) -ne $UploadName) { throw 'Unsafe staging cleanup path.' }
        if (Test-Path -LiteralPath $Resolved) { Remove-Item -LiteralPath $Resolved -Recurse -Force }
    }
}
