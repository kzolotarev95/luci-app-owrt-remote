# Совместимость Xray с текущим VLESS reverse (v120)

Причина `rc=23` подтверждена настоящими бинарниками: в Xray 26.7.11 и новее
публичный VLESS outbound с `encryption=none` и `security=none` запрещён.
Это несовместимость ядра с действующим транспортом, а не ошибка мобильного
буфера обмена, JSON, UUID или reverse_tag. TLS только на HTTPS-сайте Hub
не защищает отдельное VLESS-соединение на 8443.

Локальное исправление сохраняет существующий протокол и VPS-конфигурацию.
По умолчанию используется последняя сборка до запрета — **26.6.27** из
`yichya/openwrt-xray`. TLS/REALITY этим исправлением не добавляются; для
использования 26.7.11+ нужен отдельный согласованный переход обоих концов.

Источники проверки:

- https://github.com/XTLS/Xray-core/blob/v26.7.11/infra/conf/xray.go
- https://github.com/XTLS/Xray-core/blob/v26.6.27/infra/conf/xray.go
- https://github.com/yichya/openwrt-xray/releases/tag/v26.6.27

## Поведение

- Учитываются реальные имена IPK этого релиза с двойным указанием архитектуры,
  включая ARM64, MIPSel и особые имена `i386_pentium`/`arm_cortex-a5-vfpv4`.
- Найденный работающий старый core сохраняется. Известный core 26.7.11+
  для `encryption=none` заменяется совместимым: сначала проверенный boot-кэш,
  затем загрузка закреплённого пакета. Ошибка сети не удаляет старый бинарник.
- До замены runtime/кэша/UCI кандидат выполняет `-test` на несекретной пробной
  конфигурации публичного VLESS reverse. `xray version` больше недостаточно.
- Возвращаемый на Flash core также проверяется перед установкой в RAM.
  Известный несовместимый core не записывается под именем совместимого кэша.
- При извлечении IPK используется отдельный каталог в RAM. Перемещение бинарника
  не создаёт дополнительную полную RAM-копию. Fallback через `opkg install`
  удалён: он мог заменить системный core ещё до проверки кандидата.
- Реальная проверка нового рабочего JSON остаётся обязательной. Ошибка не
  заменяет JSON, не считается автоматически ошибкой переносов строк, а кнопка
  установки больше не перезапускает сервис после проваленной проверки.
- Неудачная установка, неудачная проверка JSON и ошибка рестарта имеют разные
  заголовки. Успех установки отображается только после успешных этапов.

## Установка локального исправления

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\k.zolotarev95\Documents\openwrt overlay\vps\deploy-xray-compat-fix.ps1" -Router "root@192.168.2.1" -Action Install
```

Скрипт сохраняет агент и CGI в
`/root/owrt-remote-backup-before-xray-compat-v120`, устанавливает код и
подготавливает совместимый core. Он не меняет рабочий JSON и не перезапускает
туннель. После успеха (`XRAY_COMPAT_ROUTER_Install_OK`) повторно применить
команды из «OpenWrt config» в «Xray настройки» панели роутера.

Откат возвращает только предыдущие агент и CGI. Проверенный совместимый
бинарник, boot-кэш и настройки сохраняются:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\k.zolotarev95\Documents\openwrt overlay\vps\deploy-xray-compat-fix.ps1" -Router "root@192.168.2.1" -Action Rollback
```

## Проверки

`test_xray_bootstrap.py`: выбор IPK/APK, замена уже установленного несовместимого
core, офлайн-восстановление, отклонение неподходящего кандидата до замены UCI,
сохранение старого файла и Flash-кэша, уборка временных файлов и блокировок.

`test_xray_apply.py`: реальный CGI POST для всех трёх ошибок установки;
проверка, что при `rc=23` рестарта нет и успех не отображается.

`test_xray_compat_deploy.py`: установка/откат, сохранность первой копии,
автоматический возврат кода при ошибке подготовки core, отсутствие рестарта.

Дополнительно тесты принимают `OWRT_TEST_XRAY_BIN` (26.6.27) и
`OWRT_TEST_NEW_XRAY_BIN` (26.10.21). Они воспроизводят реальный отказ нового
core и проверяют полный сгенерированный JSON на публичном домене и IP.
`OWRT_TEST_BUSYBOX` включает BusyBox ash для bootstrap-тестов.

Локально проверены настоящие Windows-бинарники обеих версий и извлечение
настоящих ARM64/MIPSel IPK через BusyBox ash. Linux ARM64/MIPSel-бинарники
на Windows не исполнялись; финальная проверка туннеля нужна на роутере.

## Туннель запущен, но Админка и SSH зависают

Дополнительный дефект воспроизведён с настоящими ядрами: `xray -test`
принимает JSON и VLESS reverse устанавливается, но Freedom в 26.6.27
по умолчанию блокирует весь трафик от `vless-reverse`. В debug-журнале:
`blocked target: tcp:127.0.0.1:80, blackholing connection ...`.
На VPS видны локальные `accepted`, однако HTTP/SSH не получают ответ.
Heartbeat использует отдельное соединение и продолжает работать.

