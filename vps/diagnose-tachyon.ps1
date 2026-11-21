param([string]$Router = 'root@192.168.2.1')
$ErrorActionPreference = 'Stop'
$Probe = @'
set +e
echo '=== PACKAGE METADATA ==='
if command -v apk >/dev/null 2>&1; then
    for p in tachyon luci-app-tachyon; do apk info -e "$p"; apk info -v "$p"; done
elif command -v opkg >/dev/null 2>&1; then
    opkg status tachyon luci-app-tachyon | sed -n '/^Package:/p;/^Version:/p;/^Status:/p'
fi
echo '=== SERVICE FILES ==='
ls -l /etc/init.d/*tachyon* /www/cgi-bin/tachyon* 2>/dev/null
echo '=== UHTTPD LISTENERS AND DOCUMENT ROOTS ==='
uci -q show uhttpd | sed -n '/=uhttpd$/p;/\.listen_http=/p;/\.home=/p;/\.cgi_prefix=/p;/\.ubus_prefix=/p'
echo '=== TACHYON HTTP SETTINGS (NO SECRETS) ==='
uci -q show tachyon | sed -n '/\.web_port=/p;/\.http_port=/p;/\.ui_port=/p;/\.listen_port=/p'
echo '=== PROCD SERVICE STATES ==='
for s in tachyon tachyon-core tachyon-web tachyon-http uhttpd; do
    printf '%s: ' "$s"
    ubus call service list "{\"name\":\"$s\"}" 2>/dev/null | jsonfilter -e '@.*.instances.*.running'
done
echo '=== LOCAL HTTP RESPONSES ==='
for u in http://127.0.0.1:1680/ http://192.168.2.1:1680/ http://127.0.0.1/cgi-bin/luci/admin/services/tachyon; do
    printf '%s ' "$u"
    curl -q -s --noproxy '*' --max-time 3 -o /dev/null -w 'HTTP=%{http_code}\n' "$u"
done
echo '=== PUBLIC READ-ONLY HEALTH ENDPOINTS ==='
for p in tachyon-api tachyon-agent; do
    printf '%s: ' "$p"
    curl -q -fsS --noproxy '*' --max-time 3 "http://127.0.0.1/cgi-bin/$p/health" 2>/dev/null |
        jsonfilter -e '@.success' -e '@.status' -e '@.version' -e '@.tachyon_core'
done
echo 'TACHYON_READ_ONLY_DIAGNOSTICS_DONE'
echo '=== REMOTE ROUTER ID AND TACHYON STATE ==='
uci -q get owrtremote.main.router_id
owrt-remote tachyon-status
echo '=== INSTALLED XRAY TACHYON ROUTE (NO KEYS) ==='
cfg="$(uci -q get owrtremote.main.xray_config)"
[ -n "$cfg" ] || cfg=/etc/xray/owrt-remote-client.json
printf 'config=%s\n' "$cfg"
jsonfilter -i "$cfg" -e '@.outbounds[@.tag="router-tachyon"].settings' -e '@.routing.rules' 2>/dev/null
echo '=== CANDIDATE TACHYON ROUTE (NO KEYS) ==='
owrt-remote render-client --stdout 2>/dev/null | jsonfilter -e '@.outbounds[@.tag="router-tachyon"].settings' -e '@.routing.rules' 2>/dev/null
echo 'TACHYON_ROUTE_READ_ONLY_DONE'
'@
$Probe.Replace([string][char]13, [string]::Empty) | & ssh -T -o ConnectTimeout=10 $Router 'sh -s'
if ($LASTEXITCODE -ne 0) { throw 'SSH diagnostics failed. Router settings were not changed.' }
