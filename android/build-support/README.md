# Локальная сборка Android в Windows 11

Из корня репозитория в PowerShell:

```powershell
./android/build-support/build.ps1 -InstallTools
```

Первая команда загружает инструменты, проверяет контрольные суммы, принимает
лицензии Android SDK в локальном SDK и запускает `assembleDebug` и `lintDebug`.
Нужны Windows, Python 3.11 или новее, `curl.exe` и доступ к официальным серверам
Google, Gradle и Adoptium. Android Studio не требуется.

Повторная сборка:

```powershell
./android/build-support/build.ps1
```

Выбор отдельных задач:

```powershell
./android/build-support/build.ps1 -Tasks @('assembleDebug')
```

Инструменты, лицензии, журналы и кеш Gradle находятся только в игнорируемом
каталоге `.test-output/android-build-tools/`. Скрипт не меняет системный PATH,
реестр или настройки других Android-проектов. `tools-manifest.json` в этом
каталоге содержит версии, локальные пути и контрольные суммы архивов.

Зафиксированный набор: Temurin JDK 17.0.21.1+2, Gradle 8.11.1, Android SDK 35
revision 2, Build Tools 35.0.0, Command-line Tools 19.0 и Platform Tools 37.0.3.
Он рассчитан на AGP 8.9.2, `compileSdk = 35`, `minSdk = 26` и Java 17.
Совместимость AGP 8.9 с Gradle 8.11.1, JDK 17 и SDK 35 подтверждена в
[документации Android](https://developer.android.com/build/releases/past-releases/agp-8-9-0-release-notes).

SHA256 JDK сверена с [метаданными Adoptium](https://api.adoptium.net/v3/assets/latest/17/hotspot?architecture=x64&image_type=jdk&os=windows&vendor=eclipse),
SHA256 Gradle — с [официальной контрольной суммой](https://services.gradle.org/distributions/gradle-8.11.1-bin.zip.sha256).
Для архивов SDK проверяются SHA1 из
[репозитория Google](https://dl.google.com/android/repository/repository2-1.xml)
и дополнительно закреплённые SHA256. Ссылки и суммы закреплены в
`install-toolchain.py`; команда не выбирает новые версии автоматически.

Для просмотра устройств после включения USB-отладки:

```powershell
& './.test-output/android-build-tools/sdk/platform-tools/adb.exe' devices
```

Установка APK выполняется отдельно после подключения устройства; сборка
сама не подключается к роутеру, VPS или Android-устройству.

## Фотографии моделей в Android

Копии уже проверенных фотографий и минимальный каталог находятся в
`android/app/src/main/assets/router-models`. Они не загружаются с сети в приложении.

```powershell
# Только проверка каталога и SHA256 относительно источников Hub:
python .\android\build-support\sync-router-photos.py --check
# После изменения проверенного каталога Hub синхронизировать Android:
python .\android\build-support\sync-router-photos.py
```

Скрипт не меняет источники Hub и не скачивает новые изображения.
