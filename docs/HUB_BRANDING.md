# OWRT Hub v123

Во всех текущих интерфейсах используется название «OWRT Hub»: панели VPS
и роутера, экран входа, заголовки вкладок, уведомления и приложение PWA.
Заголовок с версией — «OWRT Hub v123». Пункт меню OpenWrt в «Службах» —
«OWRT Hub». Пакет и адрес GitHub остаются `luci-app-owrt-remote`, пути и
имена служб остаются прежними.

## Локальная установка

PowerShell Windows 11:

```powershell
$Fix = Join-Path $env:USERPROFILE 'Documents\openwrt overlay\vps\deploy-hub-branding.ps1'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Fix -Vps root@132.243.19.232 -Action Install
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Fix -Router root@192.168.2.1 -Action Install
```

На VPS используется существующий механизм установки v123 с атомарной заменой,
перезапуском Hub и проверкой SHA256 работающего процесса. Первая резервная копия
сохраняется в `/opt/owrt-remote/owrt-remote-hub.py.bak-before-v123`.

На роутере устанавливаются CGI, представление LuCI, JavaScript эффектов и файл
меню. Новый установщик сохраняет полный исходный комплект в отдельной копии
`/root/owrt-remote-backup-before-router-branding-v123`, включая старое меню.
После замены очищается кэш LuCI и перезапускается rpcd. Remote и Xray не
перезапускаются, конфигурация и ключи не заменяются. Повторная установка не
перезаписывает первую копию. Ошибка после замены возвращает файлы, которые
были установлены непосредственно перед попыткой.

После установки обновите обе панели и страницу «Службы» через Ctrl+F5.

## Откат

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Fix -Vps root@132.243.19.232 -Action Rollback
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Fix -Router root@192.168.2.1 -Action Rollback
```

Откат VPS возвращает первую сохранённую сборку v123. Откат роутера возвращает
комплект из копии `router-branding-v123`, в том числе прежнее название меню.
Добавьте `-PrepareOnly` к команде для подготовки без подключения к устройству.

Версия интерфейсов — v123, версия пакета — 123-r1. Тег `v123` запускает
сборку IPK/APK, проверку в OpenWrt VM и публикацию в GitHub Releases.
