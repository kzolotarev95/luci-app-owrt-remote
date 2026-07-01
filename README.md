<div align="center">

# OpenWrt Remote Hub

**Удаленный доступ к OpenWrt через свой VPS**

<p>
  Карточки роутеров
  ·
  Online/Offline
  ·
  LuCI
  ·
  SSH Web Terminal
  ·
  Xray Reverse
  ·
  HTTPS через nginx
</p>

<p>
  <img alt="OpenWrt" src="https://img.shields.io/badge/OPENWRT-24.10.x-00A3E0?style=for-the-badge&labelColor=555555">
  <img alt="LuCI" src="https://img.shields.io/badge/LUCI-SUPPORTED-44CC11?style=for-the-badge&labelColor=555555">
  <img alt="VPS" src="https://img.shields.io/badge/VPS-HUB-7C3AED?style=for-the-badge&labelColor=555555">
  <img alt="Xray" src="https://img.shields.io/badge/XRAY-REVERSE-F97316?style=for-the-badge&labelColor=555555">
  <img alt="HTTPS" src="https://img.shields.io/badge/HTTPS-NGINX-22C55E?style=for-the-badge&labelColor=555555">
  <img alt="Build" src="https://img.shields.io/badge/BUILD-V275-A855F7?style=for-the-badge&labelColor=555555">
</p>

<p>

</p>

</div>

## Быстрый старт

### VPS: поставить Hub, Xray, firewall и HTTPS через nginx одной командой

```sh
curl -fsSL "https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote/main/vps/install-vps.sh?v=$(date +%s)" | sudo sh
```

После установки в конце будет вход:

```text
login:    admin
password: admin
```

### OpenWrt: поставить  Remote Hub на роутер 

```sh
wget -O - "https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote/main/install.sh?v=$(date +%s)" | sh
```

Проверка на роутере:

```sh
owrt-remote doctor
owrt-remote status
```

Если нужен домен, передай его установщику:

```sh
curl -fsSL "https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote/main/vps/install-vps.sh?v=$(date +%s)" | sudo sh -s -- hub.example.com
```

Если HTTPS не нужен:

```sh
curl -fsSL "https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote/main/vps/install-vps.sh?v=$(date +%s)" | sudo env AUTO_HTTPS=0 sh
```

## Схема

<img width="2172" height="724" alt="ChatGPT Image 30 июн  2026 г , 11_31_11" src="https://github.com/user-attachments/assets/ea79f783-170e-4db1-be4d-2b217e623b99" />


Роутер сам подключается к VPS изнутри сети. Наружу LuCI и SSH на роутере открывать не нужно.


## Раскрыть разделы

<details open>
<summary><b>Установка VPS подробно</b></summary>

Если хочешь поставить повторно, но не сбрасывать текущий пароль:

```sh
curl -fsSL "https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote/main/vps/install-vps.sh?v=$(date +%s)" | sudo env RESET_LOGIN=0 sh
```

Если HTTPS не включился автоматически, HTTP-панель все равно остается рабочей. После проверки firewall можно запустить:

```sh
curl -fsSL "https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote/main/vps/enable-https.sh?v=$(date +%s)" | sudo sh -s -- YOUR_VPS_IP
```

</details>

<details>
<summary><b>HTTPS / SSL</b></summary>

Установщик пытается включить HTTPS сам. Схема такая: Hub работает внутри на `80` и `8088`, а HTTPS на `443` принимает nginx и прокидывает запросы в Hub.

Для установки должны быть открыты порты:

```text
80/tcp   - HTTP-панель и проверка Let's Encrypt
443/tcp  - HTTPS-панель через nginx
8088/tcp - прямой HTTP-порт Hub, можно закрыть позже в firewall провайдера
```

Включить HTTPS вручную на IP:

```sh
curl -fsSL "https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote/main/vps/enable-https.sh?v=$(date +%s)" | sudo sh -s -- YOUR_VPS_IP
```

Включить HTTPS вручную на домен:

```sh
curl -fsSL "https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote/main/vps/enable-https.sh?v=$(date +%s)" | sudo EMAIL="you@example.com" sh -s -- hub.example.com
```

Проверка на VPS:

```sh
sudo ss -lntp | grep -E ':(80|443|8088)\b'
curl -sS http://127.0.0.1:8088/health
curl -k https://127.0.0.1/health
sudo nginx -t
```

