# Версия v123

В панели VPS на ПК и телефоне используется название **OWRT Hub v123**.
Версия синхронизирована на экране входа, в CGI и LuCI роутера, а также
в рецепте пакета: `PKG_VERSION=123`, `PKG_RELEASE=1`.

Версия выпускается тегом `v123`: GitHub Actions собирает IPK/APK и проверяет
установку, обновление и удаление пакетов в OpenWrt VM. Опубликованные v122
и v122-r2 сохраняются без изменения.

## Проверка на VPS и роутере

PowerShell Windows 11:

```powershell
$Fix = Join-Path $env:USERPROFILE 'Documents\openwrt overlay\vps\deploy-version-v123.ps1'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Fix -Vps root@132.243.19.232 -Action Install
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Fix -Router root@192.168.2.1 -Action Install
```

После установки обновить страницу через Ctrl+F5. Установка VPS сохраняет
первую резервную копию, заменяет исходник атомарно, перезапускает только Hub
и проверяет SHA256 загруженного процесса через `/health?panel-update=1`.
На роутере заменяются только CGI и представление LuCI, настройки и Xray
не изменяются. При сбое восстанавливаются файлы до текущей попытки установки.
Установка на VPS и установка на роутер выполняются двумя отдельными командами:
изменение версии только на VPS не обновляет файлы интерфейса роутера.

Копии:

- VPS: `/opt/owrt-remote/owrt-remote-hub.py.bak-before-v123`.
- Роутер: `/root/owrt-remote-backup-before-v123`.

Для отката повторить соответствующую команду с `-Action Rollback`.
Для подготовки без подключения к устройству добавить `-PrepareOnly`.
