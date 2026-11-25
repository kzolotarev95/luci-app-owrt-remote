[CmdletBinding(DefaultParameterSetName = 'Vps')]
param(
    [Parameter(ParameterSetName = 'Vps')][ValidateNotNullOrEmpty()][string]$Vps = 'root@13.143.167.219',
    [Parameter(Mandatory = $true, ParameterSetName = 'Router')][ValidateNotNullOrEmpty()][string]$Router,
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [switch]$PrepareOnly
)
$ErrorActionPreference = 'Stop'
if ($PSCmdlet.ParameterSetName -eq 'Router') {
    & (Join-Path $PSScriptRoot 'deploy-remove-global-logs.ps1') -Router $Router -Action $Action -PrepareOnly:$PrepareOnly
} else {
    & (Join-Path $PSScriptRoot 'deploy-native-mobile-api.ps1') -Vps $Vps -Action $Action `
        -BackupName 'owrt-remote-backup-before-hub-v124' -PrepareOnly:$PrepareOnly
}
if ($PrepareOnly) { Write-Host 'OWRT Hub v124: files prepared; device not changed.' }
else { Write-Host "OWRT Hub v124 $Action completed. Refresh the panel." }
