[CmdletBinding(DefaultParameterSetName = 'Vps')]
param(
    [Parameter(ParameterSetName = 'Vps')][ValidateNotNullOrEmpty()][string]$Vps = 'root@13.143.167.219',
    [Parameter(Mandatory = $true, ParameterSetName = 'Router')][ValidateNotNullOrEmpty()][string]$Router,
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [switch]$PrepareOnly
)
$ErrorActionPreference = 'Stop'
$Options = @{ Action = $Action; PrepareOnly = $PrepareOnly; BackupName = 'owrt-remote-backup-before-vps-host-heartbeat-v124' }
if ($PSCmdlet.ParameterSetName -eq 'Router') { $Options.Router = $Router }
else { $Options.Vps = $Vps }
& (Join-Path $PSScriptRoot 'deploy-remove-global-logs.ps1') @Options
