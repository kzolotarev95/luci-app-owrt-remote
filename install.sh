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

fetch() {
	local src dst
	src="$1"
	dst="$2"
	if command -v wget >/dev/null 2>&1; then
		wget -O "$dst" "$src"
	elif command -v curl >/dev/null 2>&1; then
		curl -fsSL "$src" -o "$dst"
	else
		die "для удаленной установки нужен wget или curl"
	fi
}

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
	if [ -f "$src" ]; then
		cp "$src" "$dst"
	else
		bust="$(date +%s 2>/dev/null || echo $$)"
		fetch "$RAW_URL/files/$rel?v=$bust" "$dst"
	fi
	chmod 0644 "$dst"
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
			run_step packages-index opkg update || return $?
			run_step packages-install opkg install "$@"
			;;
		apk)
			run_step packages-index apk update || return $?
			run_step packages-install apk add "$@"
			;;
		*)
			return 1
			;;
	esac
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
	local remote_bin
	[ "${ROOT%/}" = "" ] || return 0
	remote_bin="$(target_path usr/sbin/owrt-remote)"
	[ -x "$remote_bin" ] || die "missing $remote_bin after install"
	info "Preparing Xray and its persistent boot cache..."
	run_step prepare-xray "$remote_bin" prepare-xray || die "failed to prepare Xray; see stage/command/rc above"
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
	warn 'Xray did not start within 30 seconds; configuration retained. Check Global logs on the router.'
	return 1
}

# Stop the old log writer before replacing its storage code. Its bounded legacy
# archive is migrated by the new log service, without moving any other /tmp data.
if [ "${ROOT%/}" = "" ] && [ -x /etc/init.d/owrt-remote-logs ]; then
	if /etc/init.d/owrt-remote-logs running >/dev/null 2>&1; then
		run_step logs-stop /etc/init.d/owrt-remote-logs stop
	fi
fi

INSTALL_STAGE=files
install_file "usr/sbin/owrt-remote" 0755
install_file "usr/lib/owrt-remote/wan.sh" 0644
install_file "usr/lib/owrt-remote/control.sh" 0644
install_file "usr/lib/owrt-remote/logs.sh" 0644
install_file "etc/init.d/owrt-remote-logs" 0755
install_file "etc/init.d/owrt-remote" 0755
install_file "etc/hotplug.d/iface/99-owrt-remote" 0755
install_config
install_file "www/cgi-bin/owrt-remote" 0755
install_file "www/luci-static/resources/owrt_remote_effects.js" 0644
install_file "usr/share/luci/menu.d/luci-app-owrt-remote.json" 0644
install_file "usr/share/rpcd/acl.d/luci-app-owrt-remote.json" 0644
install_file "www/luci-static/resources/view/owrt_remote.js" 0644

ensure_wol_support

if [ "${ROOT%/}" = "" ]; then
	run_step logs-enable /etc/init.d/owrt-remote-logs enable
	# restart calls procd service delete, which returns "Not found" on the
	# first installation. start is an idempotent procd service set operation.
	run_step logs-start /etc/init.d/owrt-remote-logs start
fi

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
		info 'Remote is disabled; apply OpenWrt config from Hub to start the tunnel and Heartbeat.'
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
