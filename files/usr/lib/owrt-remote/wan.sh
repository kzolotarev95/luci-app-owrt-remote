#!/bin/sh
# Private routing state for router-originated Remote traffic only. No LAN,
# dnsmasq, Podkop configuration or sing-box service is modified.
WAN_STATE="${TMPDIR:-/tmp}/owrt-remote-wan"
# BusyBox ip accepts a limited range of table IDs; stay below 256.
WAN_TABLE=210
WAN_PRIORITY=104
# Only used to recognize and remove the previous implementation's rules.
WAN_MARK=0x40000000
PODKOP_BYPASS_MARK=0x00200000

wan_ip_literal() {
	[ -n "$1" ] || return 1
	case "$1" in
		*:* ) printf '%s' "$1" | grep -Eq '^[0-9a-fA-F:]+$' ;;
		*) printf '%s' "$1" | awk -F. 'NF != 4 { exit 1 } { for (i=1;i<=4;i++) if ($i !~ /^[0-9]+$/ || $i > 255) exit 1 }' ;;
	esac
}

wan_url_host() {
	local authority
	authority="${1#*://}"
	authority="${authority%%/*}"
	case "$authority" in
		\[*\]*) authority="${authority#\[}"; printf '%s' "${authority%%\]*}" ;;
		*) printf '%s' "${authority%%:*}" ;;
	esac
}

wan_url_port() {
	local authority port
	authority="${1#*://}"; authority="${authority%%/*}"
	case "$authority" in
		\[*\]:*) port="${authority##*\]:}" ;;
		*:* ) port="${authority##*:}" ;;
		*) case "$1" in https://*) port=443 ;; *) port=80 ;; esac ;;
	esac
	port="$(safe_num "$port" 0)"
	[ "$port" -gt 0 ] && [ "$port" -le 65535 ] || return 1
	printf '%s' "$port"
}

wan_interfaces() {
	printf '%s\n' "${OWRT_REMOTE_WAN_INTERFACE:-$(uci_get wan_interface wan)}" | awk '
		{ for (i=1; i<=NF; i++) {
			if ($i !~ /^[a-zA-Z0-9_.:-]+$/) exit 1
			if (!seen[$i]++) { printf "%s%s", sep, $i; sep=" " }
		} }
	'
}

wan_interface_device() {
	local iface device
	iface="$1"
	device="$(ubus call "network.interface.$iface" status 2>/dev/null | jsonfilter -e '@.l3_device' 2>/dev/null)"
	[ -n "$device" ] || return 1
	case "$device" in *[!a-zA-Z0-9_.:-]*) return 1 ;; esac
	case "$device" in tun*|sing*|podkop*|lo) return 1 ;; esac
	printf '%s' "$device"
}

wan_interface_ready() {
	local device
	device="$(wan_interface_device "$1")" || return 1
	ip -4 route show table main default dev "$device" | grep -q '^default'
}

wan_select_interface() {
	local interfaces current iface
	interfaces="$(wan_interfaces)" || return 1
	current="$(cat "$WAN_STATE/active-interface" 2>/dev/null)"
	# Keep a working uplink rather than interrupting it when a primary returns.
	if [ -n "$current" ]; then
		case " $interfaces " in *" $current "*)
			if wan_interface_ready "$current"; then printf '%s' "$current"; return; fi
			;;
		esac
	fi
	for iface in $interfaces; do
		if wan_interface_ready "$iface"; then printf '%s' "$iface"; return; fi
	done
	return 1
}

wan_device() {
	local iface
	iface="${OWRT_REMOTE_ACTIVE_WAN_INTERFACE:-}"
	[ -n "$iface" ] || iface="$(wan_select_interface)" || return 1
	wan_interface_device "$iface"
}

wan_dns_server() {
	local dns iface
	dns="$(uci_get wan_dns '')"
	if [ -z "$dns" ]; then
		iface="${OWRT_REMOTE_ACTIVE_WAN_INTERFACE:-}"
		[ -n "$iface" ] || iface="$(wan_select_interface)" || return 1
		dns="$(ubus call "network.interface.$iface" status 2>/dev/null | jsonfilter -e '@["dns-server"][0]' 2>/dev/null)"
	fi
	[ -n "$dns" ] || dns=1.1.1.1
	wan_ip_literal "$dns" || return 1
	case "$dns" in 127.*|::1) dns=1.1.1.1 ;; esac
	printf '%s' "$dns"
}

