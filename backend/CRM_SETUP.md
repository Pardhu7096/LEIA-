# CRM drafts: parking, electricity and water

This change adds staff draft detail and update endpoints, optional amenity fields,
source/date checked, and an edit form. Photos, submission/review, customer search,
map integration and automatic staff token refresh are not part of this change.

## Apply on the existing Windows development database

After merging/pulling the changes, stop the backend, then run in PowerShell:

```powershell
cd D:\LEIA\backend
.\.venv\Scripts\python.exe .\migrate_crm.py --owner postgres
```

Enter the **local PostgreSQL owner's password** at the hidden prompt. This is not
your LEIA account password. Host, port (5433 in the original installation), and
database come from the existing backend `.env`. Do not edit or publish that file.
The owner/password override lasts only for the migration process.

If the `.env` database user already owns both CRM tables, omit `--owner postgres`.
The migration must run as the owner because the earlier tables were created in
SQL Shell as postgres, while the API uses leia_app.

The runner applies both files in one transaction, tracks them in
`public.crm_schema_migrations`, and serializes concurrent runners. Rerunning it
is safe. Migration 001 adopts the existing tables without dropping records;
002 adds fields with nullable numbers and `unknown` choices. Existing drafts and
owner details are preserved. If leia_app exists, 001 grants table SELECT/INSERT/
UPDATE. For a differently named API database role, grant those privileges to that
role separately. This is an additive migration; a code rollback can leave the
new columns in place.

Restart the backend:

```powershell
.\.venv\Scripts\python.exe -m uvicorn main:app --reload --port 8000
```

Update agent dependencies if required:

```powershell
cd D:\LEIA\agent-app
npm ci
npm run web -- --port 8082
```

Sign in, choose **Edit draft** on Madhura Apartments, fill the new sections, save,
and reopen. Verify original price, owner fields and notes remain. Blank numeric
fields mean unknown; enter 0 only when known to be zero. Parking extra cost is a
purchase charge; recurring parking costs can be described in parking notes.
Water and maintenance charges are monthly rupee amounts.

## API behavior

- `GET /staff/properties/{id}`: owner agent or admin; includes editable private
  fields and version. Another agent receives 404. Responses are not cacheable.
- `PUT /staff/properties/{id}`: full replacement of editable draft fields.
  Send title, property_type, all fields to retain, and the version returned by GET.
  Nullable fields can explicitly be cleared. Server-owned status, availability,
  owner ID, review fields and timestamps cannot be supplied by the client.
- Only draft records are editable. A stale version or non-draft returns 409.
  Version checking and public/private writes happen in the same locked transaction.
- Source options are observed, owner_reported, document_checked or unknown.
  These record the agent's account of the source, not a guarantee of verification.
- Owner email and internal review notes are not exposed or overwritten by the form.
- Customer-facing `/properties` and chat continue to use the existing sample data.

Staff tokens still expire after 15 minutes. The form retains entries on a failed
save but does not persist unsaved values across sign-out/refresh. Copy unsaved
notes before signing out; automatic refresh/offline drafts remain future work.

## Checks

```powershell
cd D:\LEIA\agent-app
npm run typecheck
npm run lint
npm run test:forms
```

Form tests use Node's TypeScript stripping support; Node 22.18+ or 24 is required.

```powershell
cd D:\LEIA\backend
.\.venv\Scripts\python.exe -m pip install -r requirements-test.txt
.\.venv\Scripts\python.exe -m pytest tests/test_crm.py -q
```

Without TEST_DATABASE_URL, database integration tests skip. For the full suite,
point TEST_DATABASE_URL at an **empty disposable** database named `crm_test` or
`leia_test`. Tests also support PGlite's ephemeral `postgres` database. Never use
the application database. The suite refuses existing public.users tables and
leaves its records for inspection; recreate the disposable database for each run.
Integration checks cover migration adoption/rerun, saved field roundtrips,
owner/admin/customer permissions, private-data isolation, stale versions,
non-draft locks, clearing optional fields and transactional rollback.
