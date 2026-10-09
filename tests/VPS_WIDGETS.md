# Виджеты ресурсов VPS (v112)

Три карточки ЦП, ОЗУ и ДИСК находятся над кнопками «Рестарт Xray VPS»,
профилем и выходом. На ПК ширина и высота совпадают с кнопками под ними.
На мобильном карточки видны над кнопкой открытия меню хаба.

Панель запрашивает `/api/vps/resources` сразу и каждые 3 секунды.
ЦП — общая загрузка процессоров по разнице счётчиков `/proc/stat`.
ОЗУ — занятая память, рассчитанная как `MemTotal - MemAvailable`.
ДИСК — занятое место файловой системы `/`. В подсказках указаны число
vCPU и объёмы занятой/общей памяти и диска.

Доступ требует входа в Hub. Показания общие для всех вкладок и кешируются
на 3 секунды. Сбор не открывает SQLite и не запускает внешние команды.
Опрос приостанавливается в скрытой вкладке; после возвращения возобновляется.
При ошибке связи вместо устаревших чисел показывается «—».

## Установка и откат с Windows 11

В PowerShell, из любого каталога:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\k.zolotarev95\Documents\openwrt overlay\vps\deploy-vps-widgets.ps1" -Vps "root@193.233.82.38" -Action Install
```

После `VPS_WIDGETS_V112_Install_OK` обновите страницу через Ctrl+F5.
Скрипт загружает локальный файл, проверяет его до замены, перезапускает
службу и проверяет health. При ошибке после замены восстанавливает файл
перед текущей попыткой. База данных и настройки не заменяются.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\k.zolotarev95\Documents\openwrt overlay\vps\deploy-vps-widgets.ps1" -Vps "root@193.233.82.38" -Action Rollback
```

Откат использует неизменяемую копию первого состояния до установки:
`/opt/owrt-remote/owrt-remote-hub.py.bak-before-vps-widgets-v112`.
Успешное завершение: `VPS_WIDGETS_V112_Rollback_OK`.

## Локальные проверки

```powershell
python tests/test_vps_resources.py -v
python tests/test_vps_resources.py --export-preview .test-output/vps-widgets
node --check .test-output/vps-widgets/dashboard.js
node tests/test_vps_resources_ui.cjs .test-output/vps-widgets/dashboard.js
```

Проверяются вычисления, совместный кеш при одновременных запросах,
ошибки чтения, авторизация HTTP и отсутствие доступа к SQLite.
JS-проверки охватывают обновление обеих версий панели, отсутствие
параллельных запросов, таймауты, восстановление связи и скрытие вкладки.
