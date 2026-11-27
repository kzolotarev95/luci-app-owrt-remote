# Выравнивание панели и Android 0.3.6 — локальная v125

На iOS заголовок «Роутеры» выровнен по центру аналогично карте, доступности
и диску. Фильтры находятся ниже заголовка. На ПК и iOS значения диска
больше не раздвигаются к краям блока: три пары подписей и значений
собраны в компактную группу рядом с индикатором.

Исходный стиль — `vps/dashboard-vantage.css`. Его актуальная копия встроена
в `vps/owrt-remote-hub.py` через `scripts/embed-dashboard-design.py`.
Android: `NativeUi.java`, `MainActivity.java`, `NativeNetworkMap.java`,
`NativeRouterActivity.java`, `NativeSettingsActivity.java`. Все изменения APK
описаны в [CHANGES-0.3.6.md](../android/CHANGES-0.3.6.md).

## Локальная установка и откат VPS

PowerShell Windows 11:

```powershell
$Fix = Join-Path $env:USERPROFILE 'Documents\openwrt overlay\vps\deploy-dashboard-mobile-alignment.ps1'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Fix -Vps root@13.143.167.219 -Action Install
```

Скрипт загружает текущую локальную серверную сборку, проверяет Python,
сохраняет резервную копию Hub и мобильного API, перезапускает Hub и
подтверждает ревизию и API. При неудачной проверке восстанавливает файлы.
Резервная копия:
`/root/owrt-remote-backup-before-dashboard-mobile-alignment-v125`.
База и настройки подключения не заменяются.

Откат из той же резервной копии:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Fix -Vps root@13.143.167.219 -Action Rollback
```

Изменения подготовлены локально. Установку на VPS и реальных телефонах
пользователь выполняет отдельно; тесты используют изолированные данные.

## Проверки

- Android 15, обычный экран и узкий экран 320 dp со шрифтом 130%:
  по 23 сценария в `NativeRedesignInstrumentation`. Проверены геометрия
  заголовков, отсутствие наложения кнопок и дробления слов, высота статистики,
  компактность диска, порядок логотипа и приветствия, один ряд банков,
  терминал VPS, сохранение настроек, обновление данных и анимация карты.
- `assembleDebug`, `assembleDebugAndroidTest`, `lintDebug` — успешно;
  ошибок lint нет. SHA256 APK совпадает с файлом `.sha256`, подпись совпадает
  с предыдущей локальной 0.3.5.
- `tests/test_ios_dashboard.cjs`: Chrome и WebKit на ширинах
  320, 390, 430 и 844 px — восемь проверок; заголовок «Роутеры» по центру,
  компактные пары значений диска, сохранение фотографий и анимации.
- `tests/test_vantage_dashboard.cjs`: 24 сочетания ширины 320–2560 px
  и количества роутеров — успешно. Размеры диска и доступности на ПК
  сохраняют равенство, значения диска находятся рядом с подписями.
- Проверены встроенные ресурсы дашборда, SHA256 мобильного API и обслуживания
  журналов, компиляция Python и подготовка PowerShell-установщика без загрузки
  на сервер (`-PrepareOnly`).

Проверки iOS выполнялись с iOS-профилем в браузерных движках; на физическом
iPhone и физическом Android новую сборку ещё нужно проверить.
