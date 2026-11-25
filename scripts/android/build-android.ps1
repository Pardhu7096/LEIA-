<#
.SYNOPSIS
  Reproducible release-APK build for a LEIA Android app (agent | customer).

.DESCRIPTION
  Builds from a clean `git archive` of an exact commit (never from a working tree),
  in a short path, with the pinned toolchain in toolchain.json. Refuses to run on a
  toolchain mismatch. Verifies the resulting APK and writes build-manifest.json next
  to it. Never overwrites an existing output folder. Never writes signing secrets
  to disk or logs.

  -DryRun checks the toolchain, resolves the commit, exports and validates the
  source, and prints the plan without installing packages or running Gradle.

.EXAMPLE
  .\build-android.ps1 -App customer -Commit c84a473 -ApiUrl https://leia-backend-staging.onrender.com -DryRun
.EXAMPLE
  .\build-android.ps1 -App customer -Commit c84a473 -ApiUrl https://leia-backend-staging.onrender.com
.EXAMPLE
  # Release signing (only after the owner has created the keystore; see docs/android/SIGNING.md)
  $env:LEIA_KEYSTORE_PASSWORD = '<from password manager>'   # set in this window only
  .\build-android.ps1 -App customer -Commit <sha> -ApiUrl <url> -Signing release -KeystorePath E:\leia-release.jks -KeyAlias leia-upload
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet('agent', 'customer')][string]$App,
    [Parameter(Mandatory = $true)][string]$Commit,
    [Parameter(Mandatory = $true)][ValidatePattern('^https://[a-z0-9.-]+(:\d+)?$')][string]$ApiUrl,
    [ValidateSet('debug', 'release')][string]$Signing = 'debug',
    [string]$KeystorePath,
    [string]$KeyAlias,
    [string]$RepoPath = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$OutputRoot = 'D:\LEIA-Releases\builds',
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
$started = [DateTime]::UtcNow

