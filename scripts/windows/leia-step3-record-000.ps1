<#
LEIA step 3 (approved 14 Nov 2026): record migration 000 in the history and let
leia_app read the history. Exactly two reviewed statements, in one transaction:

  GRANT SELECT ON public.crm_schema_migrations TO leia_app;
  INSERT INTO public.crm_schema_migrations (name) VALUES ('000_core_baseline.sql');

Before: verifies the worktree version, the step-1 backup (SHA-256) and that the live
database is still exactly the backed-up state (structured comparison with the
step-1 report, including every table's content checksum), then repeats the dry run
and requires exactly the two statements above.
After: verifies that 000-006 are recorded exactly once, 007 is still pending,
leia_app can read the history, and nothing else changed (schema, owners, row counts
and content checksums of every other table).

Does NOT apply 007, run the M12 plan, merge or deploy.
Stop the LEIA backend first, then run in Windows PowerShell 5.1 (or 7):
  powershell -ExecutionPolicy Bypass -File D:\LEIA-schema-audit\scripts\windows\leia-step3-record-000.ps1
Asks once for the postgres password; never logged; removed in finally.
#>
[CmdletBinding()]
param(
    [string]$AuditWorktree  = "D:\LEIA-schema-audit",
    [string]$BackupDir      = "D:\LEIA-backups",
    [string]$Python         = "D:\LEIA\backend\.venv\Scripts\python.exe",
    [string]$PgBin          = "C:\Program Files\PostgreSQL\18\bin",
    [string]$ExpectedBranch = "audit/leia-ec2-readiness",
    [string]$RequiredCommit = "4d573ab0a59f9acb04d02e45729bfab7b10d8e8c",
    [string]$OwnerRole      = "postgres",
    [string]$BackupStamp    = "20261114_141755",
    [string]$Through        = "006_request_limits.sql",
    [string]$Record         = "000_core_baseline.sql"
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$Stamp   = Get-Date -Format "yyyyMMdd_HHmmss"
$Log     = Join-Path $BackupDir "step3-$Stamp.log"
$Backend = Join-Path $AuditWorktree "backend"
$Tool    = Join-Path $Backend "reconcile_migrations.py"
$Exe     = ""
if ($env:OS -eq "Windows_NT") { $Exe = ".exe" }
$ReadOnlyOption  = "-c default_transaction_read_only=on"
$SecretVariables = @("PGPASSWORD", "DB_PASSWORD", "DB_USER", "DB_NAME", "PGOPTIONS")
$Reviewed = @(
    "GRANT SELECT ON public.crm_schema_migrations TO leia_app;",
    "INSERT INTO public.crm_schema_migrations (name) VALUES ('$Record');"
)

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

function Get-Statements($Lines) {
    return @($Lines | ForEach-Object { "$_".Trim() } | Where-Object { $_ -match '^(GRANT|INSERT|CREATE|REVOKE|ALTER|DELETE|UPDATE) .*;$' })
}

$failed = $false
$completed = $false
$committed = $false
$secure = $null
try {
    if (-not (Test-Path -LiteralPath $BackupDir -PathType Container)) { throw "$BackupDir does not exist." }
    Write-StepLog "LEIA step 3 (record $Record) - $(Get-Date -Format o) - PowerShell $($PSVersionTable.PSVersion)"
    foreach ($name in $SecretVariables) {
        if (Test-Path "Env:$name") { throw "Environment variable $name is already set in this window; open a new PowerShell window." }
    }
    $Psql = Get-Tool "psql"
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "$Python not found." }

    # ---- 1. Worktree version --------------------------------------------------------
    Write-StepLog "== Pre-check 1: audit worktree"
    $git = "git"
    $branch = (Invoke-Native -Label "current branch" -Program $git -Arguments @("-C", $AuditWorktree, "rev-parse", "--abbrev-ref", "HEAD"))[0]
    if ($branch -ne $ExpectedBranch) { throw "Worktree is on '$branch', expected '$ExpectedBranch'. Nothing changed." }
    $tracked = Invoke-Native -Label "tracked changes (must be none)" -Program $git -Arguments @(
        "-C", $AuditWorktree, "status", "--porcelain=v1", "--untracked-files=no")
    if ($tracked.Count -gt 0) { throw "Worktree has uncommitted changes to tracked files. Nothing changed." }
    try {
        Invoke-Native -Label "required commit present" -Program $git -Arguments @(
            "-C", $AuditWorktree, "merge-base", "--is-ancestor", $RequiredCommit, "HEAD") | Out-Null
    } catch { throw "Worktree does not contain commit $RequiredCommit; run: git -C $AuditWorktree pull --ff-only" }
    Invoke-Native -Label "worktree head" -Program $git -Arguments @("-C", $AuditWorktree, "log", "--oneline", "-1") | Out-Null

    # ---- 2. Backup file ----------------------------------------------------------------
    Write-StepLog "== Pre-check 2: backup"
    $Dump = Join-Path $BackupDir "leia-$BackupStamp-pre007.dump"
    $recordedHash = ((Get-Content -LiteralPath "$Dump.sha256" -TotalCount 1) -split '\s+')[0]
    $actualHash = (Get-FileHash -LiteralPath $Dump -Algorithm SHA256).Hash
    Write-StepLog "    recorded $recordedHash"
    Write-StepLog "    actual   $actualHash"
    if ($recordedHash -ne $actualHash) { throw "Backup checksum does not match. Nothing changed." }

    # ---- 3. Database target and read-only guard ---------------------------------------
    Write-StepLog "== Pre-check 3: database"
    $envFile = Join-Path $Backend ".env"
    $settings = @{}
    foreach ($line in Get-Content -LiteralPath $envFile) {
        if ($line -match '^(DB_HOST|DB_PORT|DB_NAME)=(.*)$') { $settings[$Matches[1]] = $Matches[2].Trim() }
    }
    foreach ($key in @("DB_HOST", "DB_PORT", "DB_NAME")) {
        if (-not $settings.ContainsKey($key) -or -not $settings[$key]) { throw "$key missing from $envFile." }
    }
    $DbName = $settings["DB_NAME"]
    if ($DbName -ne "leia") { throw "DB_NAME is '$DbName', expected 'leia'. Nothing changed." }
    $Connection = @("-h", $settings["DB_HOST"], "-p", $settings["DB_PORT"], "-U", $OwnerRole, "-w")
    $env:PGOPTIONS = $ReadOnlyOption
    $secure = Read-Host "Enter the PostgreSQL '$OwnerRole' password (input hidden)" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
    $env:DB_PASSWORD = $env:PGPASSWORD
    $env:DB_USER = $OwnerRole
    $who = Invoke-Sql $DbName ("select current_database() || '|' || current_user || '|' || " +
        "current_setting('server_version') || '|' || current_setting('default_transaction_read_only')") "target and read-only guard"
    Write-StepLog "    $($who[0])"
    if ($who[0] -notmatch "^leia\|$OwnerRole\|[^|]+\|on$") { throw "Unexpected target or read-only guard not active. Nothing changed." }

    # ---- 4. Live database is exactly the backed-up state ------------------------------
    Write-StepLog "== Pre-check 4: database still equals the backup (history, schema, data)"
    $Before = Join-Path $BackupDir "status-before-step3-$Stamp.json"
    Invoke-Native -Label "status (owner, text)" -Program $Python -Arguments @($Tool, "status") | Out-Null
    Invoke-Native -Label "status (owner, json)" -Program $Python -Arguments @($Tool, "status", "--json", "--output", $Before) | Out-Null
    try {
        Invoke-Native -Label "compare with the step-1 backup report" -Program $Python -Arguments @(
            $Tool, "compare-status", (Join-Path $BackupDir "status-live-$BackupStamp.json"), $Before) | Out-Null
    } catch { throw "The database changed since the step-1 backup; take a new backup (step 1) before step 3. Nothing changed." }

    # ---- 5. Dry run must show exactly the reviewed statements -------------------------
    Write-StepLog "== Pre-check 5: dry run"
    $dry = Invoke-Native -Label "adopt dry run" -Program $Python -Arguments @(
        $Tool, "adopt", "--through", $Through, "--expect-record", $Record)
    $planned = Get-Statements $dry
    if (($planned -join "`n") -ne ($Reviewed -join "`n")) { throw "Dry run differs from the reviewed statements. Nothing changed." }
    Write-StepLog "    dry run = reviewed statements (2)"

    # ---- 6. Execute (the only step that writes) ----------------------------------------
    Write-StepLog "== EXECUTE: one transaction"
    Remove-Item Env:PGOPTIONS -ErrorAction SilentlyContinue
    try {
        $run = Invoke-Native -Label "adopt --apply" -Program $Python -Arguments @(
            $Tool, "adopt", "--through", $Through, "--expect-record", $Record, "--apply")
    } finally { $env:PGOPTIONS = $ReadOnlyOption }
    $executed = Get-Statements $run
    if (-not ($run -match '^Committed\. Recorded: ')) { throw "No commit confirmation; check the log." }
    $committed = $true
    if (($executed -join "`n") -ne ($Reviewed -join "`n")) { throw "Executed statements differ from the reviewed ones; see log." }
    Write-StepLog "    executed exactly the 2 reviewed statements and committed"

    # ---- 7. Post-change verification -------------------------------------------------
    Write-StepLog "== Verify"
    $After = Join-Path $BackupDir "status-after-step3-$Stamp.json"
    Invoke-Native -Label "status (owner, text)" -Program $Python -Arguments @($Tool, "status") | Out-Null
    Invoke-Native -Label "status (owner, json)" -Program $Python -Arguments @($Tool, "status", "--json", "--output", $After) | Out-Null
    Invoke-Native -Label "verify-adoption (only the reviewed change)" -Program $Python -Arguments @(
        $Tool, "verify-adoption", $Before, $After, "--added", $Record) | Out-Null
    $rows = Invoke-Sql $DbName "select name || ' x' || count(*) from public.crm_schema_migrations group by name order by name" "history rows"
    foreach ($row in $rows) { Write-StepLog "    $row" }
    $expectedRows = @("000_core_baseline.sql x1", "001_property_drafts.sql x1", "002_draft_amenities.sql x1",
        "003_property_media.sql x1", "004_property_gallery.sql x1", "005_property_chat_messages.sql x1",
        "006_request_limits.sql x1")
    if (($rows -join "`n") -ne ($expectedRows -join "`n")) { throw "History is not exactly 000-006, once each." }
    # The API's own login can now read the history (it is what /health/ready uses).
    $savedUser = $env:DB_USER; $savedPassword = $env:DB_PASSWORD
    Remove-Item Env:DB_USER, Env:DB_PASSWORD
    try {
        $app = Invoke-Native -Label "status as the app login from .env" -Program $Python -Arguments @($Tool, "status")
    } finally { $env:DB_USER = $savedUser; $env:DB_PASSWORD = $savedPassword }
    if (-not ($app -match 'leia_app can read it: yes')) { throw "leia_app still cannot read the history." }
    if (-not ($app -match '^pending: 007_room_panoramas\.sql$')) { throw "Unexpected pending list." }

    Write-StepLog "Undo, only if ever needed (as postgres, one transaction):"
    Write-StepLog "    DELETE FROM public.crm_schema_migrations WHERE name = '$Record'; REVOKE SELECT ON public.crm_schema_migrations FROM leia_app;"
    Write-StepLog "RESULT: OK - 000 recorded and history readable by leia_app; 000-006 recorded once each; 007 pending; no other change."
    $completed = $true
} catch {
    $failed = $true
    if ($committed) {
        Write-StepLog "RESULT: STOPPED AFTER COMMIT - $($_.Exception.Message) The two statements were committed; do not rerun. Send the log."
    } else {
        Write-StepLog "RESULT: STOPPED - $($_.Exception.Message)"
    }
} finally {
    if (-not $completed -and -not $failed) { Write-StepLog "RESULT: STOPPED - interrupted (for example Ctrl+C)." }
    foreach ($name in $SecretVariables) { Remove-Item "Env:$name" -ErrorAction SilentlyContinue }
    if ($secure) { $secure.Dispose() }
    Write-Host "`nLog: $Log  (tell Claude it is done)"
}
if ($failed) { exit 1 } else { exit 0 }
