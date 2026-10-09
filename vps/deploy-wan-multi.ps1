param(
    [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$Router,
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$UploadName = 'owrt-wan-multi-' + [guid]::NewGuid().ToString('N')
$StageRoot = Join-Path ([System.IO.Path]::GetTempPath()) $UploadName
$Utf8 = [System.Text.UTF8Encoding]::new($false)
$Payloads = @{
    'agent' = 'files\usr\sbin\owrt-remote'
    'wan.sh' = 'files\usr\lib\owrt-remote\wan.sh'
    'cgi' = 'files\www\cgi-bin\owrt-remote'
    'luci' = 'files\www\luci-static\resources\view\owrt_remote.js'
    'hotplug' = 'files\etc\hotplug.d\iface\99-owrt-remote'
}
$RemoteScript = @'
#!/bin/sh
set -eu
stage=/tmp/__UPLOAD_NAME__
backup=/root/owrt-remote-backup-before-wan-multi-v112
config=/etc/config/owrtremote
changed=0
item_path() {
    case "$1" in
        agent) printf /usr/sbin/owrt-remote ;;
        wan.sh) printf /usr/lib/owrt-remote/wan.sh ;;
        cgi) printf /www/cgi-bin/owrt-remote ;;
        luci) printf /www/luci-static/resources/view/owrt_remote.js ;;
        hotplug) printf /etc/hotplug.d/iface/99-owrt-remote ;;
    esac
}
save_files() {
    dir=$1
    mkdir -p "$dir"
    chmod 700 "$dir"
    cp -p "$config" "$dir/config"
    for item in agent wan.sh cgi luci hotplug; do
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
restore_files() {
    dir=$1
    test -f "$dir/complete"
    /etc/init.d/owrt-remote stop >/dev/null 2>&1 || true
    cp -p "$dir/config" "$config"
    for item in agent wan.sh cgi luci hotplug; do
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
        restore_files "$stage/previous" || true
    fi
    for item in agent wan.sh cgi luci hotplug; do
        path=$(item_path "$item")
        rm -f "$path.new-__UPLOAD_NAME__"
    done
    rm -rf "$stage"
    exit "$code"
}
trap cleanup EXIT
test -s "$config"
if [ '__ACTION__' = Install ]; then
    for item in agent wan.sh cgi hotplug; do test -s "$stage/$item"; sh -n "$stage/$item"; done
    test -s "$stage/luci"
    for item in cgi luci; do grep -F -q 'v112' "$stage/$item"; done
    grep -F -q 'wan_interface_picker' "$stage/cgi"
    grep -F -q 'wan_select_interface()' "$stage/wan.sh"
    grep -F -q 'wan_reconcile_xray()' "$stage/agent"
    if [ "$(uci -q get owrtremote.main.wan_direct || echo 0)" = 1 ]; then
        OWRT_REMOTE_WAN_HELPER="$stage/wan.sh" sh "$stage/agent" wan-preflight
    fi
    if [ ! -e "$backup" ]; then
        save_files "$stage/retained-backup"
        mv "$stage/retained-backup" "$backup"
    fi
    test -f "$backup/complete"
    save_files "$stage/previous"
    changed=1
    /etc/init.d/owrt-remote stop
    mkdir -p /usr/lib/owrt-remote /etc/hotplug.d/iface /www/luci-static/resources/view
    for item in agent wan.sh cgi luci hotplug; do
        path=$(item_path "$item")
        cp "$stage/$item" "$path.new-__UPLOAD_NAME__"
        case "$item" in
            wan.sh|luci) chmod 644 "$path.new-__UPLOAD_NAME__" ;;
            *) chmod 755 "$path.new-__UPLOAD_NAME__" ;;
        esac
        mv -f "$path.new-__UPLOAD_NAME__" "$path"
    done
    /etc/init.d/owrt-remote restart
    if [ "$(uci -q get owrtremote.main.enabled || echo 0)" = 1 ]; then
        ready=0
        for attempt in 1 2 3 4 5 6 7 8 9 10; do
            if /usr/sbin/owrt-remote status | grep -q '^xray_state: running'; then ready=1; break; fi
            sleep 2
        done
        test "$ready" = 1
    fi
    for item in agent wan.sh cgi luci hotplug; do
        path=$(item_path "$item")
        cmp "$stage/$item" "$path"
    done
else
    test -f "$backup/complete"
    save_files "$stage/previous"
    changed=1
    restore_files "$backup"
fi
printf '\nBackup: %s\nWAN_MULTI_ROUTER_%s_OK\n' "$backup" '__ACTION__'
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
    Write-Host "WAN multi-interface $Action finished successfully."
} finally {
    if (-not $PrepareOnly) {
        $Resolved = [System.IO.Path]::GetFullPath($StageRoot)
        $Expected = [System.IO.Path]::GetFullPath((Join-Path ([System.IO.Path]::GetTempPath()) $UploadName))
        if ($Resolved -ne $Expected -or (Split-Path -Leaf $Resolved) -ne $UploadName) { throw 'Unsafe staging cleanup path.' }
        if (Test-Path -LiteralPath $Resolved) { Remove-Item -LiteralPath $Resolved -Recurse -Force }
    }
}
