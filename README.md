<div align="center">

# OpenWrt Remote Hub

Remote access to OpenWrt through your own VPS: router cards, online/offline heartbeat, Xray VLESS reverse tunnel and LuCI proxy without ZeroTier, Tailscale or WireGuard.

[Telegram](https://t.me/kzolotarev95) · [GitHub](https://github.com/kzolotarev95) · [NetHaven VPN](https://t.me/+LZDsQJhUfcNhYWEy)

</div>

## What It Does

`luci-app-owrt-remote` builds this route:

```text
Browser -> VPS Hub -> local Xray entry port -> VLESS reverse tunnel -> OpenWrt LuCI
```

The VPS shows router cards with online/offline state. A router behind NAT connects outward to the VPS and keeps a reverse Xray tunnel alive. You click `Админка` in the Hub and open the router LuCI through the VPS.

No public LuCI ports on the router are needed.

## Current Features

- VPS dashboard at `http://YOUR_VPS_IP:8088/`.
- Normal Hub login and password, no secret token in browser URLs.
- Login/password can be changed inside the Hub panel.
- Multiple router cards.
- Online/offline heartbeat.
- `OpenWrt config` button with ready UCI commands for a router.
- `Client JSON` button for generated router Xray config.
- LuCI proxy through `/access/<router-id>/`.
- Lightweight OpenWrt agent: shell + CGI, no heavy LuCI Lua app.

## VPS Install

Use Ubuntu/Debian VPS with Python 3 and Xray.

### 1. Install Xray On VPS

```sh
sudo apt update
sudo apt install -y curl unzip python3
sudo bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install
xray version || /usr/local/bin/xray version
```

### 2. Install Hub

```sh
sudo mkdir -p /opt/owrt-remote /var/lib/owrt-remote /etc/xray
sudo wget -O /opt/owrt-remote/owrt-remote-hub.py "https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote/main/vps/owrt-remote-hub.py"
sudo wget -O /etc/systemd/system/owrt-remote.service "https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote/main/vps/owrt-remote.service"
sudo chmod +x /opt/owrt-remote/owrt-remote-hub.py
sudo systemctl daemon-reload
```

Set your Hub login and password:

```sh
sudo /opt/owrt-remote/owrt-remote-hub.py init
sudo /opt/owrt-remote/owrt-remote-hub.py set-login --username admin --password 'CHANGE_ME_STRONG_PASSWORD'
```

Start the Hub:

```sh
sudo systemctl enable --now owrt-remote
sudo systemctl status owrt-remote --no-pager -l
```

Open:

```text
http://YOUR_VPS_IP:8088/
```

### 3. Add First Router On VPS

Example:

```sh
sudo /opt/owrt-remote/owrt-remote-hub.py add-router \
  --id main \
  --name "Главный роутер" \
  --role main \
  --entry-port 18080 \
  --vps-host YOUR_VPS_IP
```

Render and start VPS Xray reverse service:

```sh
sudo /opt/owrt-remote/owrt-remote-hub.py render-xray --out /etc/xray/owrt-remote.json
XRAY_BIN="$(command -v xray || command -v /usr/local/bin/xray || command -v /usr/bin/xray)"
sudo tee /etc/systemd/system/owrt-remote-xray.service >/dev/null <<EOF
[Unit]
Description=OpenWrt Remote Xray Reverse
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$XRAY_BIN run -config /etc/xray/owrt-remote.json
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now owrt-remote-xray
```

Firewall:

```sh
sudo ufw allow 8088/tcp
sudo ufw allow 8443/tcp
```

Check:

```sh
sudo ss -lntp | grep -E ':(8088|8443|18080)\b'
```

`18080` should listen only on `127.0.0.1`. The public ports are `8088` for Hub and `8443` for Xray.

## OpenWrt Install

Install the OpenWrt agent:

```sh
wget -O - "https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote/main/install.sh?v=$(date +%s)" | sh
```

Open local panel:

```text
LuCI -> Службы -> OpenWrt Remote
```

Or direct:

```text
http://192.168.1.1/cgi-bin/owrt-remote
```

In the VPS Hub open router card -> `OpenWrt config`, paste the UCI commands into the router shell, then:

```sh
owrt-remote render-client
/etc/init.d/owrt-remote enable
/etc/init.d/owrt-remote restart
owrt-remote doctor
owrt-remote heartbeat
```

## Xray On OpenWrt

If your router has enough flash:

```sh
opkg update
opkg install xray-core
```

If flash is small, run Xray from RAM for testing:

```sh
mkdir -p /var/lock /var/run /tmp/owrt-xray
cd /tmp/owrt-xray
wget -O xray.zip "https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-arm64-v8a.zip"
command -v unzip >/dev/null 2>&1 || { opkg update; opkg install unzip; }
unzip -o xray.zip xray
chmod +x /tmp/owrt-xray/xray
/tmp/owrt-xray/xray version
uci set owrtremote.main.xray_bin='/tmp/owrt-xray/xray'
uci commit owrtremote
/etc/init.d/owrt-remote restart
```

For another architecture, download the matching Xray release asset from:

```text
https://github.com/XTLS/Xray-core/releases/latest
```

## Update

VPS:

```sh
sudo wget -O /opt/owrt-remote/owrt-remote-hub.py "https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote/main/vps/owrt-remote-hub.py?v=$(date +%s)"
sudo chmod +x /opt/owrt-remote/owrt-remote-hub.py
sudo systemctl restart owrt-remote
sudo /opt/owrt-remote/owrt-remote-hub.py render-xray --out /etc/xray/owrt-remote.json
sudo systemctl restart owrt-remote-xray
```

OpenWrt:

```sh
wget -O - "https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote/main/install.sh?v=$(date +%s)" | sh
owrt-remote render-client
/etc/init.d/owrt-remote restart
owrt-remote heartbeat
```

For cache-proof updates, use a commit SHA instead of `main`:

```sh
SHA="PUT_COMMIT_SHA_HERE"
export RAW_URL="https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote/$SHA"
wget -O - "$RAW_URL/install.sh" | sh
```

## Hub Commands

```sh
sudo /opt/owrt-remote/owrt-remote-hub.py init
sudo /opt/owrt-remote/owrt-remote-hub.py set-login --username admin --password 'NEW_PASSWORD'
sudo /opt/owrt-remote/owrt-remote-hub.py add-router --id main --name "Главный роутер" --role main --entry-port 18080 --vps-host YOUR_VPS_IP
sudo /opt/owrt-remote/owrt-remote-hub.py list-routers
sudo /opt/owrt-remote/owrt-remote-hub.py render-xray --out /etc/xray/owrt-remote.json
sudo /opt/owrt-remote/owrt-remote-hub.py print-openwrt-config --id main --hub-url http://YOUR_VPS_IP:8088 --vps-host YOUR_VPS_IP
```

## OpenWrt Agent Commands

```sh
owrt-remote status
owrt-remote doctor
owrt-remote render-client
owrt-remote render-client --stdout
owrt-remote heartbeat
```

## Troubleshooting

Router heartbeat:

```sh
owrt-remote doctor
owrt-remote status
owrt-remote heartbeat
```

VPS Xray:

```sh
sudo systemctl status owrt-remote-xray --no-pager -l
sudo journalctl -u owrt-remote-xray -n 100 --no-pager
curl -v --max-time 10 http://127.0.0.1:18080/cgi-bin/luci
```

If `curl` returns LuCI HTML or `403 Forbidden` with login page, the tunnel works. Open Hub and click `Админка`.

## Files On OpenWrt

| Path | Purpose |
| --- | --- |
| `/usr/sbin/owrt-remote` | CLI agent: render config, heartbeat, status |
| `/etc/init.d/owrt-remote` | procd service: Xray reverse + heartbeat loop |
| `/www/cgi-bin/owrt-remote` | local CGI configuration panel |
| `/www/luci-static/resources/view/owrt_remote.js` | LuCI menu redirect |
| `/usr/share/luci/menu.d/luci-app-owrt-remote.json` | LuCI menu item |
| `/usr/share/rpcd/acl.d/luci-app-owrt-remote.json` | LuCI ACL |
| `/etc/config/owrtremote` | UCI settings |
| `/etc/owrt-remote/web.key` | local panel private key |

## Security Notes

- Do not expose router LuCI directly to the internet.
- Router entry ports on VPS listen on `127.0.0.1` only.
- Hub uses login/password and cookie session.
- Router heartbeat uses separate `AGENT_TOKEN`.
- For production, put Hub behind HTTPS with Caddy, Nginx or another reverse proxy.