Нормальная картина: `443` слушает `nginx`, а `80` и `8088` слушает `python3` Hub. Certbot ставит auto-renew, а скрипт добавляет hook для перезагрузки nginx после обновления сертификата.

</details>

<details>
<summary><b>Добавить первый роутер</b></summary>

На VPS:

```sh
VPS_HOST="YOUR_VPS_IP"

sudo /opt/owrt-remote/owrt-remote-hub.py add-router \
  --id main \
  --name "Главный роутер" \
  --role main \
  --entry-port 18080 \
  --vps-host "$VPS_HOST"

sudo /opt/owrt-remote/owrt-remote-hub.py render-xray --out /etc/xray/owrt-remote.json
sudo systemctl restart owrt-remote-xray
```

Потом открой Hub, нажми у роутера `OpenWrt config`, скопируй команды и вставь их в SSH роутера.

Вывести команды прямо на VPS:

```sh
sudo /opt/owrt-remote/owrt-remote-hub.py print-openwrt-config \
  --id main \
  --hub-url "https://$VPS_HOST" \
  --vps-host "$VPS_HOST"
```

После вставки команд на OpenWrt:

```sh
owrt-remote render-client
/etc/init.d/owrt-remote enable
/etc/init.d/owrt-remote restart
owrt-remote heartbeat
```

</details>

<details>
<summary><b>Добавить второй и следующие роутеры</b></summary>

Каждому роутеру нужен свой `id` и свой `entry-port`.

Пример второго роутера:

```sh
VPS_HOST="YOUR_VPS_IP"

sudo /opt/owrt-remote/owrt-remote-hub.py add-router \
  --id node-2 \
  --name "Второй роутер" \
  --role node \
  --entry-port 18090 \
  --vps-host "$VPS_HOST"

sudo /opt/owrt-remote/owrt-remote-hub.py render-xray --out /etc/xray/owrt-remote.json
sudo systemctl restart owrt-remote-xray
```

Порты:

| Что | Пример | Для чего |
| --- | --- | --- |
| `entry-port` | `18080`, `18090`, `18100` | LuCI каждого роутера через VPS |
| `ssh-entry-port` | `19080`, `19090`, `19100` | SSH web-terminal, создается как `entry-port + 1000` |
| `vps-port` | `8443` | Xray VLESS reverse на VPS |
| `admin-port` | `80` | LuCI внутри OpenWrt |
| `ssh-port` | `22` | Dropbear/SSH внутри OpenWrt |

Наружу открывать надо только:

```text
80/tcp
443/tcp
8088/tcp
8443/tcp
```

Порты `18080`, `18090`, `19080`, `19090` должны слушать только `127.0.0.1` на VPS. Их наружу открывать не надо.

</details>

<details>
<summary><b>Установка агента на OpenWrt</b></summary>

На роутере:

```sh
wget -O - "https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote/main/install.sh?v=$(date +%s)" | sh
```

После установки:

```sh
owrt-remote doctor
owrt-remote status
```

Если Xray не помещается в память через `opkg`, можно поставить временный Xray в `/tmp` кнопкой `Поставить Xray в /tmp` в панели роутера. После ребута агент сам восстановит Xray в `/tmp`, если он пропал.

</details>

<details>
<summary><b>Проверка после установки</b></summary>

На VPS:

```sh
sudo systemctl status owrt-remote --no-pager -l
sudo systemctl status owrt-remote-xray --no-pager -l
sudo ss -lntp | grep -E ':(80|443|8088|8443|18080|18090|19080|19090)\b'
curl -sS http://127.0.0.1:8088/health
curl -k https://127.0.0.1/health
```

Должно быть:

- `owrt-remote` active/running;
- `owrt-remote-xray` active/running;
- `*:80`, `*:443`, `*:8088`, `*:8443`;
- `127.0.0.1:18080` для LuCI первого роутера;
- `127.0.0.1:19080` для SSH первого роутера.

На OpenWrt:

```sh
owrt-remote doctor
owrt-remote status
owrt-remote heartbeat
```

</details>

<details>
<summary><b>Частые проблемы</b></summary>

### Панель VPS не открывается

```sh
sudo systemctl status owrt-remote --no-pager -l
sudo journalctl -u owrt-remote -n 100 --no-pager
sudo ss -lntp | grep -E ':(80|443|8088)\b'
curl -sS http://127.0.0.1:8088/health
```

