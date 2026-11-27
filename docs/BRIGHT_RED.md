# Единый ярко-красный цвет

Рабочая палитра VPS, iOS, панели роутера и Android переведена на `#ff3030`
(RGB 255,48,48). Заменён общий RED и отдельные прежние малиновые/розовые
значения в ошибках, статусах, графиках, границах, свечении, кнопках,
плашках версии и градиенте имён. У RGBA и восьмизначных HEX сохранена альфа.
Рабочие цвета больше не зависят от того, является ли элемент статусом Offline.

Веб: `vps/hub-palette.css`, отдельные шаблоны Hub, CGI роутера и исходник
`dashboard-vantage.css`. Встроенные копии пересобраны скриптом
`embed-dashboard-design.py`, SHA256 роутерного манифеста обновлены.
Общий `--red` объявлен и вне темы, чтобы вспомогательные страницы и все
три оформления получали одинаковое значение.

Android: `NativeUi.BRIGHT_RED`, ссылка на неё из `RouterNameView` и ошибки
`BrowserActivity`. APK — `artifacts/android/OWRT-Hub-Android-0.3.11-v125.apk`.

## Установка локального исправления

PowerShell Windows 11, VPS (ПК и iOS):

```powershell
$Fix = Join-Path $env:USERPROFILE 'Documents\openwrt overlay\vps\deploy-bright-red.ps1'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Fix -Vps root@13.143.167.219 -Action Install
```

Роутер:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Fix -Router root@192.168.2.1 -Action Install
```

Резервная копия на каждой машине:
`/root/owrt-remote-backup-before-bright-red-v125`.
Откат соответствующей машины:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Fix -Vps root@13.143.167.219 -Action Rollback
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Fix -Router root@192.168.2.1 -Action Rollback
```

VPS-ветка проверяет Hub/мобильный модуль и работающую ревизию.
Роутерная ветка сохраняет UI и модули обновления, затем заменяет их атомарно,
без перезапуска Xray. Настройки, ключи и Xray не изменяются.
После установки обновить страницу; Android установить новой APK поверх
предыдущей локальной сборки. Работа локальная, GitHub не обновлялся.

## Проверки

`tests/test_shared_palette.cjs` проверяет 9 страниц Hub в Chromium/WebKit,
включая общий красный в default/winter/lite. `test_router_sidebar_ui.cjs`
проверяет реальные CGI, мобильное меню, оформления и прокси-пути.
`test_router_name_gradient.cjs` проверяет новый конец градиента в обоих
движках и сохранение оформления после переименования/обновления статусов.

Android: `NativeRedesignInstrumentation -e brightRedOnly true` проверяет
палитры, кнопку опасного действия, Offline и отрисовку градиента на эмуляторе.
Используются только отдельные локальные фикстуры; серверы пользователя
и физический телефон автоматически не меняются.

Результат локальной проверки: 9 страниц в Chromium (390/1366 px) и WebKit
(390 px), три оформления, 4 сценария градиента и 7 сценариев панели роутера
прошли. Android assembleDebug/assembleDebugAndroidTest и lintDebug выполнены:
0 ошибок lint, 39 предупреждений. Проверка палитры и реальных Offline-текстов
на эмуляторе завершилась `status=passed`. Подпись APK совпадает с предыдущими
локальными сборками. SHA256 находится рядом с APK в файле `.apk.sha256`.
