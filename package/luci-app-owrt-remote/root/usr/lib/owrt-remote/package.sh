#!/bin/sh

# Native package installation needs the same private panel key as install.sh.
# No network downloads or nested opkg/apk operations run under the package lock.
set -eu

configure() {
	local dir key temp
	dir=/etc/owrt-remote
	umask 077
	mkdir -p "$dir"
	if [ ! -s "$dir/web.key" ]; then
		temp="$(mktemp "$dir/.web.key.XXXXXX")"
		trap 'rm -f "$temp"' EXIT
		dd if=/dev/urandom of="$temp" bs=32 count=1 2>/dev/null
		[ "$(wc -c <"$temp")" -eq 32 ] || { echo 'ERROR: insufficient random bytes for panel key' >&2; return 1; }
		key="$(sha256sum "$temp")"
		key="${key%% *}"
		[ "${#key}" = 64 ] || { echo 'ERROR: could not generate panel access key' >&2; return 1; }
		printf '%s\n' "$key" >"$temp"
		chmod 0600 "$temp"
		mv -f "$temp" "$dir/web.key"
		trap - EXIT
	fi
	chmod 0600 "$dir/web.key"
	# Native conffiles handling preserves existing UCI settings on upgrades.
	[ ! -f /etc/config/owrtremote ] || chmod 0600 /etc/config/owrtremote
	rm -f /tmp/luci-indexcache /tmp/luci-indexcache.*
	rm -rf /tmp/luci-modulecache
	if [ -x /etc/init.d/rpcd ]; then
		/etc/init.d/rpcd reload || { echo 'ERROR: rpcd reload failed' >&2; return 1; }
	fi
	printf '%s\n' 'OpenWrt Remote: installed. Open LuCI > Services > OpenWrt Remote.'
	printf '%s\n' 'Apply OpenWrt config from Hub to prepare Xray and start Remote.'
}

case "${1:-}" in
	configure) configure ;;
	*) echo 'Usage: package.sh configure' >&2; exit 2 ;;
esac
