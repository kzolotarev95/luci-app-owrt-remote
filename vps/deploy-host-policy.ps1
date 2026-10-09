param(
    [Parameter(Mandatory = $true)][ValidateSet('Router', 'Vps')][string]$Target,
    [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$Machine,
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [string]$PinnedVpsHost = '193.233.82.38',
    [ValidatePattern('^[a-zA-Z0-9_-]+$')][string]$WanInterface = 'wan',
    [switch]$PreserveConnectionSettings,
    [ValidatePattern('^[a-zA-Z0-9_-]+$')][string]$BackupName = 'host-policy-v112',
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$UploadName = 'owrt-host-policy-' + [guid]::NewGuid().ToString('N')
$StageRoot = Join-Path ([System.IO.Path]::GetTempPath()) $UploadName
$Utf8 = [System.Text.UTF8Encoding]::new($false)
$Payloads = @{}
if ($PinnedVpsHost -notmatch '^[a-zA-Z0-9][a-zA-Z0-9.:-]*$') { throw 'Invalid VPS host. Use an IP or domain without URL or port.' }

if ($Target -eq 'Router') {
    $Payloads = @{
        'agent' = 'files\usr\sbin\owrt-remote'
        'wan.sh' = 'files\usr\lib\owrt-remote\wan.sh'
        'cgi' = 'files\www\cgi-bin\owrt-remote'
        'luci' = 'files\www\luci-static\resources\view\owrt_remote.js'
        'init' = 'files\etc\init.d\owrt-remote'
    }
    $RemoteScript = @'
#!/bin/sh
set -eu
stage=/tmp/__UPLOAD_NAME__
backup=/root/owrt-remote-backup-before-__BACKUP_NAME__
config=/etc/config/owrtremote
changed=0
save_files() {
    dir=$1
    mkdir -p "$dir"
    chmod 700 "$dir"
    cp -p "$config" "$dir/config"
    for item in agent wan.sh cgi luci init; do
        case "$item" in
            agent) path=/usr/sbin/owrt-remote ;;
            wan.sh) path=/usr/lib/owrt-remote/wan.sh ;;
            cgi) path=/www/cgi-bin/owrt-remote ;;
            luci) path=/www/luci-static/resources/view/owrt_remote.js ;;
            init) path=/etc/init.d/owrt-remote ;;
        esac
        if [ -f "$path" ]; then cp -p "$path" "$dir/$item"; else touch "$dir/$item.absent"; fi
    done
    xray_config=$(uci -q get owrtremote.main.xray_config || echo /etc/xray/owrt-remote-client.json)
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
    /usr/sbin/owrt-remote wan-cleanup >/dev/null 2>&1 || true
    cp -p "$dir/config" "$config"
    for item in agent wan.sh cgi luci init; do
        case "$item" in
            agent) path=/usr/sbin/owrt-remote ;;
            wan.sh) path=/usr/lib/owrt-remote/wan.sh ;;
            cgi) path=/www/cgi-bin/owrt-remote ;;
            luci) path=/www/luci-static/resources/view/owrt_remote.js ;;
            init) path=/etc/init.d/owrt-remote ;;
        esac
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
        echo 'Operation failed; restoring this attempt.' >&2
        logread -e owrt-remote | tail -n 25 >&2 || true
        restore_files "$stage/previous" || true
    fi
    rm -rf "$stage"
    exit "$code"
}
trap cleanup EXIT
test -s "$config"
if [ '__ACTION__' = Install ]; then
    for item in agent wan.sh cgi init; do test -s "$stage/$item"; sh -n "$stage/$item"; done
    test -s "$stage/luci"
    for item in cgi luci; do grep -F -q 'v112' "$stage/$item"; done
    # curl is needed to preserve HTTPS SNI and bypass the local DNS/proxy.
    if ! command -v curl >/dev/null 2>&1; then
        if command -v opkg >/dev/null 2>&1; then opkg update && opkg install curl ca-bundle
        elif command -v apk >/dev/null 2>&1; then apk add curl ca-certificates
        else echo 'Install curl and CA certificates first.' >&2; exit 1; fi
    fi
    for tool in ip nft ubus jsonfilter nslookup curl; do command -v "$tool" >/dev/null 2>&1 || { echo "Missing tool: $tool" >&2; exit 1; }; done
    # Check the router's real ip implementation and WAN before stopping Remote.
    if [ '__PRESERVE_CONNECTION__' = 1 ]; then
        expected_host=$(uci -q get owrtremote.main.vps_host)
        expected_mode=$(uci -q get owrtremote.main.vps_host_mode || echo manual)
        expected_direct=$(uci -q get owrtremote.main.wan_direct || echo 0)
        if [ "$expected_direct" = 1 ]; then OWRT_REMOTE_WAN_HELPER="$stage/wan.sh" sh "$stage/agent" wan-preflight; fi
    else
        expected_host='__PINNED_HOST__'
        expected_mode=manual
        expected_direct=1
        OWRT_REMOTE_WAN_HELPER="$stage/wan.sh" OWRT_REMOTE_WAN_INTERFACE='__WAN_INTERFACE__' sh "$stage/agent" wan-preflight
    fi
    if [ ! -e "$backup" ]; then
        save_files "$stage/retained-backup"
        mv "$stage/retained-backup" "$backup"
    fi
    test -f "$backup/complete"
    save_files "$stage/previous"
    changed=1
    # procd may already have no instance after a previous failed installation.
    /etc/init.d/owrt-remote stop || true
    mkdir -p /usr/lib/owrt-remote /www/luci-static/resources/view
    for item in agent wan.sh cgi luci init; do
        case "$item" in
            agent) path=/usr/sbin/owrt-remote; mode=755 ;;
            wan.sh) path=/usr/lib/owrt-remote/wan.sh; mode=644 ;;
            cgi) path=/www/cgi-bin/owrt-remote; mode=755 ;;
            luci) path=/www/luci-static/resources/view/owrt_remote.js; mode=644 ;;
            init) path=/etc/init.d/owrt-remote; mode=755 ;;
        esac
        cp "$stage/$item" "$path.new"
        chmod "$mode" "$path.new"
        mv "$path.new" "$path"
    done
    if [ '__PRESERVE_CONNECTION__' != 1 ]; then
        uci set 'owrtremote.main.vps_host=__PINNED_HOST__'
        uci set owrtremote.main.vps_host_mode=manual
        uci set owrtremote.main.wan_direct=1
        uci set 'owrtremote.main.wan_interface=__WAN_INTERFACE__'
        uci commit owrtremote
    fi
    /usr/sbin/owrt-remote render-client >/dev/null
    /etc/init.d/owrt-remote restart
    ready=0
    for attempt in 1 2 3 4 5 6 7 8 9 10; do
        if /usr/sbin/owrt-remote status | grep -q '^xray_state: running'; then ready=1; break; fi
        sleep 2
    done
    test "$ready" = 1
    /usr/sbin/owrt-remote heartbeat
    if [ "$expected_mode" = manual ]; then test "$(uci -q get owrtremote.main.vps_host)" = "$expected_host"; fi
    test "$(uci -q get owrtremote.main.vps_host_mode || echo manual)" = "$expected_mode"
    test "$(uci -q get owrtremote.main.wan_direct || echo 0)" = "$expected_direct"
    if nft list table inet owrt_remote_wan >/dev/null 2>&1; then
        echo 'Legacy Remote marking table still exists.' >&2
        exit 1
    fi
    /usr/sbin/owrt-remote status
    grep -F -q 'v112' /www/cgi-bin/owrt-remote
    grep -F -q 'v112' /www/luci-static/resources/view/owrt_remote.js
    echo HOST_POLICY_ROUTER_V112_OK
