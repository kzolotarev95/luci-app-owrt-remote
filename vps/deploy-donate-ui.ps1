param(
    [string]$Vps = 'root@132.243.19.232',
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'deploy-tachyon.ps1') -Vps $Vps -Action $Action `
    -BackupName donate-ui -BackupVersion v122 -PrepareOnly:$PrepareOnly
if (-not $PrepareOnly) { Write-Host "Donate UI $Action finished successfully." }
