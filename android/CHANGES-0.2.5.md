# Android 0.2.5-v124

Приложение для выпуска OWRT Hub v124. Удалены глобальные журналы роутеров:
кнопки, экран просмотра и опрос архива больше не используются. История команд
Xray остаётся доступна в «Настройки Xray». Обновлены подписи и номер сборки.

APK устанавливается поверх 0.2.4: пакет `ru.owrthub.app`, прежняя подпись и
формат сохранённой сессии сохраняются. Перед использованием обновите VPS до
v124 через «Обновление панели». Для смены VPS host через Heartbeat нужно также
обновить агент на соответствующем роутере.

Готовый APK и пакеты OpenWrt опубликованы в
[Releases → v124](https://github.com/kzolotarev95/luci-app-owrt-remote/releases/tag/v124).
APK Android и пакет APK OpenWrt 25.12 — разные форматы.

Подпись — прежний локальный debug-ключ; это тестовая сборка для ручной установки.
Ключ в GitHub не публикуется. Контрольные суммы находятся в
[android/apk/SHA256SUMS](apk/SHA256SUMS) и отдельном `ANDROID-SHA256SUMS` релиза.

При включённой USB-отладке можно установить обновление и откатить его:

```powershell
$Repo = Join-Path $env:USERPROFILE 'Documents\openwrt overlay'
$Adb = Join-Path $Repo '.test-output\android-build-tools\sdk\platform-tools\adb.exe'
& $Adb -d install -r (Join-Path $Repo 'android\apk\OWRT-Hub-Android-0.2.5-v124.apk')
# Откат тестовой APK с сохранением данных:
& $Adb -d install -r -d (Join-Path $Repo 'android\apk\OWRT-Hub-Android-0.2.4-v123.apk')
```

Сборка и проверки выполняются локальным Android SDK; отчёты Lint, подписи и
контрольных сумм сохраняются в игнорируемом каталоге `.test-output/release-v124`.
Производственные VPS и роутер при сборке APK не меняются.

Проверки выпуска: assembleDebug/assembleDebugAndroidTest/Lint, 0 ошибок Lint;
подпись совпадает с 0.2.4. Изолированный Android 15/API 35: 22 smoke-проверки
входа, сессии, поиска, карточек, Xray, заметок, тем и выхода. Проверки политики
URL — 176, API-клиента — 67; тестовый native backend — 10 HTTP-тестов.
