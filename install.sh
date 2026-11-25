#!/bin/sh

set -eu

export PATH="/bin:/sbin:/usr/bin:/usr/sbin:${PATH:-}"

RAW_URL="${RAW_URL:-https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote/main}"
ROOT="${ROOT:-/}"
SCRIPT_DIR="$(CDPATH= cd "$(dirname "$0")" 2>/dev/null && pwd)"
INSTALL_DIAGNOSTIC="${OWRT_REMOTE_INSTALL_DIAGNOSTIC:-0}"
INSTALL_STAGE=init
INSTALL_CANDIDATE=''
umask 077
INSTALL_TMP="$(mktemp -d "${TMPDIR:-/tmp}/owrt-install.XXXXXX")"
cleanup_install() {
	code=$?
	trap - EXIT
	[ "$code" = 0 ] || printf 'ERROR: installation stage=%s rc=%s\n' "$INSTALL_STAGE" "$code" >&2
	[ -z "$INSTALL_CANDIDATE" ] || rm -f "$INSTALL_CANDIDATE"
	rm -rf "$INSTALL_TMP"
	exit "$code"
}
trap cleanup_install EXIT
trap 'exit 1' INT TERM

# No shell xtrace: it could expose UCI keys or the generated panel access key.
# Each external step has an explicit label, return code and separate streams.
run_step() {
	local rc label
	label="$1"; shift
	INSTALL_STAGE="$label"
	printf 'Install [%s]: %s\n' "$label" "$*" >&2
	rc=0
	"$@" >"$INSTALL_TMP/stdout" 2>"$INSTALL_TMP/stderr" || rc=$?
	if [ "$rc" != 0 ] || [ "$INSTALL_DIAGNOSTIC" = 1 ]; then
		printf 'Install [%s]: rc=%s; stdout:\n' "$label" "$rc" >&2
		head -c 32768 "$INSTALL_TMP/stdout" >&2
		printf '\nInstall [%s]: stderr:\n' "$label" >&2
		head -c 32768 "$INSTALL_TMP/stderr" >&2
		printf '\n' >&2
	fi
	: >"$INSTALL_TMP/stdout"; : >"$INSTALL_TMP/stderr"
	return "$rc"
}

info() {
	printf '%s\n' "$*"
}

warn() {
	printf 'WARN: %s\n' "$*" >&2
}

die() {
	printf 'ERROR: %s\n' "$*" >&2
	exit 1
}

target_path() {
	printf '%s/%s' "${ROOT%/}" "$1"
}

