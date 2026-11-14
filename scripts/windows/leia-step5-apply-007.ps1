<#
LEIA step 5 - APPLY migration 007_room_panoramas.sql to the development database.
Requires the owner's explicit approval. Do not run before it is given.

Before (read-only): worktree version; 007 is the reviewed file (SHA-256); the step-4
backup's SHA-256; the database is still exactly the state the step-4 backup was
taken from (structured comparison incl. content checksums); a dry run shows only 007.
Apply: the project runner, one transaction: migrate_crm.py --expect 007_room_panoramas.sql
After: verify-migration (only 007's two schema changes, no data change), history
000-007 once each, nothing pending, the app login sees the same.

Stop the LEIA backend first (007 briefly locks property_media), then:
  powershell -ExecutionPolicy Bypass -File D:\LEIA-schema-audit\scripts\windows\leia-step5-apply-007.ps1 -BackupStamp <stamp from the step-4 log>
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$BackupStamp,
    [string]$AuditWorktree   = "D:\LEIA-schema-audit",
    [string]$BackupDir       = "D:\LEIA-backups",
    [string]$Python          = "D:\LEIA\backend\.venv\Scripts\python.exe",
    [string]$PgBin           = "C:\Program Files\PostgreSQL\18\bin",
    [string]$ExpectedBranch  = "audit/leia-ec2-readiness",
    [string]$RequiredCommit  = "6bfeb10e8beb5ae4a32d17b99ba1ead84c7660af",
    [string]$OwnerRole       = "postgres",
    [string]$Migration       = "007_room_panoramas.sql",
    [string]$MigrationSha256 = "B29AF32A508E74251F4BB0C8B35116D87C5B35D1C407F6BD493BEEA85F6D2C9A"
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$Stamp   = Get-Date -Format "yyyyMMdd_HHmmss"
$Log     = Join-Path $BackupDir "step5-$Stamp.log"
$Backend = Join-Path $AuditWorktree "backend"
$Tool    = Join-Path $Backend "reconcile_migrations.py"
$Runner  = Join-Path $Backend "migrate_crm.py"
$Down    = Join-Path (Join-Path $Backend "rollback") "007_room_panoramas.down.sql"
$Exe     = ""
if ($env:OS -eq "Windows_NT") { $Exe = ".exe" }
$ReadOnlyOption  = "-c default_transaction_read_only=on"
$SecretVariables = @("PGPASSWORD", "DB_PASSWORD", "DB_USER", "DB_NAME", "PGOPTIONS")
$OwnProcess = ([Environment]::GetCommandLineArgs() -join " ").Contains($PSCommandPath)
$Inspect = "& `"$Python`" `"$Tool`" --owner $OwnerRole status"

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

# "checks": nothing written. "apply": outcome unknown until the runner confirms.
# "verify": committed; verification incomplete.
$Phase = "checks"
$completed = $false
$secure = $null
try {
    if (-not (Test-Path -LiteralPath $BackupDir -PathType Container)) { throw "$BackupDir does not exist." }
    if ($BackupStamp -notmatch '^\d{8}_\d{6}$') { throw "BackupStamp must look like 20261114_180000." }
    Write-StepLog "LEIA step 5 (APPLY $Migration) - $(Get-Date -Format o) - PowerShell $($PSVersionTable.PSVersion)"
    foreach ($name in $SecretVariables) {
        if (Test-Path "Env:$name") { throw "Environment variable $name is already set in this window; open a new PowerShell window." }
    }
    $Psql = Get-Tool "psql"
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "$Python not found." }

    Write-StepLog "== Pre-check 1: worktree and migration file"
    $git = "git"
    $branch = (Invoke-Native -Label "current branch" -Program $git -Arguments @("-C", $AuditWorktree, "rev-parse", "--abbrev-ref", "HEAD"))[0]
    if ($branch -ne $ExpectedBranch) { throw "Worktree is on '$branch', expected '$ExpectedBranch'." }
    $tracked = Invoke-Native -Label "tracked changes (must be none)" -Program $git -Arguments @(
        "-C", $AuditWorktree, "status", "--porcelain=v1", "--untracked-files=no")
    if ($tracked.Count -gt 0) { throw "Worktree has uncommitted changes to tracked files." }
    try {
        Invoke-Native -Label "required commit present" -Program $git -Arguments @(
            "-C", $AuditWorktree, "merge-base", "--is-ancestor", $RequiredCommit, "HEAD") | Out-Null
    } catch { throw "Worktree does not contain commit $RequiredCommit; run: git -C $AuditWorktree pull --ff-only" }
    $fileSha = (Get-FileHash -LiteralPath (Join-Path (Join-Path $Backend "migrations") $Migration) -Algorithm SHA256).Hash
    if ($fileSha -ne $MigrationSha256) { throw "$Migration is not the reviewed file (SHA-256 $fileSha)." }
    Write-StepLog "    $Migration SHA-256 matches the reviewed file"

    Write-StepLog "== Pre-check 2: step-4 backup"
    $Dump = Join-Path $BackupDir "leia-$BackupStamp-pre007.dump"
    $recordedHash = ((Get-Content -LiteralPath "$Dump.sha256" -TotalCount 1) -split '\s+')[0]
    $actualHash = (Get-FileHash -LiteralPath $Dump -Algorithm SHA256).Hash
    Write-StepLog "    recorded $recordedHash"
    Write-StepLog "    actual   $actualHash"
    if ($recordedHash -ne $actualHash) { throw "Backup checksum does not match." }

    Write-StepLog "== Pre-check 3: database (read-only)"
    $settings = @{}
    foreach ($line in Get-Content -LiteralPath (Join-Path $Backend ".env")) {
        if ($line -match '^(DB_HOST|DB_PORT|DB_NAME)=(.*)$') { $settings[$Matches[1]] = $Matches[2].Trim() }
    }
    if ($settings["DB_NAME"] -ne "leia") { throw "DB_NAME is '$($settings['DB_NAME'])', expected 'leia'." }
    $Connection = @("-h", $settings["DB_HOST"], "-p", $settings["DB_PORT"], "-U", $OwnerRole, "-w")
    $env:PGOPTIONS = $ReadOnlyOption
    $secure = Read-Host "Enter the PostgreSQL '$OwnerRole' password (input hidden)" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
    $env:DB_PASSWORD = $env:PGPASSWORD
    $env:DB_USER = $OwnerRole
    $who = Invoke-Sql "leia" "select current_database() || '|' || current_user || '|' || current_setting('default_transaction_read_only')" "target and read-only guard"
    Write-StepLog "    $($who[0])"
    if ($who[0] -ne "leia|$OwnerRole|on") { throw "Unexpected target or read-only guard not active." }

    Write-StepLog "== Pre-check 4: database still equals the step-4 backup"
    $Before = Join-Path $BackupDir "status-before-step5-$Stamp.json"
    Invoke-Native -Label "status (owner, json)" -Program $Python -Arguments @($Tool, "status", "--json", "--output", $Before) | Out-Null
    try {
        Invoke-Native -Label "compare with the step-4 report" -Program $Python -Arguments @(
            $Tool, "compare-status", (Join-Path $BackupDir "status-live-after-step4-$BackupStamp.json"), $Before) | Out-Null
    } catch { throw "The database changed since the step-4 backup; run step 4 again first." }

    Write-StepLog "== Pre-check 5: dry run"
    $dry = Invoke-Native -Label "migrate_crm --dry-run --expect $Migration" -Program $Python -Arguments @(
        $Runner, "--dry-run", "--expect", $Migration)
    $applyLines = @($dry | Where-Object { $_ -match '^\s+apply ' })
    if ($applyLines.Count -ne 1 -or $applyLines[0].Trim() -ne "apply $Migration  SHA-256 $MigrationSha256") {
        throw "Dry run does not show exactly the reviewed $Migration."
    }

    Write-StepLog "== APPLY: one transaction (project runner)"
    Remove-Item Env:PGOPTIONS -ErrorAction SilentlyContinue
    $Phase = "apply"
    try {
        $run = Invoke-Native -Label "migrate_crm --expect $Migration" -Program $Python -Arguments @($Runner, "--expect", $Migration)
    } finally { $env:PGOPTIONS = $ReadOnlyOption }
    if (-not (($run -contains "Applied $Migration") -and ($run -contains "CRM database is up to date."))) {
        throw "The runner ended without confirming $Migration."
    }
    $Phase = "verify"

    Write-StepLog "== Verify"
    $After = Join-Path $BackupDir "status-after-step5-$Stamp.json"
    Invoke-Native -Label "status (owner, text)" -Program $Python -Arguments @($Tool, "status") | Out-Null
    Invoke-Native -Label "status (owner, json)" -Program $Python -Arguments @($Tool, "status", "--json", "--output", $After) | Out-Null
    Invoke-Native -Label "verify-migration (only 007's schema change, no data change)" -Program $Python -Arguments @(
        $Tool, "verify-migration", $Before, $After, "--applied", $Migration) | Out-Null
    $rows = Invoke-Sql "leia" "select name || ' x' || count(*) from public.crm_schema_migrations group by name order by name" "history rows"
    foreach ($row in $rows) { Write-StepLog "    $row" }
    $expectedRows = @("000_core_baseline.sql x1", "001_property_drafts.sql x1", "002_draft_amenities.sql x1",
        "003_property_media.sql x1", "004_property_gallery.sql x1", "005_property_chat_messages.sql x1",
        "006_request_limits.sql x1", "007_room_panoramas.sql x1")
    if (($rows -join "`n") -ne ($expectedRows -join "`n")) { throw "History is not exactly 000-007, once each." }
    $savedUser = $env:DB_USER; $savedPassword = $env:DB_PASSWORD
    Remove-Item Env:DB_USER, Env:DB_PASSWORD
    try {
        $app = Invoke-Native -Label "status as the app login from .env" -Program $Python -Arguments @($Tool, "status")
    } finally { $env:DB_USER = $savedUser; $env:DB_PASSWORD = $savedPassword }
    if (-not ($app -match '^pending: \(none\)$')) { throw "The app login still sees pending migrations." }
    if (-not ($app -match '^\s+007_room_panoramas\.sql: yes$')) { throw "The app login does not see the 007 schema." }

    Write-StepLog "Rollback, only if needed and approved (backend stopped; refuses if room panoramas exist):"
    Write-StepLog "    & `"$Psql`" -h $($settings['DB_HOST']) -p $($settings['DB_PORT']) -U $OwnerRole -W -d leia -v ON_ERROR_STOP=1 -f `"$Down`""
    Write-StepLog "RESULT: OK - $Migration applied and verified; history 000-007 once each; nothing pending; no data changed."
    $completed = $true
} catch {
    $Reason = $_.Exception.Message
} finally {
    if (-not $completed) {
        $Interrupted = -not (Test-Path Variable:Reason)
        if ($Interrupted) { $Reason = "interrupted (for example Ctrl+C)." }
        switch ($Phase) {
            "checks" { Write-StepLog "RESULT: STOPPED BEFORE ANY CHANGE - $Reason" }
            "apply" {
                Write-StepLog "RESULT: STOPPED - COMMIT OUTCOME UNCERTAIN - $Reason"
                Write-StepLog "    007 may or may not have been applied. Do NOT rerun step 5."
                Write-StepLog "    Inspect the database first, read-only: $Inspect"
                Write-StepLog "    Then send this log to Claude."
            }
            default {
                Write-StepLog "RESULT: STOPPED AFTER COMMIT, VERIFICATION INCOMPLETE - $Reason"
                Write-StepLog "    007 was applied. Do NOT rerun step 5. Inspect read-only: $Inspect ; then send this log to Claude."
            }
        }
    }
    foreach ($name in $SecretVariables) { Remove-Item "Env:$name" -ErrorAction SilentlyContinue }
    if ($secure) { $secure.Dispose() }
    Write-Host "`nLog: $Log  (tell Claude it is done)"
    if (-not $completed -and $Interrupted -and $OwnProcess) { $host.SetShouldExit(1) }
}
if ($completed) { exit 0 } else { exit 1 }