Источник: https://github.com/XTLS/Xray-core/blob/v26.6.27/proxy/freedom/freedom.go
(`getDefaultFinalRule` и проверка `finalRules` после `DestinationOverride`).

В агенте и генераторе клиентского JSON Hub добавлены `finalRules` к двум
выходам Freedom: разрешён TCP только на настроенный порт соответствующей
службы. `redirect` по-прежнему фиксирует её адрес и порт перед проверкой.
Разрешение не добавляется к остальным выходам. Heartbeat/WAN/Podkop,
UUID, reverse tags и серверная конфигурация остаются прежними.

`test_xray_reverse_transport.py` проверяет реальный HTTP-ответ и SSH banner:
26.6.27 → 26.6.27 и 26.6.27 → 26.10.21; отдельно используется JSON,
сгенерированный настоящим shell-агентом. Понижение версии VPS не нужно.

Установка исправления на роутер сохраняет агент, CGI и исходный JSON;
проверяет новый JSON через Xray, атомарно заменяет его и перезапускает
туннель. Ошибка проверки не вызывает рестарт. Ошибка рестарта возвращает
предыдущий JSON и код. Совместимый бинарник и boot-кэш сохраняются.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\k.zolotarev95\Documents\openwrt overlay\vps\deploy-xray-reverse-fix.ps1" -Router "root@192.168.2.1" -Action Install
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\k.zolotarev95\Documents\openwrt overlay\vps\deploy-xray-reverse-fix.ps1" -Router "root@192.168.2.1" -Action Rollback
```

На VPS обновляется генератор клиентского JSON. Сам Xray и его серверный
JSON не заменяются. Проверяется SHA-256 реально запущенного Hub через
локальный `/health?panel-update=1`; при ошибке возвращается предыдущий Hub.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\k.zolotarev95\Documents\openwrt overlay\vps\deploy-xray-reverse-vps.ps1" -Vps "root@138.124.79.58" -Action Install
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\k.zolotarev95\Documents\openwrt overlay\vps\deploy-xray-reverse-vps.ps1" -Vps "root@138.124.79.58" -Action Rollback
```

## Установка APK на OpenWrt 25

Пакет `openwrt-xray-26.6.27-r1-aarch64_generic.apk` имеет формат APK v3/ADB
(`ADBd` в начале файла). Загрузчик сохраняет любой пакет как `xray.pkg`;
прежний распаковщик выбирал IPK по этому временному имени. Даже при прямом
вызове ветка APK использовала `apk add` и могла установить файлы в системный
`/usr/bin`, не проверив новый бинарник заранее.

Теперь исходное имя release asset передаётся распаковщику отдельным аргументом.
Для APK выполняется `apk extract --allow-untrusted --no-chown --destination`
в отдельный каталог RAM. Команда не устанавливает зависимости и не меняет
базу пакетов. После извлечения проверяется обычный файл `usr/bin/xray`;
проверки размера, запуска и совместимости транспорта остаются обязательными.
Ошибки `apk extract` показывают код и ограниченный фрагмент настоящего вывода.
Неполный кандидат и временный каталог удаляются; старый бинарник, UCI и кэш
не заменяются при неудаче.

В OpenWrt 25.12 используется apk-tools 3.0.5 с `minimal=true`; команда
`extract` входит и в минимальную сборку. Источники:

- https://github.com/openwrt/openwrt/blob/openwrt-25.12/package/system/apk/Makefile
- https://github.com/alpinelinux/apk-tools/blob/v3.0.5/meson.build
- https://github.com/alpinelinux/apk-tools/blob/v3.0.5/src/app_extract.c

Локальная установка заменяет только агент и повторяет `prepare-xray`.
Если первая установка прервалась до создания `/etc/owrt-remote/web.key`,
после подготовки Xray создаётся случайный ключ входа. Имеющийся ключ сохраняется.
Это завершает необходимый для открытия панели шаг прерванного `install.sh`:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\k.zolotarev95\Documents\openwrt overlay\vps\deploy-xray-apk-fix.ps1" -Router "root@192.168.2.1" -Action Install
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\k.zolotarev95\Documents\openwrt overlay\vps\deploy-xray-apk-fix.ps1" -Router "root@192.168.2.1" -Action Rollback
```

Резервная копия агента: `/root/owrt-remote-backup-before-xray-apk-v120`.
Откат возвращает агент, сохраняя подготовленный бинарник и boot-кэш.
Рабочий JSON не пересобирается, туннель не перезапускается.

Тесты проверяют полный bootstrap с форматом APK и временным именем `.pkg`,
неудачное извлечение, отсутствие бинарника, несовместимого кандидата и IPK.
В тестах shell работает настоящий BusyBox ash, вызов `apk` заменён тестовым
обработчиком. Настоящий APK скачан и его формат проверен; запуск нативного
`apk extract` на OpenWrt 25.12.5 требуется подтвердить установкой на устройстве.
