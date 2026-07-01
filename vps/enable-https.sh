#!/bin/sh
set -u

APP_NAME="OpenWrt Remote Hub"
RAW_BASE="${RAW_URL:-https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote/main}"
STATE_DIR="${OWRT_REMOTE_STATE_DIR:-/var/lib/owrt-remote}"
ACME_WEBROOT="$STATE_DIR/acme-webroot"
HOSTNAME_ARG="${1:-${HTTPS_HOST:-}}"
EMAIL="${EMAIL:-}"
STAGING="${STAGING:-0}"

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

detect_host() {
	if [ -n "$HOSTNAME_ARG" ]; then
		printf '%s\n' "$HOSTNAME_ARG"
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

is_ipv4() {
	printf '%s\n' "$1" | grep -Eq '^[0-9]{1,3}(\.[0-9]{1,3}){3}$'
}

install_certbot() {
	if command -v certbot >/dev/null 2>&1 && certbot --help all 2>/dev/null | grep -q -- '--ip-address'; then
		return
	fi

	info "Ставлю свежий Certbot через snap..."
	if command -v apt-get >/dev/null 2>&1; then
		$SUDO apt-get update
		$SUDO apt-get install -y snapd
	else
		die "нужен Ubuntu/Debian с apt-get или уже установленный certbot с --ip-address"
	fi

	$SUDO systemctl enable --now snapd.socket >/dev/null 2>&1 || true
	$SUDO snap wait system seed.loaded >/dev/null 2>&1 || true
	$SUDO snap install core >/dev/null 2>&1 || true
	$SUDO snap refresh core >/dev/null 2>&1 || true
	if $SUDO snap list certbot >/dev/null 2>&1; then
		$SUDO snap refresh certbot >/dev/null 2>&1 || true
	else
		$SUDO snap install --classic certbot >/dev/null 2>&1 || true
	fi
	$SUDO ln -sf /snap/bin/certbot /usr/bin/certbot

	command -v certbot >/dev/null 2>&1 || die "certbot не установился"
}

install_nginx() {
	if ! command -v apt-get >/dev/null 2>&1; then
		die "для HTTPS-прокси нужен Ubuntu/Debian с apt-get"
	fi
	info "Ставлю nginx для стабильного HTTPS..."
	$SUDO apt-get update
	policy_created=0
	if [ ! -e /usr/sbin/policy-rc.d ]; then
		$SUDO tee /usr/sbin/policy-rc.d >/dev/null <<'EOF'
#!/bin/sh
exit 101
EOF
		$SUDO chmod +x /usr/sbin/policy-rc.d
		policy_created=1
	fi
	if ! $SUDO apt-get install -y nginx; then
		if [ "$policy_created" = "1" ]; then
			$SUDO rm -f /usr/sbin/policy-rc.d
		fi
		die "nginx не установился"
	fi
	if [ "$policy_created" = "1" ]; then
		$SUDO rm -f /usr/sbin/policy-rc.d
	fi
	$SUDO rm -f /etc/nginx/sites-enabled/default /etc/nginx/sites-available/default
	$SUDO systemctl stop nginx >/dev/null 2>&1 || true
}

refresh_hub_files() {
	cache_bust="$(date +%s)"
	$SUDO mkdir -p /opt/owrt-remote "$STATE_DIR"
	if command -v wget >/dev/null 2>&1; then
		$SUDO wget -O /opt/owrt-remote/owrt-remote-hub.py "$RAW_BASE/vps/owrt-remote-hub.py?v=$cache_bust"
		$SUDO wget -O /etc/systemd/system/owrt-remote.service "$RAW_BASE/vps/owrt-remote.service?v=$cache_bust"
	else
		$SUDO curl -fsSL -o /opt/owrt-remote/owrt-remote-hub.py "$RAW_BASE/vps/owrt-remote-hub.py?v=$cache_bust"
		$SUDO curl -fsSL -o /etc/systemd/system/owrt-remote.service "$RAW_BASE/vps/owrt-remote.service?v=$cache_bust"
	fi
	$SUDO chmod +x /opt/owrt-remote/owrt-remote-hub.py
	$SUDO systemctl daemon-reload
	$SUDO systemctl restart owrt-remote
}

ensure_hub_http() {
	$SUDO mkdir -p "$ACME_WEBROOT/.well-known/acme-challenge"
	$SUDO chmod 755 "$STATE_DIR" "$ACME_WEBROOT" "$ACME_WEBROOT/.well-known" "$ACME_WEBROOT/.well-known/acme-challenge" 2>/dev/null || true
	if ! curl -fsS --max-time 3 http://127.0.0.1:8088/health >/dev/null 2>&1; then
		$SUDO systemctl restart owrt-remote
	fi
	i=1
	while [ "$i" -le 20 ]; do
		if curl -fsS --max-time 2 http://127.0.0.1:8088/health >/dev/null 2>&1; then
			return
		fi
		sleep 1
		i=$((i + 1))
	done
	die "Hub не ответил на http://127.0.0.1:8088/health"
}

certbot_email_args() {
	if [ -n "$EMAIL" ]; then
		printf '%s\n' "-m $EMAIL"
	else
		printf '%s\n' "--register-unsafely-without-email"
	fi
}

issue_cert() {
	host="$1"
	email_args="$(certbot_email_args)"
	staging_arg=""
	if [ "$STAGING" = "1" ]; then
		staging_arg="--staging"
	fi

	if is_ipv4 "$host"; then
		info "Получаю Let's Encrypt SSL для IP: $host"
		# shellcheck disable=SC2086
		$SUDO certbot certonly --non-interactive --agree-tos $staging_arg $email_args \
			--cert-name "$host" \
			--webroot --webroot-path "$ACME_WEBROOT" \
			--preferred-profile shortlived \
			--ip-address "$host"
	else
		info "Получаю Let's Encrypt SSL для домена: $host"
		# shellcheck disable=SC2086
		$SUDO certbot certonly --non-interactive --agree-tos $staging_arg $email_args \
			--cert-name "$host" \
			--webroot --webroot-path "$ACME_WEBROOT" \
			-d "$host"
	fi
}

enable_nginx_tls() {
	host="$1"
	live_dir="/etc/letsencrypt/live/$host"
	[ -f "$live_dir/fullchain.pem" ] || die "не найден сертификат: $live_dir/fullchain.pem"
	[ -f "$live_dir/privkey.pem" ] || die "не найден ключ: $live_dir/privkey.pem"

	# HTTPS обслуживает nginx. Hub остается обычным HTTP на 80/8088,
	# так LuCI и SSH-terminal не спотыкаются о встроенный TLS Python.
	$SUDO mkdir -p /etc/systemd/system/owrt-remote.service.d
	$SUDO tee /etc/systemd/system/owrt-remote.service.d/https.conf >/dev/null <<EOF
[Service]
Environment=OWRT_REMOTE_EXTRA_PORTS=80
Environment=OWRT_REMOTE_PUBLIC_URL=https://$host
Environment=OWRT_REMOTE_TLS_CERT=
Environment=OWRT_REMOTE_TLS_KEY=
Environment=OWRT_REMOTE_TLS_PORTS=
EOF

	$SUDO mkdir -p /etc/nginx/conf.d /etc/nginx/sites-available /etc/nginx/sites-enabled
	$SUDO tee /etc/nginx/conf.d/owrt-remote-map.conf >/dev/null <<'EOF'
map $http_upgrade $owrt_remote_connection_upgrade {
	default upgrade;
	'' close;
}
EOF
	$SUDO tee /etc/nginx/sites-available/owrt-remote >/dev/null <<EOF
server {
	listen 443 ssl http2;
	server_name $host;

	ssl_certificate $live_dir/fullchain.pem;
	ssl_certificate_key $live_dir/privkey.pem;

	client_max_body_size 64m;

	location / {
		proxy_pass http://127.0.0.1:8088;
		proxy_http_version 1.1;
		proxy_set_header Host \$host;
		proxy_set_header X-Real-IP \$remote_addr;
		proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
		proxy_set_header X-Forwarded-Proto https;
		proxy_set_header X-Forwarded-Host \$host;
		proxy_set_header Upgrade \$http_upgrade;
		proxy_set_header Connection \$owrt_remote_connection_upgrade;
		proxy_read_timeout 3600s;
		proxy_send_timeout 3600s;
		proxy_connect_timeout 30s;
		proxy_buffering off;
		proxy_request_buffering off;
	}
}
EOF
	$SUDO ln -sf /etc/nginx/sites-available/owrt-remote /etc/nginx/sites-enabled/owrt-remote
	$SUDO rm -f /etc/nginx/sites-enabled/default /etc/nginx/sites-available/default

	$SUDO mkdir -p /etc/letsencrypt/renewal-hooks/deploy
	$SUDO tee /etc/letsencrypt/renewal-hooks/deploy/owrt-remote-restart.sh >/dev/null <<'EOF'
#!/bin/sh
systemctl reload nginx >/dev/null 2>&1 || systemctl restart nginx >/dev/null 2>&1 || true
EOF
	$SUDO chmod +x /etc/letsencrypt/renewal-hooks/deploy/owrt-remote-restart.sh

	if command -v ufw >/dev/null 2>&1; then
		$SUDO ufw allow 443/tcp >/dev/null 2>&1 || true
	fi

	$SUDO systemctl daemon-reload
	$SUDO systemctl restart owrt-remote
	$SUDO nginx -t
	$SUDO systemctl enable --now nginx
	$SUDO systemctl restart nginx
}

check_https() {
	i=1
	while [ "$i" -le 20 ]; do
		if curl -kfsS --max-time 3 https://127.0.0.1/health >/dev/null 2>&1; then
			return 0
		fi
		sleep 1
		i=$((i + 1))
	done
	return 1
}

main() {
	host="$(detect_host)"
	[ -n "$host" ] || die "не смог определить IP/домен. Запусти: sh -s -- YOUR_DOMAIN_OR_IP"

	info "Включаю HTTPS для $APP_NAME..."
	info "Адрес: $host"
	refresh_hub_files
	install_certbot
	install_nginx
	if is_ipv4 "$host" && ! certbot --help all 2>/dev/null | grep -q -- '--ip-address'; then
		die "этот certbot не умеет IP-сертификаты. Нужен свежий certbot 5.4+ через snap."
	fi
	ensure_hub_http
	issue_cert "$host"
	enable_nginx_tls "$host"
	if ! check_https; then
		$SUDO systemctl status owrt-remote --no-pager -l || true
		die "HTTPS не ответил на https://127.0.0.1/health"
	fi

	info ""
	info "============================================================"
	info "HTTPS включен"
	info "============================================================"
	info "Панель:"
	info "  https://$host/"
	info "  http://$host/"
	info "  http://$host:8088/"
	info ""
	info "Проверь:"
	info "  sudo ss -lntp | grep -E ':(80|443|8088)'"
	info "  curl -k https://127.0.0.1/health"
	info "============================================================"
}

main "$@"
