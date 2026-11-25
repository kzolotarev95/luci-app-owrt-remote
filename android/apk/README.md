# OWRT Hub для Android

[Скачать APK 0.2.5-v124](https://github.com/kzolotarev95/luci-app-owrt-remote/raw/refs/heads/main/android/apk/OWRT-Hub-Android-0.2.5-v124.apk) · [Releases v124](https://github.com/kzolotarev95/luci-app-owrt-remote/releases/tag/v124)

Android 8.0 и новее. Нативный клиент для своего VPS Hub. Это APK Android;
пакеты APK OpenWrt 25.12 устанавливаются на роутер и имеют другой формат.

Установите 0.2.5 поверх 0.2.4: имя пакета, подпись и сохранённая сессия
совместимы. В приложении нет заранее заданного сервера — укажите свой HTTPS-адрес
и войдите с учётной записью Hub. Для текущего выпуска обновите VPS до v124.

Удалены глобальные журналы роутеров; результаты команд доступны в «Настройки
Xray». Подробности и откат — в [CHANGES-0.2.5.md](../CHANGES-0.2.5.md).

APK — тестовая сборка с прежним локальным debug-ключом, не выпуск Google Play.
Ключ подписи в GitHub не публикуется. SHA256 обеих сохранённых версий — в
[SHA256SUMS](SHA256SUMS). Предыдущая
[0.2.4-v123](OWRT-Hub-Android-0.2.4-v123.apk) остаётся для отката.

```powershell
Get-FileHash .\OWRT-Hub-Android-0.2.5-v124.apk -Algorithm SHA256
```

Сборка, доступные функции и ограничения — в [README Android](../README.md).