$script:cleanupOnFail = $null
function Fail([string]$msg) {
    # Validation failures before the build remove their own export folder.
    if ($script:cleanupOnFail -and (Test-Path -LiteralPath $script:cleanupOnFail)) {
        Remove-Item -LiteralPath $script:cleanupOnFail -Recurse -Force
    }
    throw "BUILD STOPPED: $msg"
}
function Get-Prop($obj, [string]$path) {
    # StrictMode-safe nested property lookup: returns $null when any part is missing.
    $cur = $obj
    foreach ($part in $path.Split('.')) {
        if ($null -eq $cur -or -not ($cur.PSObject.Properties.Name -contains $part)) { return $null }
        $cur = $cur.$part
    }
    return $cur
}
function Step([string]$msg) { Write-Host "==> $msg" }
function Get-Sha256([string]$path) { (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLower() }

$tcPath = Join-Path $PSScriptRoot 'toolchain.json'
$tc = Get-Content -LiteralPath $tcPath -Raw | ConvertFrom-Json
$appCfg = $tc.apps.$App
if (-not $appCfg) { Fail "unknown app $App" }

# ------------------------------------------------------------------ toolchain
Step 'Checking pinned toolchain'
$javaHome = if ($env:LEIA_JAVA_HOME) { $env:LEIA_JAVA_HOME } elseif ($env:JAVA_HOME) { $env:JAVA_HOME } else { $tc.java.default_home }
$javaExe = Join-Path $javaHome 'bin\java.exe'
if (-not (Test-Path -LiteralPath $javaExe)) { Fail "JAVA_HOME '$javaHome' has no bin\java.exe (expected $($tc.java.default_home))" }
$releaseFile = Join-Path $javaHome 'release'
$javaRuntime = if (Test-Path $releaseFile) { (Select-String -LiteralPath $releaseFile -Pattern '^JAVA_RUNTIME_VERSION="(.+)"').Matches[0].Groups[1].Value } else { '' }
if ($javaRuntime -ne $tc.java.runtime) { Fail "Java runtime is '$javaRuntime', pinned '$($tc.java.runtime)'" }

$nodeVersion = (& node --version).Trim().TrimStart('v')
if ($nodeVersion -ne $tc.node) { Fail "Node is $nodeVersion, pinned $($tc.node)" }
$npmVersion = (& npm --version).Trim()

$sdkRoot = if ($env:ANDROID_HOME) { $env:ANDROID_HOME } else { $tc.android_sdk.default_root }
$required = @(
    "platforms\$($tc.android_sdk.platform)",
    "build-tools\$($tc.android_sdk.build_tools)",
    "ndk\$($tc.android_sdk.ndk)",
    "cmake\$($tc.android_sdk.cmake)"
)
if ($tc.android_sdk.PSObject.Properties.Name -contains 'additional_build_tools') {
    foreach ($v in $tc.android_sdk.additional_build_tools) { $required += "build-tools\$v" }
}
foreach ($rel in $required) {
    if (-not (Test-Path -LiteralPath (Join-Path $sdkRoot $rel))) { Fail "Android SDK component missing: $sdkRoot\$rel" }
}
foreach ($dir in @($tc.caches.gradle_user_home, $tc.caches.npm_cache, $tc.caches.short_build_root, $OutputRoot)) {
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
}

# ------------------------------------------------------------------ signing inputs
if ($Signing -eq 'release') {
    if (-not $KeystorePath -or -not $KeyAlias) { Fail 'release signing needs -KeystorePath and -KeyAlias' }
    if (-not (Test-Path -LiteralPath $KeystorePath -PathType Leaf)) { Fail "keystore not found: $KeystorePath" }
    $ksFull = (Resolve-Path -LiteralPath $KeystorePath).Path
    if ($ksFull.StartsWith($RepoPath, [StringComparison]::OrdinalIgnoreCase)) { Fail 'keystore must not live inside the Git repository' }
    if (-not $env:LEIA_KEYSTORE_PASSWORD) { Fail 'set LEIA_KEYSTORE_PASSWORD in this window (never in files)' }
    if (-not $tc.signing.release_cert_sha256) { Fail 'toolchain.json has no release_cert_sha256; record the approved fingerprint first' }
    $expectedSigner = $tc.signing.release_cert_sha256.ToLower()
} else {
    $expectedSigner = $tc.signing.debug_public_cert_sha256.ToLower()
}

# ------------------------------------------------------------------ source
Step "Resolving commit $Commit"
$sha = (& git -C $RepoPath rev-parse --verify "$Commit^{commit}" 2>$null)
if ($LASTEXITCODE -ne 0 -or -not $sha) { Fail "commit '$Commit' not found in $RepoPath (fetch first)" }
$sha = $sha.Trim(); $short = $sha.Substring(0, 7)
$stamp = $started.ToString('yyyyMMddTHHmmssZ')
# Native (CMake/ninja) builds embed the absolute source path twice in object paths, and
# ninja is limited to 260-character Windows paths. The first e7c585a build failed at 269
# characters with D:\LB\customer-e7c585a-<UTC>\frontend\. Keep the project root tiny:
# D:\LB\<a|c><4 hex>[n]\s (about 14 characters). Descriptive names go in the manifest.
$baseName = '{0}{1}' -f $App.Substring(0, 1), $short.Substring(0, 4)
$work = Join-Path $tc.caches.short_build_root $baseName
$n = 1
while (Test-Path -LiteralPath $work) {
    $n++
    if ($n -gt 9) { Fail "too many work folders for $baseName in $($tc.caches.short_build_root); remove old ones first" }
    $work = Join-Path $tc.caches.short_build_root "$baseName$n"
}
$src = Join-Path $work 's'
$maxSrcLength = 16
if ($src.Length -gt $maxSrcLength) { Fail "project path '$src' is $($src.Length) characters; native builds need <= $maxSrcLength (Windows 260-character limit in ninja)" }
New-Item -ItemType Directory -Path $work | Out-Null
$script:cleanupOnFail = $work
$zipPath = Join-Path $work 'source.zip'
# Archive the app folder's tree so its contents land directly in $src (no extra path segment).
& git -C $RepoPath archive --format=zip -o $zipPath "$($sha):$($appCfg.dir)"
if ($LASTEXITCODE -ne 0) { Fail 'git archive failed' }
Expand-Archive -LiteralPath $zipPath -DestinationPath $src
Remove-Item -LiteralPath $zipPath
Set-Content -LiteralPath (Join-Path $work 'BUILD-INFO.txt') -Value "app=$App commit=$sha started_utc=$stamp" -Encoding ASCII

Step 'Validating app.json identity'
$appJson = (Get-Content -LiteralPath (Join-Path $src 'app.json') -Raw | ConvertFrom-Json).expo
$pkgName = Get-Prop $appJson 'android.package'
$vcRaw = Get-Prop $appJson 'android.versionCode'
if ($pkgName -ne $appCfg.application_id) { Fail "android.package in $($appCfg.dir)/app.json at $short is '$pkgName', expected '$($appCfg.application_id)' (commit predates GEO-M0?)" }
if ($appJson.name -ne $appCfg.display_name) { Fail "name is '$($appJson.name)', expected '$($appCfg.display_name)'" }
if ($null -eq $vcRaw) { Fail "android.versionCode missing in $($appCfg.dir)/app.json at $short" }
$versionCode = [int]$vcRaw
if ($versionCode -lt [int]$appCfg.min_version_code) { Fail "versionCode $versionCode is below $($appCfg.min_version_code) (installed demos use $($appCfg.original_demo.version_code))" }

$deps = & node -e @"
const l=require(process.argv[1]).packages;const n=['expo','react-native','react','expo-modules-core','hermes-compiler'];
const o={};for(const k of n){const p=l['node_modules/'+k];if(p)o[k]=p.version;}console.log(JSON.stringify(o));
"@ (Join-Path $src 'package-lock.json') | ConvertFrom-Json
if ($deps.expo -ne $appCfg.expo) { Fail "expo in lockfile is $($deps.expo), pinned $($appCfg.expo)" }
if ($deps.'react-native' -ne $appCfg.react_native) { Fail "react-native in lockfile is $($deps.'react-native'), pinned $($appCfg.react_native)" }
$lockSha = Get-Sha256 (Join-Path $src 'package-lock.json')

$plan = [ordered]@{
    app = $App; commit = $sha; api_url = $ApiUrl; signing = $Signing; expected_signer_sha256 = $expectedSigner
    application_id = $appCfg.application_id; version_name = $appJson.version; version_code = $versionCode
    java = $javaRuntime; node = $nodeVersion; npm = $npmVersion; sdk_root = $sdkRoot; work = $work
}
if ($DryRun) {
    Step 'DRY RUN: toolchain and source validated; no packages installed, nothing built'
    [pscustomobject]$plan | Format-List | Out-String | Write-Host
    Remove-Item -LiteralPath $work -Recurse -Force
    return
}
# From here on a failed build keeps its folder so build.log can be inspected.
$script:cleanupOnFail = $null

# ------------------------------------------------------------------ build
$env:JAVA_HOME = $javaHome
$env:ANDROID_HOME = $sdkRoot; $env:ANDROID_SDK_ROOT = $sdkRoot
$env:GRADLE_USER_HOME = $tc.caches.gradle_user_home
$env:npm_config_cache = $tc.caches.npm_cache
$env:EXPO_PUBLIC_API_URL = $ApiUrl
$env:NODE_ENV = 'production'; $env:CI = '1'
# Keep temporary files on the build drive (Java and Node take their temp dir from TMP/TEMP on Windows).
$tmpDir = Join-Path $work 'tmp'
New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null
$env:TMP = $tmpDir; $env:TEMP = $tmpDir
$log = Join-Path $work 'build.log'

$cmakeObjectPathMax = 250
$cmakeStagingDir = (Join-Path $work 'n') -replace '\\', '/'

function Set-CMakePathLimits([string]$GradleFile, [string]$StagingDir, [int]$ObjectPathMax) {
    # The app module's native build (React Native codegen for libraries such as
    # react-native-gesture-handler) produces object paths over Windows' 260-character limit
    # even from a short root (about 327 chars from D:\LB\ce7c5\s). The only successful
    # build (2026-11-24) needed both settings below, edited into the generated file by hand.
    # Apply them deterministically, and refuse if the template no longer matches.
    $text = [IO.File]::ReadAllText($GradleFile)
    if ($text -match 'CMAKE_OBJECT_PATH_MAX|buildStagingDirectory') {
        throw "app/build.gradle already contains CMake path settings; the Expo template changed"
    }
    $androidRx = '(?m)^android \{\r?$'
    $defaultRx = '(?m)^    defaultConfig \{\r?$'
    $a = [regex]::Matches($text, $androidRx).Count
    $d = [regex]::Matches($text, $defaultRx).Count
    if ($a -ne 1 -or $d -ne 1) { throw "app/build.gradle layout changed (android blocks: $a, defaultConfig blocks: $d)" }
    $nl = if ($text.Contains("`r`n")) { "`r`n" } else { "`n" }
    $androidBlock = "android {$nl    externalNativeBuild {$nl        cmake {$nl            buildStagingDirectory `"$StagingDir`"$nl        }$nl    }"
    $defaultBlock = "    defaultConfig {$nl        externalNativeBuild {$nl            cmake {$nl                arguments `"-DCMAKE_OBJECT_PATH_MAX=$ObjectPathMax`"$nl            }$nl        }"
    $text = [regex]::Replace($text, $androidRx, $androidBlock)
    $text = [regex]::Replace($text, $defaultRx, $defaultBlock)
    [IO.File]::WriteAllText($GradleFile, $text, (New-Object System.Text.UTF8Encoding($false)))
}

