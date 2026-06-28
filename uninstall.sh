#!/bin/sh

set -eu

ROOT="${ROOT:-/}"
PURGE="${PURGE:-0}"

target_path() {
	printf '%s/%s' "${ROOT%/}" "$1"
}

rmf() {
	rm -f "$(target_path "$1")"
}

if [ -x "$(target_path etc/init.d/owrt-remote)" ]; then
	"$(target_path etc/init.d/owrt-remote)" stop >/dev/null 2>&1 || true
	"$(target_path etc/init.d/owrt-remote)" disable >/dev/null 2>&1 || true
fi

rmf usr/sbin/owrt-remote
rmf etc/init.d/owrt-remote
rmf www/cgi-bin/owrt-remote
rmf usr/lib/lua/luci/controller/owrt_remote.lua
rmf usr/share/luci/menu.d/luci-app-owrt-remote.json
rmf usr/share/rpcd/acl.d/luci-app-owrt-remote.json
rmf www/luci-static/resources/view/owrt_remote.js

if [ "$PURGE" = "1" ]; then
	rmf etc/config/owrtremote
	rmf etc/owrt-remote/web.key
	rm -f "$(target_path etc/xray/owrt-remote-client.json)" 2>/dev/null || true
	rmdir "$(target_path etc/owrt-remote)" 2>/dev/null || true
fi

rm -rf "$(target_path tmp/luci-indexcache)" "$(target_path tmp/luci-modulecache)" "$(target_path tmp/luci-indexcache.)"* "$(target_path tmp/luci-modulecache.)"* 2>/dev/null || true

if [ -x "$(target_path etc/init.d/rpcd)" ]; then
	"$(target_path etc/init.d/rpcd)" restart >/dev/null 2>&1 || true
fi

if [ -x "$(target_path etc/init.d/uhttpd)" ]; then
	"$(target_path etc/init.d/uhttpd)" reload >/dev/null 2>&1 || "$(target_path etc/init.d/uhttpd)" restart >/dev/null 2>&1 || true
fi

printf '%s\n' "OpenWrt Remote удален."
if [ "$PURGE" != "1" ]; then
	printf '%s\n' "Конфиг и web key оставлены. Для полного удаления запусти с PURGE=1."
fi