else
    test -f "$backup/complete"
    save_files "$stage/previous"
    changed=1
    restore_files "$backup"
    /usr/sbin/owrt-remote status
    echo HOST_POLICY_ROUTER_V112_ROLLBACK_OK
fi
'@
} else {
    $Payloads = @{
        'hub.py' = 'vps\owrt-remote-hub.py'
        'test_host_policy.py' = 'tests\test_host_policy.py'
        'resources.py' = 'tests\test_resource_lifecycle.py'
        'recaptcha.py' = 'tests\test_recaptcha.py'
    }
    $RemoteScript = @'
#!/bin/sh
set -eu
stage=/tmp/__UPLOAD_NAME__
target=/opt/owrt-remote/owrt-remote-hub.py
backup=/opt/owrt-remote/owrt-remote-hub.py.bak-before-host-policy-v112
changed=0
cleanup() {
    code=$?
    trap - EXIT
    if [ "$code" -ne 0 ] && [ "$changed" = 1 ]; then
        echo 'Operation failed; restoring this attempt.' >&2
        journalctl -u owrt-remote -n 30 --no-pager >&2 || true
        cp -p "$stage/previous.py" "$target.new"
        mv "$target.new" "$target"
        systemctl restart owrt-remote || true
    fi
    rm -rf "$stage"
    exit "$code"
}
trap cleanup EXIT
test -s "$target"
python_bin=/opt/owrt-remote/venv/bin/python
[ -x "$python_bin" ] || python_bin=python3
if [ '__ACTION__' = Install ]; then
    grep -F -q 'v112' "$stage/hub.py"
    "$python_bin" -m py_compile "$stage/hub.py"
    OWRT_HOST_POLICY_HUB_SOURCE="$stage/hub.py" "$python_bin" "$stage/test_host_policy.py" HostPolicyTests -v
    OWRT_RESOURCE_HUB_SOURCE="$stage/hub.py" "$python_bin" "$stage/resources.py" -v
    OWRT_RECAPTCHA_HUB_SOURCE="$stage/hub.py" "$python_bin" "$stage/recaptcha.py" -v
    if [ ! -e "$backup" ]; then cp -p "$target" "$backup"; fi
    test -s "$backup"
    candidate="$stage/hub.py"
else
    test -s "$backup"
    "$python_bin" -m py_compile "$backup"
    candidate="$backup"
fi
cp -p "$target" "$stage/previous.py"
cp "$candidate" "$target.new"
chmod 755 "$target.new"
changed=1
mv "$target.new" "$target"
systemctl restart owrt-remote
healthy=0
for attempt in 1 2 3 4 5 6 7 8 9 10; do
    if systemctl is-active --quiet owrt-remote && curl -fsS --max-time 5 http://127.0.0.1:8088/health; then healthy=1; break; fi
    sleep 2
done
test "$healthy" = 1
cmp "$candidate" "$target"
printf '\nHOST_POLICY_VPS_V112_%s_OK\n' '__ACTION__'
'@
}