# BEGIN SHARED ROUTER DOWNLOADER
# Shared, embedded in install.sh and the router agent by embed-router-download.py.
# No dependency on curl/jsonfilter/timeout is needed to bootstrap curl itself.
download_clock() {
	if [ -r /proc/uptime ]; then cut -d. -f1 /proc/uptime; else date +%s; fi
}
download_note() {
	if command -v log >/dev/null 2>&1; then log "$*"; else printf '%s\n' "$*" >&2; fi
}
download_origin() {
	local authority
	case "$1" in https://?*) ;; *) return 3 ;; esac
	authority="${1#https://}"; authority="${authority%%/*}"
	case "$authority" in ''|*[@\?\#\ ]*|*[!A-Za-z0-9.:-]*) return 3 ;; esac
	case "$authority" in *:*) ;; *) authority="$authority:443" ;; esac
	printf 'https://%s' "$authority"
}
download_signal_tree() {
	local child
	# Kill descendants first: a timed-out shell function may own a DNS client.
	if [ -r "/proc/$1/task/$1/children" ]; then
		for child in $(cat "/proc/$1/task/$1/children"); do download_signal_tree "$child" "$2"; done
	fi
	kill -"$2" "$1" 2>/dev/null || true
}
download_kill_tree() {
	local ticks
	download_signal_tree "$1" TERM
	# Allow shell EXIT traps to release locks and staging files first.
	ticks=0
	while kill -0 "$1" 2>/dev/null && [ "$ticks" -lt 2 ]; do sleep 1; ticks=$((ticks+1)); done
	download_signal_tree "$1" KILL
}
download_run() (
	seconds="$1"; shift; child=''
	trap 'result=$?; [ -z "$child" ] || { download_kill_tree "$child"; wait "$child" 2>/dev/null || true; }; exit "$result"' EXIT
	trap 'exit 28' INT TERM
	"$@" & child=$!
	ticks=0
	while kill -0 "$child" 2>/dev/null; do
		[ "$ticks" -lt "$seconds" ] || return 28
		sleep 1; ticks=$((ticks+1))
	done
	rc=0; wait "$child" || rc=$?; child=''; return "$rc"
)
download_curl() (
	url="$1"; dst="$2"; stats="$3"; method="$4"; deadline="$5"; hop=0
	while [ "$hop" -le 5 ]; do
		origin="$(download_origin "$url")" || return 3
		set -- -q -fsS --connect-timeout 10 --noproxy '*' --proto '=https' --max-filesize 33554432
		[ "$method" != HEAD ] || set -- "$@" -I
		DL_DEVICE=''; DL_RESOLVE=''
		# The agent resolves every redirect through its selected WAN. The
		# installer has no routing hook and uses the router's system resolver.
		if command -v download_route >/dev/null 2>&1; then download_route "$origin" || return $?; fi
		[ -z "$DL_DEVICE" ] || set -- "$@" --interface "$DL_DEVICE"
		[ -z "$DL_RESOLVE" ] || set -- "$@" --resolve "$DL_RESOLVE"
		remaining=$((deadline - $(download_clock)))
		[ "$remaining" -gt 0 ] || return 28
		rc=0
		curl "$@" --max-time "$remaining.000" -o "$dst" -w '%{http_code} %{redirect_url}\n' "$url" >"$stats" || rc=$?
		[ "$rc" = 0 ] || return "$rc"
		code=''; redirect=''; read -r code redirect <"$stats" || true
		case "$code" in
			301|302|303|307|308) [ -n "$redirect" ] || return 47; url="$redirect"; hop=$((hop+1)) ;;
			200) [ -s "$dst" ] || return 52; return 0 ;;
			*) return 22 ;;
		esac
	done
	return 47
)
download_reason() {
	case "$1" in
		3) printf invalid_https_url; return ;; 6) printf DNS; return ;;
		23|27) printf write; return ;; 28|124|137) printf timeout; return ;;
		35|51|58|60|77|80|82|83|90|91) printf TLS; return ;;
	esac
	if LC_ALL=C grep -Eqi 'no space|permission denied|read-only|write error|file too large' "$2"; then printf write
	elif LC_ALL=C grep -Eqi 'resolv|bad address|name or service|DNS' "$2"; then printf DNS
	elif LC_ALL=C grep -qi 'uclient context' "$2"; then printf client_context
	elif LC_ALL=C grep -Eqi 'TLS|SSL|certificate' "$2"; then printf TLS
	elif LC_ALL=C grep -Eqi 'timed out|timeout' "$2"; then printf timeout
	elif [ "$3" != 000 ]; then printf HTTP
	else printf network; fi
}
download_tools() {
	local tool path identity seen
	seen='|'
	for tool in curl uclient-fetch wget; do
		path="$(command -v "$tool" 2>/dev/null)" || continue
		identity="$(readlink -f "$path" 2>/dev/null)"; identity="${identity:-$path}"
		case "$seen" in *"|$identity|"*) continue ;; esac
		seen="$seen$identity|"; printf '%s\n' "$tool"
	done
}
remote_download() (
	url="$1"; dst="$2"; method="${3:-GET}"
	work="$(mktemp -d "${TMPDIR:-/tmp}/owrt-download.XXXXXX")" || return 23
	trap 'result=$?; rm -rf "$work"; [ "$result" = 0 ] || rm -f "$dst"; exit "$result"' EXIT
	trap 'exit 28' INT TERM
	unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
	# Also bound native clients and their error/response files on small RAM.
	ulimit -f 32768 || return 23
	deadline=$(( $(download_clock) + 120 ))
	[ -z "${DOWNLOAD_RESULT_FILE:-}" ] || : >"$DOWNLOAD_RESULT_FILE"
	for outer in "${XRAY_PREPARE_DEADLINE:-}" "${XRAY_FILE_DEADLINE:-}" "${DOWNLOAD_SOURCE_DEADLINE:-}"; do
		[ -z "$outer" ] || [ "$outer" -ge "$deadline" ] || deadline="$outer"
	done
	origin="$(download_origin "$url")" || { download_note 'Download: reason=invalid_https_url; rc=3'; return 3; }
	tools="$(download_tools)"; set -- $tools; left="$#"; rc=127
	[ "$left" -gt 0 ] || download_note 'Download: stage=tools; reason=no_http_client; rc=127'
	for tool in $tools; do
		remaining=$((deadline - $(download_clock)))
		[ "$remaining" -gt 0 ] || { rc=28; reason=timeout; break; }
		# Reserve time for genuinely different fallback clients. No blind
		# retries on dozens of releases when their common host is unavailable.
		seconds=$((remaining / left)); [ "$seconds" -gt 0 ] || seconds=1
		left=$((left-1)); attempt_end=$(( $(download_clock) + seconds ))
		rm -f "$dst"; : >"$work/stats"; : >"$work/error"; rc=0
		case "$tool" in
			curl) download_run "$seconds" download_curl "$url" "$dst" "$work/stats" "$method" "$attempt_end" 2>"$work/error" || rc=$? ;;
			*)
				if command -v download_native_notice >/dev/null 2>&1; then download_native_notice; fi
				download_run "$seconds" "$tool" -T "$seconds" -O "$dst" "$url" >"$work/stats" 2>"$work/error" || rc=$? ;;
		esac
		code=000
		if [ "$tool" = curl ]; then
			read -r code ignored <"$work/stats" || true
			case "$code" in [1-5][0-9][0-9]) ;; *) code=000 ;; esac
		else
			code="$(LC_ALL=C sed -n -e 's/.*HTTP\/[0-9.]*  *\([1-5][0-9][0-9]\).*/\1/p' -e 's/.*HTTP error \([1-5][0-9][0-9]\).*/\1/p' -e 's/.*awaiting response[. ]*\([1-5][0-9][0-9]\).*/\1/p' "$work/error" | tail -n 1)"
			code="${code:-000}"
		fi
		if [ "$rc" = 0 ]; then
			[ -s "$dst" ] && [ "$(wc -c <"$dst")" -le 33554432 ] || rc=23
		fi
		reason="$(download_reason "$rc" "$work/error" "$code")"
		[ "$rc" != 0 ] || reason=ok
		# Never print URLs, query strings, redirect signatures or raw headers.
		download_note "Download: stage=${DOWNLOAD_STAGE:-file}; tool=$tool; host=$origin; http=$code; rc=$rc; reason=$reason"
		[ -z "${DOWNLOAD_RESULT_FILE:-}" ] || printf '%s %s %s\n' "$rc" "$reason" "$code" >"$DOWNLOAD_RESULT_FILE"
		[ "$rc" != 0 ] || return 0
		case "$reason" in write|invalid_https_url) break ;; esac
	done
	[ "$rc" != 28 ] || download_note 'Download: reason=timeout; file/preparation deadline reached or HTTP client timed out'
	[ -z "${DOWNLOAD_RESULT_FILE:-}" ] || printf '%s %s %s\n' "$rc" "${reason:-no_http_client}" "${code:-000}" >"$DOWNLOAD_RESULT_FILE"
	return "$rc"
)
# END SHARED ROUTER DOWNLOADER
fetch() { remote_download "$1" "$2"; }

