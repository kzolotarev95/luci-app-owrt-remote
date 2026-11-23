param(
    [string[]]$Tasks = @('assembleDebug', 'lintDebug'),
    [switch]$InstallTools
)

$ErrorActionPreference = 'Stop'
$androidRoot = Split-Path -Parent $PSScriptRoot
$repositoryRoot = Split-Path -Parent $androidRoot
$toolRoot = Join-Path $repositoryRoot '.test-output/android-build-tools'
$jdkRoot = Join-Path $toolRoot 'jdk-17'
$sdkRoot = Join-Path $toolRoot 'sdk'
$gradleScript = Join-Path $toolRoot 'gradle-8.11.1/bin/gradle.bat'

if ($InstallTools) {
    & python (Join-Path $PSScriptRoot 'install-toolchain.py')
    if ($LASTEXITCODE -ne 0) { throw 'Android toolchain installation failed.' }
}

foreach ($required in @((Join-Path $jdkRoot 'bin/java.exe'), $gradleScript, (Join-Path $sdkRoot 'platforms/android-35/android.jar'))) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Missing $required. Run this script with -InstallTools first."
    }
}

$environmentNames = @('JAVA_HOME', 'ANDROID_HOME', 'ANDROID_SDK_ROOT', 'GRADLE_USER_HOME', 'ANDROID_USER_HOME', 'PATH')
$savedEnvironment = @{}
foreach ($name in $environmentNames) { $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }

try {
    $env:JAVA_HOME = $jdkRoot
    $env:ANDROID_HOME = $sdkRoot
    $env:ANDROID_SDK_ROOT = $sdkRoot
    $env:GRADLE_USER_HOME = Join-Path $toolRoot 'gradle-user-home'
    $env:ANDROID_USER_HOME = Join-Path $toolRoot 'android-user-home'
    $env:PATH = "$jdkRoot/bin;$sdkRoot/platform-tools;$env:PATH"
    & $gradleScript --no-daemon --console=plain -p $androidRoot @Tasks
    $buildExitCode = $LASTEXITCODE
}
finally {
    foreach ($name in $environmentNames) { [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], 'Process') }
}

if ($buildExitCode -ne 0) { throw "Gradle failed with exit code $buildExitCode." }
