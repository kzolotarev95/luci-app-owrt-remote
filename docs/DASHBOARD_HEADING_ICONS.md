# Значки заголовков VPS-панели

К заголовкам добавлены векторные значки: роутер у «Роутеры», линия активности у
«Доступность роутеров» и схема сети у «Карта подключений». Размер 23 × 23 px,
золотистый цвет темы и толщина линий соответствуют значку «Диск VPS».
Значки декоративные (`aria-hidden`), внешние файлы и запросы не нужны.

Проверены локальные браузерные сценарии карты и карточек на ширинах
320, 390, 844, 1280, 1366, 1920 и 2560 px; снимок ПК-панели проверен визуально.

Установка текущего локального Hub v125 с накопленными изменениями:

```powershell
$Fix = Join-Path $env:USERPROFILE 'Documents\openwrt overlay\vps\deploy-dashboard-heading-icons.ps1'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Fix -Vps root@13.143.167.219 -Action Install
# Откат:
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Fix -Vps root@13.143.167.219 -Action Rollback
```

Резервная копия: `/root/owrt-remote-backup-before-dashboard-heading-icons-v125`.
После установки обновите страницу через Ctrl+F5.
