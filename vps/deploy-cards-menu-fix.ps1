[CmdletBinding(DefaultParameterSetName = 'Vps')]
param(
    [Parameter(ParameterSetName = 'Vps')][ValidateNotNullOrEmpty()][string]$Vps = 'root@13.143.167.219',
    [Parameter(Mandatory = $true, ParameterSetName = 'Router')][ValidateNotNullOrEmpty()][string]$Router,
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [switch]$PrepareOnly
)
$ErrorActionPreference = 'Stop'
if ($PSCmdlet.ParameterSetName -eq 'Router') {
    & (Join-Path $PSScriptRoot 'deploy-router-sidebar-ui.ps1') -Router $Router -Action $Action `
        -BackupName 'owrt-remote-backup-before-router-menu-state-v123' -PrepareOnly:$PrepareOnly
} else {
    & (Join-Path $PSScriptRoot 'deploy-native-mobile-api.ps1') -Vps $Vps -Action $Action `
        -BackupName 'owrt-remote-backup-before-cards-menu-state-v123' -PrepareOnly:$PrepareOnly
}
