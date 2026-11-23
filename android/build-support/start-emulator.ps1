param(
    [int]$Port = 5554,
    [switch]$Software
)

$ErrorActionPreference = 'Stop'
$androidRoot = Split-Path -Parent $PSScriptRoot
$repositoryRoot = Split-Path -Parent $androidRoot
$toolRoot = Join-Path $repositoryRoot '.test-output/android-build-tools'
$sdkRoot = Join-Path $toolRoot 'sdk'
$emulatorPath = Join-Path $sdkRoot 'emulator/emulator.exe'
$avdManagerPath = Join-Path $sdkRoot 'cmdline-tools/19.0/bin/avdmanager.bat'
$avdRoot = Join-Path $toolRoot 'avd'
$avdPath = Join-Path $avdRoot 'owrt-hub-test'
$systemImage = Join-Path $sdkRoot 'system-images/android-35/google_apis/x86_64/system.img'

if (($Port -lt 5554) -or ($Port -gt 5682) -or ($Port % 2 -ne 0)) {
    throw 'Choose an even emulator console port between 5554 and 5682.'
}
foreach ($required in @($emulatorPath, $avdManagerPath, $systemImage)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Missing emulator component: $required" }
}
if (Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue) {
    throw "Port $Port is already in use. Choose another even port."
}

$environmentNames = @('JAVA_HOME', 'ANDROID_HOME', 'ANDROID_SDK_ROOT', 'ANDROID_USER_HOME', 'ANDROID_EMULATOR_HOME', 'ANDROID_AVD_HOME')
$savedEnvironment = @{}
foreach ($name in $environmentNames) { $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }

try {
    $userHome = Join-Path $toolRoot 'android-user-home'
    $emulatorHome = Join-Path $toolRoot 'emulator-user-home'
    New-Item -ItemType Directory -Force -Path $avdRoot, $userHome, $emulatorHome | Out-Null
    # The emulator's child-process launcher can misparse a checkout path containing spaces.
    $fileSystem = New-Object -ComObject Scripting.FileSystemObject
    $sdkRoot = $fileSystem.GetFolder($sdkRoot).ShortPath
    $env:JAVA_HOME = $fileSystem.GetFolder((Join-Path $toolRoot 'jdk-17')).ShortPath
    $env:ANDROID_HOME = $sdkRoot
    $env:ANDROID_SDK_ROOT = $sdkRoot
    $env:ANDROID_USER_HOME = $fileSystem.GetFolder($userHome).ShortPath
    $env:ANDROID_EMULATOR_HOME = $fileSystem.GetFolder($emulatorHome).ShortPath
    $env:ANDROID_AVD_HOME = $fileSystem.GetFolder($avdRoot).ShortPath
    $avdPath = Join-Path $env:ANDROID_AVD_HOME 'owrt-hub-test'
    $emulatorPath = Join-Path $sdkRoot 'emulator/emulator.exe'
    $avdManagerPath = Join-Path $sdkRoot 'cmdline-tools/19.0/bin/avdmanager.bat'

    if (-not (Test-Path -LiteralPath (Join-Path $avdPath 'config.ini'))) {
        'no' | & $avdManagerPath create avd --name owrt-hub-test --package 'system-images;android-35;google_apis;x86_64' --device pixel_2 --path $avdPath
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the isolated test AVD.' }
    }

    & $emulatorPath -accel-check
    $accelerationAvailable = ($LASTEXITCODE -eq 0)
    $emulatorArguments = @(
        '-avd', 'owrt-hub-test', '-port', $Port, '-no-window', '-no-audio',
        '-no-boot-anim', '-no-snapshot', '-read-only', '-gpu', 'swiftshader',
        '-memory', '2048', '-cores', '2'
    )
    if ($Software -or -not $accelerationAvailable) { $emulatorArguments += @('-accel', 'off') }
    $process = Start-Process -FilePath $emulatorPath -ArgumentList $emulatorArguments -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $toolRoot "emulator-$Port.stdout.log") `
        -RedirectStandardError (Join-Path $toolRoot "emulator-$Port.stderr.log")
    Write-Output "Started disposable AVD, PID $($process.Id), serial emulator-$Port."
    Write-Output "ADB: $(Join-Path $sdkRoot 'platform-tools/adb.exe')"
}
finally {
    foreach ($name in $environmentNames) { [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], 'Process') }
}
