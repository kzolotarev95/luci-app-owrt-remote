param(
    [Parameter(Mandatory = $true, ParameterSetName = 'Vps')][ValidateNotNullOrEmpty()][string]$Vps,
    [Parameter(Mandatory = $true, ParameterSetName = 'Router')][ValidateNotNullOrEmpty()][string]$Router,
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'
if ($PSCmdlet.ParameterSetName -eq 'Router') {
    & (Join-Path $PSScriptRoot 'deploy-router-branding.ps1') -Router $Router -Action $Action -PrepareOnly:$PrepareOnly
} else {
    & (Join-Path $PSScriptRoot 'deploy-version-v123.ps1') -Vps $Vps -Action $Action -PrepareOnly:$PrepareOnly
}
