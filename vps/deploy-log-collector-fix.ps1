param(
    [ValidateNotNullOrEmpty()][string]$Router = 'root@192.168.2.1',
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$UploadName = 'owrt-log-collector-' + [guid]::NewGuid().ToString('N')
$StageRoot = Join-Path ([System.IO.Path]::GetTempPath()) $UploadName
$Utf8 = [System.Text.UTF8Encoding]::new($false)
$Payloads = @{
    'logs.sh' = 'files\usr\lib\owrt-remote\logs.sh'
    'logs-init' = 'files\etc\init.d\owrt-remote-logs'
}
$RemoteScript = @'
#!/bin/sh
set -eu
stage=/tmp/__UPLOAD_NAME__
backup_dir=/root/owrt-remote-backup-before-log-collector-v121
changed=0
phase=preflight
items='logs.sh logs-init'
item_path() {
    case "$1" in
        logs.sh) printf /usr/lib/owrt-remote/logs.sh ;;
        logs-init) printf /etc/init.d/owrt-remote-logs ;;
    esac
}
complete_backup() {
    [ -f "$1/complete" ] && [ -s "$1/logs.sh" ] && [ -s "$1/logs-init" ]
}
collector_running() {
    ubus call service list '{"name":"owrt-remote-logs"}' | jsonfilter -e '@["owrt-remote-logs"].instances.journal.running' | grep -qx true
}
save_files() {
    local item path
    mkdir -p "$1"
    chmod 700 "$1"
    for item in $items; do path=$(item_path "$item"); cp -p "$path" "$1/$item" || return 1; done
    if /etc/init.d/owrt-remote-logs enabled; then : >"$1/enabled"; fi
    if collector_running; then : >"$1/running"; fi
    : >"$1/complete"
}
restore_files() {
    local item path
    complete_backup "$1" || return 1
    /etc/init.d/owrt-remote-logs stop || true
    for item in $items; do
        path=$(item_path "$item")
        cp -p "$1/$item" "$path.new-__UPLOAD_NAME__" && mv -f "$path.new-__UPLOAD_NAME__" "$path" || return 1
    done
    if [ -f "$1/enabled" ]; then /etc/init.d/owrt-remote-logs enable; else /etc/init.d/owrt-remote-logs disable; fi
    if [ -f "$1/running" ]; then /etc/init.d/owrt-remote-logs start; fi
}
cleanup() {
    code=$?
    trap - EXIT
    if [ "$code" -ne 0 ]; then
        printf 'Failed during %s (rc=%s).\n' "$phase" "$code" >&2
        if [ "$changed" = 1 ]; then
            echo 'Restoring journal code/service state from this attempt; journal data retained.' >&2
            restore_files "$stage/previous" || echo 'Restore failed; retained backup is available.' >&2
        fi
    fi
    for item in $items; do path=$(item_path "$item"); rm -f "$path.new-__UPLOAD_NAME__"; done
    rm -rf "$stage"
    exit "$code"
}
trap cleanup EXIT
for item in $items; do path=$(item_path "$item"); test -s "$path"; done
command -v ubus >/dev/null
command -v jsonfilter >/dev/null
if [ '__ACTION__' = Install ]; then
    for item in $items; do test -s "$stage/$item"; sh -n "$stage/$item"; done
    command -v logread >/dev/null
    command -v logger >/dev/null
    command -v mkfifo >/dev/null
    grep -Fq 'log_collector_progress()' "$stage/logs.sh"
    grep -Fq 'LOG_HEARTBEAT_SECONDS=300' "$stage/logs.sh"
    if [ ! -e "$backup_dir" ]; then save_files "$stage/retained"; mv "$stage/retained" "$backup_dir"; fi
    complete_backup "$backup_dir" || { echo "Incomplete backup: $backup_dir. Nothing changed." >&2; exit 1; }
    source_dir=$stage
else
    complete_backup "$backup_dir" || { echo "No complete backup: $backup_dir. Nothing changed." >&2; exit 1; }
    for item in $items; do sh -n "$backup_dir/$item"; done
    source_dir=$backup_dir
fi
save_files "$stage/previous"
phase=install
changed=1
/etc/init.d/owrt-remote-logs stop
for item in $items; do
    path=$(item_path "$item")
    cp "$source_dir/$item" "$path.new-__UPLOAD_NAME__"
    case "$item" in logs.sh) chmod 644 "$path.new-__UPLOAD_NAME__" ;; *) chmod 755 "$path.new-__UPLOAD_NAME__" ;; esac
    mv -f "$path.new-__UPLOAD_NAME__" "$path"
done
if [ '__ACTION__' = Install ]; then
    /etc/init.d/owrt-remote-logs enable
    /etc/init.d/owrt-remote-logs start
    phase=verify-delivery
    marker=collector-check-__UPLOAD_NAME__
    ready=0
    for attempt in 1 2 3 4 5 6 7 8 9 10; do
        logger -t owrt-remote "$marker"
        sleep 1
        if collector_running && sh /usr/lib/owrt-remote/logs.sh read | grep -Fq "$marker"; then ready=1; break; fi
    done
    [ "$ready" = 1 ] || { echo 'Collector did not capture the test event; restoring code.' >&2; exit 1; }
    # A forced flush verifies the write path once at deployment, not each Heartbeat.
    phase=verify-flush
    sh /usr/lib/owrt-remote/logs.sh flush force
    sh /usr/lib/owrt-remote/logs.sh read | grep -Fq "$marker"
    sh /usr/lib/owrt-remote/logs.sh status
    sh /usr/lib/owrt-remote/logs.sh diagnostics
else
    if [ -f "$backup_dir/enabled" ]; then /etc/init.d/owrt-remote-logs enable; else /etc/init.d/owrt-remote-logs disable; fi
    if [ -f "$backup_dir/running" ]; then /etc/init.d/owrt-remote-logs start; fi
fi
for item in $items; do path=$(item_path "$item"); cmp "$source_dir/$item" "$path"; done
printf 'Backup: %s\n' "$backup_dir"
printf '\nLOG_COLLECTOR_ROUTER_%s_OK\n' '__ACTION__'
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
    Write-Host "Log collector fix $Action finished successfully."
} finally {
    if (-not $PrepareOnly) {
        $Resolved = [System.IO.Path]::GetFullPath($StageRoot)
        $Expected = [System.IO.Path]::GetFullPath((Join-Path ([System.IO.Path]::GetTempPath()) $UploadName))
        if ($Resolved -ne $Expected -or (Split-Path -Leaf $Resolved) -ne $UploadName) { throw 'Unsafe staging cleanup path.' }
        if (Test-Path -LiteralPath $Resolved) { Remove-Item -LiteralPath $Resolved -Recurse -Force }
    }
}
