#!/bin/sh
# Fixed Xray operations delivered by authenticated Heartbeat. Never eval Hub input.
CONTROL_STATE="${OWRT_REMOTE_CONTROL_STATE:-/etc/owrt-remote/control}"
CONTROL_RUNTIME="${OWRT_REMOTE_CONTROL_RUNTIME:-/tmp/owrt-remote-control}"

xray_control_stopped() { [ -e "$CONTROL_STATE/stopped" ]; }

xray_control_lock() {
    local path owner self rest
    path="$1"
    mkdir -p "$CONTROL_RUNTIME" || return 1
    if ! mkdir "$path" 2>/dev/null; then
        owner="$(cat "$path/pid" 2>/dev/null)"
        case "$owner" in
            ''|*[!0-9]*)
                sleep 1
                owner="$(cat "$path/pid" 2>/dev/null)"
                ;;
        esac
        case "$owner" in ''|*[!0-9]*) ;; *) kill -0 "$owner" 2>/dev/null && return 1 ;; esac
        # Only reap a dead, fully initialized owner; never another process's new lock.
        mkdir "$path/reap" 2>/dev/null || return 1
        [ "$(cat "$path/pid" 2>/dev/null)" = "$owner" ] || { rmdir "$path/reap"; return 1; }
        rm -f "$path/pid"
        rmdir "$path/reap" "$path" 2>/dev/null || return 1
        mkdir "$path" 2>/dev/null || return 1
    fi
    self=$$
    [ ! -r /proc/self/stat ] || read -r self rest </proc/self/stat
    printf '%s\n' "$self" >"$path/pid"
}

xray_control_save() (
    umask 077
    mkdir -p "$CONTROL_STATE" || return 1
    state_xray=stopped; xray_running && state_xray=running
    temp="$(mktemp "$CONTROL_STATE/.result.XXXXXX")" || return 1
    trap 'rm -f "$temp"' EXIT
    trap 'exit 1' INT TERM
    printf '{"id":%s,"action":%s,"status":%s,"rc":%s,"xray":%s,"message":%s,"boot":%s,"accepted":%s}\n' \
        "$(json_string "$1")" "$(json_string "$2")" "$(json_string "$3")" "$4" \
        "$(json_string "$state_xray")" "$(json_string "$5")" \
        "$(json_string "$(cat /proc/sys/kernel/random/boot_id 2>/dev/null)")" "$(safe_num "$(uptime_seconds)" 0)" >"$temp" &&
        mv -f "$temp" "$CONTROL_STATE/result.json"
    rc=$?; rm -f "$temp"; return "$rc"
)

xray_control_result_json() {
    local state id action service boot accepted now
    if [ -s "$CONTROL_STATE/result.json" ]; then
        state="$(jsonfilter -i "$CONTROL_STATE/result.json" -e '@.status' 2>/dev/null)"
        case "$state" in running|succeeded|failed|interrupted) ;; *) printf null; return 0 ;; esac
        if [ "$state" = running ]; then
            boot="$(jsonfilter -i "$CONTROL_STATE/result.json" -e '@.boot')"
            accepted="$(jsonfilter -i "$CONTROL_STATE/result.json" -e '@.accepted')"
            now="$(safe_num "$(uptime_seconds)" 0)"
            accepted="$(safe_num "$accepted" 0)"
            service="$(ubus call service list '{"name":"owrt-remote"}' 2>/dev/null)" || service=''
            if [ "$boot" != "$(cat /proc/sys/kernel/random/boot_id 2>/dev/null)" ] || {
                [ $((now-accepted)) -ge 15 ] && [ -n "$service" ] &&
                [ "$(printf '%s' "$service" | jsonfilter -e '@["owrt-remote"].instances.control.running' 2>/dev/null)" != true ];
            }; then
                id="$(jsonfilter -i "$CONTROL_STATE/result.json" -e '@.id')"
                action="$(jsonfilter -i "$CONTROL_STATE/result.json" -e '@.action')"
                # A worker may have written its final result between the first read and procd query.
                if [ "$(jsonfilter -i "$CONTROL_STATE/result.json" -e '@.status')" = running ]; then
                    xray_control_save "$id" "$action" interrupted 1 'Обработчик прерван или роутер перезагрузился; повтор команды автоматически не выполняется.' || return 1
                fi
            fi
        fi
        head -c 4096 "$CONTROL_STATE/result.json"
    else
        printf null
    fi
}

