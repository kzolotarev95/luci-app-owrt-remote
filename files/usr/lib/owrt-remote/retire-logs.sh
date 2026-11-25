#!/bin/sh
# Upgrade cleanup only. There is no journal reader, writer or transport here.
set -eu
retire_root="${IPKG_INSTROOT:-${OWRT_REMOTE_RETIRE_ROOT:-}}"
retire_root="${retire_root%/}"
case "$retire_root" in ''|/*) ;; *) echo 'Cleanup root must be absolute.' >&2; exit 1 ;; esac

if [ -z "$retire_root" ]; then
    if [ -x /etc/init.d/owrt-remote-logs ]; then
        if /etc/init.d/owrt-remote-logs running >/dev/null 2>&1; then
            /etc/init.d/owrt-remote-logs stop
        fi
        /etc/init.d/owrt-remote-logs disable
    fi
    # A native package upgrade may already have removed the old init file.
    if command -v ubus >/dev/null 2>&1 &&
        ubus -S call service list '{"name":"owrt-remote-logs"}' 2>/dev/null | grep -q '"owrt-remote-logs"'; then
        ubus call service delete '{"name":"owrt-remote-logs"}'
    fi
    # Reap a legacy worker left outside procd. Never match Xray or Heartbeat.
    retired_pids=''
    for retired_proc in /proc/[0-9]*/cmdline; do
        [ -r "$retired_proc" ] || continue
        if tr '\000' '\n' <"$retired_proc" 2>/dev/null | awk '
            $0 == "/usr/lib/owrt-remote/logs.sh" {found=1}
            previous == "/usr/sbin/owrt-remote" && $0 == "logs-sync" {found=1}
            {previous=$0} END {exit found ? 0 : 1}'; then
            retired_pid="${retired_proc%/cmdline}"
            retired_pid="${retired_pid##*/}"
            retired_pids="$retired_pids $retired_pid"
            kill "$retired_pid" 2>/dev/null || true
        fi
    done
    if [ -n "$retired_pids" ]; then
        sleep 1
        for retired_pid in $retired_pids; do
            # A PID can be reused after the worker exits; check its command again.
            [ -r "/proc/$retired_pid/cmdline" ] || continue
            if tr '\000' '\n' <"/proc/$retired_pid/cmdline" 2>/dev/null | awk '
                $0 == "/usr/lib/owrt-remote/logs.sh" {found=1}
                previous == "/usr/sbin/owrt-remote" && $0 == "logs-sync" {found=1}
                {previous=$0} END {exit found ? 0 : 1}'; then
                kill -9 "$retired_pid" 2>/dev/null || true
            fi
        done
    fi
fi

# Only the old feature's own directories. Configuration, Xray cache, notes,
# SSH keys and system logs are outside this list. Do not follow directory links.
for retired_path in /tmp/owrt-remote-log-runtime /tmp/owrt-remote-logs \
    /overlay/owrt-remote/logs /etc/owrt-remote/logs; do
    rm -rf -- "$retire_root$retired_path"
done
rm -f -- "$retire_root/usr/lib/owrt-remote/logs.sh" \
    "$retire_root/etc/init.d/owrt-remote-logs"
for retired_link in "$retire_root"/etc/rc.d/*owrt-remote-logs; do
    if [ -e "$retired_link" ] || [ -L "$retired_link" ]; then rm -f -- "$retired_link"; fi
done
