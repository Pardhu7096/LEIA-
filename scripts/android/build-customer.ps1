<#
.SYNOPSIS  Build the LEIA Customer APK (com.leiarealestate.customer). See build-android.ps1.
.EXAMPLE   .\build-customer.ps1 -Commit c84a473 -ApiUrl https://leia-backend-staging.onrender.com -DryRun
.EXAMPLE   .\build-customer.ps1 -Commit <sha> -ApiUrl <url> -OutputRoot D:\LEIA-Releases\builds-validation
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Commit,
    [Parameter(Mandatory = $true)][string]$ApiUrl,
    [ValidateSet('debug', 'release')][string]$Signing = 'debug',
    [string]$KeystorePath, [string]$KeyAlias,
    [string]$RepoPath, [string]$OutputRoot,
    [switch]$DryRun
)
# Only parameters the caller actually passed are forwarded (@PSBoundParameters), so
# build-android.ps1 keeps its own defaults for RepoPath and OutputRoot.
& (Join-Path $PSScriptRoot 'build-android.ps1') -App customer @PSBoundParameters
