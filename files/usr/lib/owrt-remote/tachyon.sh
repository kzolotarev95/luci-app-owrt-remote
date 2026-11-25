#!/bin/sh
# Discovery only: never starts/stops Tachyon or changes its UCI/network settings.
TACHYON_RUNTIME="${OWRT_REMOTE_TACHYON_RUNTIME:-${TMPDIR:-/tmp}/owrt-remote-tachyon}"

tachyon_inventory_failed() {
    if [ -s "$TACHYON_RUNTIME/status" ]; then
        sed 's/"checked":true/"checked":false/' "$TACHYON_RUNTIME/status"
    else printf '{"checked":false}'; fi
}

tachyon_json() (
    local dir package version package_version mode port service available api code pkg rc now last admin_port candidate_port probe checked attempt failures
    umask 077
    ulimit -f 64
    mkdir -p "$TACHYON_RUNTIME" || { printf '{"checked":false}'; return; }
    if [ "${1:-}" != force ] && [ -s "$TACHYON_RUNTIME/status" ]; then
        now="$(cut -d. -f1 /proc/uptime)"
        last="$(cat "$TACHYON_RUNTIME/updated" 2>/dev/null)"
        case "$last" in ''|*[!0-9]*) ;; *)
            if [ "$now" -ge "$last" ] && [ $((now-last)) -lt 30 ]; then cat "$TACHYON_RUNTIME/status"; return; fi ;;
        esac
    fi
    dir="$(mktemp -d "$TACHYON_RUNTIME/probe.XXXXXX")" || { printf '{"checked":false}'; return; }
    trap 'rm -f "$dir/package" "$dir/json"; rmdir "$dir" 2>/dev/null || true' EXIT
    trap 'exit 1' INT TERM
    package=''; version=''
    if command -v apk >/dev/null 2>&1; then
        apk info >"$dir/package" 2>/dev/null || { tachyon_inventory_failed; return; }
        for pkg in tachyon luci-app-tachyon; do
            grep -qx "$pkg" "$dir/package" || continue
            package="$pkg"
            # apk info -v prints the package description on OpenWrt 25.12.
            # The installed version is available through the manifest.
            version="$(apk list --installed --manifest "$pkg" 2>/dev/null | awk -v wanted="$pkg" '
                $1 == wanted && $2 != "" {print $2; exit}
                $1 ~ ("^" wanted "-") {sub("^" wanted "-", "", $1); print $1; exit}
            ')"
            [ -n "$version" ] || version="$(apk query --installed --from system --fields name,version --format json "$pkg" 2>/dev/null |
                jsonfilter -e '@[0].version' 2>/dev/null)"
            break
        done
    elif command -v opkg >/dev/null 2>&1; then
        for pkg in tachyon luci-app-tachyon; do
            opkg status "$pkg" >"$dir/package" 2>/dev/null || { tachyon_inventory_failed; return; }
            package="$(awk '/^Package:/{p=$2} /^Status:.* installed$/{print p;exit}' "$dir/package")"
            [ -n "$package" ] || continue
            version="$(awk -v wanted="$package" '/^Package:/{p=$2} p==wanted && /^Version:/{print $2;exit}' "$dir/package")"
            break
        done
    else
        tachyon_inventory_failed; return
    fi
    case "$version" in ''|*[!0-9a-zA-Z.+~_-]*) version='' ;; esac
    [ "${#version}" -le 80 ] || version=''
    case "$package" in tachyon|luci-app-tachyon) ;; *) package='' ;; esac
    if [ -z "$package" ]; then
        printf '{"checked":true,"installed":false}\n' >"$dir/json"
    else
        # A transient package-version lookup failure must not turn a known v2
        # standalone panel back into LuCI when rc.2's health API returns 401.
        if [ -z "$version" ] && [ "$(jsonfilter -i "$TACHYON_RUNTIME/status" -e '@.mode' 2>/dev/null)" = standalone ]; then
            rc="$(jsonfilter -i "$TACHYON_RUNTIME/status" -e '@.package_version' 2>/dev/null)"
            case "$rc" in ''|*[!0-9a-zA-Z.+~_-]*) ;; *) [ "${#rc}" -gt 80 ] || version="$rc" ;; esac
        fi
        package_version="$version"
        port=0; mode=luci; api=''
        candidate_port="$(uci -q get tachyon.settings.listen_port 2>/dev/null)"
        case "$candidate_port" in ''|*[!0-9]*) candidate_port=1680 ;; esac
        [ "${#candidate_port}" -le 5 ] || candidate_port=1680
        [ "$candidate_port" -ge 1024 ] && [ "$candidate_port" -le 65535 ] || candidate_port=1680
        candidate_port="$(printf '%s' "$candidate_port" | awk '{printf "%d", $1}')"
        # Older versions can use this port for a proxy. A real read-only
        # Tachyon API response confirms the standalone UI, even if package
        # metadata is missing or stale. An open port alone proves nothing.
        probe="$(curl -q -fsS --noproxy '*' --connect-timeout 1 --max-time 2 --max-filesize 65536 \
            "http://127.0.0.1:$candidate_port/api/agent/v1/health" 2>/dev/null |
            jsonfilter -e '@.core_running' 2>/dev/null)"
        case "$probe" in
            true|false) mode=standalone; port="$candidate_port"; api='/api/agent/v1' ;;
            *)
                # Keep v2 routed to its own port when it is temporarily down;
                # never substitute an available LuCI page for an offline UI.
                case "$version" in 2.*|[3-9].*) mode=standalone; port="$candidate_port" ;; esac
                ;;
        esac
        service=unknown
        for pkg in tachyon tachyon-core; do
            rc="$(ubus call service list "{\"name\":\"$pkg\"}" 2>/dev/null |
                jsonfilter -e "@[\"$pkg\"].instances.*.running" 2>/dev/null)"
            if printf '%s\n' "$rc" | grep -qx true; then service=running; break; fi
            [ -z "$rc" ] || service=stopped
        done
        available=false; checked=true
        if [ "$mode" = standalone ]; then
            for attempt in 1 2; do
                code="$(curl -q -s --noproxy '*' --connect-timeout 1 --max-time 3 -o /dev/null \
                    -w '%{http_code}' "http://127.0.0.1:$port/" 2>/dev/null)"
                case "$code" in 2??|3??|401|403) break ;; esac
            done
        else
            port=0
            admin_port="$(uci_get admin_port 80)"
            case "$admin_port" in ''|*[!0-9]*) admin_port=80 ;; esac
            [ "$admin_port" -gt 0 ] && [ "$admin_port" -le 65535 ] || admin_port=80
            code="$(curl -q -s --noproxy '*' --connect-timeout 1 --max-time 2 -o /dev/null \
                -w '%{http_code}' "http://127.0.0.1:$admin_port/cgi-bin/luci/admin/services/tachyon" 2>/dev/null)"
        fi
        # This badge describes the web UI, independently of Tachyon's core.
        # rc.2 protects its agent API with authentication; a reachable web UI
        # is still available when an optional/core procd instance is stopped.
        case "$code" in 2??|3??|401|403) available=true ;; esac
        [ -n "$version" ] || [ "$mode" = standalone ] || available=false
        if [ "$available" = true ]; then
            rm -f "$TACHYON_RUNTIME/web-failures"
        elif [ "$(jsonfilter -i "$TACHYON_RUNTIME/status" -e '@.available' 2>/dev/null)" = true ]; then
            failures="$(cat "$TACHYON_RUNTIME/web-failures" 2>/dev/null)"
            case "$failures" in ''|*[!0-9]*) failures=0 ;; esac
            [ "$failures" = 0 ] || failures=1
            failures=$((failures+1))
            printf '%s\n' "$failures" >"$TACHYON_RUNTIME/web-failures"
            # One failed observation is unknown, rather than an invented outage.
            # The Hub bounds retention of the previous confirmation to 120s.
            [ "$failures" -ge 2 ] || checked=false
        fi
        if [ "$mode" = standalone ] && [ "$available" = true ] && [ "$api" = /api/agent/v1 ]; then
            rc="$(curl -q -fsS --noproxy '*' --connect-timeout 1 --max-time 1 --max-filesize 65536 \
                "http://127.0.0.1:$port/api/agent/v1/openapi.json" 2>/dev/null | jsonfilter -e '@.info.version' 2>/dev/null)"
            case "$rc" in ''|*[!a-zA-Z0-9.+~_-]*) ;; *) [ "${#rc}" -gt 80 ] || version="$rc" ;; esac
        fi
        for pkg in tachyon-api tachyon-agent; do
            [ -z "$api" ] || break
            [ -e "/www/cgi-bin/$pkg" ] || continue
            rc="$(curl -q -fsS --noproxy '*' --max-time 1 \
                "http://127.0.0.1/cgi-bin/$pkg/health" 2>/dev/null |
                jsonfilter -e '@.success' 2>/dev/null)"
            [ "$rc" != true ] || { api="/cgi-bin/$pkg"; break; }
        done
        printf '{"checked":%s,"installed":true,"version":%s,"package_version":%s,"mode":"%s","port":%s,"service":"%s","available":%s,"api":%s}\n' \
            "$checked" "$(json_string "$(printf '%s' "$version" | head -c 80)")" "$(json_string "$(printf '%s' "$package_version" | head -c 80)")" \
            "$mode" "$port" "$service" "$available" "$(json_string "$api")" >"$dir/json"
    fi
    cat "$dir/json"
    mv -f "$dir/json" "$TACHYON_RUNTIME/status"
    cut -d. -f1 /proc/uptime >"$TACHYON_RUNTIME/updated"
)

