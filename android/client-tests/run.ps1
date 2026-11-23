param([string]$JavaHome, [string]$AndroidJar)

$ErrorActionPreference = 'Stop'
$androidRoot = Split-Path -Parent $PSScriptRoot
$repositoryRoot = Split-Path -Parent $androidRoot
$toolRoot = Join-Path $repositoryRoot '.test-output/android-build-tools'
if (-not $JavaHome) { $JavaHome = Join-Path $toolRoot 'jdk-17' }
if (-not $AndroidJar) { $AndroidJar = Join-Path $toolRoot 'sdk/platforms/android-35/android.jar' }
$compiler = Join-Path $JavaHome 'bin/javac.exe'
$runtime = Join-Path $JavaHome 'bin/java.exe'
$classes = Join-Path ([System.IO.Path]::GetTempPath()) ('owrt-client-tests-' + [guid]::NewGuid().ToString('N'))

New-Item -ItemType Directory -Path $classes | Out-Null
try {
    $buildConfig = Join-Path $classes 'BuildConfig.java'
    Set-Content -LiteralPath $buildConfig -Encoding ascii -Value 'package ru.owrthub.app; final class BuildConfig { static final boolean DEBUG = true; }'
    & $compiler -encoding UTF-8 -source 17 -target 17 -cp $AndroidJar -d $classes `
        $buildConfig `
        (Join-Path $androidRoot 'app/src/main/java/ru/owrthub/app/HubUrlPolicy.java') `
        (Join-Path $androidRoot 'app/src/main/java/ru/owrthub/app/HubApiClient.java') `
        (Join-Path $androidRoot 'app/src/main/java/ru/owrthub/app/HubSessionStore.java') `
        (Join-Path $PSScriptRoot 'HubApiClientTest.java')
    if ($LASTEXITCODE -ne 0) { throw 'Native client checks did not compile.' }
    & $runtime -cp "$classes;$AndroidJar" ru.owrthub.app.HubApiClientTest
    if ($LASTEXITCODE -ne 0) { throw 'Native client checks failed.' }
}
finally {
    if (Test-Path -LiteralPath $classes) {
        $resolvedClasses = (Resolve-Path -LiteralPath $classes).ProviderPath
        $resolvedTemp = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\')
        if ([System.IO.Path]::GetDirectoryName($resolvedClasses) -ne $resolvedTemp -or
            [System.IO.Path]::GetFileName($resolvedClasses) -notmatch '^owrt-client-tests-[0-9a-f]{32}$') {
            throw 'Refusing to remove a directory outside the client test temporary path.'
        }
        Remove-Item -LiteralPath $resolvedClasses -Recurse -Force
    }
}
