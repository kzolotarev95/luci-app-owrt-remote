param(
    [Parameter(Mandatory = $true)][ValidateSet('Router', 'Vps')][string]$Target,
    [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$Machine,
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [string]$HubUrl = '',
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$UploadName = 'owrt-hub-url-' + [guid]::NewGuid().ToString('N')
$StageRoot = Join-Path ([System.IO.Path]::GetTempPath()) $UploadName
$Utf8 = [System.Text.UTF8Encoding]::new($false)
if ($HubUrl) {
    $HubUri = $null
    if (-not [Uri]::TryCreate($HubUrl, [UriKind]::Absolute, [ref]$HubUri) -or
        $HubUri.Scheme -notin @('http', 'https') -or -not $HubUri.Host -or
        $HubUri.UserInfo -or $HubUri.Query -or $HubUri.Fragment -or
        $HubUri.AbsolutePath -ne '/' -or $HubUrl -notmatch '^https?://[A-Za-z0-9.\[\]:-]+/?$') {
        throw 'HubUrl must be an HTTP/HTTPS origin, for example https://hub.example:9443.'
    }
    if ($Target -ne 'Router' -or $Action -ne 'Install') { throw 'HubUrl is only used by Router Install.' }
    $HubUrl = $HubUrl.TrimEnd('/')
}

if ($Target -eq 'Router') {
    $Payloads = @{
        'agent' = 'files\usr\sbin\owrt-remote'
        'wan.sh' = 'files\usr\lib\owrt-remote\wan.sh'
        'cgi' = 'files\www\cgi-bin\owrt-remote'
        'luci' = 'files\www\luci-static\resources\view\owrt_remote.js'
    }
    $RemoteScript = @'
#!/bin/sh
set -eu
stage=/tmp/__UPLOAD_NAME__
backup=/root/owrt-remote-backup-before-hub-url-v121
chmod 700 "$stage"
config=/etc/config/owrtremote
changed=0
item_path() {
    case "$1" in
        agent) printf /usr/sbin/owrt-remote ;;
        wan.sh) printf /usr/lib/owrt-remote/wan.sh ;;
        cgi) printf /www/cgi-bin/owrt-remote ;;
        luci) printf /www/luci-static/resources/view/owrt_remote.js ;;
    esac
}
save_files() {
    dir=$1
    mkdir -p "$dir"
    chmod 700 "$dir"
    cp -p "$config" "$dir/config"
    for item in agent wan.sh cgi luci; do
        path=$(item_path "$item")
        test -s "$path"
        cp -p "$path" "$dir/$item"
    done
    xray_config=$(uci -q get owrtremote.main.xray_config || true)
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
    for item in agent wan.sh cgi luci; do
        path=$(item_path "$item")
        cp -p "$dir/$item" "$path"
    done
    xray_config=$(cat "$dir/xray-path")
    if [ -f "$dir/xray.absent" ]; then rm -f "$xray_config"; else cp -p "$dir/xray" "$xray_config"; fi
    /etc/init.d/owrt-remote restart
}
cleanup() {
    code=$?
    trap - EXIT
    if [ "$code" -ne 0 ] && [ "$changed" = 1 ]; then
        echo 'Operation failed; restoring the files and settings from this attempt.' >&2
        restore_files "$stage/previous" || true
    fi
    for item in agent wan.sh cgi luci; do
        path=$(item_path "$item")
        rm -f "$path.new-__UPLOAD_NAME__"
    done
    rm -rf "$stage"
    exit "$code"
}
trap cleanup EXIT
test -s "$config"
if [ '__ACTION__' = Install ]; then
    for item in agent wan.sh cgi; do test -s "$stage/$item"; sh -n "$stage/$item"; done
    test -s "$stage/luci"
    grep -F -q 'heartbeat_set_url_if_changed()' "$stage/agent"
    grep -F -q 'reason=https-downgrade' "$stage/agent"
    for item in agent wan.sh; do grep -F -q -- '--post301' "$stage/$item"; done
    for item in cgi luci; do grep -F -q 'v121' "$stage/$item"; done
    if [ ! -e "$backup" ]; then
        save_files "$stage/retained-backup"
        mv "$stage/retained-backup" "$backup"
    fi
    test -f "$backup/complete"
    save_files "$stage/previous"
    changed=1
    /etc/init.d/owrt-remote stop >/dev/null 2>&1 || true
    for item in agent wan.sh cgi luci; do
        path=$(item_path "$item")
        cp "$stage/$item" "$path.new-__UPLOAD_NAME__"
        case "$item" in wan.sh|luci) chmod 644 "$path.new-__UPLOAD_NAME__" ;; *) chmod 755 "$path.new-__UPLOAD_NAME__" ;; esac
        mv -f "$path.new-__UPLOAD_NAME__" "$path"
    done
    if [ -n '__HUB_URL__' ]; then
        uci set 'owrtremote.main.hub_url=__HUB_URL__'
        uci commit owrtremote
    fi
    /etc/init.d/owrt-remote restart
    for item in agent wan.sh cgi luci; do
        path=$(item_path "$item")
        cmp "$stage/$item" "$path"
    done
    printf 'Installed Hub URL: %s\n' "$(uci -q get owrtremote.main.hub_url || true)"
    # Failure to reach Hub is reported separately from successful file installation.
    if /usr/sbin/owrt-remote heartbeat >"$stage/heartbeat.json"; then
        printf 'HEARTBEAT_OK\n'
    else
        printf 'Files installed, but Heartbeat failed. Check the connection and router logs.\n' >&2
        printf 'HEARTBEAT_FAILED\n'
    fi
    printf 'Hub URL after Heartbeat: %s\n' "$(uci -q get owrtremote.main.hub_url || true)"
