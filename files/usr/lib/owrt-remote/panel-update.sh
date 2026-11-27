#!/bin/sh
# Authenticated CGI dispatches this worker; no SSH or package-manager changes.
set -eu
umask 077
ROOT="${OWRT_PANEL_ROOT:-}"
STATE="$ROOT/tmp/owrt-remote-panel-update"
BACKUP="$ROOT/etc/owrt-remote/panel-update-backup"
SERVICE="$ROOT/etc/init.d/owrt-remote"
SELF="$ROOT/usr/lib/owrt-remote/panel-update.sh"
RAW='https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote'
API='https://api.github.com/repos/kzolotarev95/luci-app-owrt-remote/commits/main'
paths='usr/sbin/owrt-remote
usr/lib/owrt-remote/wan.sh
usr/lib/owrt-remote/control.sh
usr/lib/owrt-remote/tachyon.sh
usr/lib/owrt-remote/retire-logs.sh
usr/lib/owrt-remote/panel-update.sh
usr/lib/owrt-remote/panel-download.sh
etc/init.d/owrt-remote
etc/hotplug.d/iface/99-owrt-remote
www/cgi-bin/owrt-remote
www/luci-static/resources/owrt_remote_effects.js
www/luci-static/resources/owrt_remote_update.js
www/luci-static/resources/view/owrt_remote.js
usr/share/luci/menu.d/luci-app-owrt-remote.json
usr/share/rpcd/acl.d/luci-app-owrt-remote.json
usr/share/owrt-remote/panel-version'

