<#
LEIA step 6: controlled functional test of room-by-room 360 panoramas on the local
development backend (approved 14 Nov 2026 19:24).

  1. Checks: worktree version; the step-1 backup taken today (its log says RESULT: OK and
     its SHA-256 matches); the backend is on this computer and answering; no QA listing
     was created before (only one is allowed).
  2. Read-only baseline of every published listing (public requests only).
  3. create --apply: ONE "[QA] Room panoramas <time>" listing through the API (asks for
     an agent and an admin login; passwords hidden, never stored) and all checks.
  4. Second read-only snapshot; every listing except the QA one must be unchanged.

It never deletes or edits existing listings and never runs the cleanup SQL.
Start the backend (D:\LEIA\backend) first, then:
  powershell -ExecutionPolicy Bypass -File D:\LEIA-schema-audit\scripts\windows\leia-step6-functional-test.ps1 -BackupStamp <stamp of today's step-1 run>
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$BackupStamp,
    [string]$ApiUrl          = "http://127.0.0.1:8000",
    [string]$AuditWorktree   = "D:\LEIA-schema-audit",
    [string]$BackupDir       = "D:\LEIA-backups",
    [string]$Python          = "D:\LEIA\backend\.venv\Scripts\python.exe",
    [string]$ExpectedBranch  = "audit/leia-ec2-readiness",
    [string]$RequiredCommit  = "2ef5ac7b3f78c81f2c240520a6301e1e41c23559"
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$Stamp  = Get-Date -Format "yyyyMMdd_HHmmss"
$Log    = Join-Path $BackupDir "step6-$Stamp.log"
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

# "checks": nothing written. "create": a QA listing may exist from here on.
$Phase = "checks"
$completed = $false
$QaId = $null
try {
    if (-not (Test-Path -LiteralPath $BackupDir -PathType Container)) { throw "$BackupDir does not exist." }
    Write-StepLog "LEIA step 6 (functional test, room panoramas) - $(Get-Date -Format o) - PowerShell $($PSVersionTable.PSVersion)"
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

    Write-StepLog "== 1d. Only one QA listing"
    New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
    $previous = @(Get-ChildItem -LiteralPath $OutDir -Filter "qa-listing-*.txt" -ErrorAction SilentlyContinue)
    if ($previous.Count -gt 0) { throw "A QA listing was already created ($($previous[0].Name)); not creating another." }

    Write-StepLog "== 2. Read-only baseline"
    Invoke-Native -Label "check-existing (before)" -Program $Python -Arguments @($QaTool, "--api", $ApiUrl, "check-existing", "--out", $OutDir) | Out-Null
    $baseline = (Get-ChildItem -LiteralPath $OutDir -Filter "existing-*.json" | Sort-Object Name | Select-Object -Last 1).FullName

    Write-StepLog "== 3. Create ONE QA listing and test (interactive logins)"
    $Phase = "create"
    $env:PYTHONUNBUFFERED = "1"
    # Run in this console (not piped) so the login prompts are visible.
    & $Python $QaTool --api $ApiUrl create --out $OutDir --apply
    $createCode = $LASTEXITCODE
    $marker = @(Get-ChildItem -LiteralPath $OutDir -Filter "qa-listing-*.txt")
    if ($marker.Count -eq 1) { $QaId = (Get-Content -LiteralPath $marker[0].FullName -TotalCount 1).Trim() }
    $run = Get-ChildItem -LiteralPath $OutDir -Filter "qa-run-*.json" | Sort-Object Name | Select-Object -Last 1
    if ($null -eq $run) { throw "create did not finish (exit $createCode); no test report written." }
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
    Write-StepLog "RESULT: OK - automated functional test passed; one QA listing created; existing listings unchanged."
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
            Write-StepLog "    A QA listing may exist (id: $(if ($QaId) { $QaId } else { 'see qa-listing-*.txt in ' + $OutDir })). Do NOT rerun; send this log to Claude."
        }
    }
    Write-Host "`nLog: $Log  (tell Claude it is done)"
    if (-not $completed -and $Interrupted -and $OwnProcess) { $host.SetShouldExit(1) }
}
if ($completed) { exit 0 } else { exit 1 }