function Invoke-Logged([string]$Label, [scriptblock]$Command) {
    # Windows PowerShell 5.1 turns a native tool's stderr lines (compiler notes, warnings)
    # into error records; with ErrorActionPreference=Stop that aborts the script even when
    # the tool succeeds. Log everything and judge success only by the exit code.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Command *>> $log
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    if ($code -ne 0) { Fail "$Label failed with exit code $code (see $log)" }
}

Push-Location $src
try {
    Step 'npm ci'
    Invoke-Logged 'npm ci' { & npm ci --no-audit --no-fund }
    Step 'expo prebuild (android)'
    Invoke-Logged 'expo prebuild' { & npx expo prebuild --platform android --clean --no-install }

    Step 'Pinning Gradle wrapper'
    $wrapper = Join-Path $src 'android\gradle\wrapper\gradle-wrapper.properties'
    $wp = Get-Content -LiteralPath $wrapper
    $url = ($wp | Where-Object { $_ -like 'distributionUrl=*' }) -replace '^distributionUrl=', '' -replace '\\:', ':'
    if ($url -ne $tc.gradle.distribution_url) { Fail "Gradle wrapper wants $url, pinned $($tc.gradle.distribution_url)" }
    $wp = @($wp | Where-Object { $_ -notlike 'distributionSha256Sum=*' }) + "distributionSha256Sum=$($tc.gradle.distribution_sha256)"
    Set-Content -LiteralPath $wrapper -Value $wp -Encoding ASCII

    Step "Applying CMake path limits (object path max $cmakeObjectPathMax, staging $cmakeStagingDir)"
    try { Set-CMakePathLimits (Join-Path $src 'android\app\build.gradle') $cmakeStagingDir $cmakeObjectPathMax }
    catch { Fail $_.Exception.Message }

    # Bounded parallelism: on the 7.9 GB build machine the default worker count exhausted
    # virtual memory (clang crashes, "Insufficient system resources"). Values are pinned in
    # toolchain.json and match the only successful build (2026-11-24).
    $maxWorkers = [int](Get-Prop $tc 'gradle.max_workers')
    $kotlinStrategy = Get-Prop $tc 'gradle.kotlin_compiler_execution_strategy'
    if ($maxWorkers -lt 1 -or -not $kotlinStrategy) { Fail 'toolchain.json gradle.max_workers / kotlin_compiler_execution_strategy missing' }
    Step "Gradle assembleRelease (arm64-v8a, max-workers $maxWorkers, Kotlin $kotlinStrategy)"
    Push-Location (Join-Path $src 'android')
    try {
        $abiArg = "-PreactNativeArchitectures=$($tc.android_app.abi)"
        $workersArg = "--max-workers=$maxWorkers"
        $kotlinArg = "-Pkotlin.compiler.execution.strategy=$kotlinStrategy"
        Invoke-Logged 'Gradle assembleRelease' { & .\gradlew.bat ':app:assembleRelease' '--no-daemon' '--console=plain' $workersArg $kotlinArg $abiArg }
    } finally { Pop-Location }
} finally { Pop-Location }

