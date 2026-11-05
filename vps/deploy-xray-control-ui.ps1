param(
    [Parameter(Mandatory = $true)][string]$Vps,
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'deploy-xray-control.ps1') -Vps $Vps -Action $Action -BackupName 'xray-control-ui' -PrepareOnly:$PrepareOnly
