<#
LEIA step 1 (version 2): update the audit worktree safely, diagnose the pg_dump login
failure, back up the `leia` database as its owner, and verify the backup by restoring
it into a NEW scratch database and comparing structure, history, row counts and
per-table content checksums.

The live database is never changed:
  * every connection to it runs with default_transaction_read_only=on (PGOPTIONS),
    and the script checks that this setting is active before going further;
  * pg_dump only reads (one read-only snapshot, ACCESS SHARE locks);
  * the restore names the scratch database explicitly, refuses an existing name or
    the live name, and the dump is made without -C, so it cannot target `leia`.

Run in Windows PowerShell 5.1 (or PowerShell 7), preferably with the LEIA backend
stopped so data does not change between the backup and the comparison:

  powershell -ExecutionPolicy Bypass -File D:\LEIA-backups\leia-step1-backup-and-check.ps1

Add -DropScratchAfterVerify to delete the scratch database after a successful
comparison (it never drops anything else). Without it, the scratch database is kept
and the log prints the command that drops it.

Asks once for the owner password. It is kept in memory for this run only, passed to
child processes through environment variables that are removed in a finally block,
and never written to the log or to disk.
#>
[CmdletBinding()]
param(
    [string]$AuditWorktree  = "D:\LEIA-schema-audit",
    [string]$BackupDir      = "D:\LEIA-backups",
    [string]$Python         = "D:\LEIA\backend\.venv\Scripts\python.exe",
    [string]$PgBin          = "C:\Program Files\PostgreSQL\18\bin",
    [string]$ExpectedBranch = "audit/leia-ec2-readiness",
    [string]$RequiredCommit = "58258b6e8eaf6f56fc3ab2f3986a740369dd5816",
    [string]$OwnerRole      = "postgres",
    [switch]$NoPull,
    [switch]$DropScratchAfterVerify
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$Stamp   = Get-Date -Format "yyyyMMdd_HHmmss"
$Log     = Join-Path $BackupDir "step1-$Stamp.log"
$Backend = Join-Path $AuditWorktree "backend"
$Tool    = Join-Path $Backend "reconcile_migrations.py"
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

function Get-FreeBytes([string]$Root) {
    return (New-Object System.IO.DriveInfo($Root)).AvailableFreeSpace
}

function Get-Root([string]$Path) {
    return [System.IO.Path]::GetPathRoot([System.IO.Path]::GetFullPath($Path))
}

function Set-ReadOnlySessions([bool]$On) {
    if ($On) { $env:PGOPTIONS = $ReadOnlyOption } else { Remove-Item Env:PGOPTIONS -ErrorAction SilentlyContinue }
}

$failed = $false
$completed = $false
$secure = $null
try {
    if (-not (Test-Path -LiteralPath $BackupDir -PathType Container)) { throw "$BackupDir does not exist." }
    Write-StepLog "LEIA step 1 v2 - $(Get-Date -Format o) - PowerShell $($PSVersionTable.PSVersion)"
    foreach ($name in $SecretVariables) {
        if (Test-Path "Env:$name") { throw "Environment variable $name is already set in this window; open a new PowerShell window." }
    }

    # ---- 1. Tools ----------------------------------------------------------------
    $Psql      = Get-Tool "psql"
    $PgDump    = Get-Tool "pg_dump"
    $PgRestore = Get-Tool "pg_restore"
    $CreateDb  = Get-Tool "createdb"
    $DropDb    = Get-Tool "dropdb"
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "$Python not found." }
    Invoke-Native -Label "pg_dump version" -Program $PgDump -Arguments @("--version") | Out-Null

    # ---- 2. Password sources pg_dump uses silently (values never shown) -------------
    Write-StepLog "== Password sources"
    Write-StepLog ("PGUSER: '{0}'; PGPASSFILE: '{1}'" -f $env:PGUSER, $env:PGPASSFILE)
    $pgpass = $null
    if ($env:APPDATA) { $pgpass = Join-Path $env:APPDATA "postgresql\pgpass.conf" }
    if ($pgpass -and (Test-Path -LiteralPath $pgpass)) {
        Write-StepLog "pgpass.conf exists; entries (host:port:db:user, password omitted):"
        foreach ($line in Get-Content -LiteralPath $pgpass) {
            if ($line -and -not $line.StartsWith("#")) { Write-StepLog ("    " + (($line -split ":")[0..3] -join ":")) }
        }
    } else { Write-StepLog "pgpass.conf: none" }

    # ---- 3. Database settings from the worktree's .env (no secret lines read out) ---
    Write-StepLog "== Database settings"
    $settings = @{}
    $envFile = Join-Path $Backend ".env"
    if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) { throw "$envFile not found." }
    foreach ($line in Get-Content -LiteralPath $envFile) {
        if ($line -match '^(DB_HOST|DB_PORT|DB_NAME|DB_USER|DB_SSLMODE)=(.*)$') {
            Write-StepLog "    $line"
            $settings[$Matches[1]] = $Matches[2].Trim()
        }
    }
    foreach ($key in @("DB_HOST", "DB_PORT", "DB_NAME")) {
        if (-not $settings.ContainsKey($key) -or -not $settings[$key]) { throw "$key missing from $envFile." }
    }
    $DbHost = $settings["DB_HOST"]; $DbPort = $settings["DB_PORT"]; $DbName = $settings["DB_NAME"]
    if ($DbPort -notmatch '^\d{1,5}$') { throw "DB_PORT is not a port number." }
    if ($DbName -notmatch '^[A-Za-z0-9_]+$') { throw "DB_NAME has unexpected characters." }
    $LocalServer = @("localhost", "127.0.0.1", "::1") -contains $DbHost.ToLower()
    Write-StepLog "Target: host=$DbHost port=$DbPort database=$DbName (local server: $LocalServer)"
    $Connection = @("-h", $DbHost, "-p", $DbPort, "-U", $OwnerRole, "-w")

    # ---- 4. Audit worktree: right branch, no local changes, fast-forward only ------
    Write-StepLog "== Audit worktree"
    $git = "git"
    $branch = (Invoke-Native -Label "current branch" -Program $git -Arguments @("-C", $AuditWorktree, "rev-parse", "--abbrev-ref", "HEAD"))[0]
    if ($branch -ne $ExpectedBranch) { throw "Worktree is on '$branch', expected '$ExpectedBranch'. Nothing changed." }
    $tracked = Invoke-Native -Label "tracked changes (must be none)" -Program $git -Arguments @(
        "-C", $AuditWorktree, "status", "--porcelain=v1", "--untracked-files=no")
    if ($tracked.Count -gt 0) { throw "Worktree has uncommitted changes to tracked files. Nothing changed." }
    $untracked = Invoke-Native -Label "untracked files (kept)" -Program $git -Quiet -Arguments @(
        "-C", $AuditWorktree, "status", "--porcelain=v1", "--untracked-files=all")
    Write-StepLog "    $($untracked.Count) untracked file(s); git never deletes them and refuses to overwrite them."
    if (-not $NoPull) {
        Invoke-Native -Label "git fetch" -Program $git -Arguments @("-C", $AuditWorktree, "fetch", "origin", $ExpectedBranch) | Out-Null
        $ahead = (Invoke-Native -Label "local commits not on GitHub (must be 0)" -Program $git -Arguments @(
            "-C", $AuditWorktree, "rev-list", "--count", "FETCH_HEAD..HEAD"))[0]
        if ($ahead -ne "0") { throw "Worktree has $ahead local commit(s) not on GitHub. Nothing changed." }
        Invoke-Native -Label "fast-forward" -Program $git -Arguments @("-C", $AuditWorktree, "merge", "--ff-only", "FETCH_HEAD") | Out-Null
    }
    try {
        Invoke-Native -Label "required commit present" -Program $git -Arguments @(
            "-C", $AuditWorktree, "merge-base", "--is-ancestor", $RequiredCommit, "HEAD") | Out-Null
    } catch {
        throw "Worktree does not contain commit $RequiredCommit (the reviewed tool version). Run without -NoPull, or update the worktree."
    }
    Invoke-Native -Label "worktree head" -Program $git -Arguments @("-C", $AuditWorktree, "log", "--oneline", "-3") | Out-Null
    if (-not (Test-Path -LiteralPath $Tool -PathType Leaf)) { throw "$Tool not found after update." }

    # ---- 5. Status as the app's own login (.env), read-only ------------------------
    Write-StepLog "== Status as the app login from .env (diagnostic; exit 2 = cannot read history)"
    Set-ReadOnlySessions $true
    Invoke-Native -Label "status (app login)" -Program $Python -Arguments @($Tool, "status") -Allowed @(0, 1, 2) | Out-Null

    # ---- 6. Owner password, once --------------------------------------------------
    $secure = Read-Host "Enter the PostgreSQL '$OwnerRole' password (input hidden)" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
    $env:DB_PASSWORD = $env:PGPASSWORD
    $env:DB_USER = $OwnerRole

    $check = Invoke-Sql $DbName "select current_user || '|' || current_setting('default_transaction_read_only')" "owner sign-in and read-only guard"
    Write-StepLog "    $($check[0])"
    if ($check[0] -ne "$OwnerRole|on") { throw "Read-only guard is not active; stopping before any further step." }

    # ---- 7. Why did leia_app's login fail? (diagnostic, never stops the run) --------
    Write-StepLog "== Login diagnosis"
    try {
        Invoke-Native -Label "leia_app role" -Program $Psql -Arguments ($Connection + @("-d", $DbName, "-Atc",
            "select rolname || ' login=' || rolcanlogin || ' has_password=' || (rolpassword is not null) from pg_authid where rolname = 'leia_app'")) | Out-Null
        Invoke-Native -Label "authentication rules (pg_hba.conf)" -Program $Psql -Arguments ($Connection + @("-d", $DbName, "-Atc",
            "select line_number || ' ' || type || ' ' || array_to_string(database, ',') || ' ' || array_to_string(user_name, ',') || ' ' || coalesce(address, '') || ' ' || coalesce(auth_method, '') || ' ' || coalesce(error, '') from pg_hba_file_rules order by line_number")) | Out-Null
        $logLines = Invoke-Sql $DbName ("select l from (select name, modification from pg_ls_logdir() order by modification desc limit 3) f, " +
            "lateral (select current_setting('log_directory') || '/' || f.name as p) x, " +
            "lateral regexp_split_to_table(pg_read_file(x.p, greatest((pg_stat_file(x.p)).size - 2000000, 0), 2000000), E'\n') l " +
            "where l ~ 'FATAL:.*leia_app' or l ~ 'DETAIL:.*(password|Role|matched)'") "server log lines"
        $shown = 0
        foreach ($line in $logLines) { if ($shown -lt 60) { Write-StepLog "    $line"; $shown++ } }
        if ($logLines.Count -eq 0) { Write-StepLog "    (no matching lines in the newest 3 log files)" }
    } catch {
        Write-StepLog "    Diagnosis incomplete: $($_.Exception.Message)"
    }

    # ---- 8. Owner status (structured) ---------------------------------------------
    Write-StepLog "== Owner status, read-only"
    $LiveJson = Join-Path $BackupDir "status-live-$Stamp.json"
    Invoke-Native -Label "status (owner, text)" -Program $Python -Arguments @($Tool, "status") | Out-Null
    Invoke-Native -Label "status (owner, json)" -Program $Python -Arguments @($Tool, "status", "--json", "--output", $LiveJson) | Out-Null
    $live = Get-Content -LiteralPath $LiveJson -Raw | ConvertFrom-Json
    $tableCount = @($live.row_counts.PSObject.Properties).Count
    if ($tableCount -eq 0) { throw "Status reports no tables." }
    Write-StepLog "    tables: $tableCount; pending: $(@($live.pending) -join ', ')"

    # ---- 9. Space ---------------------------------------------------------------
    $sizeText = (Invoke-Sql $DbName "select pg_database_size(current_database())" "database size")[0]
    [int64]$dbBytes = 0
    if (-not [int64]::TryParse("$sizeText", [ref]$dbBytes) -or $dbBytes -le 0) { throw "Could not read the database size ('$sizeText')." }
    Write-StepLog ("Database size: {0:N1} MB" -f ($dbBytes / 1MB))
    $need = @{}
    $need[(Get-Root $BackupDir)] = [int64]($dbBytes + 512MB)
    if ($LocalServer) {
        $dataDir = (Invoke-Sql $DbName "show data_directory" "data directory")[0]
        $dataRoot = Get-Root $dataDir
        $extra = [int64](2 * $dbBytes + 1GB)      # restored copy plus write-ahead log
        if ($need.ContainsKey($dataRoot)) { $need[$dataRoot] += $extra } else { $need[$dataRoot] = $extra }
        Write-StepLog "Data directory: $dataDir"
    } else {
        Write-StepLog "Database server is not local: its free space is not checked here."
    }
    foreach ($root in @($need.Keys)) {
        $free = Get-FreeBytes $root
        Write-StepLog ("Free on {0}: {1:N2} GB; needed: {2:N2} GB" -f $root, ($free / 1GB), ($need[$root] / 1GB))
        if ($free -lt $need[$root]) { throw "Not enough free space on $root." }
    }

    # ---- 10. Backup (read-only snapshot) ---------------------------------------------
    Write-StepLog "== Backup"
    $Dump = Join-Path $BackupDir "leia-$Stamp-pre007.dump"
    if (Test-Path -LiteralPath $Dump) { throw "$Dump already exists." }
    Invoke-Native -Label "pg_dump" -Program $PgDump -Arguments ($Connection + @("-d", $DbName, "-Fc", "-f", $Dump)) | Out-Null
    $dumpBytes = (Get-Item -LiteralPath $Dump).Length
    if ($dumpBytes -le 0) { throw "Dump file is empty." }
    $toc = Invoke-Native -Label "pg_restore --list" -Program $PgRestore -Quiet -Arguments @("--list", $Dump)
    $dataEntries = @($toc | Where-Object { $_ -match ' TABLE DATA ' }).Count
    Write-StepLog "    $dumpBytes bytes; $dataEntries TABLE DATA entries (live tables: $tableCount)"
    if ($dataEntries -ne $tableCount) { throw "Dump has $dataEntries tables with data, the database has $tableCount." }
    $hash = (Get-FileHash -LiteralPath $Dump -Algorithm SHA256).Hash
    Set-Content -LiteralPath "$Dump.sha256" -Value "$hash  $(Split-Path $Dump -Leaf)" -Encoding ASCII
    Write-StepLog "    SHA256 $hash"

    # ---- 11. Restore rehearsal into a NEW database -----------------------------------
    Write-StepLog "== Restore rehearsal"
    $Scratch = "leia_restore_check_$Stamp"
    if ($Scratch -eq $DbName -or $Scratch -notmatch '^leia_restore_check_\d{8}_\d{6}$') { throw "Unsafe scratch name '$Scratch'." }
    $exists = Invoke-Sql $DbName "select count(*) from pg_database where datname = '$Scratch'" "scratch name is free"
    if ($exists[0] -ne "0") { throw "Database $Scratch already exists." }
    Set-ReadOnlySessions $false   # creating and filling the scratch database needs writes
    Invoke-Native -Label "createdb $Scratch" -Program $CreateDb -Arguments ($Connection + @("--maintenance-db=postgres", $Scratch)) | Out-Null
    Invoke-Native -Label "pg_restore into $Scratch" -Program $PgRestore -Arguments (
        $Connection + @("-d", $Scratch, "--exit-on-error", "--single-transaction", $Dump)) | Out-Null
    Set-ReadOnlySessions $true

    $RestoredJson = Join-Path $BackupDir "status-restored-$Stamp.json"
    $env:DB_NAME = $Scratch
    try {
        Invoke-Native -Label "status (restored copy, json)" -Program $Python -Arguments @($Tool, "status", "--json", "--output", $RestoredJson) | Out-Null
    } finally { Remove-Item Env:DB_NAME -ErrorAction SilentlyContinue }
    Invoke-Native -Label "compare live and restored (schema, history, counts, checksums)" -Program $Python -Arguments @(
        $Tool, "compare-status", $LiveJson, $RestoredJson) | Out-Null

    # ---- 12. Scratch database ---------------------------------------------------------
    if ($DropScratchAfterVerify) {
        Set-ReadOnlySessions $false
        Invoke-Native -Label "dropdb $Scratch" -Program $DropDb -Arguments ($Connection + @("--maintenance-db=postgres", $Scratch)) | Out-Null
        Set-ReadOnlySessions $true
        Write-StepLog "Scratch database $Scratch dropped after successful verification."
    } else {
        Write-StepLog "Scratch database kept: $Scratch. To remove it after you have looked at it:"
        Write-StepLog "    & `"$DropDb`" -h $DbHost -p $DbPort -U $OwnerRole -W --maintenance-db=postgres $Scratch"
    }
    Write-StepLog "RESULT: OK - backup $Dump verified by restore (schema, history, row counts and content checksums identical)."
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
