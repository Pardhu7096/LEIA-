<#
LEIA step 2: dry run only. Nothing in the database is changed.

  1. Re-checks the step-1 backup: SHA-256 against its .sha256 file, and the
     structured live/restored comparison from the step-1 reports.
  2. Under a verified read-only session (default_transaction_read_only=on):
     owner status, the migration-history dry run (exact statements it would run),
     and the least-privilege inventory of leia_app with the M12 plan written to a
     file for review (not executed).
  3. Takes a structured status before and after and requires them to be identical.

Run in Windows PowerShell 5.1 (or 7):
  powershell -ExecutionPolicy Bypass -File D:\LEIA-backups\leia-step2-dry-run.ps1
Asks once for the postgres password; it is never logged and is removed in finally.
#>
[CmdletBinding()]
param(
    [string]$AuditWorktree  = "D:\LEIA-schema-audit",
    [string]$BackupDir      = "D:\LEIA-backups",
    [string]$Python         = "D:\LEIA\backend\.venv\Scripts\python.exe",
    [string]$PgBin          = "C:\Program Files\PostgreSQL\18\bin",
    [string]$ExpectedBranch = "audit/leia-ec2-readiness",
    [string]$RequiredCommit = "561e3d582d23e6137f428139256dd4124e08b50a",
    [string]$OwnerRole      = "postgres",
    [string]$ApiRole        = "leia_app",
    [string]$BackupStamp    = "20261114_141755",
    [string]$Through        = "006_request_limits.sql"
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$Stamp   = Get-Date -Format "yyyyMMdd_HHmmss"
$Log     = Join-Path $BackupDir "step2-$Stamp.log"
$Backend = Join-Path $AuditWorktree "backend"
$Tool    = Join-Path $Backend "reconcile_migrations.py"
$Privs   = Join-Path $Backend "api_privileges.py"
$Exe     = ""
if ($env:OS -eq "Windows_NT") { $Exe = ".exe" }
$ReadOnlyOption  = "-c default_transaction_read_only=on"
$SecretVariables = @("PGPASSWORD", "DB_PASSWORD", "DB_USER", "DB_NAME", "PGOPTIONS")

function Write-StepLog([string]$Text) {
    # A log-file write failure must never hide the message on screen.
    try { Add-Content -LiteralPath $Log -Value $Text -Encoding UTF8 } catch { Write-Verbose "log write failed" }
    Write-Host $Text
}

function Invoke-Native {
    # Runs a program, logs its output, and throws unless the exit code is allowed.
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][string]$Program,
        [string[]]$Arguments = @(),
        [int[]]$Allowed = @(0),
        [switch]$Quiet
    )
    Write-StepLog ">>> $Label"
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"   # Windows PowerShell 5.1 turns stderr lines into errors
    try {
        $lines = @(& $Program @Arguments 2>&1 | ForEach-Object { "$_" })
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    $ok = $Allowed -contains $code
    if (-not $Quiet -or -not $ok) { foreach ($line in $lines) { Write-StepLog "    $line" } }
    Write-StepLog "    [exit $code]"
    if (-not $ok) { throw "$Label failed (exit code $code)." }
    return ,$lines
}

function Get-Tool([string]$Name) {
    $path = Join-Path $PgBin ($Name + $Exe)
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "$path not found." }
    return $path
}

function Invoke-Sql([string]$Database, [string]$Query, [string]$Label) {
    $output = Invoke-Native -Label $Label -Program $Psql -Quiet -Arguments (
        $Connection + @("-d", $Database, "-v", "ON_ERROR_STOP=1", "-Atc", $Query))
    return ,$output
}

