#!/bin/sh

set -eu

export PATH="/bin:/sbin:/usr/bin:/usr/sbin:${PATH:-}"

RAW_URL="${RAW_URL:-https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote/main}"
ROOT="${ROOT:-/}"
SCRIPT_DIR="$(CDPATH= cd "$(dirname "$0")" 2>/dev/null && pwd)"

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
	if [ -f "$src" ]; then
		cp "$src" "$dst"
	else
		bust="$(date +%s 2>/dev/null || echo $$)"
		fetch "$RAW_URL/files/$rel?v=$bust" "$dst"
	fi
	chmod "$mode" "$dst"
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
			opkg update >/dev/null 2>&1 || warn "не удалось обновить список opkg, пробую ставить пакеты как есть"
			opkg install "$@"
			;;
		apk)
			apk update >/dev/null 2>&1 || warn "не удалось обновить список apk, пробую ставить пакеты как есть"
			apk add "$@"
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
	info "Installing Xray to /tmp..."
	"$remote_bin" install-xray-tmp || die "failed to install Xray to /tmp"
}

install_file "usr/sbin/owrt-remote" 0755
install_file "etc/init.d/owrt-remote" 0755
install_file "etc/hotplug.d/iface/99-owrt-remote" 0755
install_config
install_file "www/cgi-bin/owrt-remote" 0755
install_file "usr/share/luci/menu.d/luci-app-owrt-remote.json" 0644
install_file "usr/share/rpcd/acl.d/luci-app-owrt-remote.json" 0644
install_file "www/luci-static/resources/view/owrt_remote.js" 0644

ensure_wol_support

rm -f "$(target_path usr/lib/lua/luci/controller/owrt_remote.lua)" 2>/dev/null || true
rm -rf "$(target_path tmp/luci-indexcache)" "$(target_path tmp/luci-modulecache)" "$(target_path tmp/luci-indexcache.)"* "$(target_path tmp/luci-modulecache.)"* 2>/dev/null || true

if [ -x "$(target_path etc/init.d/rpcd)" ]; then
	"$(target_path etc/init.d/rpcd)" restart >/dev/null 2>&1 || true
fi

if [ -x "$(target_path etc/init.d/uhttpd)" ]; then
	"$(target_path etc/init.d/uhttpd)" reload >/dev/null 2>&1 || "$(target_path etc/init.d/uhttpd)" restart >/dev/null 2>&1 || true
fi

install_xray_runtime

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