wan_resolve_host() {
	local host dns answer cache now saved_ts saved_ip
	host="$1"
	wan_ip_literal "$host" && { printf '%s' "$host"; return; }
	case "$host" in ''|*[!a-zA-Z0-9_.-]*) return 1 ;; esac
	dns="$(wan_dns_server)" || return 1
	cache="$WAN_STATE/resolve-$(safe_id "$host-$dns")"
	now="$(date +%s)"
	saved_ts=0; saved_ip=''
	[ ! -r "$cache" ] || read -r saved_ts saved_ip <"$cache"
	if [ -n "$saved_ip" ] && [ $((now - saved_ts)) -lt 300 ]; then
		printf '%s' "$saved_ip"; return
	fi
	# Query WAN's resolver explicitly, never the local dnsmasq/sing-box resolver.
	answer="$(nslookup -timeout=3 -retry=1 "$host" "$dns" 2>/dev/null | awk '
		/^Name:/ { in_answer=1 }
		in_answer && /^Address([[:space:]][0-9]+)?:/ {
			for (i=2;i<=NF;i++) if ($i ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/) { print $i; exit }
		}')"
	case "$answer" in 198.18.*|198.19.*) answer='' ;; esac
	if wan_ip_literal "$answer"; then
		mkdir -p "$WAN_STATE"
		printf '%s %s\n' "$now" "$answer" >"$cache.tmp.$$"
		mv "$cache.tmp.$$" "$cache"
		printf '%s' "$answer"; return
	fi
	if [ -n "$saved_ip" ] && [ $((now - saved_ts)) -lt 3600 ]; then
		log "WAN DNS unavailable for $host; using last successful WAN address $saved_ip"
		printf '%s' "$saved_ip"; return
	fi
	log "WAN DNS failed: host=$host resolver=$dns"
	return 1
}

wan_endpoint_ip() {
	local host pinned
	host="$(wan_url_host "$1")"
	pinned="$(uci_get vps_host '')"
	# Preserve HTTP Host and TLS SNI while connecting to the manually pinned VPS.
	if ! wan_ip_literal "$host" && [ "$(vps_host_mode)" = manual ] && wan_ip_literal "$pinned"; then
		printf '%s' "$pinned"
	else
		wan_resolve_host "$host"
	fi
}

wan_owned_rule_pattern() {
	# iif lo selects locally originated traffic, never forwarded LAN packets.
	printf '^%s:[[:space:]]*from all (fwmark %s(/0xffffffff)?|to [0-9a-fA-F:.]+(/32|/128)? iif lo) lookup %s[[:space:]]*$' "$WAN_PRIORITY" "$WAN_MARK" "$WAN_TABLE"
}

wan_address_key() {
	# Compare IPv6 rules even when ip prints a different compression/zero form.
	printf '%s\n' "${1%%/*}" | awk '
		/:/ {
			s=tolower($0); split(s, halves, "::")
			left=split(halves[1], a, ":"); right=split(halves[2], b, ":")
			for(i=1;i<=left;i++) parts[++n]=a[i]
			if(index(s,"::")) for(i=left+right;i<8;i++) parts[++n]="0"
			for(i=1;i<=right;i++) parts[++n]=b[i]
			for(i=1;i<=n;i++) { sub(/^0+/, "", parts[i]); if(parts[i]=="") parts[i]="0"; printf "%s%s", i==1?"":":", parts[i] }
			print ""; next
		}
		{ print }
	'
}

wan_destination_addresses() {
	ip -"$1" rule show | grep -E "$(wan_owned_rule_pattern)" | awk '{ for(i=1;i<NF;i++) if($i=="to") print $(i+1) }'
}

wan_has_destination_rule() {
	local family key old
	family="$1"; key="$(wan_address_key "$2")"
	for old in $(wan_destination_addresses "$family"); do
		[ "$(wan_address_key "$old")" != "$key" ] || return 0
	done
	return 1
}

wan_remove_legacy_rules() {
	# Match the complete old selector before each deletion, including duplicates.
	# A different mark mask/source/interface belongs to another application.
	while ip -"$1" rule show 2>/dev/null | grep -Eq "^$WAN_PRIORITY:[[:space:]]*from all fwmark $WAN_MARK(/0xffffffff)? lookup $WAN_TABLE[[:space:]]*$"; do
		ip -"$1" rule del priority "$WAN_PRIORITY" fwmark "$WAN_MARK" table "$WAN_TABLE" || return 1
	done
}

