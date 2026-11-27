# Дашборд OWRT Hub в стиле Vantage

Локальное оформление первого варианта: боковое меню, графитовые поверхности,
компактные показатели, карта подключений, история доступности, ресурсы VPS
и существующие карточки роутеров. Локальная версия интерфейса — v125.

Карта отражает **связь по Heartbeat**, а статус Xray показывается отдельно.
Наличие Heartbeat не доказывает исправность SSH, Xray-туннеля или веб-интерфейса
роутера. Числа и состояния должны поступать из существующих API Hub; макетные
значения не используются в рабочем дашборде.

Поиск, фильтры, группы, заметки и действия роутеров сохраняют существующие
обработчики и права. Карта использует тот же разрешённый и отфильтрованный
список устройств, что и карточки. Отдельный сетевой цикл для карты не нужен.

На карте по умолчанию показаны первые **7 роутеров** в том же порядке,
что и в текущем списке карточек. Поиск и фильтры статуса/группы применяются
к обоим представлениям. Если найдено больше семи, внизу доступны счётчик
«Показано 7 из N» и кнопка «Показать все». Она раскрывает все ветви схемы;
«Скрыть все» возвращает первые семь. При обновлении статусов раскрытие
сохраняется. При списке из семи и менее устройств лишней кнопки нет.

Пояснение о проверке SSH и суммарный счётчик Xray под картой удалены.
Статусы Xray в карточках и верхняя статистика продолжают работать.

Для установки именно этого локального исправления с отдельной копией:

```powershell
$Fix = Join-Path $env:USERPROFILE 'Documents\openwrt overlay\vps\deploy-network-map-list-fix.ps1'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Fix -Vps root@13.143.167.219 -Action Install
```

Откат: та же команда с `-Action Rollback`. Резервная копия:
`/root/owrt-remote-backup-before-network-map-list-v125`.

## Установка на VPS

В PowerShell Windows 11 из локального проекта:

```powershell
$Fix = Join-Path $env:USERPROFILE 'Documents\openwrt overlay\vps\deploy-version-v125.ps1'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Fix -Vps root@13.143.167.219 -Action Install
```

Скрипт использует существующую атомарную установку Hub и мобильного API:
проверяет Python, сохраняет резервную копию, заменяет файлы, перезапускает Hub
и проверяет фактически запущенную ревизию. Данные и конфигурация роутеров
не заменяются, Xray на VPS не перезапускается.

Не устанавливайте оформление во время активной очереди обновления или сеанса
терминала: перезапуск Hub прервёт соединение и наблюдение за очередью. Уже
запущенные на роутерах команды при этом продолжаются.

Подготовка без подключения к VPS:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Fix -Vps root@13.143.167.219 -Action Install -PrepareOnly
```

## Откат

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Fix -Vps root@13.143.167.219 -Action Rollback
```

Резервная копия: `/root/owrt-remote-backup-before-hub-v125`.
Откат возвращает Hub и mobile_api.py из этой копии; база Hub сохраняется.

## Проверки

Существующие сценарии дашборда и карточек:

```powershell
$env:NODE_PATH = Join-Path (Get-Location) '.test-output\tachyon-browser\node_modules'
node tests/test_hub_sidebar_ui.cjs
node tests/test_hub_sidebar_samples.cjs
node tests/test_router_card_details.cjs
node tests/test_dashboard_network.cjs
node tests/test_router_updates_ui.cjs
node tests/test_xray_control_ui.cjs
```

Проверки выполняются на изолированных данных. Успех локального теста
не подтверждает установку оформления на реальном VPS.

## Точки интеграции для разработки

- `vps/dashboard-vantage.css`, `vps/dashboard-vantage.js`: оформление,
  приветствие по фактическим состояниям, компактные показатели карточек
  и открытие существующих данных роутера из карты.
- `vps/dashboard-network.css`, `vps/dashboard-network.js`: схема по разрешённому
  списку роутеров, ограниченное число веток, оставшиеся устройства в раскрываемом
  списке, пауза анимации и поддержка слабых устройств.
- `hub_sidebar_html()`, `hub_sidebar_top_html()`, `hub_sidebar_overview_html()`:
  меню, шапка, обзор и контейнеры фильтров.
- `hub_vantage_header_html()`: приветствие, статистика, быстрые ЦП/ОЗУ VPS
  и контейнер карты; ресурсы обновляет существующий опрос.
- `hub_sidebar_css()`: общий слой `.hubDashboard`; цвета следуют переменным
  выбранного `html[data-hub-design]`.
- `renderRouterView()`: после `visibleRouters = filteredRouters(allRouters)`
  обновляется карта, в том числе при пустом результате, до раннего return.
- Карточка выбирается по `article[data-router-id]`; данные открываются через
  `setCardDetailsExpanded(card.querySelector('.cardBody'), true)`.
- `loadRouters({automatic:true})` уже предотвращает параллельный опрос,
  обновление скрытой страницы и сброс интерактивного состояния Wake-on-LAN.

Скрипт установки загружает только `hub.py` и `mobile_api.py`. JS/CSS-файлы
встроены в исходник Hub: обычное обновление панели не зависит от наличия
соседних файлов на VPS. После изменения этих исходников обновите встроенные
копии и проверьте их соответствие:

```powershell
python scripts/embed-dashboard-design.py
python scripts/embed-dashboard-design.py --check
python -m py_compile vps/owrt-remote-hub.py
```

Дашборд не создаёт отдельного опроса состояния или журнала. При обновлении
данных карта сохраняет существующие элементы, если список, имена и статусы
не изменились. Анимация выключается в скрытой вкладке, в облегчённом дизайне
и при системной настройке уменьшения движения; ручная пауза запоминается
в текущем браузере.
