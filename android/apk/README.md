# OWRT Hub для Android

Текущая тестовая сборка: **OWRT-Hub-Android-0.3.12-v125.apk**.
Изменения и команды установки/отката серверной части: [CHANGES-0.3.12](../CHANGES-0.3.12.md).

[Скачать APK 0.3.12-v125](https://github.com/kzolotarev95/luci-app-owrt-remote/raw/refs/heads/main/android/apk/OWRT-Hub-Android-0.3.12-v125.apk) · [Releases v125](https://github.com/kzolotarev95/luci-app-owrt-remote/releases/tag/v125)

Android 8.0 и новее. Нативный клиент для своего VPS Hub. Это APK Android;
пакеты APK OpenWrt 25.12 устанавливаются на роутер и имеют другой формат.

Установите 0.3.12 поверх предыдущей версии: имя пакета, подпись и сохранённая сессия
совместимы. В приложении нет заранее заданного сервера — укажите свой HTTPS-адрес
и войдите с учётной записью Hub. Для текущего выпуска обновите VPS до v125.

Новый дашборд, карта и график доступности 6/12/24 ч, фотографии роутеров,
перестановка карточек, защита входа системным PIN/ключом/отпечатком и
сохранение пароля Wake-on-LAN. Исправлены Flash и заголовки внутренних
экранов. Полное описание — в [выпуске v125](../../docs/RELEASE-v125.md).

APK — тестовая сборка с прежним локальным debug-ключом, не выпуск Google Play.
Ключ подписи в GitHub не публикуется. SHA256 сохранённых версий — в
[SHA256SUMS](SHA256SUMS). Предыдущая
[0.2.4-v123](OWRT-Hub-Android-0.2.4-v123.apk) остаётся для отката.

```powershell
Get-FileHash .\OWRT-Hub-Android-0.3.12-v125.apk -Algorithm SHA256
```

Сборка, доступные функции и ограничения — в [README Android](../README.md).
