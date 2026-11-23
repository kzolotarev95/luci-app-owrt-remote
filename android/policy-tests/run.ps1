param([string]$JavaHome = $env:JAVA_HOME)

$ErrorActionPreference = 'Stop'
$androidRoot = Split-Path -Parent $PSScriptRoot
$compiler = if ($JavaHome) { Join-Path $JavaHome 'bin\javac.exe' } else { 'javac' }
$runtime = if ($JavaHome) { Join-Path $JavaHome 'bin\java.exe' } else { 'java' }
$classes = Join-Path ([System.IO.Path]::GetTempPath()) ('owrt-policy-tests-' + [guid]::NewGuid().ToString('N'))

New-Item -ItemType Directory -Path $classes | Out-Null
try {
    & $compiler -encoding UTF-8 -source 8 -target 8 -d $classes `
        (Join-Path $androidRoot 'app\src\main\java\ru\owrthub\app\HubUrlPolicy.java') `
        (Join-Path $PSScriptRoot 'HubUrlPolicyTest.java')
    if ($LASTEXITCODE -ne 0) { throw 'Policy tests did not compile.' }
    & $runtime -cp $classes ru.owrthub.app.HubUrlPolicyTest
    if ($LASTEXITCODE -ne 0) { throw 'Policy tests failed.' }
}
finally {
    if (Test-Path -LiteralPath $classes) {
        $resolvedClasses = (Resolve-Path -LiteralPath $classes).ProviderPath
        $resolvedTemp = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\')
        if ([System.IO.Path]::GetDirectoryName($resolvedClasses) -ne $resolvedTemp -or
            [System.IO.Path]::GetFileName($resolvedClasses) -notmatch '^owrt-policy-tests-[0-9a-f]{32}$') {
            throw 'Refusing to remove a directory outside the policy test temporary path.'
        }
        Remove-Item -LiteralPath $resolvedClasses -Recurse -Force
    }
}
