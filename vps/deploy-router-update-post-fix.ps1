param(
    [ValidateNotNullOrEmpty()][string]$Router = 'root@192.168.2.1',
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [switch]$PrepareOnly
)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'deploy-router-panel-update.ps1') -Router $Router -Action $Action `
    -BackupName 'owrt-remote-backup-before-router-update-post-v125' -PrepareOnly:$PrepareOnly