install_file() {
	local rel mode src dst bust
	rel="$1"
	mode="$2"
	src="$SCRIPT_DIR/files/$rel"
	dst="$(target_path "$rel")"
	mkdir -p "$(dirname "$dst")"
	INSTALL_CANDIDATE="$(mktemp "$dst.new.XXXXXX")"
	if [ -f "$src" ]; then
		run_step "file:$rel" cp "$src" "$INSTALL_CANDIDATE"
	else
		bust="$(date +%s 2>/dev/null || echo $$)"
		DOWNLOAD_STAGE="file:$rel"
		run_step "file:$rel" fetch "$RAW_URL/files/$rel?v=$bust" "$INSTALL_CANDIDATE"
	fi
	chmod "$mode" "$INSTALL_CANDIDATE"
	mv -f "$INSTALL_CANDIDATE" "$dst"
	INSTALL_CANDIDATE=''
}

install_config() {
	local rel src dst bust
	rel="etc/config/owrtremote"
	src="$SCRIPT_DIR/files/$rel"
	dst="$(target_path "$rel")"
	mkdir -p "$(dirname "$dst")"
	if [ -f "$dst" ]; then
		info "Оставляю существующий конфиг: $dst"
		return
	fi
	install_file "$rel" 0644
}

make_key() {
	local key_dir key_file key
	key_dir="$(target_path etc/owrt-remote)"
	key_file="$key_dir/web.key"
	mkdir -p "$key_dir"
	if [ ! -s "$key_file" ]; then
		if command -v hexdump >/dev/null 2>&1; then
			key="$(dd if=/dev/urandom bs=16 count=1 2>/dev/null | hexdump -v -e '16/1 "%02x"')"
		else
			key="$(date +%s)-$$"
		fi
		printf '%s\n' "$key" >"$key_file"
	fi
	chmod 0600 "$key_file"
	cat "$key_file"
}

