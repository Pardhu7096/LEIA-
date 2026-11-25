<#
.SYNOPSIS  Build the LEIA Agent CRM APK (com.leiarealestate.agent). See build-android.ps1.
.EXAMPLE   .\build-agent.ps1 -Commit c84a473 -ApiUrl https://leia-backend-staging.onrender.com -DryRun
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Commit,
    [Parameter(Mandatory = $true)][string]$ApiUrl,
    [ValidateSet('debug', 'release')][string]$Signing = 'debug',
    [string]$KeystorePath, [string]$KeyAlias, [switch]$DryRun
)
& (Join-Path $PSScriptRoot 'build-android.ps1') -App agent @PSBoundParameters
