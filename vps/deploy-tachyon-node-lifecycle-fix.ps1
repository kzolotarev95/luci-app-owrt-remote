[CmdletBinding(DefaultParameterSetName = 'Vps')]
param(
    [Parameter(ParameterSetName = 'Vps')][string]$Vps = 'root@13.143.167.219',
    [Parameter(Mandatory = $true, ParameterSetName = 'Router')][string]$Router,
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [switch]$PrepareOnly
)
$ErrorActionPreference = 'Stop'
if ($PSCmdlet.ParameterSetName -eq 'Router') {
    & (Join-Path $PSScriptRoot 'deploy-tachyon.ps1') -Router $Router -Action $Action `
        -BackupName 'tachyon-node-lifecycle' -BackupVersion 'v125' -PrepareOnly:$PrepareOnly
} else {
    & (Join-Path $PSScriptRoot 'deploy-native-mobile-api.ps1') -Vps $Vps -Action $Action `
        -BackupName 'owrt-remote-backup-before-tachyon-node-lifecycle-v125' -PrepareOnly:$PrepareOnly
}