router_ip() {
	local ip
	if command -v uci >/dev/null 2>&1; then
		ip="$(uci -q get network.lan.ipaddr 2>/dev/null || true)"
		if [ -n "$ip" ]; then
			printf '%s\n' "$ip"
			return
		fi
	fi
	ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
	if [ -n "$ip" ]; then
		printf '%s\n' "$ip"
		return
	fi
	printf '192.168.1.1'
}

installed_ui_version() {
	local file
	file="$(target_path www/cgi-bin/owrt-remote)"
	awk -F '"' '/^OWRT_REMOTE_UI_VERSION=/ { print $2; exit }' "$file" 2>/dev/null || true
}

openwrt_version() {
	local file
	file="$(target_path etc/openwrt_release)"
	if [ -r "$file" ]; then
		(
			. "$file" 2>/dev/null
			printf '%s %s' "${DISTRIB_ID:-OpenWrt}" "${DISTRIB_RELEASE:-unknown}"
		)
		return
	fi
	printf 'OpenWrt unknown'
}

package_manager() {
	if command -v apk >/dev/null 2>&1; then
		printf 'apk'
		return
	fi
	if command -v opkg >/dev/null 2>&1; then
		printf 'opkg'
		return
	fi
	printf 'unknown'
}

package_installed() {
	local pkg manager
	pkg="$1"
	manager="$(package_manager)"
	case "$manager" in
		opkg)
			opkg list-installed "$pkg" 2>/dev/null | grep -Fq "$pkg"
			;;
		apk)
			apk info -e "$pkg" >/dev/null 2>&1
			;;
		*)
			return 1
			;;
	esac
}

install_packages() {
	local manager
	manager="$(package_manager)"
	case "$manager" in
		opkg)
			run_step packages-index download_run 120 opkg update || return $?
			run_step packages-install download_run 120 opkg install "$@"
			;;
		apk)
			run_step packages-index download_run 120 apk update || return $?
			run_step packages-install download_run 120 apk add "$@"
			;;
		*)
			return 1
			;;
	esac
}