json_string() { printf '%s' "$1" | awk 'BEGIN{printf "\""}{if(NR>1)printf "\\n";gsub(/\\/,"\\\\");gsub(/\"/,"\\\"");gsub(/\r/,"");gsub(/\t/,"\\t");printf "%s",$0}END{printf "\""}'; }
local_version() {
    local v r
    if [ -s "$ROOT/usr/share/owrt-remote/panel-version" ]; then
        read -r v r <"$ROOT/usr/share/owrt-remote/panel-version"
    else
        v=$(sed -n 's/.*<title>OWRT Hub v\([0-9][0-9]*\)<\/title>.*/\1/p' "$ROOT/www/cgi-bin/owrt-remote" | head -n 1); r=1
    fi
    case "$v:$r" in *[!0-9:]*|:*|*:) return 1 ;; esac
    [ "${#v}" -le 6 ] && [ "${#r}" -le 6 ] || return 1
    printf '%s %s\n' "$v" "$r"
}
write_status() {
    status=$1; message=$2
    { printf '{"ok":true,"id":"%s","status":"%s","message":' "$id" "$status"; json_string "$message"
      printf ',"installed_version":"v%s","version":"%s","revision":"%s","backup":' "$installed_v" "${remote_v:+v$remote_v}" "${revision:-}"
      json_string "${saved_backup:-}"; printf ',"started_at":%s,"updated_at":%s}\n' "$started" "$(date +%s)"
    } >"$STATE/status.new"
    mv -f "$STATE/status.new" "$STATE/status.json"
}
fetch() {
    DOWNLOAD_SOURCE_DEADLINE=$deadline remote_download "$1" "$2" || {
        error='GitHub недоступен или файл не опубликован. Рабочие файлы сохранены.'; return 1;
    }
}
mode_for() {
    case "$1" in usr/sbin/owrt-remote|etc/init.d/owrt-remote|etc/hotplug.d/iface/99-owrt-remote|www/cgi-bin/owrt-remote) printf 0755 ;; *) printf 0644 ;; esac
}
validate_manifest() {
    local digest mode path extra count=0 seen='|'
    [ "$(wc -c <"$work/manifest")" -le 16384 ] || return 1
    [ "$(sed -n '1p' "$work/manifest")" = 'OWRT_ROUTER_UPDATE 1' ] || return 1
    remote_v=$(sed -n '2s/^version //p' "$work/manifest")
    remote_r=$(sed -n '3s/^release //p' "$work/manifest")
    case "$remote_v:$remote_r" in *[!0-9:]*|:*|*:) return 1 ;; esac
    [ "${#remote_v}" -le 6 ] && [ "${#remote_r}" -le 6 ] || return 1
    sed '1,3d' "$work/manifest" >"$work/inventory"
    while read -r digest mode path extra; do
        [ -z "$extra" ] && [ "${#digest}" = 64 ] || return 1
        case "$digest" in *[!0-9a-f]*) return 1 ;; esac
        printf '%s\n' "$paths" | grep -Fxq "$path" || return 1
        [ "$mode" = "$(mode_for "$path")" ] || return 1
        case "$seen" in *"|$path|"*) return 1 ;; esac
        seen="$seen$path|"; count=$((count+1))
    done <"$work/inventory"
    [ "$count" = "$(printf '%s\n' "$paths" | wc -l | tr -d ' ')" ]
}
matches() {
    local digest mode path extra actual
    while read -r digest mode path extra; do
        [ -f "$ROOT/$path" ] && [ ! -L "$ROOT/$path" ] || return 1
        actual=$(sha256sum "$ROOT/$path"); actual=${actual%% *}
        [ "$actual" = "$digest" ] || return 1
    done <"$work/inventory"
}
snapshot() {
    local folder=$1 path
    mkdir -p "$folder"
    for path in $paths; do
        [ ! -L "$ROOT/$path" ] || { error="Недопустимая ссылка: $path"; return 1; }
        mkdir -p "$folder/$(dirname "$path")"
        if [ -f "$ROOT/$path" ]; then cp -p "$ROOT/$path" "$folder/$path"; else : >"$folder/$path.missing"; fi
    done
    printf '%s\n' "$was_running" >"$folder/service-running"
    printf '%s\n' "$had_xray" >"$folder/xray-running"
    printf '%s\n' "$paths" >"$folder/paths"
    (cd "$folder"; for path in $paths; do [ ! -f "$path" ] || sha256sum "$path"; done) >"$folder/SHA256SUMS"
    : >"$folder/complete"
}
restore_files() {
    local folder=$1 path failed=0
    [ -f "$folder/complete" ] && [ "$(cat "$folder/paths")" = "$paths" ] || return 1
    (cd "$folder"; sha256sum -c SHA256SUMS >/dev/null 2>&1) || return 1
    # Validate the entire snapshot before modifying any installed file.
    for path in $paths; do
        [ -f "$folder/$path.missing" ] || [ -s "$folder/$path" ] || return 1
    done
    for path in $paths; do
        if [ -f "$folder/$path.missing" ]; then rm -f "$ROOT/$path" || failed=1
        else cp -p "$folder/$path" "$ROOT/$path.panel-new" && mv -f "$ROOT/$path.panel-new" "$ROOT/$path" || failed=1; fi
    done
    [ "$failed" = 0 ]
}
restart_service() {
    # Preserve disabled/stopped installations and remote Xray-stop markers.
    [ "$was_running" = 1 ] || return 0
    "$SERVICE" restart >/dev/null 2>&1 || return 1
    # Check again after the immediate procd handover: a process that exits
    # straight away must not count as a healthy restarted tunnel.
    sleep 2
    local attempts=0
    while [ "$attempts" -lt 20 ]; do
        if "$SERVICE" running >/dev/null 2>&1; then
            # An explicit stopped marker means Heartbeat alone is expected.
            if [ "$had_xray" = 0 ] || [ -f "$ROOT/etc/owrt-remote/control/stopped" ] ||
                xray_running; then return 0; fi
        fi
        attempts=$((attempts+1)); sleep 1
    done
    return 1
}
xray_running() { "$ROOT/usr/sbin/owrt-remote" status 2>/dev/null | grep -qx 'xray_state: running'; }
finish() {
    local rc=$? restored=0 path
    trap - EXIT INT TERM
    set +e
    if [ "$rc" != 0 ]; then
        if [ "$changed" = 1 ]; then
            if restore_files "$BACKUP" && restart_service; then restored=1; rm -f "$BACKUP/transaction"; else restored=2; fi
        fi
        case "$restored" in
            1) error="$error Предыдущая сборка восстановлена." ;;
            2) error="$error Автоматический откат не завершён; резервная копия: $BACKUP" ;;
        esac
        write_status failed "$error"
    fi
    for path in $paths; do rm -f "$ROOT/$path.panel-new"; done
    rm -rf "$work"
    rm -f "$STATE/lock/pid"
    rmdir "$STATE/lock" 2>/dev/null
    exit "$rc"
}
worker() {
    id=$1; operation=$2; approved_revision=${3:-}; work="$STATE/work-$id"
    started=$(date +%s); remote_v=''; revision=''; saved_backup=''; changed=0; was_running=0; had_xray=0
    installed_v=0
    error='Обновление не выполнено. Рабочие файлы сохранены.'
    trap finish EXIT
    trap 'error="Задача прервана."; exit 1' INT TERM
    values=$(local_version) || { error='Не удалось определить установленную версию панели.'; return 1; }
    set -- $values; installed_v=$1; installed_r=$2
    printf '%s\n' "$$" >"$STATE/lock/pid"
    . "$work/download.sh"
    deadline=$(( $(download_clock) + 480 ))
    if [ -f "$BACKUP/transaction" ]; then
        error="Предыдущая установка была прервана. Не удалось восстановить $BACKUP."
        was_running=$(cat "$BACKUP/service-running"); had_xray=$(cat "$BACKUP/xray-running")
        restore_files "$BACKUP" && restart_service || return 1
        rm -f "$BACKUP/transaction"
        set -- $(local_version); installed_v=$1; installed_r=$2
    fi
    if [ "$operation" = rollback ]; then
        write_status installing 'Восстановление предыдущей сборки…'
        [ -f "$BACKUP/complete" ] || { error='Полная резервная копия не найдена.'; return 1; }
        was_running=$(cat "$BACKUP/service-running"); had_xray=$(cat "$BACKUP/xray-running")
        restore_files "$BACKUP" && restart_service || { error='Не удалось восстановить резервную копию.'; return 1; }
        set -- $(local_version); installed_v=$1
        write_status succeeded 'Предыдущая сборка восстановлена. Обновите страницу.'
        return 0
    fi
    write_status checking 'Проверка обновлений на GitHub…'
    fetch "$API" "$work/commit.json"
    revision=$(jsonfilter -i "$work/commit.json" -e '@.sha')
    [ "${#revision}" = 40 ] || { error='GitHub вернул некорректный идентификатор сборки.'; return 1; }
    case "$revision" in *[!0-9a-f]*) error='Недопустимый идентификатор сборки.'; return 1 ;; esac
    fetch "$RAW/$revision/router-update.manifest" "$work/manifest"
    error='Источник обновления содержит неподдерживаемый или повреждённый манифест.'
    validate_manifest
    if [ "$remote_v" -lt "$installed_v" ] || { [ "$remote_v" -eq "$installed_v" ] && [ "$remote_r" -lt "$installed_r" ]; }; then
        write_status local_newer 'Установлена более новая локальная версия. Понижение версии пропущено.'; return 0
    fi
    if matches; then write_status current 'Установлена последняя версия'; return 0; fi
    if [ "$operation" = check ]; then write_status available "Доступно обновление панели: v$remote_v"; return 0; fi
    # Apply only the exact build that the user reviewed. A moving main requires
    # a fresh check/confirmation, never a silent substitution of its contents.
    if [ "$approved_revision" != "$revision" ]; then error='Источник изменился. Повторите проверку и подтвердите новую сборку.'; return 1; fi
    write_status downloading 'Загрузка и проверка файлов обновления…'
    mkdir "$work/files"
    while read -r digest mode path; do
        mkdir -p "$work/files/$(dirname "$path")"
        fetch "$RAW/$revision/files/$path" "$work/files/$path"
        [ "$(wc -c <"$work/files/$path")" -le 2097152 ] || { error="Слишком большой файл обновления: $path"; return 1; }
        actual=$(sha256sum "$work/files/$path"); actual=${actual%% *}
        [ "$actual" = "$digest" ] || { error="Контрольная сумма не совпала: $path"; return 1; }
        case "$path" in
            *.sh|usr/sbin/owrt-remote|etc/init.d/*|etc/hotplug.d/*|www/cgi-bin/*)
                sh -n "$work/files/$path" || { error="Некорректный shell-файл: $path"; return 1; } ;;
            *.json) jsonfilter -i "$work/files/$path" -e '@' >/dev/null || { error="Некорректный JSON: $path"; return 1; } ;;
        esac
    done <"$work/inventory"
    [ "$(du -sk "$work/files" | awk '{print $1}')" -le 8192 ] || { error='Обновление превышает допустимый размер (8 МиБ).'; return 1; }
    [ "$(cat "$work/files/usr/share/owrt-remote/panel-version")" = "$remote_v $remote_r" ] || { error='Версия не совпала с манифестом.'; return 1; }
    "$SERVICE" running >/dev/null 2>&1 && was_running=1
    xray_running && had_xray=1
    error='Не удалось подготовить резервную копию или новые файлы.'
    # Same-filesystem snapshot and replacements; /etc/config, web.key, Xray,
    # its persistent cache and control/stopped are never part of the inventory.
    mkdir -p "$(dirname "$BACKUP")"
    rm -rf "$BACKUP.new"
    snapshot "$BACKUP.new"
    rm -rf "$BACKUP.old"
    [ ! -e "$BACKUP" ] || mv "$BACKUP" "$BACKUP.old"
    mv "$BACKUP.new" "$BACKUP"
    : >"$BACKUP/transaction"
    saved_backup=$BACKUP
    while read -r digest mode path; do
        mkdir -p "$ROOT/$(dirname "$path")"
        cp "$work/files/$path" "$ROOT/$path.panel-new"
        chmod "$mode" "$ROOT/$path.panel-new"
    done <"$work/inventory"
    write_status installing 'Установка обновления и проверка службы…'
    changed=1
    while read -r digest mode path; do mv -f "$ROOT/$path.panel-new" "$ROOT/$path"; done <"$work/inventory"
    error='Проверка установленных файлов или перезапуск службы не пройдены.'
    matches
    restart_service
    installed_v=$remote_v
    write_status succeeded 'Панель роутера обновлена. Страница будет перезагружена.'
    changed=0
    rm -f "$BACKUP/transaction"
    rm -rf "$BACKUP.old"
}

if ! mkdir -m 700 "$STATE" 2>/dev/null; then
    [ -d "$STATE" ] && [ ! -L "$STATE" ] && [ "$(ls -nd "$STATE" | awk '{print $3}')" = "$(id -u)" ] || exit 1
    chmod 700 "$STATE"
fi
case "${1:-}" in
    worker) worker "$2" "$3" "${4:-}" ;;
    status)
        if [ -s "$STATE/status.json" ]; then
            state=$(jsonfilter -i "$STATE/status.json" -e '@.status')
            case "$state" in checking|downloading|installing)
                pid=$(cat "$STATE/lock/pid" 2>/dev/null || true)
                updated=$(jsonfilter -i "$STATE/status.json" -e '@.updated_at')
                # Ignore the small window before the launcher publishes PID.
                if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null && [ $(( $(date +%s) - updated )) -ge 3 ]; then
                    printf '{"ok":true,"status":"failed","message":"Задача была прервана. Повторите проверку; незавершённая установка будет восстановлена."}\n'; exit 0
                fi ;;
            esac
            cat "$STATE/status.json"
        else printf '{"ok":true,"status":"idle","message":"Проверить обновление панели"}\n'; fi ;;
    check|install|rollback)
        operation=$1; expected=${2:-}
        if [ "$operation" = install ]; then
            [ "${#expected}" = 40 ] || exit 2
            case "$expected" in *[!0-9a-f]*) exit 2 ;; esac
        fi
        if ! mkdir "$STATE/lock" 2>/dev/null; then
            # A vanished worker (power loss/kill) must not block updates forever.
            pid=$(cat "$STATE/lock/pid" 2>/dev/null || true)
            if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
                rm -f "$STATE/lock/pid"; rmdir "$STATE/lock" 2>/dev/null || true
                mkdir "$STATE/lock" 2>/dev/null || { sh "$SELF" status; exit 0; }
            else sh "$SELF" status; exit 0; fi
        fi
        id="$(date +%s)-$$"; work="$STATE/work-$id"
        launched=0
        trap '[ "$launched" = 1 ] || { rm -rf "$work"; rm -f "$STATE/lock/pid"; rmdir "$STATE/lock" 2>/dev/null || true; }' EXIT
        mkdir "$work"
        cp "$SELF" "$work/worker.sh"
        cp "$ROOT/usr/lib/owrt-remote/panel-download.sh" "$work/download.sh"
        # Detach stdio/HUP before the request or Xray reverse tunnel goes away.
        started=$(date +%s); remote_v=''; revision=''; saved_backup=''
        set -- $(local_version); installed_v=$1
        write_status checking 'Задача запущена…'
        (trap '' HUP; exec sh "$work/worker.sh" worker "$id" "$operation" "$expected") </dev/null >/dev/null 2>&1 &
        printf '%s\n' "$!" >"$STATE/lock/pid" 2>/dev/null || true
        launched=1
        sh "$SELF" status ;;
    *) echo 'Usage: sh panel-update.sh check|status|install COMMIT|rollback' >&2; exit 2 ;;
esac
