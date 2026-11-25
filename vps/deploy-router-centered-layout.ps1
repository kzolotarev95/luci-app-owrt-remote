param(
    [ValidateNotNullOrEmpty()][string]$Router = 'root@192.168.2.1',
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [switch]$PrepareOnly
)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'deploy-router-sidebar-ui.ps1') -Router $Router -Action $Action `
    -BackupName 'owrt-remote-backup-before-router-centered-layout-v123' -PrepareOnly:$PrepareOnly
