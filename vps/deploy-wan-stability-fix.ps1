param(
    [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$Router,
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$UploadName = 'owrt-wan-stability-' + [guid]::NewGuid().ToString('N')
$StageRoot = Join-Path ([System.IO.Path]::GetTempPath()) $UploadName
$Utf8 = [System.Text.UTF8Encoding]::new($false)
$Payloads = @{
    'agent' = 'files\usr\sbin\owrt-remote'
    'wan.sh' = 'files\usr\lib\owrt-remote\wan.sh'
}
$RemoteScript = @'
#!/bin/sh
set -eu
stage=/tmp/__UPLOAD_NAME__
backup=/root/owrt-remote-backup-before-wan-stability-v120
config=/etc/config/owrtremote
changed=0
fail() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}
item_path() {
    case "$1" in
        agent) printf /usr/sbin/owrt-remote ;;
        wan.sh) printf /usr/lib/owrt-remote/wan.sh ;;
    esac
}
save_files() {
    dir=$1
    mkdir -p "$dir"
    chmod 700 "$dir"
    cp -p "$config" "$dir/config"
    for item in agent wan.sh; do
        path=$(item_path "$item")
        if [ -f "$path" ]; then cp -p "$path" "$dir/$item"; else touch "$dir/$item.absent"; fi
    done
    xray_config=$(uci -q get owrtremote.main.xray_config || echo /etc/xray/owrt-remote-client.json)
    [ -n "$xray_config" ] || xray_config=/etc/xray/owrt-remote-client.json
    printf '%s' "$xray_config" >"$dir/xray-path"
    if [ -f "$xray_config" ]; then cp -p "$xray_config" "$dir/xray"; else touch "$dir/xray.absent"; fi
    chmod 600 "$dir/config" "$dir/xray-path"
    [ ! -f "$dir/xray" ] || chmod 600 "$dir/xray"
    touch "$dir/complete"
}
validate_backup() {
    local dir item
    dir=$1
    [ -f "$dir/complete" ] && [ -s "$dir/config" ] && [ -s "$dir/xray-path" ] || return 1
    for item in agent wan.sh xray; do
        [ -s "$dir/$item" ] || [ -f "$dir/$item.absent" ] || return 1
    done
}
restore_files() {
    dir=$1
    validate_backup "$dir" || { echo "Incomplete backup: $dir" >&2; return 1; }
    /etc/init.d/owrt-remote stop >/dev/null 2>&1 || true
    cp -p "$dir/config" "$config"
    for item in agent wan.sh; do
        path=$(item_path "$item")
        if [ -f "$dir/$item.absent" ]; then rm -f "$path"; else cp -p "$dir/$item" "$path"; fi
    done
    xray_config=$(cat "$dir/xray-path")
    if [ -f "$dir/xray.absent" ]; then rm -f "$xray_config"; else cp -p "$dir/xray" "$xray_config"; fi
    /etc/init.d/owrt-remote restart
}
cleanup() {
    code=$?
    trap - EXIT
    if [ "$code" -ne 0 ] && [ "$changed" = 1 ]; then
        echo 'Operation failed; restoring the previous files and settings.' >&2
        restore_files "$stage/previous" || echo 'Restore failed; use the retained backup.' >&2
    fi
    for item in agent wan.sh; do
        path=$(item_path "$item")
        rm -f "$path.new-__UPLOAD_NAME__"
    done
    rm -rf "$stage"
    exit "$code"
}
trap cleanup EXIT
test -s "$config" || fail "Router configuration is missing: $config. Nothing changed."
if [ '__ACTION__' = Install ]; then
    for item in agent wan.sh; do test -s "$stage/$item"; sh -n "$stage/$item"; done
    grep -F -q 'wan_table_routes()' "$stage/wan.sh"
    grep -F -q 'Xray configuration is missing or empty' "$stage/agent"
    if [ "$(uci -q get owrtremote.main.wan_direct || echo 0)" = 1 ]; then
        # Validate the candidate helper, including empty-table compatibility,
        # before stopping the installed service or modifying its files.
        OWRT_REMOTE_WAN_HELPER="$stage/wan.sh" sh "$stage/agent" wan-preflight
    fi
    if [ ! -e "$backup" ]; then
        save_files "$stage/retained-backup"
        mv "$stage/retained-backup" "$backup"
    fi
    validate_backup "$backup" || fail "Retained backup is incomplete: $backup. Nothing changed."
    save_files "$stage/previous"
    changed=1
    /etc/init.d/owrt-remote stop
    mkdir -p /usr/lib/owrt-remote
    for item in agent wan.sh; do
        path=$(item_path "$item")
        cp "$stage/$item" "$path.new-__UPLOAD_NAME__"
        case "$item" in wan.sh) chmod 644 "$path.new-__UPLOAD_NAME__" ;; *) chmod 755 "$path.new-__UPLOAD_NAME__" ;; esac
        mv -f "$path.new-__UPLOAD_NAME__" "$path"
    done
    /etc/init.d/owrt-remote restart
    if [ "$(uci -q get owrtremote.main.enabled || echo 0)" = 1 ]; then
        ready=0
        for attempt in 1 2 3 4 5 6 7 8 9 10; do
            if /usr/sbin/owrt-remote status | grep -q '^xray_state: running'; then ready=1; break; fi
            sleep 2
        done
        if [ "$ready" != 1 ]; then
            echo 'Xray did not start.' >&2
            logread | tail -n 50 >&2 || true
            exit 1
        fi
        ready=0
        for attempt in 1 2 3; do
            if /usr/sbin/owrt-remote heartbeat >"$stage/heartbeat" 2>&1 && grep -Eq '"ok"[[:space:]]*:[[:space:]]*true' "$stage/heartbeat"; then ready=1; break; fi
            sleep 2
        done
        if [ "$ready" != 1 ]; then
            echo 'Heartbeat did not reach Hub.' >&2
            cat "$stage/heartbeat" >&2
            exit 1
        fi
        echo 'Xray: running; Heartbeat: OK'
    fi
    for item in agent wan.sh; do path=$(item_path "$item"); cmp "$stage/$item" "$path"; done
else
    if [ ! -d "$backup" ]; then
        fail "No backup on this router: $backup. Rollback requires a previous Install on this same router. Nothing changed."
    fi
    validate_backup "$backup" || fail "Backup is incomplete: $backup. Nothing changed."
    save_files "$stage/previous"
    changed=1
    restore_files "$backup"
fi
printf '\nBackup: %s\nWAN_STABILITY_ROUTER_%s_OK\n' "$backup" '__ACTION__'
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
    if ($LASTEXITCODE -ne 0) { throw 'Upload failed. Installation has not started.' }
    & ssh -T -o ConnectTimeout=10 $Router "sh /tmp/$UploadName/run.sh"
    if ($LASTEXITCODE -ne 0) { throw "$Action failed. See the output above; success was not confirmed." }
    Write-Host "WAN stability fix $Action finished successfully."
} finally {
    if (-not $PrepareOnly) {
        $Resolved = [System.IO.Path]::GetFullPath($StageRoot)
        $Expected = [System.IO.Path]::GetFullPath((Join-Path ([System.IO.Path]::GetTempPath()) $UploadName))
        if ($Resolved -ne $Expected -or (Split-Path -Leaf $Resolved) -ne $UploadName) { throw 'Unsafe staging cleanup path.' }
        if (Test-Path -LiteralPath $Resolved) { Remove-Item -LiteralPath $Resolved -Recurse -Force }
    }
}
