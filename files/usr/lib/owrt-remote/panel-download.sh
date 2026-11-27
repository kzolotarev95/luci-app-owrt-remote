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