$PreserveValue = if ($PreserveConnectionSettings) { '1' } else { '0' }
$RemoteScript = $RemoteScript.Replace('__UPLOAD_NAME__', $UploadName).Replace('__ACTION__', $Action).Replace('__PINNED_HOST__', $PinnedVpsHost).Replace('__WAN_INTERFACE__', $WanInterface).Replace('__PRESERVE_CONNECTION__', $PreserveValue).Replace('__BACKUP_NAME__', $BackupName)
try {
    [System.IO.Directory]::CreateDirectory($StageRoot) | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $StageRoot 'run.sh'), $RemoteScript.Replace("`r`n", "`n") + "`n", $Utf8)
    if ($Action -eq 'Install') {
        foreach ($Item in $Payloads.GetEnumerator()) {
            $Source = Join-Path $RepoRoot $Item.Value
            $Content = [System.IO.File]::ReadAllText($Source).Replace("`r`n", "`n")
            [System.IO.File]::WriteAllText((Join-Path $StageRoot $Item.Key), $Content, $Utf8)
        }
    }
    if ($PrepareOnly) { Write-Host "Prepared only: $StageRoot"; return }
    & scp -O -r -- $StageRoot "${Machine}:/tmp/"
    if ($LASTEXITCODE -ne 0) { throw 'Upload failed. The installation has not started.' }
    & ssh -T -o ConnectTimeout=10 $Machine "sh /tmp/$UploadName/run.sh"
    if ($LASTEXITCODE -ne 0) { throw "$Action failed. See the output above; success was not confirmed." }
    Write-Host "$Target $Action finished successfully."
} finally {
    if (-not $PrepareOnly) {
        $Resolved = [System.IO.Path]::GetFullPath($StageRoot)
        $Expected = [System.IO.Path]::GetFullPath((Join-Path ([System.IO.Path]::GetTempPath()) $UploadName))
        if ($Resolved -ne $Expected -or (Split-Path -Leaf $Resolved) -ne $UploadName) { throw 'Unsafe staging cleanup path.' }
        if (Test-Path -LiteralPath $Resolved) { Remove-Item -LiteralPath $Resolved -Recurse -Force }
    }
}
