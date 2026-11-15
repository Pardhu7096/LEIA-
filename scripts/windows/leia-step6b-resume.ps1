<#
LEIA step 6b: resume the step-6 functional test on the EXISTING QA listing (needs approval).

Step 6 (log step6-20261114_195648.log) created the QA listing, ran the agent-side steps and
submitted it, then stopped at the admin login (401). This finishes that run without
creating another listing:

  1. Checks, before any change: worktree version; today's step-1 backup (RESULT: OK and
     matching SHA-256); backend on this computer; exactly one qa-listing-*.txt and it
     names -QaId; no qa-run report yet; the stopped step-6 log names the same id.
  2. Read-only snapshot; every listing except the QA one must equal the step-6 baseline.
  3. resume --apply: admin login (up to 3 tries; a refused login changes nothing), confirms
     the account is an admin, re-checks the QA listing (title, 'submitted', 2 rooms,
     2 photos, single panorama, cover), approves it, runs the customer checks.
  4. Second read-only snapshot; every listing except the QA one unchanged.

The only database change is the QA listing's approval (and the admin login session).
It never creates, deletes or edits anything else and never runs the cleanup SQL.
  powershell -ExecutionPolicy Bypass -File D:\LEIA-schema-audit\scripts\windows\leia-step6b-resume.ps1 -BackupStamp <today's step-1 stamp> -QaId 0d9b999a-0a1c-4ed7-badb-2277cd9d84cd
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$BackupStamp,
    [Parameter(Mandatory = $true)][string]$QaId,
    [string]$StoppedLog      = "step6-20261114_195648.log",
    [string]$Baseline        = "existing-20261114_195650.json",
    [string]$ApiUrl          = "http://127.0.0.1:8000",
    [string]$AuditWorktree   = "D:\LEIA-schema-audit",
    [string]$BackupDir       = "D:\LEIA-backups",
    [string]$Python          = "D:\LEIA\backend\.venv\Scripts\python.exe",
    [string]$ExpectedBranch  = "audit/leia-ec2-readiness",
    [string]$RequiredCommit  = "52cb9e92d15d2735fdc3ac639bad0ae782630104"
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$Stamp  = Get-Date -Format "yyyyMMdd_HHmmss"
$Log    = Join-Path $BackupDir "step6b-$Stamp.log"
$OutDir = Join-Path $BackupDir "room-panorama-test"
$QaTool = Join-Path (Join-Path (Join-Path $AuditWorktree "scripts") "functional") "room_panorama_check.py"
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