xray_control_dispatch() (
    id="$(jsonfilter -i "$1" -e '@.xray_command.id' 2>/dev/null)"
    [ -n "$id" ] || return 0
    [ "${#id}" = 32 ] || return 1
    case "$id" in *[!a-f0-9]*) return 1 ;; esac
    action="$(jsonfilter -i "$1" -e '@.xray_command.action' 2>/dev/null)"
    case "$action" in start|stop|restart) ;; *) return 1 ;; esac
    xray_control_lock "$CONTROL_RUNTIME/dispatch-lock" || return 0
    trap 'rm -f "$CONTROL_RUNTIME/dispatch-lock/pid"; rmdir "$CONTROL_RUNTIME/dispatch-lock" 2>/dev/null || true' EXIT
    trap 'exit 1' INT TERM
    previous="$(jsonfilter -i "$CONTROL_STATE/result.json" -e '@.id' 2>/dev/null)"
    [ "$previous" != "$id" ] || return 0
    state="$(jsonfilter -i "$CONTROL_STATE/result.json" -e '@.status' 2>/dev/null)"
    [ "$state" != running ] || return 0
    # Persist the claim before executing. Duplicate deliveries/reboots cannot replay a restart.
    xray_control_save "$id" "$action" running 0 'Команда принята агентом роутера.' || {
        log 'ОШИБКА: Remote Xray: невозможно сохранить состояние команды; выполнение отменено'
        return 1
    }
    request="{\"name\":\"owrt-remote\",\"instances\":{\"control\":{\"command\":[\"/usr/sbin/owrt-remote\",\"xray-control-worker\",\"$id\",\"$action\"],\"stdout\":true,\"stderr\":true}}}"
    if ! ubus call service add "$request" >/dev/null 2>&1; then
        xray_control_save "$id" "$action" failed 1 'procd не принял обработчик команды; Xray не изменён.'
        return 1
    fi
    log "Remote Xray: команда=$id действие=$action принята через Heartbeat"
)

xray_control_execute() {
    local action binary state attempt
    action="$1"
    case "$action" in start|stop|restart) ;; *) return 2 ;; esac
    if [ "$action" != stop ]; then
        truthy "$(uci_get enabled 0)" || { printf 'Remote выключен в настройках роутера; сначала включи агент.\n'; return 1; }
        binary="$(xray_bin_path)"
        [ -x "$binary" ] || { printf 'Бинарник Xray отсутствует; установи Xray на роутере.\n'; return 1; }
        render_client_config >/dev/null || return $?
    fi
    if [ "$action" != start ]; then
        # Suppress WAN auto-recovery before removing only the Xray instance.
        : >"$CONTROL_STATE/stopped" || return 1
        state="$(ubus call service list '{"name":"owrt-remote"}' 2>/dev/null)" || return 1
        if printf '%s' "$state" | jsonfilter -e '@["owrt-remote"].instances.xray' 2>/dev/null | grep -q .; then
            ubus call service delete '{"name":"owrt-remote","instance":"xray"}' >/dev/null || return 1
        fi
        stop_xray || return $?
        if xray_running; then printf 'Xray не остановился.\n'; return 1; fi
        if [ "$action" = stop ]; then printf 'Xray остановлен. Агент и Heartbeat продолжают работать.\n'; return 0; fi
    fi
    rm -f "$CONTROL_STATE/stopped" || return 1
    if [ "$action" = start ] && xray_running; then printf 'Xray уже запущен.\n'; return 0; fi
    ubus call service add '{"name":"owrt-remote","instances":{"xray":{"command":["/usr/sbin/owrt-remote","run"],"env":{"OWRT_REMOTE_SERVICE_LOG":"1"},"respawn":["3600","5","5"],"stdout":true,"stderr":true}}}' >/dev/null || return 1
    attempt=0
    while [ "$attempt" -lt 20 ]; do
        sleep 1
        if xray_running; then
            sleep 2
            if xray_running; then printf 'Xray запущен; процесс проверен. Heartbeat работает независимо.\n'; return 0; fi
        fi
        attempt=$((attempt+1))
    done
    printf 'Процесс Xray не появился за 20 секунд. Проверь WAN и Глобал логи; регистрация procd не считается успешным запуском.\n'
    return 1
}

