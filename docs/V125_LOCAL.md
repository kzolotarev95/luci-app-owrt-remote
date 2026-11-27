# OWRT Hub v125 — локальная проверка

Верхние показатели VPS уменьшены. В SSH-терминале роутера рядом с
копированием, очисткой и переподключением доступны три быстрые команды:

- OpenWrt 25 и новее: `apk update`, `apk upgrade`, `apk info`.
- OpenWrt 23/24: `opkg update`, `opkg list-upgradable`, `opkg list-installed`.
- При неизвестной версии — три пустых слота для своих команд.

Если агент сообщил менеджер пакетов, используется он. Для старых агентов
набор определяется по версии OpenWrt из Heartbeat. «Настроить команды»
сохраняет три строки отдельно для каждого роутера и менеджера пакетов в
текущем браузере. Кнопка вставляет текст в SSH без Enter; пользователь
сам запускает его. В терминале VPS этих кнопок нет.

Панель роутера получила графитовые блоки, лаймовые акценты и схему
подключения к VPS/Hub. На схеме показаны адреса из настроек, интервал
Heartbeat и состояние процесса Xray. Это не проверка доступности VPS.
Зимний и облегчённый дизайн сохранены; облегчённый режим и настройка
уменьшения движения отключают анимацию.

Версии панели VPS, CGI роутера, LuCI и `PKG_VERSION` синхронизированы на 125,
`PKG_RELEASE=1`. Итоговая версия Android — `0.3.12-v125`.
Полный состав выпуска после завершения локальной проверки описан в
[RELEASE-v125.md](RELEASE-v125.md).

## Установка для проверки

PowerShell Windows 11:

```powershell
$Fix = Join-Path $env:USERPROFILE 'Documents\openwrt overlay\vps\deploy-version-v125.ps1'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Fix -Vps root@13.143.167.219 -Action Install
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Fix -Router root@192.168.2.1 -Action Install
```

Откат выполняется отдельно на каждом устройстве:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Fix -Vps root@13.143.167.219 -Action Rollback
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Fix -Router root@192.168.2.1 -Action Rollback
```

Резервная копия на каждом устройстве:
`/root/owrt-remote-backup-before-hub-v125`.
Установка VPS перезапускает Hub. На роутере заменяются только файлы
интерфейса; его UCI, Xray и службы не меняются.

После установки обновите страницы, при необходимости через Ctrl+F5.
Локальные браузерные проверки используют изолированные данные и
не выполняют команды на реальных устройствах.