wan_xray_addresses() {
	local config tag address
	config="$(uci_get xray_config /etc/xray/owrt-remote-client.json)"
	[ -r "$config" ] || return 0
	# Retain the address of the running tunnel until its config is regenerated.
	for tag in vps-interconn vps-ssh-interconn; do
		address="$(jsonfilter -i "$config" -e "@.outbounds[@.tag=\"$tag\"].settings.address" 2>/dev/null)"
		if wan_ip_literal "$address"; then printf '%s\n' "$address"; fi
	done
}

wan_apply_destination_rules() {
	local dns endpoints targets keys address family old key
	dns="$1"; endpoints="$2"
	targets="$WAN_STATE/targets.new.$$"; keys="$WAN_STATE/target-keys.$$"
	{
		printf '%s\n' "$dns"
		[ ! -r "$endpoints" ] || awk 'NF { print $1 }' "$endpoints"
		wan_xray_addresses
		if [ "${3:-0}" = 1 ]; then
			wan_destination_addresses 4
			wan_destination_addresses 6
		fi
	} | awk 'NF && !seen[$0]++' >"$targets"
	: >"$keys"
	while read -r address; do
		address="${address%%/*}"
		wan_ip_literal "$address" || return 1
		case "$address" in *:*) family=6 ;; *) family=4 ;; esac
		key="$(wan_address_key "$address")"
		printf '%s\n' "$key" >>"$keys"
		if ! wan_has_destination_rule "$family" "$address"; then
			ip -"$family" rule add priority "$WAN_PRIORITY" iif lo to "$address" table "$WAN_TABLE" || return 1
		fi
	done <"$targets"
	# Add replacements first; remove obsolete addresses only after all succeed.
	for family in 4 6; do
		for old in $(wan_destination_addresses "$family"); do
			key="$(wan_address_key "$old")"
			if ! grep -Fqx "$key" "$keys"; then
				while ip -"$family" rule del priority "$WAN_PRIORITY" iif lo to "$old" table "$WAN_TABLE" 2>/dev/null; do :; done
			fi
		done
		# Migrate only the old Remote mark rule, never Podkop's own policy rules.
		wan_remove_legacy_rules "$family" || return 1
	done
	mv "$targets" "$WAN_STATE/targets"
	rm -f "$keys"
}

wan_remove_legacy_firewall() {
	if nft list table inet owrt_remote_wan >/dev/null 2>&1; then
		nft delete table inet owrt_remote_wan || { log 'Cannot remove legacy Remote marking table'; return 1; }
	fi
}

wan_check_table() {
	local family rules routes own error
	family="$1"
	rules="$(ip -"$family" rule show)" || return 1
	own="$(wan_owned_rule_pattern)"
	# Never reuse another service's policy priority or routing table.
	if printf '%s\n' "$rules" | grep -E "^$WAN_PRIORITY:|lookup $WAN_TABLE([[:space:]]|$)" | grep -Ev "$own" | grep -q .; then
		log "WAN IPv$family table $WAN_TABLE or priority $WAN_PRIORITY is already used by another rule"; return 1
	fi
	error="$WAN_STATE/table-error.$$"
	if ! routes="$(ip -"$family" route show table "$WAN_TABLE" 2>"$error")"; then
		# iproute2 returns an error for an empty, not-yet-created table.
		if ! grep -qi 'FIB table does not exist' "$error"; then
			cat "$error" >&2; rm -f "$error"; return 1
		fi
	fi
	rm -f "$error"
	if [ -n "$routes" ] && [ ! -f "$WAN_STATE/route-owner-$family" ] && ! printf '%s\n' "$rules" | grep -Eq "$own"; then
		log "WAN IPv$family table $WAN_TABLE is not empty and does not belong to Remote"; return 1
	fi
}

wan_preflight() {
	local device tool
	for tool in ip nft ubus jsonfilter curl nslookup; do
		command -v "$tool" >/dev/null 2>&1 || { log "WAN direct requires $tool"; return 1; }
	done
	device="$(wan_device)" || { log "No selected WAN interface is ready: $(uci_get wan_interface wan)"; return 1; }
	case "$device" in tun*|sing*|podkop*) log "WAN direct cannot use a proxy/TUN device: $device"; return 1 ;; esac
	ip -4 route show table main default dev "$device" | grep -q '^default' || {
		log "WAN has no IPv4 default route on $device"; return 1
	}
	mkdir -p "$WAN_STATE"
	wan_check_table 4 || return 1
	if ip -6 rule show >/dev/null 2>&1; then wan_check_table 6 || return 1; fi
}

