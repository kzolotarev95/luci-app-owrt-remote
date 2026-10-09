# Фиксация VPS host и независимое соединение Remote через WAN (v112)

`vps_host_mode=manual` сохраняет заданный на роутере адрес. Это значение
используется и когда опция отсутствует у старой установки. Только явно
выбранное `auto` позволяет принять `hub_update.vps_host`. Ни один ответ
heartbeat не может сам включить автоматический режим. Изменения и
отклонённые предложения пишутся через `logger -t owrt-remote` в logread;
повтор одинакового отклонённого предложения не засоряет журнал.

Hub сохраняет прежнее поведение для старых агентов, ещё не сообщающих
режим. Сам обновлённый агент по умолчанию защищает ручной адрес даже
при старой версии Hub. Для полного фикса сначала обновляется роутер.

На роутере в настройках есть «Автоматически обновлять VPS host».
Блок «Настроить VPS host» убран из карточек Hub на ПК и мобильном.
Он не появляется при периодическом обновлении карточек. Сам механизм
ручного/автоматического режима и API применения адреса по SSH сохраняются.
OpenWrt config / автоматическое переприменение конфигурации больше не
удаляет всю UCI-секцию и сохраняет существующий ручной адрес и WAN-параметры.

## Прямой WAN

`wan_direct=1` включает отдельный маршрут для Remote; по умолчанию эта
опция выключена в поставляемой конфигурации и старых установках. Скрипт
локальной установки ниже включает её для тестирования на вашем роутере.

* `wan_interface=wan` — логическое имя интерфейса OpenWrt. Для резервирования
  допустим список через пробел, например `wan wwan modem4g`; в панели эти
  интерфейсы выбираются галочками. Remote выбирает один рабочий и при
  его отключении переводит соединение на другой выбранный. Реальный
  `l3_device` (включая PPPoE) берётся через ubus. Он должен иметь физический
  default route в main; TUN-устройства не принимаются как WAN.
* Таблица маршрутов `210` и приоритет `104` принадлежат Remote.
  В таблицу копируется только default route выбранного WAN.
  Номер таблицы совместим со штатным BusyBox ip. Занятые другим сервисом
  таблица или приоритет приводят к отказу до изменения маршрутов.
* Вместо дополнительных nftables-правил используются адресные `ip rule`
  с `iif lo`. Они выбирают только пакеты, созданные самим роутером, к реальным
  адресам Hub, действующего Xray и DNS выбранного WAN. LAN/FORWARD не меняются.
  Ограничение по портам не используется: весь локальный трафик к этим адресам
  идёт через WAN. Приоритет 104 выше стандартного правила Podkop 105.
* Только два VPS-outbound Xray получают SO_MARK `0x00200000` (штатный bypass
  Podkop) и привязку к WAN-интерфейсу. Дополнительная nft-таблица не создаётся;
  прежняя `inet owrt_remote_wan` и собственные правила `0x40000000` удаляются
  при переходе. Проверка предупреждений самого Podkop не изменяется.
  Локальные подключения к админке и SSH продолжают работать локально.
* Для доменов используется прямой запрос к DNS провайдера на WAN
  (можно задать `wan_dns`). Ответ FakeIP 198.18.0.0/15 отклоняется.
  Успешный ответ кешируется 5 минут, при временном сбое допускается
  использование предыдущего ответа максимум час.
* При ручном IP домен HTTPS Hub соединяется с этим IP через curl `--resolve`:
  сохраняются Host, SNI и проверка сертификата. Проверка TLS не отключается.
  Xray соединяется с IP без DNS. Запросы heartbeat ограничены по времени.
* Правила обновляются при heartbeat и старте, очищаются при остановке/откате.
  Настройки Podkop, dnsmasq и службы sing-box не изменяются.

Это отделяет соединение Remote от Podkop. Общая схема fallback для клиентов
LAN при **аварийном падении** sing-box относится к настройкам самого Podkop;
этот фикс не обещает восстановление DNS/FakeIP-сессий всей LAN.

## Что известно о домене

08.10.2026 системный DNS на компьютере разработки возвращает для
`owrt-zks95.developer.li` IPv4 `193.233.82.38`. Это не подтверждает, что
роутер получает тот же ответ. В стандартном Podkop dnsmasq направляется
на DNS sing-box. Поэтому разница «IP работает, домен периодически не
работает» может быть вызвана DNS/FakeIP/маршрутизацией, но без логов
самого роутера нельзя утверждать точную причину.

