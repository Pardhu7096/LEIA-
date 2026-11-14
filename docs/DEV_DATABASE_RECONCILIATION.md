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
- The scratch database is created with the live database's owner (a dump without
  `-C` does not carry it), so ownership is compared like everything else.
- The scratch database is kept, and the log prints the `dropdb` command; add
  `-DropScratchAfterVerify` to drop it automatically after a successful comparison.
- Every command's exit code is checked; the first failure stops the run with
  `RESULT: STOPPED - <reason>`. The owner password is asked once, kept in memory, and
  removed from the environment in a `finally` block; it is never logged.
- Stop the backend first: writes between the backup and the comparison would make the
  comparison fail (safely).

## Step 2 (dry run only): `scripts/windows/leia-step2-dry-run.ps1`

Changes nothing. Re-checks the step-1 backup (SHA-256 and the structured
comparison of the step-1 reports), then under a verified read-only session prints
the owner status, the exact statements `adopt --through 006_request_limits.sql`
would run, and the least-privilege inventory of `leia_app` with the M12 plan written
to `D:\LEIA-backups\m12-plan-<time>.sql` (not executed). It takes a structured
status before and after and requires them to be identical.

```powershell
powershell -ExecutionPolicy Bypass -File D:\LEIA-backups\leia-step2-dry-run.ps1
```

## Step 4: prepare 007 (live database read-only) — `leia-step4-prepare-007.ps1`

`007_room_panoramas.sql` (SHA-256 `B29AF32A…6D2C9A`) replaces `property_media_kind_check`
with a wider rule (adds kind `panorama_room`: a named room of 1–60 characters, never
the gallery cover) and adds the unique index `property_media_room_panorama_unique`
(one panorama per room and listing, ignoring letter case). Every row the old rule
allows is allowed by the new one, and no `panorama_room` row can exist before 007, so
neither statement can fail on existing data. The single (legacy) panorama, cover and
gallery rules are unchanged. It briefly locks `property_media` (stop the backend).

The script, with every connection to `leia` read-only: checks the worktree and the
007 file hash; requires `leia` to equal the step-3 result and 007 to be the only
pending migration with no incompatible rows; runs `migrate_crm.py --dry-run --expect
007_room_panoramas.sql`; takes a fresh backup; restores it into
`leia_step4_check_<time>` (compared Identical); on that copy only, applies 007 with
the runner and checks `verify-migration`, runs the rollback script and checks the copy
is Identical to before, and applies 007 again; finally requires `leia` to be Identical
before and after. `leia` is never written.

```powershell
git -C D:\LEIA-schema-audit pull --ff-only
powershell -ExecutionPolicy Bypass -File D:\LEIA-schema-audit\scripts\windows\leia-step4-prepare-007.ps1
```

### Database-target safeguards (step 4 and 5)

Python tools take the database from `DB_NAME`; an environment variable wins and
`backend/.env` (`DB_NAME=leia`) only fills a variable that is not set. So a script that
failed to set `DB_NAME` would reach `leia`, and an empty `DB_NAME` reaches the driver's
fallback database. Therefore every runner call that can write passes
`--expect-database <name>`, which `migrate_crm.py` checks against `current_database()`
on the same connection before taking the lock or running anything, and refuses on a
mismatch; the command line refuses any writing run without it. Rollbacks go through
the same runner (`--rollback`). Every status report the scripts use must name the
database it was meant for; the scratch copy must be new and empty before the restore;
`createdb`, `pg_restore` and `dropdb` only ever receive the checked scratch name, never
`leia`; every connection to `leia` in step 4 is read-only. `test_database_target.py`
reproduces each mistake and shows `leia` unchanged.

## Step 5: apply 007 (needs approval) — `leia-step5-apply-007.ps1 -BackupStamp <step-4 stamp>`

Pre-checks (read-only): worktree and 007 hash; the step-4 backup hash; `leia` still
equals the state the step-4 backup was taken from; dry run shows only 007. Apply: the
project runner, one transaction, `--expect 007_room_panoramas.sql`. After:
`verify-migration` (only 007's two schema changes; every table's rows and content
checksums unchanged apart from one history row), history 000–007 once each, nothing
pending, and the app's own login sees the same. A stop during the apply is reported as
COMMIT OUTCOME UNCERTAIN (do not rerun; inspect first); only a verified run exits 0.

## Rollback of 007

`backend/rollback/007_room_panoramas.down.sql` (outside the migration chain), as the
owner with the backend stopped, one transaction, through the runner so the target is
checked on the connection that writes:
`python migrate_crm.py --owner postgres --expect-database leia --rollback 007_room_panoramas.sql`.
It refuses unless 007 is recorded and no room panoramas exist (deleting customer
media is a separate decision), then restores 004's rule exactly, drops the room index
and removes the 007 history row; tests show the result is Identical to the pre-007
state and 007 can be applied again. With room panoramas present, or if anything else
went wrong: restore the step-4 backup into a new database and point `.env` at it.
Before 007 (or after a rollback) the room-panorama endpoints fail safely (HTTP 500,
nothing written); everything else works.

## M12: least privilege for the API role (plan; needs approval to apply)

`backend/api_privileges.py` holds `REQUIRED`, the table privileges the API needs.
It is derived from every SQL statement in the API (row locks and `ON CONFLICT DO
UPDATE` need UPDATE; `WHERE`/`RETURNING` need SELECT) and equals what the migrations
grant; a test keeps the two equal. No sequence privilege is needed (identity column).

| Table | `leia_app` needs |
| --- | --- |
| users | SELECT, INSERT |
| auth_sessions | SELECT, INSERT, UPDATE |
| conversations | SELECT, INSERT, UPDATE, DELETE |
| chat_messages | SELECT, INSERT, DELETE |
| properties, property_private_details | SELECT, INSERT, UPDATE |
| property_media, request_limits | SELECT, INSERT, UPDATE, DELETE |
| crm_schema_migrations | SELECT (readiness check) |

The plan (generated by step 2 from the live inventory) moves ownership of the
database and of `users`, `auth_sessions`, `conversations`, `chat_messages` (with
their indexes and identity sequence) to `postgres`, revokes everything from
`leia_app` and grants exactly the table above, plus CONNECT and schema USAGE. One
transaction with a 5-second lock timeout: on any error nothing changes.

Tested on a development-shaped database: the plan makes the role compliant and is
safe to rerun; the whole API then works as that role; CREATE TABLE/SCHEMA, ALTER,
DROP, TRUNCATE, `UPDATE users` (role escalation), history writes and sequence use
are refused. Removing any required privilege or adding an unneeded one fails the test.

Applying it (after approval): stop the backend, take a fresh backup (step 1), run the
reviewed plan file as `postgres` with `psql -v ON_ERROR_STOP=1 -f`, rerun
`api_privileges.py` (expect "least privilege: compliant"), start the backend and
smoke-test. Undo: the plan's inverse (owners back to `leia_app`) or the backup.
Consequence: from then on, migrations must run as `postgres` (`migrate_crm.py
--owner postgres`), which is already the documented practice.

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
& $PY migrate_crm.py --owner postgres --expect-database leia   # expected: Applied 007_room_panoramas.sql
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