tachyon_tunnel_port() {
    local state port
    state="$(tachyon_json)"
    [ "$(printf '%s' "$state" | jsonfilter -e '@.installed' 2>/dev/null)" = true ] || { printf 0; return; }
    [ "$(printf '%s' "$state" | jsonfilter -e '@.mode' 2>/dev/null)" = standalone ] || { printf 0; return; }
    port="$(printf '%s' "$state" | jsonfilter -e '@.port' 2>/dev/null)"
    case "$port" in ''|*[!0-9]*) port=0 ;; esac
    [ "$port" -ge 1024 ] && [ "$port" -le 65535 ] || port=0
    printf '%s' "$port"
}

tachyon_reconcile_xray() {
    local desired current config slot
    truthy "$(uci_get enabled 0)" || return 0
    xray_control_stopped && return 0
    xray_running || return 0
    [ "$(tachyon_json | jsonfilter -e '@.checked' 2>/dev/null)" = true ] || return 0
    desired="$(tachyon_tunnel_port)"
    config="$(uci_get xray_config /etc/xray/owrt-remote-client.json)"
    current="$(jsonfilter -i "$config" -e '@.outbounds[@.tag="router-tachyon"].settings.redirect' 2>/dev/null)"
    [ "$desired" != 0 ] || desired=''
    [ -z "$desired" ] || desired="127.0.0.1:$desired"
    [ "$current" != "$desired" ] || return 0
    slot="$(cut -d. -f1 /proc/uptime)"; slot=$((slot/120))
    ubus call service add "{\"name\":\"owrt-remote\",\"instances\":{\"tachyon-reconfigure\":{\"command\":[\"/usr/sbin/owrt-remote\",\"tachyon-reconfigure\",\"$desired\",\"$slot\"],\"stdout\":true,\"stderr\":true}}}" >/dev/null 2>&1
}

tachyon_reconfigure() (
    xray_control_lock "$CONTROL_RUNTIME/worker-lock" || return 0
    trap 'rm -f "$CONTROL_RUNTIME/worker-lock/pid"; rmdir "$CONTROL_RUNTIME/worker-lock" 2>/dev/null || true' EXIT
    trap 'exit 1' INT TERM
    xray_control_stopped && return 0
    truthy "$(uci_get enabled 0)" || return 0
    log 'Tachyon: обновление локального маршрута в обратном туннеле'
    xray_control_execute restart
)
