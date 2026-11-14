<#
LEIA step 4: prepare migration 007_room_panoramas.sql. The live `leia` database is
NOT changed: every connection to it is read-only (verified). Writes go only to a
new backup file and a new scratch copy.

  1. Pre-checks: worktree version, the database still equals the step-3 result,
     007 is the only pending migration, existing media rows are compatible.
  2. Read-only dry run on `leia`: exactly 007, with the reviewed SHA-256.
  3. Fresh backup (SHA-256, table count) and restore into a new scratch database
     owned like `leia`; the copy must compare Identical.
  4. Rehearsal on the scratch copy only: apply 007 with the project runner, verify
     it changed exactly 007's schema and no data; run the rollback script and
     verify the copy is Identical to before; apply 007 again.
  5. The live database is compared before/after: must be Identical.

Stop the LEIA backend first, then run in Windows PowerShell 5.1 (or 7):
  git -C D:\LEIA-schema-audit pull --ff-only
  powershell -ExecutionPolicy Bypass -File D:\LEIA-schema-audit\scripts\windows\leia-step4-prepare-007.ps1
Add -DropScratchAfterVerify to drop the scratch copy after a successful run.
Asks once for the postgres password; never logged; removed in finally.
#>
[CmdletBinding()]
param(
    [string]$AuditWorktree   = "D:\LEIA-schema-audit",
    [string]$BackupDir       = "D:\LEIA-backups",
    [string]$Python          = "D:\LEIA\backend\.venv\Scripts\python.exe",
    [string]$PgBin           = "C:\Program Files\PostgreSQL\18\bin",
    [string]$ExpectedBranch  = "audit/leia-ec2-readiness",
    [string]$RequiredCommit  = "6bfeb10e8beb5ae4a32d17b99ba1ead84c7660af",
    [string]$OwnerRole       = "postgres",
    [string]$Step3Report     = "status-after-step3-20261114_174420.json",
    [string]$Migration       = "007_room_panoramas.sql",
    [string]$MigrationSha256 = "B29AF32A508E74251F4BB0C8B35116D87C5B35D1C407F6BD493BEEA85F6D2C9A",
    [switch]$DropScratchAfterVerify
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$Stamp   = Get-Date -Format "yyyyMMdd_HHmmss"
$Log     = Join-Path $BackupDir "step4-$Stamp.log"
$Backend = Join-Path $AuditWorktree "backend"
$Tool    = Join-Path $Backend "reconcile_migrations.py"
$Runner  = Join-Path $Backend "migrate_crm.py"
$Down    = Join-Path (Join-Path $Backend "rollback") "007_room_panoramas.down.sql"
$Exe     = ""
if ($env:OS -eq "Windows_NT") { $Exe = ".exe" }
$ReadOnlyOption  = "-c default_transaction_read_only=on"
$SecretVariables = @("PGPASSWORD", "DB_PASSWORD", "DB_USER", "DB_NAME", "PGOPTIONS")
$OwnProcess = ([Environment]::GetCommandLineArgs() -join " ").Contains($PSCommandPath)

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

function Get-FreeBytes([string]$Root) {
    return (New-Object System.IO.DriveInfo($Root)).AvailableFreeSpace
}

function Get-Root([string]$Path) {
    return [System.IO.Path]::GetPathRoot([System.IO.Path]::GetFullPath($Path))
}

function Use-Scratch([string]$Name) {
    # Point the Python tools at the scratch copy, writable. Only ever the scratch name.
    if ($Name -notmatch '^leia_step4_check_\d{8}_\d{6}$') { throw "Refusing to target '$Name'." }
    $env:DB_NAME = $Name
    Remove-Item Env:PGOPTIONS -ErrorAction SilentlyContinue
}
function Use-Live {
    Remove-Item Env:DB_NAME -ErrorAction SilentlyContinue
    $env:PGOPTIONS = $ReadOnlyOption
}

$completed = $false
$secure = $null
try {
    if (-not (Test-Path -LiteralPath $BackupDir -PathType Container)) { throw "$BackupDir does not exist." }
    Write-StepLog "LEIA step 4 (prepare $Migration; live database read-only) - $(Get-Date -Format o) - PowerShell $($PSVersionTable.PSVersion)"
    foreach ($name in $SecretVariables) {
        if (Test-Path "Env:$name") { throw "Environment variable $name is already set in this window; open a new PowerShell window." }
    }
    $Psql = Get-Tool "psql"; $PgDump = Get-Tool "pg_dump"; $PgRestore = Get-Tool "pg_restore"
    $CreateDb = Get-Tool "createdb"; $DropDb = Get-Tool "dropdb"
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "$Python not found." }

    # ---- 1. Worktree version ---------------------------------------------------------
    Write-StepLog "== 1. Audit worktree"
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
    Invoke-Native -Label "worktree head" -Program $git -Arguments @("-C", $AuditWorktree, "log", "--oneline", "-1") | Out-Null
    $fileSha = (Get-FileHash -LiteralPath (Join-Path (Join-Path $Backend "migrations") $Migration) -Algorithm SHA256).Hash
    Write-StepLog "    $Migration SHA-256 $fileSha"
    if ($fileSha -ne $MigrationSha256) { throw "$Migration is not the reviewed file (SHA-256 differs)." }

    # ---- 2. Live database: read-only, unchanged since step 3, 007 compatible --------------
    Write-StepLog "== 2. Live database (read-only)"
    $settings = @{}
    foreach ($line in Get-Content -LiteralPath (Join-Path $Backend ".env")) {
        if ($line -match '^(DB_HOST|DB_PORT|DB_NAME)=(.*)$') { $settings[$Matches[1]] = $Matches[2].Trim() }
    }
    if ($settings["DB_NAME"] -ne "leia") { throw "DB_NAME is '$($settings['DB_NAME'])', expected 'leia'." }
    $Connection = @("-h", $settings["DB_HOST"], "-p", $settings["DB_PORT"], "-U", $OwnerRole, "-w")
    Use-Live
    $secure = Read-Host "Enter the PostgreSQL '$OwnerRole' password (input hidden)" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
    $env:DB_PASSWORD = $env:PGPASSWORD
    $env:DB_USER = $OwnerRole
    $who = Invoke-Sql "leia" "select current_database() || '|' || current_user || '|' || current_setting('default_transaction_read_only')" "target and read-only guard"
    Write-StepLog "    $($who[0])"
    if ($who[0] -ne "leia|$OwnerRole|on") { throw "Unexpected target or read-only guard not active." }

    $LiveBefore = Join-Path $BackupDir "status-live-before-step4-$Stamp.json"
    Invoke-Native -Label "status (owner, text)" -Program $Python -Arguments @($Tool, "status") | Out-Null
    Invoke-Native -Label "status (owner, json)" -Program $Python -Arguments @($Tool, "status", "--json", "--output", $LiveBefore) | Out-Null
    try {
        Invoke-Native -Label "unchanged since step 3" -Program $Python -Arguments @(
            $Tool, "compare-status", (Join-Path $BackupDir $Step3Report), $LiveBefore) | Out-Null
    } catch { throw "The database changed since step 3. Review the differences above before preparing 007." }
    $live = Get-Content -LiteralPath $LiveBefore -Raw | ConvertFrom-Json
    if ((@($live.pending) -join ",") -ne $Migration) { throw "Pending is '$(@($live.pending) -join ', ')', expected only $Migration." }
    if (@($live.matches.'006_request_limits.sql').Count -ne 0) { throw "Schema is not exactly the state after 006." }
    if ($live.property_media.invalid_for_007 -ne 0 -or $live.property_media.duplicate_room_panoramas -ne 0) {
        throw "Media rows conflict with 007: $($live.property_media | ConvertTo-Json -Compress)"
    }
    Write-StepLog ("    media rows: {0}; incompatible with 007: 0; duplicate room panoramas: 0" -f $live.property_media.total)

    # ---- 3. Read-only dry run on the live database ----------------------------------------
    Write-StepLog "== 3. Dry run on leia (read-only)"
    $dry = Invoke-Native -Label "migrate_crm --dry-run --expect $Migration" -Program $Python -Arguments @(
        $Runner, "--dry-run", "--expect", $Migration)
    $applyLines = @($dry | Where-Object { $_ -match '^\s+apply ' })
    if ($applyLines.Count -ne 1 -or $applyLines[0].Trim() -ne "apply $Migration  SHA-256 $MigrationSha256") {
        throw "Dry run does not show exactly the reviewed $Migration."
    }
    Write-StepLog "    dry run = $Migration only, reviewed SHA-256"

    # ---- 4. Fresh backup ---------------------------------------------------------------
    Write-StepLog "== 4. Fresh backup"
    $sizeText = (Invoke-Sql "leia" "select pg_database_size(current_database())" "database size")[0]
    [int64]$dbBytes = 0
    if (-not [int64]::TryParse("$sizeText", [ref]$dbBytes) -or $dbBytes -le 0) { throw "Could not read the database size." }
    $dataDir = (Invoke-Sql "leia" "show data_directory" "data directory")[0]
    $need = @{}
    $need[(Get-Root $BackupDir)] = [int64]($dbBytes + 512MB)
    $dataRoot = Get-Root $dataDir
    $extra = [int64](2 * $dbBytes + 1GB)
    if ($need.ContainsKey($dataRoot)) { $need[$dataRoot] += $extra } else { $need[$dataRoot] = $extra }
    foreach ($root in @($need.Keys)) {
        $free = Get-FreeBytes $root
        Write-StepLog ("    free on {0}: {1:N2} GB; needed: {2:N2} GB" -f $root, ($free / 1GB), ($need[$root] / 1GB))
        if ($free -lt $need[$root]) { throw "Not enough free space on $root." }
    }
    $Dump = Join-Path $BackupDir "leia-$Stamp-pre007.dump"
    Invoke-Native -Label "pg_dump (read-only snapshot)" -Program $PgDump -Arguments ($Connection + @("-d", "leia", "-Fc", "-f", $Dump)) | Out-Null
    $tableCount = @($live.row_counts.PSObject.Properties).Count
    $toc = Invoke-Native -Label "pg_restore --list" -Program $PgRestore -Quiet -Arguments @("--list", $Dump)
    $dataEntries = @($toc | Where-Object { $_ -match ' TABLE DATA ' }).Count
    if ((Get-Item -LiteralPath $Dump).Length -le 0 -or $dataEntries -ne $tableCount) { throw "Backup incomplete ($dataEntries of $tableCount tables)." }
    $hash = (Get-FileHash -LiteralPath $Dump -Algorithm SHA256).Hash
    Set-Content -LiteralPath "$Dump.sha256" -Value "$hash  $(Split-Path $Dump -Leaf)" -Encoding ASCII
    Write-StepLog "    $Dump ($((Get-Item -LiteralPath $Dump).Length) bytes, $dataEntries tables) SHA-256 $hash"

    # ---- 5. Restore into a new scratch copy -------------------------------------------------
    Write-StepLog "== 5. Restore into a scratch copy"
    $Scratch = "leia_step4_check_$Stamp"
    $exists = Invoke-Sql "leia" "select count(*) from pg_database where datname = '$Scratch'" "scratch name is free"
    if ($exists[0] -ne "0") { throw "Database $Scratch already exists." }
    $DbOwner = "$($live.database_owner)"
    if ($DbOwner -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') { throw "Unexpected database owner '$DbOwner'." }
    Use-Scratch $Scratch
    Invoke-Native -Label "createdb $Scratch (owner $DbOwner)" -Program $CreateDb -Arguments ($Connection + @("--maintenance-db=postgres", "--owner=$DbOwner", $Scratch)) | Out-Null
    Invoke-Native -Label "pg_restore into $Scratch" -Program $PgRestore -Arguments ($Connection + @("-d", $Scratch, "--exit-on-error", "--single-transaction", $Dump)) | Out-Null
    $CopyBefore = Join-Path $BackupDir "status-scratch-before-007-$Stamp.json"
    Invoke-Native -Label "status (scratch, json)" -Program $Python -Arguments @($Tool, "status", "--json", "--output", $CopyBefore) | Out-Null
    Invoke-Native -Label "scratch copy equals leia" -Program $Python -Arguments @($Tool, "compare-status", $LiveBefore, $CopyBefore) | Out-Null

    # ---- 6. Rehearse 007 and its rollback on the scratch copy only --------------------------
    Write-StepLog "== 6. Rehearsal on $Scratch (never leia)"
    $applied = Invoke-Native -Label "migrate_crm --expect $Migration (scratch)" -Program $Python -Arguments @($Runner, "--expect", $Migration)
    if (-not ($applied -contains "Applied $Migration")) { throw "Runner did not report applying $Migration on the scratch copy." }
    $CopyAfter = Join-Path $BackupDir "status-scratch-after-007-$Stamp.json"
    Invoke-Native -Label "status (scratch after 007, json)" -Program $Python -Arguments @($Tool, "status", "--json", "--output", $CopyAfter) | Out-Null
    Invoke-Native -Label "verify-migration (scratch)" -Program $Python -Arguments @(
        $Tool, "verify-migration", $CopyBefore, $CopyAfter, "--applied", $Migration) | Out-Null
    Invoke-Native -Label "rollback script (scratch)" -Program $Psql -Arguments ($Connection + @(
        "-d", $Scratch, "-v", "ON_ERROR_STOP=1", "-f", $Down)) | Out-Null
    $CopyRolledBack = Join-Path $BackupDir "status-scratch-rolled-back-$Stamp.json"
    Invoke-Native -Label "status (scratch after rollback, json)" -Program $Python -Arguments @($Tool, "status", "--json", "--output", $CopyRolledBack) | Out-Null
    Invoke-Native -Label "rollback restored the pre-007 copy exactly" -Program $Python -Arguments @(
        $Tool, "compare-status", $CopyBefore, $CopyRolledBack) | Out-Null
    $again = Invoke-Native -Label "007 applies again after rollback (scratch)" -Program $Python -Arguments @($Runner, "--expect", $Migration)
    if (-not ($again -contains "Applied $Migration")) { throw "007 did not apply again on the scratch copy." }
    Use-Live

    # ---- 7. Live database unchanged ---------------------------------------------------------
    Write-StepLog "== 7. Live database unchanged"
    $LiveAfter = Join-Path $BackupDir "status-live-after-step4-$Stamp.json"
    Invoke-Native -Label "status (owner, json)" -Program $Python -Arguments @($Tool, "status", "--json", "--output", $LiveAfter) | Out-Null
    Invoke-Native -Label "leia before vs after (must be identical)" -Program $Python -Arguments @($Tool, "compare-status", $LiveBefore, $LiveAfter) | Out-Null

    if ($DropScratchAfterVerify) {
        Use-Scratch $Scratch; Remove-Item Env:DB_NAME
        Invoke-Native -Label "dropdb $Scratch" -Program $DropDb -Arguments ($Connection + @("--maintenance-db=postgres", $Scratch)) | Out-Null
        Use-Live
        Write-StepLog "Scratch copy $Scratch dropped."
    } else {
        Write-StepLog "Scratch copy kept: $Scratch (now in the post-007 state). To remove it:"
        Write-StepLog "    & `"$DropDb`" -h $($settings['DB_HOST']) -p $($settings['DB_PORT']) -U $OwnerRole -W --maintenance-db=postgres $Scratch"
    }
    Write-StepLog "For step 5 (after approval) use backup stamp: $Stamp"
    Write-StepLog "RESULT: OK - step 4 rehearsal passed on a scratch copy; leia unchanged; 007 NOT applied to leia."
    $completed = $true
} catch {
    $Reason = $_.Exception.Message
} finally {
    if (-not $completed) {
        $Interrupted = -not (Test-Path Variable:Reason)
        if ($Interrupted) { $Reason = "interrupted (for example Ctrl+C)." }
        Write-StepLog "RESULT: STOPPED - $Reason"
        Write-StepLog "    leia was only read (read-only sessions); any scratch copy and backup file are left for inspection."
    }
    foreach ($name in $SecretVariables) { Remove-Item "Env:$name" -ErrorAction SilentlyContinue }
    if ($secure) { $secure.Dispose() }
    Write-Host "`nLog: $Log  (tell Claude it is done)"
    if (-not $completed -and $Interrupted -and $OwnProcess) { $host.SetShouldExit(1) }
}
if ($completed) { exit 0 } else { exit 1 }
