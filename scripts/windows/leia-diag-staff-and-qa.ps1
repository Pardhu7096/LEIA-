<#
LEIA read-only diagnostic: staff accounts and the state of the step-6 QA listing.

Shows, read-only (the session is forced read-only and checked before any query):
  1. Every staff account: email and role only (agent / admin). Also whether the stored
     email is in lower case (logins look up the lower-cased email, so a mixed-case stored
     email can never log in) and the password-hash ALGORITHM NAME only (for example
     argon2id). No password, hash, salt, token or customer email is read or shown.
  2. Number of accounts per role.
  3. The QA listing: id, title, status, version, whether it was submitted/reviewed, and
     the email and role of the account that created it.
  4. The QA listing's media: kind, room, gallery-cover flag, size in bytes (no content).
  5. Every listing whose title starts with "[QA]" (to confirm there is only one).

Changes nothing. Asks once for the postgres password; it is never logged and is removed
in finally.
  powershell -ExecutionPolicy Bypass -File D:\LEIA-schema-audit\scripts\windows\leia-diag-staff-and-qa.ps1 -QaId 0d9b999a-0a1c-4ed7-badb-2277cd9d84cd
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$QaId,
    [string]$AuditWorktree  = "D:\LEIA-schema-audit",
    [string]$BackupDir      = "D:\LEIA-backups",
    [string]$PgBin          = "C:\Program Files\PostgreSQL\18\bin",
    [string]$OwnerRole      = "postgres"
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$Stamp   = Get-Date -Format "yyyyMMdd_HHmmss"
$Log     = Join-Path $BackupDir "diag-staff-qa-$Stamp.log"
$Backend = Join-Path $AuditWorktree "backend"
$Exe     = ""
if ($env:OS -eq "Windows_NT") { $Exe = ".exe" }
$SecretVariables = @("PGPASSWORD", "PGOPTIONS")
$completed = $false
$failed = $false
$secure = $null

function Write-StepLog([string]$Text) {
    try { Add-Content -LiteralPath $Log -Value $Text -Encoding UTF8 } catch { Write-Verbose "log write failed" }
    Write-Host $Text
}

function Invoke-Sql([string]$Query, [string]$Label) {
    Write-StepLog ">>> $Label"
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $lines = @(& $Psql @Connection -d $DbName -v ON_ERROR_STOP=1 -X -A -t -F " | " -c $Query 2>&1 | ForEach-Object { "$_" })
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    foreach ($line in $lines) { Write-StepLog "    $line" }
    if ($lines.Count -eq 0) { Write-StepLog "    (no rows)" }
    if ($code -ne 0) { throw "$Label failed (exit code $code)." }
    return ,$lines
}

try {
    if (-not (Test-Path -LiteralPath $BackupDir -PathType Container)) { throw "$BackupDir does not exist." }
    Write-StepLog "LEIA read-only diagnostic (staff accounts, QA listing) - $(Get-Date -Format o)"
    $guid = [Guid]::Empty
    if (-not [Guid]::TryParse($QaId, [ref]$guid)) { throw "QaId is not a valid id." }
    $QaId = $guid.ToString()

    $Psql = Join-Path $PgBin ("psql" + $Exe)
    if (-not (Test-Path -LiteralPath $Psql -PathType Leaf)) { throw "$Psql not found." }
    $envFile = Join-Path $Backend ".env"
    if (-not (Test-Path -LiteralPath $envFile)) { $envFile = "D:\LEIA\backend\.env" }
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
    Write-StepLog "Target: $($settings['DB_HOST']):$($settings['DB_PORT'])/$DbName as $OwnerRole (read-only)"

    $env:PGOPTIONS = "-c default_transaction_read_only=on"
    $secure = Read-Host "Enter the PostgreSQL '$OwnerRole' password (input hidden)" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }

    $check = Invoke-Sql "select current_database() || ' | ' || current_setting('default_transaction_read_only');" "database and read-only guard"
    if ($check[0] -ne "$DbName | on") { throw "Read-only guard is not active; stopping before any query." }

    Write-StepLog "== 1. Staff accounts (email | role | email stored in lower case | hash algorithm)"
    Invoke-Sql @"
select email, role,
       case when email = lower(btrim(email)) then 'yes' else 'NO - cannot log in' end,
       coalesce(nullif(split_part(password_hash, '`$', 2), ''), 'unknown format')
from public.users where role in ('admin', 'agent') order by role, email;
"@ "staff accounts" | Out-Null

    Write-StepLog "== 2. Accounts per role"
    Invoke-Sql "select role, count(*) from public.users group by role order by role;" "role counts" | Out-Null

    Write-StepLog "== 3. QA listing"
    $qa = Invoke-Sql @"
select p.id, p.title, p.status, p.version,
       case when p.submitted_at is null then 'not submitted' else 'submitted' end,
       case when p.reviewed_at is null then 'not reviewed' else 'reviewed' end,
       'created by ' || u.email || ' (' || u.role || ')'
from public.properties p join public.users u on u.id = p.created_by
where p.id = '$QaId';
"@ "QA listing $QaId"
    if ($qa.Count -ne 1) { Write-StepLog "    WARNING: QA listing not found exactly once." }

    Write-StepLog "== 4. QA listing media (no content)"
    Invoke-Sql @"
select kind, coalesce(room, '-'), case when is_gallery_cover then 'gallery cover' else '-' end, octet_length(content) || ' bytes'
from public.property_media where property_id = '$QaId' order by kind, lower(coalesce(room, '')), uploaded_at;
"@ "QA media" | Out-Null

    Write-StepLog "== 5. All [QA] listings"
    Invoke-Sql "select id, status, title from public.properties where title like '[QA]%' order by created_at;" "[QA] listings" | Out-Null

    Write-StepLog "RESULT: OK - read-only diagnostic complete; nothing was changed."
    $completed = $true
} catch {
    $failed = $true
    Write-StepLog "RESULT: STOPPED - $($_.Exception.Message) (nothing was changed)"
} finally {
    if (-not $completed -and -not $failed) { Write-StepLog "RESULT: STOPPED - interrupted (nothing was changed)." }
    foreach ($name in $SecretVariables) { Remove-Item "Env:$name" -ErrorAction SilentlyContinue }
    if ($secure) { $secure.Dispose() }
    Write-Host "`nLog: $Log  (tell Claude it is done)"
}
if ($completed) { exit 0 } else { exit 1 }