wan_prepare_locked() {
	local device dns gateway family host ip url port previous OWRT_REMOTE_ACTIVE_WAN_INTERFACE
	OWRT_REMOTE_ACTIVE_WAN_INTERFACE="$(wan_select_interface)" || { log 'No selected WAN has a physical IPv4 default route'; return 1; }
	wan_preflight || return 1
	device="$(wan_device)" || return 1
	dns="$(wan_dns_server)" || return 1
	# Use only routes belonging to the selected physical WAN, never a TUN default.
	for family in 4 6; do
		# Some OpenWrt kernels disable IPv6 completely; IPv4 Remote must still work.
		if [ "$family" = 6 ] && ! ip -6 rule show >/dev/null 2>&1; then continue; fi
		# Remember ownership before the first mutation, including partial failures.
		: >"$WAN_STATE/route-owner-$family"
		gateway="$(ip -"$family" route show table main default dev "$device" | awk 'NR==1 { for(i=1;i<NF;i++) if($i=="via") print $(i+1) }')"
		if ip -"$family" route show table main default dev "$device" | grep -q '^default'; then
			if [ -n "$gateway" ]; then
				ip -"$family" route replace default via "$gateway" dev "$device" onlink table "$WAN_TABLE" || return 1
			else
				ip -"$family" route replace default dev "$device" table "$WAN_TABLE" || return 1
			fi
		else
			ip -"$family" route flush table "$WAN_TABLE" 2>/dev/null || true
		fi
		# Do not fall through to a proxy route when the physical WAN is absent.
		# A default route wins over this terminal unreachable route while WAN is up.
		ip -"$family" route replace unreachable default metric 42760 table "$WAN_TABLE" || return 1
	done
	# Route DNS before resolving Hub, retaining existing tunnel/HTTP addresses.
	[ -e "$WAN_STATE/endpoints" ] || : >"$WAN_STATE/endpoints"
	wan_apply_destination_rules "$dns" "$WAN_STATE/endpoints" 1 || return 1
	wan_remove_legacy_firewall || return 1
	host="$(uci_get vps_host '')"
	ip="$(wan_resolve_host "$host")" || return 1
	port="$(safe_num "$(uci_get vps_port 8443)" 8443)"
	[ "$port" -gt 0 ] && [ "$port" -le 65535 ] || return 1
	printf '%s %s\n' "$ip" "$port" >"$WAN_STATE/endpoints.new.$$"
	# Include fallback web ports, with curl still verifying HTTPS certificates.
	for port in 80 443 8088; do printf '%s %s\n' "$ip" "$port" >>"$WAN_STATE/endpoints.new.$$"; done
	for url in "$(uci_get hub_url '')" "$(uci_get public_url '')"; do
		[ -n "$url" ] || continue
		ip="$(wan_endpoint_ip "$url")" || continue
		port="$(wan_url_port "$url")" || continue
		printf '%s %s\n' "$ip" "$port" >>"$WAN_STATE/endpoints.new.$$"
	done
	awk '!seen[$0]++' "$WAN_STATE/endpoints.new.$$" >"$WAN_STATE/endpoints.dedup.$$"
	mv "$WAN_STATE/endpoints.dedup.$$" "$WAN_STATE/endpoints.new.$$"
	wan_apply_destination_rules "$dns" "$WAN_STATE/endpoints.new.$$" || return 1
	mv "$WAN_STATE/endpoints.new.$$" "$WAN_STATE/endpoints"
	previous="$(cat "$WAN_STATE/active-interface" 2>/dev/null)"
	printf '%s\n' "$OWRT_REMOTE_ACTIVE_WAN_INTERFACE" >"$WAN_STATE/active-interface.new.$$"
	mv "$WAN_STATE/active-interface.new.$$" "$WAN_STATE/active-interface"
	if [ -n "$previous" ] && [ "$previous" != "$OWRT_REMOTE_ACTIVE_WAN_INTERFACE" ]; then
		log "WAN failover: $previous -> $OWRT_REMOTE_ACTIVE_WAN_INTERFACE"
	fi
}

