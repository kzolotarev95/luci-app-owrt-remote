# Версия v122

Исправления Tachyon для APK 3.0.5 выпускаются как пакетная ревизия 122-r2
с тегом v122-r2. Номер интерфейсов остаётся v122; исходный тег v122 и пакеты
122-r1 сохраняются.

Выпуск использует main и отдельный тег v122. GitHub Actions собирает IPK/APK,
проверяет их и прикладывает к Releases → v122 после успешного завершения сборок
и установки/обновления/удаления в OpenWrt VM. Выпущенная v121 сохраняется.

Версия v122 указана в панели VPS, на экране входа, в мобильной шапке,
CGI и представлении LuCI роутера. Скрипты локальной установки, их проверки
и документация используют тот же номер. Ссылки источников не менялись.

Установить локальную сборку на VPS:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\k.zolotarev95\Documents\openwrt overlay\vps\deploy-version-v122.ps1" -Vps "root@132.243.19.232" -Action Install
```

В ходе текущего тестирования обновления через кнопку вместо обычной установки
нужно использовать тестовые сборки из [HUB_UPDATE.md](HUB_UPDATE.md):

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\k.zolotarev95\Documents\openwrt overlay\vps\test-hub-self-update.ps1" -Vps "root@132.243.19.232" -Action Retest
```

Установить версию интерфейса роутера (CGI и LuCI):

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\k.zolotarev95\Documents\openwrt overlay\vps\deploy-version-v122.ps1" -Router "root@192.168.2.1" -Action Install
```

Скрипт сохраняет первую резервную копию и проверяет файлы до установки.
На VPS дополнительно сверяется SHA-256 запущенной сборки через `/health?panel-update=1`;
ответ от другой сборки не подтверждает успешную установку.
Обновление через кнопку не позволяет установить более старую опубликованную
версию поверх локальной v122. Для исправлений с тем же номером версии
по-прежнему проверяется SHA-256. Отдельный баннер «последняя версия» скрыт:
при отсутствии обновлений остаётся зелёная кнопка «Обновление не требуется».
При ошибке после замены возвращает файлы предыдущей попытки.
Установщики не выполняют миграцию настроек роутера; после установки
интерфейса нужно обновить страницу через Ctrl+F5.

Для отката обычной установки VPS либо роутера повторить соответствующую
команду с `-Action Rollback`. Копии:

- VPS: `/opt/owrt-remote/owrt-remote-hub.py.bak-before-v122`.
- Роутер: `/root/owrt-remote-backup-before-v122` (CGI и LuCI).

Для возврата из тестового режима обновления VPS повторить команду
`test-hub-self-update.ps1` с `-Action Restore`: его копия хранится отдельно,
в `/opt/owrt-remote/self-update-local-test/before.py`.