# "checks": nothing written. "approve": the QA listing may be approved from here on.
$Phase = "checks"
$completed = $false
try {
    if (-not (Test-Path -LiteralPath $BackupDir -PathType Container)) { throw "$BackupDir does not exist." }
    Write-StepLog "LEIA step 6b (resume functional test on the existing QA listing) - $(Get-Date -Format o) - PowerShell $($PSVersionTable.PSVersion)"
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "$Python not found." }

    Write-StepLog "== 1a. Worktree"
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

    Write-StepLog "== 1b. Today's verified backup"
    if ($BackupStamp -notmatch '^\d{8}_\d{6}$') { throw "BackupStamp must look like 20261114_193000." }
    if ($BackupStamp.Substring(0, 8) -ne (Get-Date -Format "yyyyMMdd")) { throw "Backup $BackupStamp is not from today; run step 1 first." }
    $Step1Log = Join-Path $BackupDir "step1-$BackupStamp.log"
    if (-not (Test-Path -LiteralPath $Step1Log)) { throw "$Step1Log not found." }
    if (-not (Select-String -LiteralPath $Step1Log -Pattern '^RESULT: OK' -Quiet)) { throw "Step-1 run $BackupStamp did not end with RESULT: OK." }
    $Dump = Join-Path $BackupDir "leia-$BackupStamp-pre007.dump"
    $recordedHash = ((Get-Content -LiteralPath "$Dump.sha256" -TotalCount 1) -split '\s+')[0]
    $actualHash = (Get-FileHash -LiteralPath $Dump -Algorithm SHA256).Hash
    if ($recordedHash -ne $actualHash) { throw "Backup checksum does not match." }
    Write-StepLog "    $Dump verified (SHA-256 $actualHash; restore-tested by step 1)"

    Write-StepLog "== 1c. Backend on this computer"
    $uri = [Uri]$ApiUrl
    if ($uri.Scheme -ne "http" -or @("127.0.0.1", "localhost", "[::1]") -notcontains $uri.Host -or $uri.AbsolutePath -ne "/") {
        throw "ApiUrl must be http://127.0.0.1:<port> or http://localhost:<port>."
    }
    try { $health = Invoke-WebRequest -Uri ($ApiUrl.TrimEnd("/") + "/health") -UseBasicParsing -TimeoutSec 10 }
    catch { throw "No backend answering at $ApiUrl. Start it from D:\LEIA\backend first." }
    Write-StepLog "    $ApiUrl/health -> $($health.StatusCode)"

    Write-StepLog "== 1d. The existing QA listing"
    $guid = [Guid]::Empty
    if (-not [Guid]::TryParse($QaId, [ref]$guid)) { throw "QaId is not a valid id." }
    $QaId = $guid.ToString()
    $marker = @(Get-ChildItem -LiteralPath $OutDir -Filter "qa-listing-*.txt" -ErrorAction SilentlyContinue)
    if ($marker.Count -ne 1) { throw "Expected exactly one qa-listing-*.txt in $OutDir, found $($marker.Count)." }
    $recorded = (Get-Content -LiteralPath $marker[0].FullName -TotalCount 1).Trim()
    if ($recorded -ne $QaId) { throw "$($marker[0].Name) records $recorded, not $QaId." }
    if (@(Get-ChildItem -LiteralPath $OutDir -Filter "qa-run-*.json" -ErrorAction SilentlyContinue).Count -gt 0) {
        throw "A qa-run report already exists; the test already finished."
    }
    $stopped = Join-Path $BackupDir $StoppedLog
    if (-not (Test-Path -LiteralPath $stopped)) { throw "$stopped not found." }
    if (-not (Select-String -LiteralPath $stopped -SimpleMatch "id: $QaId" -Quiet)) { throw "$StoppedLog does not name $QaId." }
    $BaselinePath = Join-Path $OutDir $Baseline
    if (-not (Test-Path -LiteralPath $BaselinePath)) { throw "$BaselinePath not found." }
    Write-StepLog "    $($marker[0].Name) -> $QaId; baseline $Baseline; no report yet"
    $QaId = $recorded

    Write-StepLog "== 2. Read-only: listings unchanged since the step-6 baseline"
    Invoke-Native -Label "check-existing (before)" -Program $Python -Arguments @($QaTool, "--api", $ApiUrl, "check-existing", "--out", $OutDir) | Out-Null
    $baseline = (Get-ChildItem -LiteralPath $OutDir -Filter "existing-*.json" | Sort-Object Name | Select-Object -Last 1).FullName
    Invoke-Native -Label "compare with the step-6 baseline (QA listing ignored)" -Program $Python -Arguments @(
        $QaTool, "compare", $BaselinePath, $baseline, "--ignore", $QaId) | Out-Null

    Write-StepLog "== 3. Resume on the existing QA listing (interactive admin login)"
    $Phase = "approve"
    $env:PYTHONUNBUFFERED = "1"
    & $Python $QaTool --api $ApiUrl resume --out $OutDir --listing-id $QaId --baseline $BaselinePath --apply
    $createCode = $LASTEXITCODE
    $run = Get-ChildItem -LiteralPath $OutDir -Filter "qa-run-*.json" | Sort-Object Name | Select-Object -Last 1
    if ($createCode -eq 3) { $Phase = "checks"; throw "resume stopped before approving (see the message above); the QA listing is unchanged." }
    if ($null -eq $run) { throw "resume did not finish (exit $createCode); no test report written." }
    $report = Get-Content -LiteralPath $run.FullName -Raw | ConvertFrom-Json
    foreach ($r in @($report.results)) { Write-StepLog ("    {0}  {1}" -f $(if ($r.ok) { "PASS" } else { "FAIL" }), $r.check) }
    Write-StepLog "    QA listing: $($report.listing_id)  $($report.title)"
    if ($createCode -ne 0) { throw "Automated checks failed (exit $createCode); see above." }

    Write-StepLog "== 4. Existing listings unchanged"
    Invoke-Native -Label "check-existing (after)" -Program $Python -Arguments @($QaTool, "--api", $ApiUrl, "check-existing", "--out", $OutDir) | Out-Null
    $after = (Get-ChildItem -LiteralPath $OutDir -Filter "existing-*.json" | Sort-Object Name | Select-Object -Last 1).FullName
    Invoke-Native -Label "compare (QA listing ignored)" -Program $Python -Arguments @(
        $QaTool, "compare", $baseline, $after, "--ignore", $report.listing_id) | Out-Null

    Write-StepLog "Next: the manual customer-app and agent-app checks in docs\FUNCTIONAL_TEST_ROOM_PANORAMAS.md."
    Write-StepLog "The QA listing is kept; delete it only after approval (SQL printed above by the tool)."
    Write-StepLog "RESULT: OK - resumed on the existing QA listing; automated checks passed; no listing created; existing listings unchanged."
    $completed = $true
} catch {
    $Reason = $_.Exception.Message
} finally {
    Remove-Item Env:PYTHONUNBUFFERED -ErrorAction SilentlyContinue
    if (-not $completed) {
        $Interrupted = -not (Test-Path Variable:Reason)
        if ($Interrupted) { $Reason = "interrupted (for example Ctrl+C)." }
        if ($Phase -eq "checks") {
            Write-StepLog "RESULT: STOPPED BEFORE ANY CHANGE - $Reason"
        } else {
            Write-StepLog "RESULT: STOPPED - $Reason"
            Write-StepLog "    The QA listing ($QaId) may or may not be approved now. Do NOT rerun; send this log to Claude."
        }
    }
    Write-Host "`nLog: $Log  (tell Claude it is done)"
    if (-not $completed -and $Interrupted -and $OwnProcess) { $host.SetShouldExit(1) }
}
if ($completed) { exit 0 } else { exit 1 }