$failed = $false
$completed = $false
$secure = $null
try {
    if (-not (Test-Path -LiteralPath $BackupDir -PathType Container)) { throw "$BackupDir does not exist." }
    Write-StepLog "LEIA step 2 (dry run) - $(Get-Date -Format o) - PowerShell $($PSVersionTable.PSVersion)"
    foreach ($name in $SecretVariables) {
        if (Test-Path "Env:$name") { throw "Environment variable $name is already set in this window; open a new PowerShell window." }
    }
    $Psql = Get-Tool "psql"
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "$Python not found." }
    if ($ApiRole -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') { throw "Unexpected API role name." }

    # ---- 1. Worktree: right branch, clean, fast-forward to the reviewed version ----
    Write-StepLog "== Audit worktree"
    $git = "git"
    $branch = (Invoke-Native -Label "current branch" -Program $git -Arguments @("-C", $AuditWorktree, "rev-parse", "--abbrev-ref", "HEAD"))[0]
    if ($branch -ne $ExpectedBranch) { throw "Worktree is on '$branch', expected '$ExpectedBranch'. Nothing changed." }
    $tracked = Invoke-Native -Label "tracked changes (must be none)" -Program $git -Arguments @(
        "-C", $AuditWorktree, "status", "--porcelain=v1", "--untracked-files=no")
    if ($tracked.Count -gt 0) { throw "Worktree has uncommitted changes to tracked files. Nothing changed." }
    Invoke-Native -Label "git fetch" -Program $git -Arguments @("-C", $AuditWorktree, "fetch", "origin", $ExpectedBranch) | Out-Null
    $ahead = (Invoke-Native -Label "local commits not on GitHub (must be 0)" -Program $git -Arguments @(
        "-C", $AuditWorktree, "rev-list", "--count", "FETCH_HEAD..HEAD"))[0]
    if ($ahead -ne "0") { throw "Worktree has $ahead local commit(s) not on GitHub. Nothing changed." }
    Invoke-Native -Label "fast-forward" -Program $git -Arguments @("-C", $AuditWorktree, "merge", "--ff-only", "FETCH_HEAD") | Out-Null
    try {
        Invoke-Native -Label "required commit present" -Program $git -Arguments @(
            "-C", $AuditWorktree, "merge-base", "--is-ancestor", $RequiredCommit, "HEAD") | Out-Null
    } catch { throw "Worktree does not contain commit $RequiredCommit (the reviewed tool version)." }
    Invoke-Native -Label "worktree head" -Program $git -Arguments @("-C", $AuditWorktree, "log", "--oneline", "-1") | Out-Null

    # ---- 2. Backup and restore comparison, re-checked from files (no database) ------
    Write-StepLog "== Step-1 backup re-check"
    $Dump = Join-Path $BackupDir "leia-$BackupStamp-pre007.dump"
    $recorded = ((Get-Content -LiteralPath "$Dump.sha256" -TotalCount 1) -split '\s+')[0]
    $actual = (Get-FileHash -LiteralPath $Dump -Algorithm SHA256).Hash
    Write-StepLog "    recorded $recorded"
    Write-StepLog "    actual   $actual"
    if ($recorded -ne $actual) { throw "Backup checksum does not match: do not continue." }
    Write-StepLog "    SHA-256 matches ($((Get-Item -LiteralPath $Dump).Length) bytes)."
    Invoke-Native -Label "structured comparison of the step-1 live and restored reports" -Program $Python -Arguments @(
        $Tool, "compare-status",
        (Join-Path $BackupDir "status-live-$BackupStamp.json"),
        (Join-Path $BackupDir "status-restored-$BackupStamp.json"),
        "--allow-database-owner-difference", "leia_app:postgres") | Out-Null

    # ---- 3. Owner password and read-only guard ---------------------------------------
    $envFile = Join-Path $Backend ".env"
    $settings = @{}
    foreach ($line in Get-Content -LiteralPath $envFile) {
        if ($line -match '^(DB_HOST|DB_PORT|DB_NAME)=(.*)$') { $settings[$Matches[1]] = $Matches[2].Trim() }
    }
    foreach ($key in @("DB_HOST", "DB_PORT", "DB_NAME")) {
        if (-not $settings.ContainsKey($key) -or -not $settings[$key]) { throw "$key missing from $envFile." }
    }
    $DbName = $settings["DB_NAME"]
    if ($DbName -notmatch '^[A-Za-z0-9_]+$') { throw "DB_NAME has unexpected characters." }
    $Connection = @("-h", $settings["DB_HOST"], "-p", $settings["DB_PORT"], "-U", $OwnerRole, "-w")
    Write-StepLog "Target: $($settings['DB_HOST']):$($settings['DB_PORT'])/$DbName as $OwnerRole"

    $env:PGOPTIONS = $ReadOnlyOption
    $secure = Read-Host "Enter the PostgreSQL '$OwnerRole' password (input hidden)" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
    $env:DB_PASSWORD = $env:PGPASSWORD
    $env:DB_USER = $OwnerRole
    $check = Invoke-Sql $DbName "select current_user || '|' || current_setting('default_transaction_read_only')" "owner sign-in and read-only guard"
    Write-StepLog "    $($check[0])"
    if ($check[0] -ne "$OwnerRole|on") { throw "Read-only guard is not active; stopping." }

    # ---- 4. Status before ------------------------------------------------------------
    Write-StepLog "== Status before"
    $Before = Join-Path $BackupDir "status-before-dryrun-$Stamp.json"
    Invoke-Native -Label "status (owner, text)" -Program $Python -Arguments @($Tool, "status") | Out-Null
    Invoke-Native -Label "status (owner, json)" -Program $Python -Arguments @($Tool, "status", "--json", "--output", $Before) | Out-Null

    # ---- 5. Migration-history dry run (read-only) ----------------------------------------
    Write-StepLog "== Migration history: DRY RUN (adopt --through $Through, no --apply)"
    Invoke-Native -Label "adopt dry run" -Program $Python -Arguments @($Tool, "adopt", "--through", $Through) | Out-Null

    # ---- 6. Least-privilege inventory and M12 plan (read-only; plan not executed) --------
    Write-StepLog "== Least privilege for $ApiRole (M12)"
    $Plan = Join-Path $BackupDir "m12-plan-$Stamp.sql"
    Invoke-Native -Label "api_privileges inventory" -Program $Python -Allowed @(0, 1) -Arguments @(
        $Privs, "--role", $ApiRole, "--plan-owner", $OwnerRole, "--plan-file", $Plan) | Out-Null
    Write-StepLog "    plan written for review, NOT executed: $Plan"
    foreach ($line in Get-Content -LiteralPath $Plan) { Write-StepLog "    | $line" }

    # ---- 7. Status after: must be identical ----------------------------------------------
    Write-StepLog "== Status after"
    $After = Join-Path $BackupDir "status-after-dryrun-$Stamp.json"
    Invoke-Native -Label "status (owner, json)" -Program $Python -Arguments @($Tool, "status", "--json", "--output", $After) | Out-Null
    Invoke-Native -Label "before vs after (must be identical)" -Program $Python -Arguments @($Tool, "compare-status", $Before, $After) | Out-Null

    Write-StepLog "RESULT: OK - dry run complete; the database is unchanged. Nothing was recorded, granted or altered."
    $completed = $true
} catch {
    $failed = $true
    Write-StepLog "RESULT: STOPPED - $($_.Exception.Message)"
} finally {
    if (-not $completed -and -not $failed) { Write-StepLog "RESULT: STOPPED - interrupted (for example Ctrl+C)." }
    foreach ($name in $SecretVariables) { Remove-Item "Env:$name" -ErrorAction SilentlyContinue }
    if ($secure) { $secure.Dispose() }
    Write-Host "`nLog: $Log  (tell Claude it is done)"
}
if ($failed) { exit 1 } else { exit 0 }