ensure_install_dependencies() {
	local need ca
	[ "${ROOT%/}" = "" ] || return 0
	INSTALL_STAGE=dependencies
	need=''
	if ! command -v curl >/dev/null 2>&1 || ! curl --version >/dev/null 2>&1; then need="$need curl"; fi
	command -v jsonfilter >/dev/null 2>&1 || need="$need jsonfilter"
	if [ ! -s /etc/ssl/cert.pem ] && [ ! -s /etc/ssl/certs/ca-certificates.crt ]; then
		case "$(package_manager)" in apk) ca=ca-certificates ;; *) ca=ca-bundle ;; esac
		need="$need $ca"
	fi
	if [ -n "$need" ]; then
		info "Подготовка HTTPS/JSON зависимостей: $need"
		install_packages $need || die 'dependency installation failed; application files and existing Xray retained'
	fi
	INSTALL_STAGE=dependencies-check
	command -v curl >/dev/null 2>&1 && command -v jsonfilter >/dev/null 2>&1 || die 'curl/jsonfilter unavailable after dependency installation'
	curl --version >/dev/null 2>&1 || die 'installed curl cannot run on this firmware'
	[ -s /etc/ssl/cert.pem ] || [ -s /etc/ssl/certs/ca-certificates.crt ] || die 'trusted CA bundle unavailable after dependency installation'
}

ensure_wol_support() {
	local need manager tool_pkg candidate
	[ "${ROOT%/}" = "" ] || return 0
	manager="$(package_manager)"
	[ "$manager" != "unknown" ] || {
		warn "не найден пакетный менеджер для установки WOL-пакетов"
		return 0
	}
	need=""
	if ! package_installed "luci-app-wol"; then
		need="$need luci-app-wol"
	fi
	if ! command -v etherwake >/dev/null 2>&1 && ! command -v wakeonlan >/dev/null 2>&1 && ! command -v wol >/dev/null 2>&1; then
		tool_pkg="etherwake"
		for candidate in etherwake wakeonlan wol; do
			if package_installed "$candidate"; then
				tool_pkg=""
				break
			fi
		done
		if [ -n "${tool_pkg:-}" ]; then
			need="$need $tool_pkg"
		fi
	fi
	need="${need# }"
	[ -n "$need" ] || return 0
	info "Устанавливаю поддержку Wake-on-LAN: $need"
	if ! install_packages $need; then
		warn "не удалось установить WOL-пакеты автоматически: $need"
	fi
}

install_xray_runtime() {
	local remote_bin rc
	[ "${ROOT%/}" = "" ] || return 0
	remote_bin="$(target_path usr/sbin/owrt-remote)"
	[ -x "$remote_bin" ] || die "missing $remote_bin after install"
	info "Preparing Xray and its persistent boot cache..."
	rc=0; run_step prepare-xray "$remote_bin" prepare-xray || rc=$?
	if [ "$rc" != 0 ]; then
		printf 'ERROR: failed to prepare Xray; stage=prepare-xray rc=%s; see diagnostic above\n' "$rc" >&2
		return "$rc"
	fi
}

verify_heartbeat() {
	# The response may contain a command or fresh Hub configuration; show only
	# the existing redacted diagnostic fields, never the raw HTTP body.
	local rc
	rc=0
	/usr/sbin/owrt-remote heartbeat >/dev/null || rc=$?
	/usr/sbin/owrt-remote heartbeat-diagnostics
	return "$rc"
}

verify_xray_start() {
	local attempt
	if [ -e /etc/owrt-remote/control/stopped ]; then
		info 'Xray remains intentionally stopped by Remote; Heartbeat is independent.'
		return 0
	fi
	attempt=0
	while [ "$attempt" -lt 30 ]; do
		if /usr/sbin/owrt-remote status | grep -Fq 'xray_state: running'; then
			info 'Xray process verified.'
			return 0
		fi
		attempt=$((attempt+1)); sleep 1
	done
	warn 'Xray did not start within 30 seconds; configuration retained. Check the current Xray and WAN status on the router.'
	return 1
}

ensure_install_dependencies

