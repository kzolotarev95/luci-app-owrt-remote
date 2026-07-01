#!/bin/sh
set -u

APP_NAME="OpenWrt Remote Hub"
STATE_DIR="${OWRT_REMOTE_STATE_DIR:-/var/lib/owrt-remote}"
PURGE="${PURGE:-1}"
REMOVE_XRAY="${REMOVE_XRAY:-0}"

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

stop_service() {
	name="$1"
	if command -v systemctl >/dev/null 2>&1; then
		$SUDO systemctl stop "$name" >/dev/null 2>&1 || true
		$SUDO systemctl disable "$name" >/dev/null 2>&1 || true
	fi
}

remove_ufw_rule() {
	port="$1"
	if command -v ufw >/dev/null 2>&1; then
		$SUDO ufw --force delete allow "$port/tcp" >/dev/null 2>&1 || true
	fi
}

remove_xray_binary() {
	if [ "$REMOVE_XRAY" != "1" ]; then
		return
	fi
	if ! command -v curl >/dev/null 2>&1; then
		warn "curl не найден, Xray binary не удален"
		return
	fi
	info "Удаляю Xray binary через официальный installer..."
	$SUDO bash -c "$(curl -fsSL https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ remove >/dev/null 2>&1 || true
}

main() {
	info "Удаляю $APP_NAME с VPS..."

	stop_service owrt-remote
	stop_service owrt-remote-xray

	$SUDO rm -f /etc/systemd/system/owrt-remote.service
	$SUDO rm -f /etc/systemd/system/owrt-remote-xray.service
	$SUDO rm -rf /etc/systemd/system/owrt-remote.service.d
	$SUDO rm -f /etc/letsencrypt/renewal-hooks/deploy/owrt-remote-restart.sh
	$SUDO rm -f /etc/nginx/conf.d/owrt-remote-map.conf
	$SUDO rm -f /etc/nginx/sites-enabled/owrt-remote
	$SUDO rm -f /etc/nginx/sites-available/owrt-remote
	$SUDO rm -rf /opt/owrt-remote
	$SUDO rm -f /etc/xray/owrt-remote.json

	if [ "$PURGE" = "1" ]; then
		$SUDO rm -rf "$STATE_DIR"
	fi

	remove_ufw_rule 80
	remove_ufw_rule 443
	remove_ufw_rule 8088
	remove_ufw_rule 8443

	remove_xray_binary

	if command -v systemctl >/dev/null 2>&1; then
		$SUDO systemctl daemon-reload >/dev/null 2>&1 || true
		$SUDO systemctl reset-failed owrt-remote owrt-remote-xray >/dev/null 2>&1 || true
		$SUDO systemctl reload nginx >/dev/null 2>&1 || true
	fi

	info ""
	info "============================================================"
	info "$APP_NAME удален"
	info "============================================================"
	info "Удалено:"
	info "  /opt/owrt-remote"
	info "  /etc/systemd/system/owrt-remote.service"
	info "  /etc/systemd/system/owrt-remote-xray.service"
	info "  /etc/systemd/system/owrt-remote.service.d"
	info "  /etc/letsencrypt/renewal-hooks/deploy/owrt-remote-restart.sh"
	info "  /etc/nginx/conf.d/owrt-remote-map.conf"
	info "  /etc/nginx/sites-enabled/owrt-remote"
	info "  /etc/nginx/sites-available/owrt-remote"
	info "  /etc/xray/owrt-remote.json"
	if [ "$PURGE" = "1" ]; then
		info "  $STATE_DIR"
	else
		info "Оставлено:"
		info "  $STATE_DIR"
	fi
	info ""
	info "Порты ufw закрыты: 80/tcp, 443/tcp, 8088/tcp, 8443/tcp"
	if [ "$REMOVE_XRAY" != "1" ]; then
		info "Xray binary не удалялся. Чтобы удалить и его: REMOVE_XRAY=1"
	fi
	info "============================================================"
}

main "$@"