$apk = Join-Path $src 'android\app\build\outputs\apk\release\app-release.apk'
if (-not (Test-Path -LiteralPath $apk)) { Fail 'app-release.apk not produced' }

if ($Signing -eq 'release') {
    Step 'Re-signing with the release key (passwords from environment only)'
    $bt = Join-Path $sdkRoot "build-tools\$($tc.android_sdk.build_tools)"
    $signed = Join-Path $work 'app-release-signed.apk'
    $apksignerJar = Join-Path $bt 'lib\apksigner.jar'
    $unsigned = $apk
    Invoke-Logged 'apksigner sign' {
        & $javaExe -jar $apksignerJar sign --ks $ksFull --ks-key-alias $KeyAlias `
            --ks-pass env:LEIA_KEYSTORE_PASSWORD --key-pass env:LEIA_KEYSTORE_PASSWORD `
            --v1-signing-enabled false --v2-signing-enabled true --v3-signing-enabled true --out $signed $unsigned
    }
    $apk = $signed
}

# ------------------------------------------------------------------ verify
Step 'Verifying APK'
$info = & (Join-Path $PSScriptRoot 'inspect-apk.ps1') -Apk $apk -ToolchainFile $tcPath
$apiHost = ([Uri]$ApiUrl).Host
$problems = @()
if ($info.application_id -ne $appCfg.application_id) { $problems += "application_id $($info.application_id)" }
if ($info.version_code -ne $versionCode) { $problems += "versionCode $($info.version_code)" }
if ($info.label -ne $appCfg.display_name) { $problems += "label $($info.label)" }
if ($info.min_sdk -ne $tc.android_app.min_sdk -or $info.target_sdk -ne $tc.android_app.target_sdk) { $problems += "sdk $($info.min_sdk)/$($info.target_sdk)" }
if (($info.abis -join ',') -ne $tc.android_app.abi) { $problems += "abis $($info.abis -join ',')" }
if ($info.forbidden_permissions_found.Count -gt 0) { $problems += "forbidden permissions $($info.forbidden_permissions_found -join ',')" }
if (-not $info.signature_verifies) { $problems += 'signature does not verify' }
if (-not ($info.signer_cert_sha256 -contains $expectedSigner)) { $problems += "signer $($info.signer_cert_sha256 -join ',')" }
if (-not ($info.baked_api_hosts -contains $apiHost)) { $problems += "API host $apiHost not baked in ($($info.baked_api_hosts -join ','))" }
if ($problems.Count) { Fail ("APK verification failed: " + ($problems -join '; ')) }

