param(
    [ValidateNotNullOrEmpty()][string]$Vps = 'root@13.143.167.219',
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [switch]$PrepareOnly
)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'deploy-native-mobile-api.ps1') -Vps $Vps -Action $Action `
    -BackupName 'owrt-remote-backup-before-ios-install-gate-v125' -PrepareOnly:$PrepareOnly
