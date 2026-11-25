<#
.SYNOPSIS
  Read-only inspection of a LEIA Android APK: identity, SDK levels, ABIs, permissions,
  signing certificate, baked API host and SHA-256. Never modifies the APK.
.EXAMPLE
  .\inspect-apk.ps1 -Apk C:\Users\me\Downloads\LEIA-Customer-Demo.apk -Json
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Apk,
    [string]$ToolchainFile = (Join-Path $PSScriptRoot 'toolchain.json'),
    [switch]$Json
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

if (-not (Test-Path -LiteralPath $Apk -PathType Leaf)) { throw "APK not found: $Apk" }
$tc = Get-Content -LiteralPath $ToolchainFile -Raw | ConvertFrom-Json
$sdk = if ($env:ANDROID_HOME) { $env:ANDROID_HOME } else { $tc.android_sdk.default_root }
$bt = Join-Path $sdk ("build-tools\" + $tc.android_sdk.build_tools)
$aapt = Join-Path $bt 'aapt.exe'
$apksignerJar = Join-Path $bt 'lib\apksigner.jar'
$javaHome = if ($env:LEIA_JAVA_HOME) { $env:LEIA_JAVA_HOME } elseif ($env:JAVA_HOME -and (Test-Path (Join-Path $env:JAVA_HOME 'bin\java.exe'))) { $env:JAVA_HOME } else { $tc.java.default_home }
$java = Join-Path $javaHome 'bin\java.exe'
foreach ($tool in @($aapt, $apksignerJar, $java)) {
    if (-not (Test-Path -LiteralPath $tool)) { throw "Required tool missing: $tool" }
}

function Invoke-Tool([scriptblock]$Command) {
    # Windows PowerShell 5.1: native stderr (warnings, "Picked up ..." notices) must not
    # abort under ErrorActionPreference=Stop. Returns stdout+stderr lines as strings.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { $out = @(& $Command 2>&1 | ForEach-Object { "$_" }); $script:toolExit = $LASTEXITCODE }
    finally { $ErrorActionPreference = $previous }
    return $out
}
$badging = Invoke-Tool { & $aapt dump badging $Apk }
if ($script:toolExit -ne 0) { throw "aapt could not read $Apk" }
function Get-Match([string[]]$lines, [string]$pattern) {
    foreach ($l in $lines) { $m = [regex]::Match($l, $pattern); if ($m.Success) { return $m.Groups[1].Value } }
    return $null
}
$pkgLine = ($badging | Where-Object { $_ -like 'package:*' } | Select-Object -First 1)
$abis = @()
$nativeLine = ($badging | Where-Object { $_ -like 'native-code:*' } | Select-Object -First 1)
if ($nativeLine) { $abis = @([regex]::Matches($nativeLine, "'([^']+)'") | ForEach-Object { $_.Groups[1].Value }) }
$perms = @(Invoke-Tool { & $aapt dump permissions $Apk } | ForEach-Object {
        $m = [regex]::Match($_, "uses-permission: name='([^']+)'"); if ($m.Success) { $m.Groups[1].Value } })
$forbidden = @($perms | Where-Object { $tc.forbidden_permissions -contains $_ })

$sig = Invoke-Tool { & $java -jar $apksignerJar verify --verbose --print-certs $Apk }
$verifies = [bool]($sig | Where-Object { $_ -eq 'Verifies' })
$signers = @($sig | Where-Object { $_ -match '^Signer #\d+ certificate SHA-256 digest: ' } | ForEach-Object { ($_ -split ': ')[1].Trim() })
$sha1 = @($sig | Where-Object { $_ -match '^Signer #\d+ certificate SHA-1 digest: ' } | ForEach-Object { ($_ -split ': ')[1].Trim() })
$dn = Get-Match $sig '^Signer #1 certificate DN: (.+)$'
$schemes = @($sig | Where-Object { $_ -match '^Verified using (v[\d.]+) scheme .*: true$' } | ForEach-Object { [regex]::Match($_, '(v[\d.]+)').Value })

# Baked API host(s) from the Hermes bundle (strings only; no execution).
Add-Type -AssemblyName System.IO.Compression.FileSystem
$hosts = @()
$zip = [System.IO.Compression.ZipFile]::OpenRead((Resolve-Path -LiteralPath $Apk).Path)
try {
    $entry = $zip.GetEntry('assets/index.android.bundle')
    if ($entry) {
        $ms = New-Object System.IO.MemoryStream
        $s = $entry.Open(); $s.CopyTo($ms); $s.Dispose()
        $text = [System.Text.Encoding]::GetEncoding(28591).GetString($ms.ToArray())
        $hosts = @([regex]::Matches($text, 'https://([a-z0-9-]+\.onrender\.com)') | ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique)
    }
} finally { $zip.Dispose() }

$knownDebug = $tc.signing.debug_public_cert_sha256
$result = [ordered]@{
    file                = (Split-Path -Leaf $Apk)
    sha256              = (Get-FileHash -LiteralPath $Apk -Algorithm SHA256).Hash.ToLower()
    size_bytes          = (Get-Item -LiteralPath $Apk).Length
    application_id      = Get-Match @($pkgLine) "name='([^']+)'"
    version_code        = [int](Get-Match @($pkgLine) "versionCode='(\d+)'")
    version_name        = Get-Match @($pkgLine) "versionName='([^']+)'"
    label               = Get-Match $badging "^application-label:'(.+)'$"
    min_sdk             = [int](Get-Match $badging "^sdkVersion:'(\d+)'")
    target_sdk          = [int](Get-Match $badging "^targetSdkVersion:'(\d+)'")
    compile_sdk         = [int](Get-Match @($pkgLine) "compileSdkVersion='(\d+)'")
    abis                = $abis
    permissions         = $perms
    forbidden_permissions_found = $forbidden
    signature_verifies  = $verifies
    signing_schemes     = $schemes
    signer_cert_sha256  = $signers
    signer_cert_sha1    = $sha1
    signer_dn           = $dn
    signed_with_public_debug_key = ($signers -contains $knownDebug)
    baked_api_hosts     = $hosts
}
if ($Json) { $result | ConvertTo-Json -Depth 4 } else { [pscustomobject]$result }
