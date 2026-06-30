#!/bin/sh
set -u

APP_NAME="OpenWrt Remote Hub"
RAW_BASE="${RAW_URL:-https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote/main}"
STATE_DIR="${OWRT_REMOTE_STATE_DIR:-/var/lib/owrt-remote}"
HUB_LOGIN="${HUB_LOGIN:-admin}"
HUB_PASSWORD="${HUB_PASSWORD:-admin}"
RESET_LOGIN="${RESET_LOGIN:-1}"

if [ "$(id -u)" -eq 0 ]; then
	SUDO=""
else
	SUDO="sudo"
fi

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

need_cmd() {
	command -v "$1" >/dev/null 2>&1 || die "не найдена команда: $1"
}

detect_vps_host() {
	if [ -n "${VPS_HOST:-}" ]; then
		printf '%s\n' "$VPS_HOST"
		return
	fi
	if [ "${1:-}" != "" ]; then
		printf '%s\n' "$1"
		return
	fi
	if command -v curl >/dev/null 2>&1; then
		host="$(curl -4fsS --max-time 5 https://api.ipify.org 2>/dev/null || true)"
		if [ -n "$host" ]; then
			printf '%s\n' "$host"
			return
		fi
	fi
	hostname -I 2>/dev/null | awk '{print $1}'
}

install_packages() {
	if command -v apt-get >/dev/null 2>&1; then
		$SUDO apt-get update
		$SUDO apt-get install -y curl wget unzip python3 openssh-client ca-certificates ufw
		return
	fi
	die "поддерживается Ubuntu/Debian с apt-get"
}

install_xray_binary() {
	if command -v xray >/dev/null 2>&1 || command -v /usr/local/bin/xray >/dev/null 2>&1 || command -v /usr/bin/xray >/dev/null 2>&1; then
		return
	fi
	info "Ставлю Xray на VPS..."
	if ! $SUDO bash -c "$(curl -fsSL https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install; then
		warn "Xray не поставился автоматически. Панель Hub все равно будет работать, Xray можно поставить позже."
	fi
}

install_files() {
	$SUDO mkdir -p /opt/owrt-remote "$STATE_DIR" /etc/xray
	$SUDO wget -O /opt/owrt-remote/owrt-remote-hub.py "$RAW_BASE/vps/owrt-remote-hub.py"
	$SUDO wget -O /etc/systemd/system/owrt-remote.service "$RAW_BASE/vps/owrt-remote.service"
	$SUDO chmod +x /opt/owrt-remote/owrt-remote-hub.py
}

install_xray_service() {
	xray_bin="$(command -v xray || command -v /usr/local/bin/xray || command -v /usr/bin/xray || true)"
	[ -n "$xray_bin" ] || return 0
	$SUDO tee /etc/systemd/system/owrt-remote-xray.service >/dev/null <<EOF
[Unit]
Description=OpenWrt Remote Xray Reverse
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$xray_bin run -config /etc/xray/owrt-remote.json
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
	$SUDO systemctl enable owrt-remote-xray >/dev/null 2>&1 || true
}

open_firewall() {
	if command -v ufw >/dev/null 2>&1; then
		$SUDO ufw allow 80/tcp >/dev/null 2>&1 || true
		$SUDO ufw allow 8088/tcp >/dev/null 2>&1 || true
		$SUDO ufw allow 8443/tcp >/dev/null 2>&1 || true
	fi
}

start_hub() {
	$SUDO /opt/owrt-remote/owrt-remote-hub.py init >/tmp/owrt-remote-init.log 2>&1 || {
		cat /tmp/owrt-remote-init.log >&2
		die "не смог создать базу Hub"
	}
	if [ "$RESET_LOGIN" = "1" ]; then
		$SUDO /opt/owrt-remote/owrt-remote-hub.py set-login --username "$HUB_LOGIN" --password "$HUB_PASSWORD" >/dev/null
	fi
	$SUDO systemctl daemon-reload
	$SUDO systemctl enable --now owrt-remote
	$SUDO systemctl restart owrt-remote
}

check_hub() {
	HUB_PORT80_OK=0
	info "Жду запуск Hub..."
	i=1
	while [ "$i" -le 20 ]; do
		if curl -fsS --max-time 2 http://127.0.0.1:8088/health >/tmp/owrt-remote-health.log 2>&1; then
			if curl -fsS --max-time 2 http://127.0.0.1/health >/dev/null 2>&1; then
				HUB_PORT80_OK=1
			fi
			return 0
		fi
		sleep 1
		i=$((i + 1))
	done
	warn "Hub не ответил на http://127.0.0.1:8088/health"
	$SUDO systemctl status owrt-remote --no-pager -l || true
	$SUDO journalctl -u owrt-remote -n 80 --no-pager || true
	return 1
}

print_result() {
	host="$1"
	info ""
	info "============================================================"
	info "$APP_NAME установлен"
	info "============================================================"
	info "Панель:"
	if [ "${HUB_PORT80_OK:-0}" = "1" ]; then
		info "  http://$host/"
	else
		info "  http://$host/       (порт 80 не ответил, проверь firewall или занятый порт)"
	fi
	info "  http://$host:8088/"
	info ""
	info "Вход:"
	info "  login:    $HUB_LOGIN"
	info "  password: $HUB_PASSWORD"
	info ""
	info "Проверка на VPS:"
	info "  sudo systemctl status owrt-remote --no-pager -l"
	info "  sudo ss -lntp | grep -E ':(80|8088|8443)'"
	info "  curl -sS http://127.0.0.1:8088/health"
	info ""
	info "Если снаружи не открывается, открой в firewall VPS-провайдера:"
	info "  80/tcp, 8088/tcp, 8443/tcp"
	info "============================================================"
}

main() {
	info "Ставлю $APP_NAME..."
	install_packages
	need_cmd curl
	need_cmd wget
	need_cmd python3
	host="$(detect_vps_host "${1:-}")"
	[ -n "$host" ] || host="YOUR_VPS_IP"
	install_xray_binary
	install_files
	install_xray_service
	open_firewall
	start_hub
	check_hub || die "Hub установлен, но сервис не поднялся. Лог выше."
	print_result "$host"
}

main "${1:-}"
