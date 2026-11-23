param(
    [ValidateNotNullOrEmpty()][string]$Vps = 'root@132.243.19.232',
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'
# Keep this preview's first backup separate from earlier card and donation fixes.
& (Join-Path $PSScriptRoot 'deploy-tachyon.ps1') -Vps $Vps -Action $Action `
    -BackupName sidebar-ui -BackupVersion v122 -PrepareOnly:$PrepareOnly
if (-not $PrepareOnly) { Write-Host "Hub sidebar UI $Action finished successfully." }
