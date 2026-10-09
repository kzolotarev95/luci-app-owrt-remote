param(
    [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$Router,
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [switch]$PrepareOnly
)

# The picker and failover backend must always be installed together.
& (Join-Path $PSScriptRoot 'deploy-wan-multi.ps1') @PSBoundParameters