# ------------------------------------------------------------------ publish locally
$outDir = Join-Path $OutputRoot "$App\$($appJson.version)-vc$versionCode-$short-$Signing"
if (Test-Path -LiteralPath $outDir) { Fail "output folder already exists (never overwritten): $outDir" }
New-Item -ItemType Directory -Path $outDir | Out-Null
$apkName = "LEIA-$App-$($appJson.version)-vc$versionCode-$short-$Signing.apk"
Copy-Item -LiteralPath $apk -Destination (Join-Path $outDir $apkName)
$mapping = Join-Path $src 'android\app\build\outputs\mapping\release\mapping.txt'
if (Test-Path -LiteralPath $mapping) { Copy-Item -LiteralPath $mapping -Destination (Join-Path $outDir 'mapping.txt') }
Copy-Item -LiteralPath $log -Destination (Join-Path $outDir 'build.log')

$manifest = [ordered]@{
    schema           = 1
    app              = $App
    application_id   = $info.application_id
    display_name     = $info.label
    version_name     = $info.version_name
    version_code     = $info.version_code
    source           = [ordered]@{ commit = $sha; requested = $Commit; repository = (& git -C $RepoPath remote get-url origin) }
    api_environment  = [ordered]@{ url = $ApiUrl; label = $(if ($apiHost -like '*staging*') { 'staging' } else { 'other' }) }
    toolchain        = [ordered]@{
        node = $nodeVersion; npm = $npmVersion; java = $javaRuntime; gradle = $tc.gradle.version
        gradle_distribution_sha256 = $tc.gradle.distribution_sha256
        android_platform = $tc.android_sdk.platform; build_tools = $tc.android_sdk.build_tools
        ndk = $tc.android_sdk.ndk; cmake = $tc.android_sdk.cmake
    }
    dependencies     = [ordered]@{ expo = $deps.expo; react_native = $deps.'react-native'; react = $deps.react; expo_modules_core = $deps.'expo-modules-core'; hermes_compiler = $deps.'hermes-compiler'; package_lock_sha256 = $lockSha }
    signing          = [ordered]@{ mode = $Signing; cert_sha256 = $info.signer_cert_sha256; cert_sha1 = $info.signer_cert_sha1; schemes = $info.signing_schemes; public_debug_key = $info.signed_with_public_debug_key }
    apk              = [ordered]@{ file = $apkName; sha256 = $info.sha256; size_bytes = $info.size_bytes; abis = $info.abis; min_sdk = $info.min_sdk; target_sdk = $info.target_sdk; compile_sdk = $info.compile_sdk; permissions = $info.permissions }
    build            = [ordered]@{ work_dir = $work; source_dir = $src; cmake_object_path_max = $cmakeObjectPathMax; cmake_staging_dir = $cmakeStagingDir; gradle_max_workers = $maxWorkers; kotlin_execution = $kotlinStrategy; started_utc = $started.ToString('o'); finished_utc = [DateTime]::UtcNow.ToString('o'); script_sha256 = (Get-Sha256 $PSCommandPath); toolchain_sha256 = (Get-Sha256 $tcPath) }
}
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $outDir 'build-manifest.json') -Encoding UTF8
"$($info.sha256) *$apkName" | Set-Content -LiteralPath (Join-Path $outDir 'SHA256SUMS') -Encoding ASCII
Step "DONE: $outDir"
