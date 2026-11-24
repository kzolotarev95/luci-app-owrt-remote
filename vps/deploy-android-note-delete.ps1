param(
    [ValidateNotNullOrEmpty()][string]$Vps = 'root@13.143.167.219',
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [switch]$PrepareOnly
)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'deploy-native-mobile-api.ps1') -Vps $Vps -Action $Action `
    -BackupName 'owrt-remote-backup-before-android-note-delete-024-v123' -PrepareOnly:$PrepareOnly
