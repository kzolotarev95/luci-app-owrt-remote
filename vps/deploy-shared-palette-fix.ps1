param(
    [ValidateNotNullOrEmpty()][string]$Vps = 'root@13.143.167.219',
    [string]$Router,
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [switch]$PrepareOnly
)
$ErrorActionPreference = 'Stop'
if ($Router) {
    & (Join-Path $PSScriptRoot 'deploy-router-sidebar-ui.ps1') -Router $Router -Action $Action `
        -BackupName 'owrt-remote-backup-before-shared-palette-v125' -PrepareOnly:$PrepareOnly
} else {
    & (Join-Path $PSScriptRoot 'deploy-native-mobile-api.ps1') -Vps $Vps -Action $Action `
        -BackupName 'owrt-remote-backup-before-shared-palette-v125' -PrepareOnly:$PrepareOnly
}