xray_control_shell_timeout() (
    control_child=''
    trap 'result=$?; [ -z "$control_child" ] || { kill -TERM "$control_child" 2>/dev/null || true; kill -KILL "$control_child" 2>/dev/null || true; wait "$control_child" 2>/dev/null || true; }; exit "$result"' EXIT
    trap 'exit 1' INT TERM
    "$@" & control_child=$!
    ticks=0
    while kill -0 "$control_child" 2>/dev/null; do
        if [ "$ticks" -ge 45 ]; then
            kill -TERM "$control_child" 2>/dev/null || true
            sleep 1
            kill -KILL "$control_child" 2>/dev/null || true
            wait "$control_child" 2>/dev/null || true
            control_child=''
            return 124
        fi
        sleep 1
        ticks=$((ticks+1))
    done
    rc=0
    wait "$control_child" || rc=$?
    control_child=''
    return "$rc"
)

run_xray_control_limited() {
    if command -v timeout >/dev/null 2>&1; then
        timeout 45 "$@"
    elif command -v busybox >/dev/null 2>&1 && busybox timeout 45 true >/dev/null 2>&1; then
        busybox timeout 45 "$@"
    else
        xray_control_shell_timeout "$@"
    fi
}

xray_control_worker() (
    umask 077
    id="$1"; action="$2"
    [ "${#id}" = 32 ] || return 2
    case "$id" in *[!a-f0-9]*) return 2 ;; esac
    case "$action" in start|stop|restart) ;; *) return 2 ;; esac
    [ "$(jsonfilter -i "$CONTROL_STATE/result.json" -e '@.id')" = "$id" ] || return 1
    [ "$(jsonfilter -i "$CONTROL_STATE/result.json" -e '@.action')" = "$action" ] || return 1
    mkdir -p "$CONTROL_RUNTIME" || return 1
    # procd starts a single named instance; mkdir also serializes manual worker calls.
    xray_control_lock "$CONTROL_RUNTIME/worker-lock" || return 1
    trap 'rm -f "$CONTROL_RUNTIME/output" "$CONTROL_RUNTIME/worker-lock/pid"; rmdir "$CONTROL_RUNTIME/worker-lock" 2>/dev/null || true' EXIT
    [ "$(jsonfilter -i "$CONTROL_STATE/result.json" -e '@.status')" = running ] || return 0
    trap 'xray_control_save "$id" "$action" interrupted 1 "Обработчик команды был остановлен."; exit 1' INT TERM
    rc=0
    (ulimit -f 8; run_xray_control_limited /usr/sbin/owrt-remote xray-control-execute "$action") >"$CONTROL_RUNTIME/output" 2>&1 || rc=$?
    message="$(tail -c 1200 "$CONTROL_RUNTIME/output" | sed -E '/[Aa]uthorization|hub_token|vless_uuid|[Pp]assword|[Pp]asswd|[?&](key|token)=/c\[секрет скрыт]')"
    status=failed
    if [ "$rc" = 0 ]; then
        if [ "$action" = stop ]; then xray_running && rc=1; else xray_running || rc=1; fi
        [ "$rc" != 0 ] || status=succeeded
    fi
    [ "$rc" != 124 ] || message='Истекло 45 секунд выполнения команды; проверь WAN и журнал Xray.'
    [ -n "$message" ] || message="Команда завершилась с rc=$rc; проверь Глобал логи."
    xray_control_save "$id" "$action" "$status" "$rc" "$message" || return 1
    log "Remote Xray: команда=$id действие=$action результат=$status rc=$rc"
)
