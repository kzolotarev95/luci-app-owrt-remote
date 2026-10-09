# Применение OpenWrt config через панель роутера (v111)

При отправке textarea браузер кодирует переносы строк как CRLF. CGI
декодировал их без нормализации и записывал CR в исполняемый shell-скрипт.
После появления блока `if/then/else/fi` для сохранения ручного VPS host
это приводило к ошибке `unexpected end of file (expecting "then")`.

CGI удаляет CR перед записью скрипта и проверяет целиком `sh -n` до
выполнения любых команд UCI. При ошибке синтаксиса конфигурация не
меняется. Выполнение через `sh -e` останавливается при ошибке команды,
например при неудачном `render-client`, и не продолжает перезапуск.

`tests/test_xray_apply.py` отправляет настоящий URL-encoded POST в CGI
с тестовым ключом и исполняет команды строгой POSIX-оболочкой. Проверяются
CRLF и LF, heredoc, пустые строки, кодированные символы, отклонение
неполного скрипта до изменений, остановка при сбое команды и отказ
без авторизации. Полный конфиг, сгенерированный Hub, применяется через
ту же форму с подменёнными UCI/службой; ручной VPS host и WAN сохраняются.
На Windows строгая проверка использует Git for Windows dash.

## Установка и откат

Скрипт обновляет только CGI на роутере. VPS обновлять для этого фикса
не требуется. До замены сохраняется первая резервная копия CGI:
`/root/owrt-remote-backup-before-xray-apply-v111/owrt-remote`.

Из PowerShell Windows 11; замените IP, если адрес роутера отличается:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\k.zolotarev95\Documents\openwrt overlay\vps\deploy-xray-apply-fix.ps1" -Router "root@192.168.2.1" -Action Install
```

После `XRAY_APPLY_ROUTER_Install_OK` обновите панель роутера через
Ctrl+F5, снова скопируйте полный блок **OpenWrt config** из панели VPS
и нажмите **Применить настройки**. Это повторно применит настройки без
CR и пересоберёт Xray-конфиг. Предыдущая неудачная попытка могла успеть
записать часть настроек; замена CGI сама по себе их не переприменяет.
Если после успешного применения ошибка proxy остаётся, требуется
проверка запуска Xray и обратного соединения с VPS на реальном роутере.

Откат возвращает CGI до установки; настройки, повторно применённые
через форму, остаются текущими:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\k.zolotarev95\Documents\openwrt overlay\vps\deploy-xray-apply-fix.ps1" -Router "root@192.168.2.1" -Action Rollback
```

Проверки локально:

```powershell
python tests/test_xray_apply.py -v
```

Переносы строк в формах описаны в [стандарте HTML](https://html.spec.whatwg.org/multipage/form-control-infrastructure.html).
