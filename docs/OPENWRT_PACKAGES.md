# Пакеты OpenWrt Remote

Исходники пакета `luci-app-owrt-remote` находятся в
`package/luci-app-owrt-remote/`. Пакет содержит агент, CGI-панель, LuCI,
Heartbeat, управление Xray, WAN direct и журнал. Файлы берутся непосредственно
из `files/`, которую использует `install.sh`; отдельная копия агента не ведётся.

Это пакеты проекта для OpenWrt, а не включение проекта в официальный feed OpenWrt.
Публикация в Releases произойдёт только после отправки workflow и выпуска тега.

## Какой файл выбрать

| OpenWrt | Файл | Архитектура пакета |
| --- | --- | --- |
| 23.05.x | `luci-app-owrt-remote_121-r1_openwrt-23.05_all.ipk` | `all` |
| 24.10.x | `luci-app-owrt-remote_121-r1_openwrt-24.10_all.ipk` | `all` |
| 25.12.x | `luci-app-owrt-remote_121-r1_openwrt-25.12_noarch.apk` | `noarch` |

В пакете нет ELF-бинарников. Один файл своей ветки подходит `aarch64_cortex-a53`,
`aarch64_cortex-a72`, `aarch64_generic`, ARM, MIPS, MIPSEL, MIPS64, x86, x86_64
и другим архитектурам с доступными зависимостями. Формат определяется менеджером
пакетов: `opkg` использует IPK, `apk` использует APK; они не взаимозаменяемы.

Xray загружается отдельно существующим агентом при применении конфигурации Hub.
Его версия, ABI, SHA256 и загрузочный кэш проверяются существующим механизмом.
Установка пакета не запускает вложенный `opkg`/`apk` и не загружает Xray,
пока менеджер пакетов удерживает свою блокировку.

## Установка и обновление через LuCI