Если на VPS `curl` отвечает `{"ok":true}`, но снаружи сайт не открывается, проблема почти всегда в firewall VPS-провайдера.

Открой в личном кабинете VPS:

```text
80/tcp
443/tcp
8088/tcp
8443/tcp
```

### Админка пишет `proxy error: [Errno 111] Connection refused`

Пересобери Xray config:

```sh
sudo /opt/owrt-remote/owrt-remote-hub.py render-xray --out /etc/xray/owrt-remote.json
sudo systemctl restart owrt-remote-xray
sudo ss -lntp | grep -E ':(18080|18090|19080|19090)\b'
```

### SSH web-terminal просит пароль и молчит

Проверь Dropbear на OpenWrt:

```sh
/etc/init.d/dropbear status
owrt-remote heartbeat
```

В телефоне используй поле снизу терминала:

- `Вставить` - вставить текст;
- `Enter` - отправить Enter;
- `Отправить` - отправить команду или пароль.

### С мобильного интернета не открывается

Пробуй:

```text
https://YOUR_VPS_IP/
http://YOUR_VPS_IP/
http://YOUR_VPS_IP:8088/
```

Если по Wi-Fi работает, а через мобильный интернет нет, открой порты в firewall VPS-провайдера и проверь:

```sh
sudo ss -lntp | grep -E ':(80|443|8088)'
```

</details>

## Удаление

<details>
<summary><b>Удалить Hub с VPS полностью</b></summary>

```sh
curl -fsSL "https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote/main/vps/uninstall-vps.sh?v=$(date +%s)" | sudo sh
```

Удалится:

- `owrt-remote`;
- `owrt-remote-xray`;
- `/opt/owrt-remote`;
- `/etc/xray/owrt-remote.json`;
- `/var/lib/owrt-remote`;
- nginx-конфиг HTTPS, certbot renewal hook и старые TLS override-файлы;
- правила `ufw` для `80/tcp`, `443/tcp`, `8088/tcp`, `8443/tcp`.

Удалить Hub, но оставить базу роутеров:

```sh
curl -fsSL "https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote/main/vps/uninstall-vps.sh?v=$(date +%s)" | sudo env PURGE=0 sh
```

Удалить дополнительно сам Xray binary:

```sh
curl -fsSL "https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote/main/vps/uninstall-vps.sh?v=$(date +%s)" | sudo env REMOVE_XRAY=1 sh
```

</details>

<details>
<summary><b>Удалить агент с OpenWrt полностью</b></summary>

```sh
wget -O - "https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote/main/uninstall.sh?v=$(date +%s)" | PURGE=1 sh
```

Удалится:

- `/usr/sbin/owrt-remote`;
- `/etc/init.d/owrt-remote`;
- `/www/cgi-bin/owrt-remote`;
- пункт меню LuCI;
- rpcd ACL;
- `/etc/config/owrtremote`;
- `/etc/owrt-remote/web.key`;
- `/etc/xray/owrt-remote-client.json`.

Удалить только файлы панели, но оставить конфиг:

```sh
wget -O - "https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote/main/uninstall.sh?v=$(date +%s)" | sh
```

</details>

## Дерево проекта

<details>
<summary><b>Открыть дерево файлов</b></summary>

```text
.
├── install.sh
│   └── установка агента на OpenWrt
├── uninstall.sh
│   └── удаление агента с OpenWrt
├── files/
│   ├── usr/sbin/owrt-remote
│   │   └── CLI-агент, heartbeat, render-client, doctor
│   ├── etc/init.d/owrt-remote
│   │   └── procd-сервис OpenWrt
│   ├── www/cgi-bin/owrt-remote
│   │   └── веб-панель на роутере
│   ├── usr/share/luci/menu.d/luci-app-owrt-remote.json
│   │   └── пункт меню LuCI: Службы -> OpenWrt Remote
│   └── usr/share/rpcd/acl.d/luci-app-owrt-remote.json
│       └── права LuCI/rpcd
└── vps/
    ├── install-vps.sh
    │   └── установка Hub, Xray, firewall и HTTPS через nginx
    ├── enable-https.sh
    │   └── включение HTTPS/443 через nginx вручную
    ├── uninstall-vps.sh
    │   └── удаление Hub с VPS
    ├── owrt-remote-hub.py
    │   └── веб-панель VPS, карточки, proxy, SSH terminal
    └── owrt-remote.service
        └── systemd-сервис Hub
```

</details>