INSTALL_STAGE=files
install_file "usr/sbin/owrt-remote" 0755
install_file "usr/lib/owrt-remote/wan.sh" 0644
install_file "usr/lib/owrt-remote/control.sh" 0644
install_file "usr/lib/owrt-remote/tachyon.sh" 0644
install_file "usr/lib/owrt-remote/retire-logs.sh" 0644
run_step retire-journal env OWRT_REMOTE_RETIRE_ROOT="$ROOT" sh "$(target_path usr/lib/owrt-remote/retire-logs.sh)"
install_file "etc/init.d/owrt-remote" 0755
install_file "etc/hotplug.d/iface/99-owrt-remote" 0755
install_config
install_file "www/cgi-bin/owrt-remote" 0755
install_file "www/luci-static/resources/owrt_remote_effects.js" 0644
install_file "usr/share/luci/menu.d/luci-app-owrt-remote.json" 0644
install_file "usr/share/rpcd/acl.d/luci-app-owrt-remote.json" 0644
install_file "www/luci-static/resources/view/owrt_remote.js" 0644

ensure_wol_support



rm -f "$(target_path usr/lib/lua/luci/controller/owrt_remote.lua)" 2>/dev/null || true
rm -rf "$(target_path tmp/luci-indexcache)" "$(target_path tmp/luci-modulecache)" "$(target_path tmp/luci-indexcache.)"* "$(target_path tmp/luci-modulecache.)"* 2>/dev/null || true

if [ -x "$(target_path etc/init.d/rpcd)" ]; then
	if "$(target_path etc/init.d/rpcd)" running >/dev/null 2>&1; then
		run_step rpcd-reload "$(target_path etc/init.d/rpcd)" reload || die "rpcd reload failed"
	else
		run_step rpcd-start "$(target_path etc/init.d/rpcd)" start || die "rpcd start failed"
	fi
fi

if [ -x "$(target_path etc/init.d/uhttpd)" ]; then
	if "$(target_path etc/init.d/uhttpd)" running >/dev/null 2>&1; then
		run_step uhttpd-reload "$(target_path etc/init.d/uhttpd)" reload || die "uhttpd reload failed"
	else
		run_step uhttpd-start "$(target_path etc/init.d/uhttpd)" start || die "uhttpd start failed"
	fi
fi

install_xray_runtime

if [ "${ROOT%/}" = "" ]; then
	# Respect disabled clients and an intentional Remote stop. First-time
	# installs need OpenWrt config from Hub before a tunnel can be started.
	if [ "$(uci -q get owrtremote.main.enabled 2>/dev/null || true)" = 1 ]; then
		run_step config-test /usr/sbin/owrt-remote render-client || die "Xray configuration validation failed"
		run_step service-enable /etc/init.d/owrt-remote enable
		if /etc/init.d/owrt-remote running >/dev/null 2>&1; then
			run_step service-reload /etc/init.d/owrt-remote reload || die "Remote service reload failed"
		else
			run_step service-start /etc/init.d/owrt-remote start || die "Remote service start failed"
		fi
		run_step xray-running verify_xray_start || die "Xray start verification failed"
		run_step heartbeat verify_heartbeat || die "Heartbeat verification failed (files and working Xray retained)"
	else
		info 'Установлено. Добавьте OpenWrt config из Hub для запуска туннеля'
	fi
fi

key="$(make_key)"
ip="$(router_ip)"
ui_version="$(installed_ui_version)"
owrt_version="$(openwrt_version)"
pkg_manager="$(package_manager)"

info "OpenWrt Remote установлен."
info "OpenWrt: $owrt_version"
info "PKG:    $pkg_manager"
if [ -n "$ui_version" ]; then
	info "UI:     $ui_version"
fi
info "LuCI:   Службы -> OpenWrt Remote"
info "Панель: http://$ip/cgi-bin/owrt-remote?key=$key"
info "CLI:    owrt-remote doctor"
info "Xray:   если пишет 'нет Xray', нажми в панели 'Поставить Xray в /tmp' или выполни: owrt-remote install-xray-tmp"
