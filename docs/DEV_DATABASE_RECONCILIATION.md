# Development database: backup, restore rehearsal, migration history and 007

For the Windows development database (`leia`, PostgreSQL 18.6, port 5433). Run in
PowerShell from the audit worktree. Steps A–C change nothing. Steps D and E change the
database and need the owner's explicit approval first.

Nothing here prints a password: `--owner` and `-W` prompt for it privately.

```powershell
cd D:\LEIA-schema-audit
git status                      # untracked files are kept by the pull below
git pull --ff-only              # audit/leia-ec2-readiness
cd backend
$PY = "D:\LEIA\backend\.venv\Scripts\python.exe"
$PG = "C:\Program Files\PostgreSQL\18\bin"
$env:DB_HOST = "127.0.0.1"; $env:DB_PORT = "5433"; $env:DB_NAME = "leia"
New-Item -ItemType Directory -Force D:\LEIA-backups | Out-Null
```

## Automated A–C: `scripts/windows/leia-step1-backup-and-check.ps1`

Runs A, C and B below in one go, in Windows PowerShell 5.1 or 7 (no database change):

```powershell
powershell -ExecutionPolicy Bypass -File D:\LEIA-backups\leia-step1-backup-and-check.ps1
```

- Updates the worktree only if it is on `audit/leia-ec2-readiness`, has no changes to
  tracked files and no local commits (fast-forward only; untracked files are kept), and
  checks that the reviewed tool version is present.
- Every connection to the live database runs with `default_transaction_read_only=on`
  and the script verifies that before going on. Only `createdb`/`pg_restore` into the
  new `leia_restore_check_<timestamp>` database run without it.
- Verifies the backup by restoring it and comparing the `status --json` reports of the
  live and restored databases with `compare-status`: schema, owners, migration history,
  row counts and per-table content checksums.
- The scratch database is kept, and the log prints the `dropdb` command; add
  `-DropScratchAfterVerify` to drop it automatically after a successful comparison.
- Every command's exit code is checked; the first failure stops the run with
  `RESULT: STOPPED - <reason>`. The owner password is asked once, kept in memory, and
  removed from the environment in a `finally` block; it is never logged.
- Stop the backend first: writes between the backup and the comparison would make the
  comparison fail (safely).

## A. Why `pg_dump -U leia_app` failed (read-only)

The Python app reads `DB_USER`/`DB_PASSWORD` from `backend\.env`. `pg_dump` does not
read that file. It takes a password from the prompt, from `PGPASSWORD` or from
`%APPDATA%\postgresql\pgpass.conf`. When either of the last two is present it is
used **silently, without a prompt**, so a stale entry there fails exactly like this.

```powershell
# Settings the app uses (the password line is not shown)
Select-String -Path D:\LEIA\backend\.env -Pattern '^DB_(HOST|PORT|NAME|USER|SSLMODE)='
# Silent password sources (shows only whether they exist / which logins they cover)
"PGPASSWORD set: $([bool]$env:PGPASSWORD)  PGUSER: $env:PGUSER  PGPASSFILE: $env:PGPASSFILE"
$pgpass = "$env:APPDATA\postgresql\pgpass.conf"
if (Test-Path $pgpass) { Get-Content $pgpass | ForEach-Object { ($_ -split ':')[0..3] -join ':' } }
```

The server log states the exact reason. Find the data directory and read the last
authentication lines:

```powershell
& "$PG\psql.exe" -h 127.0.0.1 -p 5433 -U postgres -W -d leia -Atc "SHOW data_directory"
Get-ChildItem "<data_directory>\log" | Sort-Object LastWriteTime | Select-Object -Last 1 |
  Get-Content -Tail 200 | Select-String -Context 0,2 'leia_app'
```

| `DETAIL:` line in the log | Meaning |
| --- | --- |
| `Password does not match for user "leia_app"` | The password typed or found in `PGPASSWORD`/`pgpass.conf` differs from `.env` |
| `Role "leia_app" does not exist` | The app logs in as a different user (see the `.env` line above), or a different server on that port |
| `User "leia_app" has no password assigned` | The app does not use a password for this role (e.g. a `trust` line in `pg_hba.conf` for its address) |
| `Connection matched file ".../pg_hba.conf" line N` | Which rule applied: compare `127.0.0.1` vs `::1` (`localhost`) with the app's `DB_HOST` |

**Do not take the backup as `leia_app` in any case.** It is the least-privilege API
role and cannot read every table (for example `crm_schema_migrations`, which older
runners never granted to it), so `pg_dump` would stop with "permission denied" even
with the right password. Back up as the database owner (`postgres`). No change to
`pg_hba.conf` is needed.