1. Скачай пакет своей ветки из [GitHub Releases](https://github.com/kzolotarev95/luci-app-owrt-remote/releases).
2. Открой **Система → Программное обеспечение**, обнови списки пакетов.
3. Нажми **Загрузить пакет**, выбери файл и подтверди установку.
4. Открой **Службы → OpenWrt Remote** и примени OpenWrt config из своей VPS-панели.

Обновление выполняется загрузкой пакета новой версии тем же способом.
Перезагрузка роутера не требуется. Зависимости устанавливает менеджер пакетов:
`luci-base`, `rpcd-mod-file`, `uhttpd`, `jsonfilter`, `curl`, `ca-bundle`, `etherwake`.
Для их первичной загрузки нужны доступные репозитории своей прошивки.

На OpenWrt 25.12 штатная LuCI принимает сторонний APK только с доверенной
подписью. Для подписанных выпусков один раз установи опубликованный публичный
ключ проекта в `/etc/apk/keys/owrt-remote-packages.pem`, проверив его отпечаток
по доверенному каналу. Далее подписанные APK устанавливаются через LuCI.
Без ключа штатная LuCI отклоняет пакет; для локального неподписанного APK
используй SSH-команду ниже. Глобально отключать проверку подписей не нужно.

## Установка по SSH из PowerShell Windows 11

Пример для OpenWrt 24.10 и основного роутера `192.168.2.1`:

```powershell
$Repo = "C:\Users\k.zolotarev95\Documents\openwrt overlay"
$Package = Join-Path $Repo "artifacts\openwrt-packages\24.10\luci-app-owrt-remote_121-r1_openwrt-24.10_all.ipk"
ssh root@192.168.2.1 'sysupgrade -b /root/owrt-before-package.tar.gz'
if ($LASTEXITCODE -ne 0) { throw 'Резервная копия не создана' }
scp -O -- "$Package" 'root@192.168.2.1:/tmp/owrt-remote.ipk'
if ($LASTEXITCODE -ne 0) { throw 'Не удалось загрузить пакет' }
ssh root@192.168.2.1 'opkg update && opkg install /tmp/owrt-remote.ipk'
if ($LASTEXITCODE -ne 0) { throw 'Установка не завершена; смотри ошибку выше' }
```

На OpenWrt 23.05 выбери файл из каталога `23.05`. Для OpenWrt 25.12 загрузи
файл из `25.12` как `/tmp/owrt-remote.apk` и выполни:

```powershell
ssh root@192.168.2.1 'apk update && apk add --allow-untrusted /tmp/owrt-remote.apk'
```

`--allow-untrusted` нужен только неподписанному локальному APK. Для подписанного
APK с установленным доверенным ключом используется `apk add` без этого флага.
После установки проверь `owrt-remote status` и `owrt-remote doctor`.
На чистой установке Remote выключен до ввода своего Hub config — это штатно.

## Сохранение настроек и откат

`/etc/config/owrtremote` объявлен conffile. Обновление сохраняет изменённые UCI,
ключ `/etc/owrt-remote/web.key`, Xray, JSON, постоянный кэш и журнал. Ключ панели
создаётся только при его отсутствии, с правами `0600`, через атомарную замену.

Удаление пакета останавливает и отключает его procd-службы. Пакет не очищает
сгенерированные данные пользователя и не запускает `uninstall.sh`:

```powershell
# Для IPK:
ssh root@192.168.2.1 'opkg remove luci-app-owrt-remote'
# Для APK:
ssh root@192.168.2.1 'apk del luci-app-owrt-remote'
```

Для отката к прежнему пакетному выпуску сохрани его файл до обновления,
удали новый пакет и установи сохранённый IPK/APK. Изменённый UCI при удалении
сохраняется. Чтобы вернуть настройки из сделанной выше резервной копии,
извлеки только конфигурацию Remote, затем перезапусти службу:

```powershell
ssh root@192.168.2.1 'tar -xzf /root/owrt-before-package.tar.gz -C / etc/config/owrtremote && /etc/init.d/owrt-remote restart'
```

`sysupgrade -b` сохраняет настройки, а не старые программные файлы.
При переходе с `install.sh` сохрани исходный checkout/предыдущий установщик:
удаление пакета само по себе не восстанавливает старую ручную установку.

## Сборка

Локальный IPK без SDK можно собрать и проверить на Windows с Python 3.11+:

```powershell
Set-Location "C:\Users\k.zolotarev95\Documents\openwrt overlay"
python scripts/openwrt_package.py local-ipk --output artifacts/remote-local.ipk
python scripts/openwrt_package.py verify artifacts/remote-local.ipk
```

Локальный APK создаётся настоящим `apk-tools v3 mkpkg` из host tools SDK 25.12.
Целевой `apk` на роутере может не содержать команду `mkpkg`. На Linux:

```sh
python3 scripts/openwrt_package.py local-apk --apk /path/to/sdk/staging_dir/host/bin/apk --output artifacts/remote-local.apk
```

Релизная сборка выполняется официальным SDK на Linux x86_64. Скрипт находит
имя SDK в индексе OpenWrt, проверяет SHA256, использует feeds этого SDK,
собирает пакет и проверяет содержимое, права, владельца, зависимости и conffiles:

Поскольку пакет не содержит ELF, SDK собирает только его (`NO_DEPS=1`),
без повторной компиляции curl/LuCI/криптобиблиотек для каждой архитектуры.
Зависимости остаются в метаданных, а их реальная установка проверяется в VM.
Добавление ELF в такой пакет отклоняется сборщиком.

```sh
sh scripts/build-openwrt-package.sh --release 24.10.3 --target ramips --subtarget mt7621 --arch mipsel_24kc --output artifacts
```

SDK поддерживаются в форматах `.tar.xz` и `.tar.zst`. Для подготовки автономного
каталога пакета внутри своего SDK:

```sh
python3 scripts/openwrt_package.py prepare --output /path/to/sdk/package/luci-app-owrt-remote
```

## GitHub Actions и подпись APK

Workflow `.github/workflows/openwrt-packages.yml` запускается на тегах `v*`,
публикации Release, изменениях пакета в pull request и вручную. В PR и при
обычном ручном запуске он только собирает и тестирует. Для повторной публикации
после исправления сборочного инструмента доступен ручной запуск с
`publish_release=true` и существующим `release_tag`, например `v121`.
Он не перемещает тег и проверяет, что содержимое `files/` и установочные
помощники совпадают с этим тегом. Рецепт и инструменты сборки могут содержать
исправления; точный commit сборки записывается в отчёте. После проверок
пакеты прикладываются к Release.
Тег должен совпадать с `PKG_VERSION`: текущий пример — `v121`.
Следующий выпуск должен получить новую версию или пакетную ревизию в Makefile.

На каждом запуске выбирается последняя стабильная patch-версия веток
23.05, 24.10 и 25.12. Матрица `package/openwrt-targets.json` содержит 10 SDK
на каждую ветку: A53, A72, generic ARM64, ARM A7/A9, MIPS, MIPSEL, MIPS64,
x86 и x86_64. Сохраняются отчёты всех 30 сборок.

После сборки универсальные пакеты устанавливаются в три изолированные x86_64
OpenWrt VM. Проверяется первичная установка, настоящая смена пакетной ревизии,
работа CGI, BusyBox ash, службы, удаление, сохранность настроек/ключа/Xray и
переход с не принадлежащей пакетному менеджеру конфигурации. Тестовая ревизия
из `test-fixtures/` не публикуется. Только после успешного завершения
всей матрицы публикуются три пакета, SHA256SUMS и отчёты SDK.

Для подписанных APK добавь в GitHub Actions secret `OPENWRT_APK_SIGNING_KEY`
постоянный приватный RSA PEM-ключ сопровождающего. Пример создания на доверенной
машине (приватный файл не добавлять в Git):

```sh
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out /secure/owrt-remote-private.pem
openssl pkey -in /secure/owrt-remote-private.pem -pubout -out owrt-remote-packages.pem
```

Workflow подписывает APK, проверяет установку с публичным ключом и добавляет
`owrt-remote-packages.pem` в Release. Приватный ключ остаётся secret/временным
файлом runner и не попадает в artifacts. Если secret отсутствует, выпуск APK
явно помечается в отчёте как неподписанный и устанавливается по SSH с
`--allow-untrusted`. IPK устанавливается локальным файлом обычным способом.
Новый случайный ключ на каждом запуске не создаётся.

## Проверки перед публикацией

```sh
python3 -m unittest discover -s tests -p test_openwrt_package.py -v
python3 scripts/test-openwrt-package-vm.py --release 24.10.3 --package-dir artifacts --output vm-test
```

Для VM-теста нужны QEMU, SSH и один основной пакет плюс пакет следующей
ревизии в `test-fixtures/`. SDK-сборка создаёт такой набор с `--upgrade-fixture`.
VM использует только loopback и не подключается к реальным роутерам.

Проверка установки на x86_64 не доказывает работу конкретного Xray на каждом
физическом CPU. Xray, Heartbeat с реальным Hub, WAN direct и аппаратные
особенности требуют отдельной проверки на устройствах.

Основа реализации: [OpenWrt SDK](https://openwrt.org/docs/guide-developer/toolchain/using_the_sdk),
[пакетный Makefile OpenWrt](https://github.com/openwrt/openwrt/blob/openwrt-25.12/include/package-pack.mk),
[жизненный цикл пакетов](https://github.com/openwrt/openwrt/blob/openwrt-25.12/package/base-files/files/lib/functions.sh).
