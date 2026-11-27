# Локальная 0.3.3-v125

- Общая молния в круге на салатовом фоне: иконка приложения, адаптивный Android
  launcher, шапка, меню и узел Hub в карте подключений.
- Монохромная версия того же знака для уведомлений.
- Синхронизированы проверенные фотографии и алиасы моделей с локальным Hub.

APK: `artifacts/android/OWRT-Hub-Android-0.3.3-v125.apk`, versionCode 13.
Подписана прежним локальным ключом; устанавливается поверх 0.3.2 без удаления.
Версия Hub остаётся v125. Изменения VPS/роутера и команды установки/отката:
[PHOTO_BRANDING_FIX.md](../docs/PHOTO_BRANDING_FIX.md).

Установка и откат через USB-отладку в PowerShell Windows 11:

```powershell
$Repo = Join-Path $env:USERPROFILE 'Documents\openwrt overlay'
$Adb = Join-Path $Repo '.test-output\android-build-tools\sdk\platform-tools\adb.exe'
& $Adb -d install -r (Join-Path $Repo 'artifacts\android\OWRT-Hub-Android-0.3.3-v125.apk')
# Откат на сохранённую предыдущую APK с тем же ключом:
& $Adb -d install -r -d (Join-Path $Repo 'artifacts\android\OWRT-Hub-Android-0.3.2-v125.apk')
```

Проверки: `assembleDebug`, `assembleDebugAndroidTest`, `lintDebug` и 17 сценариев
в эмуляторе Android 15 прошли. Скриншоты шапки, входа, меню и карты проверены.
SHA256 APK: `69537881eae7141ae18d45b69b5e7c0f115efc3d5c747680a7075b94bc560f50`.
