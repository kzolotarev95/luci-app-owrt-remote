# Локальная 0.3.4-v125

- Независимое скрытие карты подключений, графика доступности и диска VPS.
  Выбор сохраняется при обновлении данных и перезапуске приложения.
- Иконки сети и активности рядом с заголовками карты и доступности.
- Нативные POST-запросы не отправляют выдуманный браузерный Origin, из-за
  которого вход мог завершаться ошибкой за reverse proxy. Проверки TLS,
  перенаправлений, сессии и CSRF сохранены.

APK: `artifacts/android/OWRT-Hub-Android-0.3.4-v125.apk`, versionCode 14.
Изменения серверной части и установка/откат:
[DASHBOARD_PANELS_MOBILE_LOGIN.md](../docs/DASHBOARD_PANELS_MOBILE_LOGIN.md).

Установка и откат через USB-отладку в PowerShell Windows 11:

```powershell
$Repo = Join-Path $env:USERPROFILE 'Documents\openwrt overlay'
$Adb = Join-Path $Repo '.test-output\android-build-tools\sdk\platform-tools\adb.exe'
& $Adb -d install -r (Join-Path $Repo 'artifacts\android\OWRT-Hub-Android-0.3.4-v125.apk')
# Откат на сохранённую предыдущую APK с тем же ключом:
& $Adb -d install -r -d (Join-Path $Repo 'artifacts\android\OWRT-Hub-Android-0.3.3-v125.apk')
```

Проверено: 18 сценариев в эмуляторе Android 15, включая узкий экран со шрифтом
130%, вход без Origin, сохранение свёрнутых блоков и отсутствие кадров анимации
скрытой карты. Сборка и lint без ошибок; подпись совпадает с предыдущей APK.
Проверка на реальном телефоне ещё предстоит.

SHA256 APK: `624e03423f0dd05071785b0c1701a6c99b5da917d48a20d4c47bbe314dab32d2`.