wan_prepare() (
	local try_num owner
	mkdir -p "$WAN_STATE"
	try_num=0
	while ! mkdir "$WAN_STATE/lock" 2>/dev/null; do
		owner="$(cat "$WAN_STATE/lock/pid" 2>/dev/null)"
		case "$owner" in ''|*[!0-9]*) ;; *)
			if ! kill -0 "$owner" 2>/dev/null; then
				rm -f "$WAN_STATE/lock/pid"
				rmdir "$WAN_STATE/lock" 2>/dev/null || true
			fi
			;;
		esac
		try_num=$((try_num+1)); [ "$try_num" -lt 15 ] || return 1
		sleep 1
	done
	printf '%s' "$$" >"$WAN_STATE/lock/pid"
	trap 'rm -f "$WAN_STATE/lock/pid" "$WAN_STATE/endpoints.new.$$" "$WAN_STATE/endpoints.dedup.$$" "$WAN_STATE/targets.new.$$" "$WAN_STATE/target-keys.$$" "$WAN_STATE/active-interface.new.$$"; rmdir "$WAN_STATE/lock" 2>/dev/null || true' EXIT
	trap 'exit 1' INT TERM
	wan_prepare_locked
)

wan_post_json() {
	local url token file host port ip device selected OWRT_REMOTE_ACTIVE_WAN_INTERFACE
	url="$1"; token="$2"; file="$3"
	selected="$(wan_select_interface)" || return 1
	if [ "$selected" != "$(cat "$WAN_STATE/active-interface" 2>/dev/null)" ]; then
		wan_prepare || return 1
	fi
	OWRT_REMOTE_ACTIVE_WAN_INTERFACE="$(cat "$WAN_STATE/active-interface")" || return 1
	host="$(wan_url_host "$url")"
	port="$(wan_url_port "$url")" || return 1
	ip="$(wan_endpoint_ip "$url")" || return 1
	device="$(wan_device)" || return 1
	# A resolver cache may expire between prepare and this request. Refresh
	# direct routes before sending to a newly resolved IP.
	if ! grep -Fqx "$ip $port" "$WAN_STATE/endpoints" 2>/dev/null; then
		wan_prepare || return 1
		grep -Fqx "$ip $port" "$WAN_STATE/endpoints" || return 1
	fi
	case "$ip" in *:*) ip="[$ip]" ;; esac
	# No redirect-following or certificate bypass; Host and SNI remain the URL's.
	curl -fsS --noproxy '*' --interface "$device" --connect-timeout 4 --max-time 10 \
		--resolve "$host:$port:$ip" \
		-H "Authorization: Bearer $token" -H 'Content-Type: application/json' \
		--data-binary "@$file" "$url"
}

wan_cleanup() {
	local family owned address
	nft delete table inet owrt_remote_wan 2>/dev/null || true
	for family in 4 6; do
		owned=0
		if [ -f "$WAN_STATE/route-owner-$family" ] || ip -"$family" rule show 2>/dev/null | grep -Eq "$(wan_owned_rule_pattern)"; then owned=1; fi
		for address in $(wan_destination_addresses "$family"); do
			while ip -"$family" rule del priority "$WAN_PRIORITY" iif lo to "$address" table "$WAN_TABLE" 2>/dev/null; do :; done
		done
		wan_remove_legacy_rules "$family" 2>/dev/null || true
		if [ "$owned" = 1 ]; then ip -"$family" route flush table "$WAN_TABLE" 2>/dev/null || true; fi
		rm -f "$WAN_STATE/route-owner-$family"
	done
	# Only volatile resolver cache owned by this module.
	rm -f "$WAN_STATE"/resolve-* "$WAN_STATE/endpoints" "$WAN_STATE/targets" "$WAN_STATE/active-interface"
	rm -f "$WAN_STATE/lock/pid"
	rmdir "$WAN_STATE/lock" "$WAN_STATE" 2>/dev/null || true
}

wan_diagnostics() {
	printf 'vps_host=%s mode=%s WAN_direct=%s\n' "$(uci_get vps_host '')" "$(vps_host_mode)" "$(uci_get wan_direct 0)"
	printf 'WAN_selected=%s WAN_active=%s\n' "$(wan_interfaces)" "$(cat "$WAN_STATE/active-interface" 2>/dev/null)"
	printf 'WAN_device=%s WAN_DNS=%s\n' "$(wan_device)" "$(wan_dns_server)"
	printf 'System DNS (compare with WAN DNS):\n'
	nslookup -timeout=3 -retry=1 "$(wan_url_host "$(uci_get hub_url '')")" 2>&1 || true
	printf '\nWAN DNS:\n'
	nslookup -timeout=3 -retry=1 "$(wan_url_host "$(uci_get hub_url '')")" "$(wan_dns_server)" 2>&1 || true
	printf '\nRemote routes/rules:\n'
	ip -4 rule show
	ip -4 route show table "$WAN_TABLE"
	printf 'Remote does not create additional nftables marking rules.\n'
}