else
    test -f "$backup/complete"
    save_files "$stage/previous"
    changed=1
    restore_files "$backup"
fi
printf '\nBackup: %s\nHUB_URL_ROUTER_V121_%s_OK\n' "$backup" '__ACTION__'
'@
} else {
    $Payloads = @{
        'hub.py' = 'vps\owrt-remote-hub.py'
        'host_policy.py' = 'tests\test_host_policy.py'
        'resources.py' = 'tests\test_resource_lifecycle.py'
    }
    $RemoteScript = @'
#!/bin/sh
set -eu
stage=/tmp/__UPLOAD_NAME__
target=/opt/owrt-remote/owrt-remote-hub.py
chmod 700 "$stage"
backup=/opt/owrt-remote/owrt-remote-hub.py.bak-before-hub-url-v121
candidate=$target.new-__UPLOAD_NAME__
changed=0
cleanup() {
    code=$?
    trap - EXIT
    if [ "$code" -ne 0 ] && [ "$changed" = 1 ]; then
        echo 'Operation failed; restoring Hub from this attempt.' >&2
        cp -p "$stage/previous.py" "$candidate"
        mv -f "$candidate" "$target"
        systemctl restart owrt-remote || true
    fi
    rm -f "$candidate"
    rm -rf "$stage"
    exit "$code"
}
trap cleanup EXIT
test -s "$target"
python_bin=/opt/owrt-remote/venv/bin/python
[ -x "$python_bin" ] || python_bin=python3
if [ '__ACTION__' = Install ]; then
    grep -F -q 'def router_hub_origins(' "$stage/hub.py"
    grep -F -q 'v121' "$stage/hub.py"
    "$python_bin" -m py_compile "$stage/hub.py"
    OWRT_HOST_POLICY_HUB_SOURCE="$stage/hub.py" "$python_bin" "$stage/host_policy.py" HostPolicyTests -v
    OWRT_RESOURCE_HUB_SOURCE="$stage/hub.py" "$python_bin" "$stage/resources.py" IntegrationTests -v
    if [ ! -e "$backup" ]; then cp -p "$target" "$backup"; fi
    source_file="$stage/hub.py"
else
    test -s "$backup"
    "$python_bin" -m py_compile "$backup"
    source_file="$backup"
fi
cp -p "$target" "$stage/previous.py"
cp "$source_file" "$candidate"
chmod 755 "$candidate"
changed=1
mv -f "$candidate" "$target"
systemctl restart owrt-remote
healthy=0
for attempt in 1 2 3 4 5 6 7 8 9 10; do
    if systemctl is-active --quiet owrt-remote && curl -fsS --max-time 5 http://127.0.0.1:8088/health; then healthy=1; break; fi
    sleep 2
done
test "$healthy" = 1
cmp "$source_file" "$target"
printf '\nBackup: %s\nHUB_URL_VPS_V121_%s_OK\n' "$backup" '__ACTION__'
'@
}
$RemoteScript = $RemoteScript.Replace('__UPLOAD_NAME__', $UploadName).Replace('__ACTION__', $Action).Replace('__HUB_URL__', $HubUrl)
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
    & scp -O -r -- $StageRoot "${Machine}:/tmp/"
    if ($LASTEXITCODE -ne 0) { throw 'Upload failed. Installation has not started.' }
    & ssh -T -o ConnectTimeout=10 $Machine "sh /tmp/$UploadName/run.sh"
    if ($LASTEXITCODE -ne 0) { throw "$Action failed. See the output above; success was not confirmed." }
    Write-Host "Hub URL $Target $Action finished successfully."
} finally {
    if (-not $PrepareOnly) {
        $Resolved = [System.IO.Path]::GetFullPath($StageRoot)
        $Expected = [System.IO.Path]::GetFullPath((Join-Path ([System.IO.Path]::GetTempPath()) $UploadName))
        if ($Resolved -ne $Expected -or (Split-Path -Leaf $Resolved) -ne $UploadName) { throw 'Unsafe staging cleanup path.' }
        if (Test-Path -LiteralPath $Resolved) { Remove-Item -LiteralPath $Resolved -Recurse -Force }
    }
}
