# Native Android redesign checks

Use an isolated Android emulator, not a personal device: the instrumentation
clears its own app session and preferences. Both servers below are disposable
loopback-only fixtures. No actual Hub/router/VPS is accessed.

```powershell
python .\android\test-fixtures\native_server.py --port 8767 --showcase
```

The opt-in showcase supplies nine devices, known model aliases, two offline
routers, an unknown model, groups, notes, and long names. An online device sorts
after the first seven rows, allowing the expanded-map animation to be checked.
Default fixture behavior
is unchanged for the existing smoke and audit suites.

In a second PowerShell window after starting the emulator:

```powershell
& .\android\build-support\build.ps1 -Tasks @('assembleDebug','assembleDebugAndroidTest','-PnativeRedesign')
$Adb = '.\.test-output\android-build-tools\sdk\platform-tools\adb.exe'
& $Adb -s emulator-5554 reverse tcp:8767 tcp:8767
& $Adb -s emulator-5554 install -r .\android\app\build\outputs\apk\debug\app-debug.apk
& $Adb -s emulator-5554 install -r .\android\app\build\outputs\apk\androidTest\debug\app-debug-androidTest.apk
& $Adb -s emulator-5554 shell am instrument -w -e fixtureUrl http://127.0.0.1:8767 ru.owrthub.app.test/ru.owrthub.app.NativeRedesignInstrumentation
```

Checks cover native graphite login/dashboard without duplicate router cards,
actual model photos, the seven-row map and expansion through live refresh, filters independent of the map, menu
sections remembered on reopen, statistics visibility across relaunch, centered
details, native support, light mode preference and landscape bounds. The map
checks observe presented animation frames, changed connection pixels on a router
after the seventh row, and stopped callbacks after detachment or in light mode. Screenshots
are stored in the test app's external-files `native-redesign` directory.

The existing `NativeSmokeInstrumentation` and `NativeAuditInstrumentation` remain
separate functional checks against `native_server.py --port 8766` without showcase.
For the audit runner build with `-PnativeAudit`; without either flag use the smoke
runner. Test builds never change the production API or bypass Hub authentication.
Start a fresh default fixture for each full audit run: it deliberately changes
update availability, notes, sessions and users. Reusing an already exercised
fixture does not reproduce the initial-state assertions.
