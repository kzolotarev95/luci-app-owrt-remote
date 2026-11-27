# Фотографии и общая палитра — локальная v125

Проверены 87 подготовленных фотографий в сравнении с исходниками. Для AX3000T
выбран полный ракурс с четырьмя поднятыми антеннами. Исправлены маски антенн
у Acer Predator Connect W6x, Benton SN-R1, Cudy WR3000P/WR3000U,
Keenetic Buddy 6 и Hopper. Увеличено изображение Creatlentem внутри мини-карточки.

Для исправленных масок в `vps/router-model-photos/catalog.json` сохранены
контрольные участки `quality_checks`. Браузерный тест декодирует итоговые WebP
и проверяет непрозрачность этих участков: вырезание антенны теперь вызывает
ошибку проверки. Это дополняет визуальное сравнение с оригиналом. При замене
ракурса контрольные участки нужно повторно проверить по новому исходнику.

Дополнительно исправлена маска Cudy WR3000U v1: у боковых антенн были обрезаны
крепления и нижние участки. Контур восстановлен по той же исходной фотографии,
антенны не дорисовывались и не деформировались. Проверены 15 участков четырёх
антенн, двух креплений и корпуса; непрозрачная маска образует один связный объект.
Новый SHA256 меняет URL изображения и обновляет браузерный кэш.

Отдельная установка текущего локального Hub с исправленным фото:

```powershell
$Fix = Join-Path $env:USERPROFILE 'Documents\openwrt overlay\vps\deploy-cudy-wr3000u-photo-fix.ps1'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Fix -Vps root@13.143.167.219 -Action Install
# Откат:
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Fix -Vps root@13.143.167.219 -Action Rollback
```

Резервная копия: `/root/owrt-remote-backup-before-cudy-wr3000u-photo-v125`.

`vps/hub-palette.css` задаёт общие серые фоны, текст, границы и акцентные цвета.
Он встроен в общий head страниц Hub и обе страницы CGI роутера. Авторизация,
обновление роутеров, заметки, Xray, поддержка и SSH/VPS-терминал используют
палитру дашборда. Xterm получает фон и цвета текста из тех же CSS-переменных.
Зимний и облегчённый режимы продолжают использовать свои палитры.

Золотистый цвет ссылки поддержки закреплён отдельным правилом, включая наведение
и клавиатурный фокус: обычное правило серых пунктов меню его не перекрывает.

## Проверки

- `python scripts/embed-router-model-photos.py --check`
- `python scripts/embed-dashboard-design.py --check`
- `python -m unittest tests.test_hub_design_global tests.test_router_model_photos`
- `node tests/test_router_model_photos.cjs` — 320, 390, 1366, 2560 px и маски антенн.
- `node tests/test_shared_palette.cjs` — девять страниц, 390 и 1366 px.
- `node tests/test_hub_design_ui.cjs` — переключение дизайна, сохранение, lite, мобильный вид.
- `node tests/test_router_sidebar_ui.cjs` — реальный CGI и формы на изолированных данных.

Node-проверки используют Playwright из локального `NODE_PATH`, как остальные
браузерные проверки проекта. SSH-доступ к устройствам для этих тестов не нужен.

## Установка и откат

PowerShell Windows 11:

```powershell
$Fix = Join-Path $env:USERPROFILE 'Documents\openwrt overlay\vps\deploy-shared-palette-fix.ps1'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Fix -Vps root@13.143.167.219 -Action Install
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Fix -Router root@192.168.2.1 -Action Install
```

Откат соответствующего устройства:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Fix -Vps root@13.143.167.219 -Action Rollback
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Fix -Router root@192.168.2.1 -Action Rollback
```

На каждом устройстве создаётся отдельная резервная копия:
`/root/owrt-remote-backup-before-shared-palette-v125`.
VPS получает встроенные фото вместе с Hub; отдельно копировать изображения
не требуется. Роутер получает интерфейс CGI, LuCI и существующий файл эффектов.
После установки обновите вкладки через Ctrl+F5. Версия остаётся v125.
