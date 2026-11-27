param(
    [string]$Vps = '',
    [string]$Router = '',
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [switch]$PrepareOnly
)
$ErrorActionPreference = 'Stop'
if (-not $Vps -and -not $Router) { $Vps = 'root@13.143.167.219' }
if ($Vps) {
    & (Join-Path $PSScriptRoot 'deploy-native-mobile-api.ps1') -Vps $Vps -Action $Action `
        -BackupName 'owrt-remote-backup-before-model-alias-v125' -PrepareOnly:$PrepareOnly
}
if ($Router) {
    & (Join-Path $PSScriptRoot 'deploy-router-panel-update.ps1') -Router $Router -Action $Action `
        -BackupName 'owrt-remote-backup-before-update-amber-v125' -PrepareOnly:$PrepareOnly
}