После установки команда `owrt-remote network-diagnostics` сравнивает
системный DNS с прямым WAN DNS и показывает собственные правила Remote.
В вывод не включаются Hub token, UUID и SSH-пароли. Для проверки поведения
при работающем и остановленном Podkop нужен настоящий OpenWrt-роутер.

Источники:
[DNS, OUTPUT и диагностика Podkop 0.7.22](https://github.com/itdoginfo/podkop/blob/0.7.22/podkop/files/usr/bin/podkop),
[маркировка Podkop 0.7.22](https://github.com/itdoginfo/podkop/blob/0.7.22/podkop/files/usr/lib/constants.sh),
[Xray sockopt](https://xtls.github.io/en/config/transports/sockopt.html),
[локальные пакеты и iif lo](https://man7.org/linux/man-pages/man8/ip-rule.8.html),
[ограничение ID таблиц в BusyBox ip](https://github.com/mirror/busybox/blob/master/networking/libiproute/rt_names.c).

## Установка и откат с Windows 11

В PowerShell из каталога проекта. При другом IP роутера или логическом
имени WAN замените значения параметров. VPS host фиксируется на указанном IP.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "vps\deploy-host-policy.ps1" -Target Router -Machine "root@192.168.2.1" -PinnedVpsHost "193.233.82.38" -WanInterface "wan" -Action Install
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "vps\deploy-host-policy.ps1" -Target Vps -Machine "root@193.233.82.38" -Action Install
```

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "vps\deploy-host-policy.ps1" -Target Router -Machine "root@192.168.2.1" -Action Rollback
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "vps\deploy-host-policy.ps1" -Target Vps -Machine "root@193.233.82.38" -Action Rollback
```

Установка загружает локальные файлы, без Git. До остановки службы проверяются
доступность WAN и поддержка таблицы реальной командой ip на роутере.
На роутер загружается и файл LuCI: версия v112 появляется и в основной
CGI-панели, и в странице входа в неё через LuCI.
Сначала сохраняются файлы
и конфигурация; после замены проверяется heartbeat/health. При ошибке
автоматически восстанавливается состояние перед текущей попыткой.
Сохранённая резервная копия для ручного отката не перезаписывается.

* Роутер: `/root/owrt-remote-backup-before-host-policy-v112` — агент,
  helper, init, CGI, UCI-конфигурация и Xray-конфиг (в закрытом каталоге).
* VPS: `/opt/owrt-remote/owrt-remote-hub.py.bak-before-host-policy-v112`.
  База роутеров и настройки авторизации не заменяются.

Успешная установка заканчивается `HOST_POLICY_ROUTER_V112_OK` на роутере
и `HOST_POLICY_VPS_V112_Install_OK` на VPS. Старые резервные копии предыдущей
версии сохраняются; новые команды отката используют отдельные копии для v112.

### Переход с nftables-маркировки на адресную маршрутизацию

Для уже настроенного роутера используется отдельная резервная копия.
Эти команды сохраняют существующие VPS host, ручной/auto режим и WAN-настройки.
Обновление VPS для этого изменения не требуется.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "vps\deploy-host-policy.ps1" -Target Router -Machine "root@192.168.2.1" -PreserveConnectionSettings -BackupName "podkop-compat-v112" -Action Install
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "vps\deploy-host-policy.ps1" -Target Router -Machine "root@192.168.2.1" -BackupName "podkop-compat-v112" -Action Rollback
```

Копия: `/root/owrt-remote-backup-before-podkop-compat-v112`.
После установки повторите диагностику Podkop. Предупреждение от прежних правил
Remote должно исчезнуть. Настоящие дополнительные правила других приложений
по-прежнему обнаруживаются штатной диагностикой.

## Локальные проверки

```powershell
python -m unittest discover -s tests -v
python tests/test_recaptcha.py --export-js .test-output/recaptcha
node --check .test-output/recaptcha/dashboard.js
node tests/test_host_policy_ui.cjs .test-output/recaptcha/dashboard.js
```

Shell-тесты исполняют код агента в Git for Windows sh или Linux sh
с подменёнными UCI, ubus, DNS, nft, ip и curl. Проверяются ручной/auto режим,
сохранение IP при rebind, реальные аргументы curl/Xray, WAN-таблицы,
миграция прежних правил, отсутствие дубликатов при heartbeat, сохранение
адреса работающего туннеля при смене DNS, IPv6, ошибки DNS и восстановление.
Оригинальная функция `check_nft_rules` из Podkop 0.7.22 исполняется на подменённом
nft: прежние правила дают предупреждение, переход убирает его, а посторонние
маркирующие правила по-прежнему вызывают предупреждение.
Это не заменяет аппаратную проверку nftables и Podkop на OpenWrt.
