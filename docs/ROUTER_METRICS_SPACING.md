# Компактные показатели в карточках VPS-панели

Xray, ОЗУ и температура собраны по центру карточки с промежутками 16 px.
Колонки занимают ширину содержимого, вместо растягивания на всю карточку.
Промежуток между подписью и значением уменьшен с 4 до 3 px. Размеры шрифта,
цвета и данные показателей сохранены. Проверены ширины 320, 390, 1366 и 2560 px.

Установка локального Hub v125 с накопленными изменениями, PowerShell Windows 11:

```powershell
$Fix = Join-Path $env:USERPROFILE 'Documents\openwrt overlay\vps\deploy-router-metrics-spacing.ps1'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Fix -Vps root@13.143.167.219 -Action Install
# Откат:
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Fix -Vps root@13.143.167.219 -Action Rollback
```

Резервная копия: `/root/owrt-remote-backup-before-router-metrics-spacing-v125`.
После установки обновите страницу через Ctrl+F5. На роутере ничего менять не нужно.
