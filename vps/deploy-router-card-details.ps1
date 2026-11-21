param(
    [string]$Vps = 'root@132.243.19.232',
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'
# The shared VPS installer validates the source, keeps an independent backup,
# replaces Hub atomically and verifies the running source revision after restart.
& (Join-Path $PSScriptRoot 'deploy-tachyon.ps1') -Vps $Vps -Action $Action `
    -BackupName router-card-details -BackupVersion v122 -PrepareOnly:$PrepareOnly
if (-not $PrepareOnly) { Write-Host "Router card details $Action finished successfully." }