## B. Verified backup and restore rehearsal (changes only a new scratch database)

```powershell
$DUMP = "D:\LEIA-backups\leia-$(Get-Date -Format yyyyMMdd-HHmm)-pre007.dump"
& "$PG\pg_dump.exe" -h 127.0.0.1 -p 5433 -U postgres -W -d leia -Fc -f $DUMP
& "$PG\pg_restore.exe" --list $DUMP | Select-String "TABLE DATA"     # every table listed
Get-FileHash $DUMP -Algorithm SHA256                                   # record the hash

& "$PG\createdb.exe" -h 127.0.0.1 -p 5433 -U postgres -W leia_restore_check
& "$PG\pg_restore.exe" -h 127.0.0.1 -p 5433 -U postgres -W -d leia_restore_check --exit-on-error $DUMP

# Same tables, owners, history and exact row counts in both?
& $PY reconcile_migrations.py --owner postgres > D:\LEIA-backups\status-leia.txt
$env:DB_NAME = "leia_restore_check"
& $PY reconcile_migrations.py --owner postgres > D:\LEIA-backups\status-restore.txt
$env:DB_NAME = "leia"
Compare-Object (Get-Content D:\LEIA-backups\status-leia.txt) (Get-Content D:\LEIA-backups\status-restore.txt)
```

Pass: the only difference is the `database:` line. Keep the dump outside the
repository. `leia_restore_check` can be dropped afterwards with
`& "$PG\dropdb.exe" -h 127.0.0.1 -p 5433 -U postgres -W leia_restore_check`.

## C. Migration history status (read-only)

```powershell
& $PY reconcile_migrations.py --owner postgres
```

It prints the connected user, database owner, table owners, whether
`crm_schema_migrations` exists and what it records, pending migrations, which
migration's schema the database matches exactly, 007 compatibility of the existing
media rows, and exact row counts. Decide from it:

| Status shows | Next |
| --- | --- |
| history exists, records `001`…`006`, `006: yes` | No reconciliation needed. Go to E (the runner applies `000`, a no-op here, and `007`). |
| history missing or incomplete, `006: yes` | D, then E |
| `006: no` | **Stop.** Send the output: the schema is not what 000–006 build. |
| `incompatible with 007` or duplicates above 0 | **Stop.** Send the output. |

Your earlier comparison printed only the two 007 differences. Because the expected
schema contains `crm_schema_migrations`, a missing table would have been a third
difference, so the table very probably exists and was only invisible to `leia_app`.

## D. Record already-present migrations (approval required)

`reconcile_migrations.py adopt` never runs migration SQL. Under the runner's lock and
in one transaction it compares the live schema with `tests/expected_schema_006.json`
(the exact schema 000–006 build) and records `000`…`006` only if there is no
difference at all. Otherwise it records nothing.

```powershell
& $PY reconcile_migrations.py --owner postgres adopt --through 006_request_limits.sql          # dry run
# After approval:
& $PY reconcile_migrations.py --owner postgres adopt --through 006_request_limits.sql --apply
```

## E. Apply 007 with the project's runner (approval required)

Stop the local backend, then:

```powershell
& $PY migrate_crm.py --owner postgres            # expected: Applied 007_room_panoramas.sql
& $PY reconcile_migrations.py --owner postgres   # pending: (none); same row counts as in B
```

Zero schema differences = the status line `007_room_panoramas.sql: yes` (it is the same
comparison as `schema_report.py --compare tests\expected_schema.json`). Restart the backend and check sign-in, a listing with photos and a panorama, and a
saved chat.

What 007 does: replaces `property_media_kind_check` with a wider rule (every
currently valid row stays valid) and adds the unique index
`property_media_room_panorama_unique`. It briefly locks `property_media`. The whole
run is one transaction: if anything fails, nothing is applied.

If `000` is also applied here (history without `000`), it creates nothing that exists
and re-issues the API grants: SELECT/INSERT on `users`; SELECT/INSERT/UPDATE on
`auth_sessions`; SELECT/INSERT/UPDATE/DELETE on `conversations`; SELECT/INSERT/DELETE
on `chat_messages`, which is what the API uses.

Undo, only if needed: restore `$DUMP` into a new database and point `.env` at it, or
(with no `panorama_room` rows) drop the index, restore the 004 constraint and delete
the `007_room_panoramas.sql` history row, as the owner, in one transaction.
