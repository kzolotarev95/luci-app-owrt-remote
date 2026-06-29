<div align="center">

# OpenWrt Remote Hub

Удаленный доступ к OpenWrt через свой VPS: красивые карточки роутеров, online/offline, heartbeat, Xray VLESS reverse и вход в LuCI извне без ZeroTier, Tailscale и WireGuard.

[Telegram](https://t.me/kzolotarev95) · [GitHub](https://github.com/kzolotarev95) · [NetHaven VPN](https://t.me/+LZDsQJhUfcNhYWEy)

</div>

## Что это

`luci-app-owrt-remote` делает схему:

```text
Браузер -> VPS Hub -> локальный Xray entry port -> VLESS reverse tunnel -> LuCI роутера
```

Роутер сам подключается к VPS изнутри сети и держит reverse-туннель. На VPS открывается веб-панель с карточками роутеров. Нажимаешь `Админка` и попадаешь в LuCI нужного OpenWrt через VPS.

LuCI роутера напрямую в интернет открывать не нужно.

## Возможности

- Панель VPS: `http://YOUR_VPS_IP:8088/`.
- Первый вход: `admin` / `admin`.
- Логин и пароль можно поменять прямо в панели Hub.
- Несколько роутеров в одной панели.
- Online/offline по heartbeat.
- В карточке роутера: аптайм, RAM, flash, температура, load, Xray/service.
- Температура подсвечивается цветом: зеленая, желтая, красная.
- Кнопка `Админка` для входа в LuCI через VPS.
- Кнопка `SSH` открывает web-terminal прямо из карточки роутера.
- Кнопка `OpenWrt config` с готовыми UCI-командами для роутера.
- Кнопка `Client JSON` с клиентским Xray-конфигом.
- Легкий агент на OpenWrt: shell + CGI, без тяжелого Lua-приложения.

## Установка VPS

Нужен Ubuntu/Debian VPS, Python 3 и Xray.

### 1. Установить Xray на VPS

```sh
sudo apt update
sudo apt install -y curl unzip python3 openssh-client
sudo bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install
xray version || /usr/local/bin/xray version
```

### 2. Установить Hub

```sh
sudo mkdir -p /opt/owrt-remote /var/lib/owrt-remote /etc/xray
sudo wget -O /opt/owrt-remote/owrt-remote-hub.py "https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote/main/vps/owrt-remote-hub.py"
sudo wget -O /etc/systemd/system/owrt-remote.service "https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote/main/vps/owrt-remote.service"
sudo chmod +x /opt/owrt-remote/owrt-remote-hub.py
sudo systemctl daemon-reload
```

Создать базу и первый логин:

```sh
sudo /opt/owrt-remote/owrt-remote-hub.py init
```

По умолчанию создается вход:

```text
login: admin
password: admin
```

Пароль можно сразу сменить:

```sh
sudo /opt/owrt-remote/owrt-remote-hub.py set-login --username admin --password 'NEW_PASSWORD'
```

Запустить Hub:

```sh
sudo systemctl enable --now owrt-remote
sudo systemctl status owrt-remote --no-pager -l
```

Открыть панель:

```text
http://YOUR_VPS_IP:8088/
```

### 3. Добавить первый роутер на VPS

Пример:

```sh
sudo /opt/owrt-remote/owrt-remote-hub.py add-router \
  --id main \
  --name "Главный роутер" \
  --role main \
  --entry-port 18080 \
  --vps-host YOUR_VPS_IP
```

Порты не путать:

- `entry_port` задается на VPS в карточке роутера. Он должен быть уникальным: `18080`, `18090`, `18100`.
- `ssh_entry_port` создается автоматически как `entry_port + 1000`: для `18080` будет `19080`, для `18090` будет `19090`.
- `vps_port` на OpenWrt обычно один для всех роутеров: `8443`.
- `admin_port` на OpenWrt обычно `80`, это локальный порт LuCI внутри роутера.
- `ssh_port` на OpenWrt обычно `22`, это локальный порт SSH/dropbear внутри роутера.

Пример второго роутера:

```sh
sudo /opt/owrt-remote/owrt-remote-hub.py add-router \
  --id node-2 \
  --name "Второй роутер" \
  --role node \
  --entry-port 18090 \
  --vps-host YOUR_VPS_IP
```

Если карточка пишет `router has no entry_port`, задай порт безопасно, без смены UUID:

```sh
sudo /opt/owrt-remote/owrt-remote-hub.py set-entry-port --id node-2 --entry-port 18090
sudo /opt/owrt-remote/owrt-remote-hub.py render-xray --out /etc/xray/owrt-remote.json
sudo systemctl restart owrt-remote-xray
```

Если кнопка `Админка` пишет `proxy error: [Errno 111] Connection refused`, значит VPS Xray еще не слушает `entry_port` этого роутера. В Hub нажми `Обновить Xray VPS` или выполни:

```sh
sudo /opt/owrt-remote/owrt-remote-hub.py render-xray --out /etc/xray/owrt-remote.json
sudo systemctl restart owrt-remote-xray
sudo ss -lntp | grep -E ':(8443|18080|18090|18100|18095|19080|19090|19095)\b'
```

Для роутера с `entry 18095` в выводе должны появиться `127.0.0.1:18095` для LuCI и `127.0.0.1:19095` для SSH.

Сгенерировать Xray config для VPS:

```sh
sudo /opt/owrt-remote/owrt-remote-hub.py render-xray --out /etc/xray/owrt-remote.json
```

Создать systemd-сервис Xray reverse:

```sh
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

Открыть наружу нужно только два порта:

```sh
sudo ufw allow 8088/tcp
sudo ufw allow 8443/tcp
```

Если с мобильного интернета панель не открывается, проверь на VPS:

```sh
sudo systemctl restart owrt-remote
sudo ss -lntp | grep ':8088'
```

Правильно, когда видно `0.0.0.0:8088` или `*:8088`. Если видно только `127.0.0.1:8088`, Hub доступен только внутри VPS. Если порт слушает правильно, но с телефона не открывается `http://YOUR_VPS_IP:8088/`, открой `8088/tcp` еще и в firewall личного кабинета VPS-провайдера.

Если по домашнему интернету работает, а через мобильного оператора нет, чаще всего оператор режет нестандартный порт `8088` или WebSocket. Надежный вариант для телефона: повесить Hub на домен через HTTPS/443, а внутри проксировать на `127.0.0.1:8088`.

Важно: нормальный доверенный SSL на голый IP вида `https://193.233.126.205` обычно не выпускается. Для зеленого HTTPS нужен домен, например `remote.example.com`, A-запись на VPS и прокси на `127.0.0.1:8088`. Самоподписанный сертификат на IP можно сделать, но телефон и браузер будут показывать предупреждение.

Что за порты:

| Порт | Где | Для чего | Открывать наружу |
| --- | --- | --- | --- |
| `8088/tcp` | VPS | Веб-панель Hub | да |
| `8443/tcp` | VPS | Xray VLESS reverse, сюда подключаются роутеры | да |
| `18080/tcp` | VPS localhost | вход к первому роутеру через Hub | нет |
| `18090/tcp` | VPS localhost | вход ко второму роутеру через Hub | нет |
| `18100/tcp` | VPS localhost | вход к третьему роутеру через Hub | нет |
| `19080/tcp` | VPS localhost | SSH к первому роутеру через web-terminal | нет |
| `19090/tcp` | VPS localhost | SSH ко второму роутеру через web-terminal | нет |

Если у VPS-провайдера есть отдельный firewall в личном кабинете, там тоже открой `8088/tcp` и `8443/tcp`.

Проверить:

```sh
sudo ss -lntp | grep -E ':(8088|8443|18080|18090|18100)\b'
```

Порты `18080`, `18090`, `18100`, `19080`, `19090` должны слушать только `127.0.0.1`. Наружу нужны `8088` для Hub и `8443` для Xray.

## SSH web-terminal

В карточке online-роутера кнопка `SSH` открывает терминал в браузере.

Как это устроено:

- LuCI идет через `entry_port`, например `18080`.
- SSH идет через `ssh_entry_port`, например `19080`.
- Для SSH создается отдельный VLESS reverse-туннель, поэтому LuCI и SSH не мешают друг другу.
- Hub сам подключается к `root@127.0.0.1:19080` на VPS.
- Через Xray reverse это попадает в SSH/dropbear роутера на `127.0.0.1:22`.

После обновления обязательно обнови обе стороны. На VPS:

```sh
sudo /opt/owrt-remote/owrt-remote-hub.py render-xray --out /etc/xray/owrt-remote.json
sudo systemctl restart owrt-remote-xray
```

На каждом OpenWrt роутере заново вставь свежий `OpenWrt config` из карточки Hub или выведи его на VPS:

```sh
sudo /opt/owrt-remote/owrt-remote-hub.py print-openwrt-config --id ROUTER_ID --hub-url http://YOUR_VPS_IP:8088 --vps-host YOUR_VPS_IP
```

Потом на роутере:

```sh
owrt-remote render-client
/etc/init.d/owrt-remote restart
owrt-remote heartbeat
```

Если кнопка `SSH` серая, проверь на роутере:

```sh
/etc/init.d/dropbear status
owrt-remote heartbeat
```

## Установка на OpenWrt

Поставить агент:

```sh
wget -O - "https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote/main/install.sh?v=$(date +%s)" | sh
```

Открыть локальную панель:

```text
LuCI -> Службы -> OpenWrt Remote
```

Или напрямую:

```text
http://192.168.1.1/cgi-bin/owrt-remote
```

В VPS Hub открой карточку роутера -> `OpenWrt config`, вставь готовые UCI-команды в терминал роутера, потом:

```sh
owrt-remote render-client
/etc/init.d/owrt-remote enable
/etc/init.d/owrt-remote restart
owrt-remote doctor
owrt-remote heartbeat
```

## Xray на OpenWrt

OpenWrt Remote не кладет Xray автоматически в прошивку: бинарник большой, и на многих роутерах не хватает flash. Если в проверке видно `нет Xray: /usr/bin/xray`, поставь его одним из способов ниже.

Если хватает flash-памяти:

```sh
opkg update
opkg install xray-core
```

Если flash мало, можно запустить Xray из RAM:

```sh
owrt-remote install-xray-tmp
owrt-remote render-client
/etc/init.d/owrt-remote restart
owrt-remote doctor
```

То же самое можно сделать кнопкой `Поставить Xray в /tmp` в локальной панели роутера.

## Обновление

### VPS

```sh
sudo wget -O /opt/owrt-remote/owrt-remote-hub.py "https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote/main/vps/owrt-remote-hub.py?v=$(date +%s)"
sudo wget -O /etc/systemd/system/owrt-remote.service "https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote/main/vps/owrt-remote.service?v=$(date +%s)"
sudo chmod +x /opt/owrt-remote/owrt-remote-hub.py
sudo systemctl daemon-reload
sudo systemctl restart owrt-remote
sudo /opt/owrt-remote/owrt-remote-hub.py render-xray --out /etc/xray/owrt-remote.json
sudo systemctl restart owrt-remote-xray
```

### OpenWrt

```sh
wget -O - "https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote/main/install.sh?v=$(date +%s)" | sh
owrt-remote render-client
/etc/init.d/owrt-remote restart
owrt-remote heartbeat
```

### Обновление строго по commit SHA

Так удобнее, если GitHub raw отдает старый кэш:

```sh
SHA="PUT_COMMIT_SHA_HERE"
export RAW_URL="https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote/$SHA"
wget -O - "$RAW_URL/install.sh" | sh
```

## Команды VPS Hub

```sh
sudo /opt/owrt-remote/owrt-remote-hub.py init
sudo /opt/owrt-remote/owrt-remote-hub.py set-login --username admin --password admin
sudo /opt/owrt-remote/owrt-remote-hub.py add-router --id main --name "Главный роутер" --role main --entry-port 18080 --vps-host YOUR_VPS_IP
sudo /opt/owrt-remote/owrt-remote-hub.py list-routers
sudo /opt/owrt-remote/owrt-remote-hub.py render-xray --out /etc/xray/owrt-remote.json
sudo /opt/owrt-remote/owrt-remote-hub.py print-openwrt-config --id main --hub-url http://YOUR_VPS_IP:8088 --vps-host YOUR_VPS_IP
```

## Команды OpenWrt агента

```sh
owrt-remote status
owrt-remote doctor
owrt-remote render-client
owrt-remote render-client --stdout
owrt-remote heartbeat
```

## Диагностика

На роутере:

```sh
owrt-remote doctor
owrt-remote status
owrt-remote heartbeat
```

На VPS:

```sh
sudo systemctl status owrt-remote --no-pager -l
sudo systemctl status owrt-remote-xray --no-pager -l
sudo journalctl -u owrt-remote-xray -n 100 --no-pager
curl -v --max-time 10 http://127.0.0.1:18080/cgi-bin/luci
```

Если `curl` возвращает HTML LuCI или `403 Forbidden` со страницей входа LuCI, туннель работает. Открывай Hub и нажимай `Админка`.

## Что ставится на OpenWrt

| Путь | Назначение |
| --- | --- |
| `/usr/sbin/owrt-remote` | CLI-агент: render config, heartbeat, status |
| `/etc/init.d/owrt-remote` | procd-сервис: Xray reverse + heartbeat loop |
| `/www/cgi-bin/owrt-remote` | локальная CGI-панель настройки |
| `/www/luci-static/resources/view/owrt_remote.js` | LuCI redirect |
| `/usr/share/luci/menu.d/luci-app-owrt-remote.json` | пункт меню LuCI |
| `/usr/share/rpcd/acl.d/luci-app-owrt-remote.json` | LuCI ACL |
| `/etc/config/owrtremote` | UCI-настройки |
| `/etc/owrt-remote/web.key` | приватный ключ локальной панели |

## Безопасность

- Не открывай LuCI роутера напрямую в интернет.
- Entry-порты роутеров на VPS слушают только `127.0.0.1`.
- Hub защищен логином и паролем.
- Heartbeat от роутеров защищен отдельным `AGENT_TOKEN`.
- Для постоянного боевого режима лучше поставить Hub за HTTPS через Caddy или Nginx.
- После первого входа `admin/admin` лучше сразу поменять пароль в блоке `Доступ к Hub`.
