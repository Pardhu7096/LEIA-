# LEIA — AWS EC2 deployment readiness audit

- **Date:** 13 November 2026 · **Target:** safe production deployment by 15 November 2026
- **Audited commit:** `e99ffc3` (branch `codex/customer-quality-checks`) · **Audit branch:** `audit/leia-ec2-readiness`
- **Draft pull request:** https://github.com/Pardhu7096/LEIA-/pull/6 (into `codex/customer-quality-checks`; see C1 for why not `main`)
- **Limits of this audit:** nothing was provisioned, deployed or changed on AWS, GitHub settings or any
  real database. "Verified" below means reproduced in a local sandbox (Ubuntu 24.04, PostgreSQL 16,
  Nginx 1.24, Python 3.13, Node 22) or read from the GitHub API. It never means validated on AWS.

## Conclusion: NOT READY FOR STAGING

Three things stand between this repository and a first staging deployment, all verified:

1. **The delivery workflow cannot be started.** PR #5 was merged into `codex/customer-quality-checks`,
   not `main`. `main` is still the single initial commit (`fce7de8`) with no workflows, no deploy
   script, no CRM. GitHub only offers a manually started workflow when its file is on the default
   branch, and its Actions API lists one registered workflow: "CRM checks".
2. **No staging server exists that this pipeline has been pointed at**, and the GitHub environments
   and secrets it needs could not be confirmed (that API is not readable from this audit).
3. **The fixes that make a deployment able to succeed are on this audit branch, unreviewed.** At the
   audited commit an empty server database could not be built, both apps called `http://localhost:8000`,
   and a failed first deployment left the release link pointing at itself.

It becomes READY FOR STAGING when: this branch is reviewed and merged, the integration branch is merged
to `main`, a staging server is prepared per `docs/EC2_RUNBOOK.md`, and the `staging` environment
secrets are set. A production deployment on 15 November would mean skipping a staging deployment, a
real smoke test, backups with a tested restore, and monitoring; none of those exist yet.

## 1. Readiness score

Each criterion: 0 = missing or broken, 1 = present but incomplete or only locally verified,
2 = verified working. Evidence is in the findings and test sections.

| # | Criterion | At `e99ffc3` | With this branch | Evidence |
| --- | --- | --- | --- | --- |
| 1 | Deployable code and delivery workflow on the default branch | 0 | 0 | `main` = 1 commit; workflow not registered (C1) |
| 2 | Tests gate every delivery | 2 | 2 | `verify` job precedes `deploy`; CI green on `e99ffc3`; suite passes locally |
| 3 | Database can be built and migrated from the repository | 0 | 1 | `migrate_crm.py` failed on an empty database; baseline added and reconciled with the reported development-schema differences, tested on PostgreSQL 18.6 and 16; one read-only comparison against the real development database outstanding (C2) |
| 4 | Settings and secrets handling | 1 | 2 | No secrets in any branch history; documented `.env` was incomplete; now template + preflight (H5) |
| 5 | Failed deployments detected, rolled back, never reported as success | 1 | 2 | Two script defects reproduced and fixed; readiness probe; 16 script tests (H1–H3) |
| 6 | API security: sign-in, roles, uploads, request limits | 2 | 2 | Code review plus 36-step local smoke test as the least-privilege database role |
| 7 | Apps reach the API over HTTPS, no localhost | 0 | 1 | Hard-coded URLs replaced by build setting; not yet built against a real domain (C3, H4) |
| 8 | Staging/production isolation and production safeguards | 1 | 1 | Workflow gates are sound; `main` is unprotected; environment rules unverified (H6, M3) |
| 9 | Monitoring, backup and recovery | 0 | 1 | Runbook and readiness endpoint exist; nothing is configured or tested (H7) |
| 10 | Infrastructure provisioned and validated | 0 | 0 | Out of scope by instruction; owner checklist in section 5 |
| | **Total** | **7 / 20** | **12 / 20** | Staging needs criterion 1 at 2 and criteria 3, 7, 10 at ≥ 1 |

## 2. Findings

Status: **Fixed** = changed on this branch and tested locally · **Open** = needs the owner.

### Critical

| ID | Finding | Files | Status |
| --- | --- | --- | --- |
| C1 | Nothing after the initial commit is on `main`. PRs #1–#5 were all merged into `codex/customer-quality-checks`. The delivery workflow therefore cannot be dispatched, and production deliveries are restricted to `main`, which does not contain the product. | repository branches; `.github/workflows/leia-ec2-delivery.yml` | Open |
| C2 | An empty database cannot be built. No migration creates `users`, `auth_sessions`, `conversations` or `chat_messages` (made by hand on the development machine); `migrate_crm.py` stops at `001` with `relation "public.users" does not exist`. The tests hide this by creating their own cut-down copies of those tables. | `backend/migrations/`, `backend/migrate_crm.py`, `backend/tests/test_crm.py` | Fixed — reconciled 13 Nov; final read-only check in section 7 |
| C3 | Both apps call `http://localhost:8000` unconditionally. Any installed or hosted build would talk to the user's own device, over plain HTTP. | `frontend/src/context/AuthContext.tsx:11`, `agent-app/App.tsx:18-23` | Fixed |

### High

| ID | Finding | Files | Status |
| --- | --- | --- | --- |
| H1 | The deployment health check cannot see a broken release. `/health` returns 200 with the database unreachable while `/properties` returns 500, so a release with wrong database settings or missing migrations is reported "healthy". | `backend/main.py`, `deploy/ec2/deploy-release.sh` | Fixed |
| H2 | A failed **first** deployment corrupts the server. `readlink -f` returns a path even when `current` does not exist, so the rollback re-points `current` at itself (reproduced). | `deploy/ec2/deploy-release.sh:18,26-31` | Fixed |
| H3 | The same commit can never be delivered twice: `ln -s … .env` fails with "File exists" (reproduced). This is exactly the retry after fixing settings or applying a migration. | `deploy/ec2/deploy-release.sh:23` | Fixed |
| H4 | Allowed web origins are hard-coded to localhost in two places. A hosted agent CRM or customer web app is refused at sign-in ("Origin not allowed") and blocked by CORS. Native apps are unaffected. | `backend/main.py:43-54`, `backend/auth.py:38-45` | Fixed |
| H5 | The documented server settings are incomplete. They omit `JWT_SECRET` (the API refuses to start without it — reproduced), `COOKIE_SECURE` (defaults to off, so session cookies would be sent without the Secure flag) and the Google keys. No `.env.example` existed. | `docs/EC2_DELIVERY.md` | Fixed |
| H6 | `main` has no branch protection and no rulesets (GitHub API). The production gate trusts `main`, so it is only as strong as who can push there. Whether the `staging`/`production` environments exist or require reviewers could not be read. | GitHub settings | Open |
| H7 | No backup, restore, monitoring or alerting exists. The database is the only copy of every account, listing, chat and image. | — | Open (runbook §11–13) |

### Medium

| ID | Finding | Files | Status |
| --- | --- | --- | --- |
| M1 | Migrations are applied by hand and nothing checked that they had been. | `deploy/ec2/deploy-release.sh` | Fixed — delivery now stops with "Pending migrations"; applying them stays manual by design |
| M2 | Unsafe assumption about SSH. The docs say to restrict SSH in the security group, but GitHub-hosted runners have no fixed address, so the workflow needs port 22 open to the internet or a different approach. | `docs/EC2_DELIVERY.md`, workflow | Open — decision needed (runbook §2) |
| M3 | Environment isolation at the database. Migrations grant rights to a role literally named `leia_app`; roles are cluster-wide, so staging and production in one PostgreSQL cluster would share an API login. | `backend/migrations/*.sql` | Open — use separate instances or clusters |
| M4 | Login and registration limits depend on proxy configuration that did not exist. If Nginx appends rather than overwrites `X-Forwarded-For`, a caller can pick a new address per attempt; if it sends none, all users share one quota. | `backend/rate_limits.py`, server config | Example config provided and tested locally; open until installed |
| M5 | Dependencies are version ranges resolved on the server at deploy time, with no lock file, and the runtime set includes the test tools. CI and production can run different versions (this audit resolved FastAPI 0.162, Starlette 1.10, Pillow 13). | `backend/requirements-*.txt` | Open — add a pinned lock file |
| M6 | The server runs its own hand-installed copy of `deploy-release.sh`. It silently drifts from the repository; this branch changes it, so it must be reinstalled. | `deploy/ec2/deploy-release.sh` | Open (documented) |
| M7 | Public listing and image routes have no request limit and return multi-megabyte images straight from the database with caching disabled. | `backend/property_media.py`, `backend/main.py` | Nginx limit provided; open until installed |
| M8 | The interactive API explorer (`/docs`, `/openapi.json`) is public by default (reproduced). | `backend/main.py:37` | Nginx block provided; open until installed |
| M9 | The 360° tour page loads its viewer from a public CDN without an integrity hash, and its content policy allows inline script. | `backend/property_media.py:163-185` | Open |
| M10 | Every request opens new PostgreSQL connections (two to four for a signed-in request). Fine at launch scale, a limit later. | `backend/database.py` | Open |
| M11 | Preflight accepted a database on another host without an encrypted connection (`DB_SSLMODE` unset), so credentials and data could cross the network in clear text. | `backend/preflight.py` | Fixed 14 Nov — deployed preflight requires `require`/`verify-ca`/`verify-full` for a non-local `DB_HOST` |

### Low and informational

| ID | Finding | Status |
| --- | --- | --- |
| L1 | Stated stack mentions SQLAlchemy; the backend uses psycopg 3 with hand-written SQL (parameterised throughout). No action needed. | Info |
| L2 | The customer "contact/enquiry flow" is a phone link only (`frontend/src/components/ContactLEIA.tsx`). There is no enquiry endpoint or stored lead, so there is nothing for the backend to support. Not added: out of scope. | Info |
| L3 | Registration reveals whether an email already has an account (409), limited to 5 attempts per hour per address; there is no email verification. | Open |
| L4 | Sign-in is limited per client address only (10 per minute, customers and staff combined); no per-account lockout. | Open |
| L5 | A reused old refresh token is rejected but does not revoke the session family; access tokens stay valid up to 15 minutes after sign-out. | Open |
| L6 | GitHub Actions are pinned to version tags, not commit hashes. | Open |
| L7 | The uploaded archive uses a predictable name in `/tmp`. Harmless on a single-purpose server. | Open |
| L8 | `npm audit --omit=dev`: 9 moderate advisories (customer app), 5 moderate (agent app), all in the Expo command-line and build tooling (`@expo/cli`, `glob`, `minimatch`, `node-forge`), none high or critical. `pip-audit` on the backend: none. | Open |
| L9 | Old releases and their virtual environments are never removed. | Open (runbook §10) |
| L10 | Pull-request checks did not run for changes limited to `frontend/` or `deploy/`. | Fixed |
| L11 | Creating the first agent and admin accounts had no documented procedure (public registration only creates customers). | Fixed (runbook §9) |

### What was checked and found sound

- **Secrets:** pattern scan of every branch and its full history found no keys, passwords or `.env`
  files. `.gitignore` excludes them; the release archive excludes `.env`.
- **Tokens:** HS256 with a 32+ character secret enforced at start-up; issuer, audience, expiry and
  token type all required; 15-minute access tokens held in memory by the apps; refresh tokens random,
  stored hashed, rotated, HttpOnly, path-scoped, and separated between customers and staff.
- **Authorisation:** the role is read from the database on every request; staff routes require
  agent or admin; publishing requires admin; agents see only their own listings (others get 404).
- **Uploads:** staff only; size capped while streaming; decoded and re-encoded as JPEG (metadata
  dropped); format allow-list; pixel limit; stored in PostgreSQL, so nothing is written to disk.
- **Workflow:** manual trigger only; `contents: read`; no stored checkout credentials; pinned SSH
  host key; one deployment per environment at a time; production needs `main` plus a typed phrase.
- **Logging:** no passwords, tokens, keys or message text are logged; request-limit records store a
  keyed hash rather than addresses or account ids.

## 3. Code changes on this branch

| Change | Why (finding) |
| --- | --- |
| `backend/migrations/000_core_baseline.sql` — creates the four hand-made tables with `IF NOT EXISTS` and least-privilege grants | C2 |
| `backend/preflight.py` — checks settings (names only in output) and that the database is reachable and fully migrated | H1, H5, M1 |
| `backend/main.py` — `GET /health/ready`; CORS uses the shared origin list | H1, H4 |
| `backend/auth.py` — `LEIA_ALLOWED_ORIGINS` replaces the localhost defaults when set | H4 |
| `backend/database.py` — optional `DB_SSLMODE` for a remote database | PostgreSQL connection security |
| `backend/migrate_crm.py` — API role may read migration history | H1, M1 |
| `deploy/ec2/deploy-release.sh` — preflight before the switch; readiness instead of liveness; first-deployment and same-commit fixes; rollback result reported | H1–H3 |
| `.github/workflows/leia-ec2-delivery.yml` — job time limits; SSH fails fast instead of hanging; runs the whole test folder | failure handling |
| `.github/workflows/crm-checks.yml` — new tests; pull-request path filter matches push | L10 |
| `frontend/src/lib/apiUrl.ts`, `agent-app/src/lib/apiUrl.ts` and their callers — API address from `EXPO_PUBLIC_API_URL`; release builds require HTTPS | C3 |
| `backend/.env.example`, `deploy/ec2/*.example`, `docs/EC2_RUNBOOK.md`, `docs/EC2_DELIVERY.md` | H5, H7, M2–M8 |
| `backend/tests/test_crm.py` — one line (`CREATE TABLE IF NOT EXISTS`) so it coexists with the baseline | C2 |
| `000_core_baseline.sql` reconciled with the development schema: `email_lowercase` and `session_expiry_valid` CHECK constraints; indexes `auth_sessions_user_id_idx`, `chat_messages_conversation_idx`, `conversations_user_updated_idx`; `conversations.title` `TEXT`; no default on `conversations.id` | C2 |
| `backend/schema_report.py` — read-only schema description and `--compare` against `backend/tests/expected_schema.json` | C2 |
| `backend/tests/test_core_schema.py`, `tests/expected_schema.json`, `tests/fixtures/development_core_tables.sql` — schema, constraint, index and adoption regressions | C2 |

Not changed: application features, the database driver, dependency versions, the deployment model.

## 4. Test results

Commands were run from the repository root unless a folder is shown. `<url>` is a disposable local
database: `postgresql://postgres:<password>@localhost:5432/crm_test`.

**Existing tests, unmodified `e99ffc3` — all passed**

| Command | Result |
| --- | --- |
| `python -m compileall -q backend` | OK |
| `TEST_DATABASE_URL=<url> python -m pytest backend/tests/test_crm.py backend/tests/test_property_media.py -q` | 31 passed |
| `agent-app`: `npm ci && npm run typecheck && npm run lint` | clean |
| `agent-app`: `npm run test:forms` · `node --experimental-strip-types --test tests/staffSession.test.mjs` | 18 passed · 2 passed |
| `frontend`: `npm ci && npx tsc --noEmit && npm run lint` | clean |
| `frontend`: `node --experimental-strip-types --test tests/roomTours.test.mjs` | 7 passed |
| GitHub Actions "CRM checks" on `e99ffc3` (read from the API) | success |

**This branch — all passed**

| Command | Result |
| --- | --- |
| `TEST_DATABASE_URL=<url> python -m pytest backend/tests -q` | 72 passed (31 existing + 41 new) |
| `python -m pytest backend/tests -q` (no database) | 64 passed, 8 skipped (database tests) |
| `agent-app`: `npm run typecheck`, `npm run lint`, `npm run test:forms` | clean, 18 passed |
| `agent-app`: `node --experimental-strip-types --test tests/staffSession.test.mjs tests/apiUrl.test.mjs` | 8 passed |
| `frontend`: `npx tsc --noEmit`, `npm run lint` | clean |
| `frontend`: `node --experimental-strip-types --test tests/roomTours.test.mjs tests/apiUrl.test.mjs` | 13 passed |
| `shellcheck deploy/ec2/deploy-release.sh` · `yamllint .github/workflows/` | clean |
| `pip-audit -r backend/requirements-runtime.txt` | no known vulnerabilities |
| GitHub Actions "CRM checks" on this branch, push and pull request (Python 3.12, Node 24, PostgreSQL 16) | success; `crm-api` log: 72 passed |

New tests: `test_preflight.py` (25: settings rules, origin handling, readiness, and an empty database
built only from migrations then exercised through registration, sign-in, roles, chat and listing
creation) and `test_deploy_release.py` (16: the real script against stand-in commands — healthy
release, unhealthy release, failed first deployment, failed rollback, restart failure, preflight
failure, same-commit retry, bad arguments, environment separation, workflow safeguards). Removing the
baseline migration or the readiness check makes them fail, as intended.

**Local rehearsal (not AWS, not CI)** — the real script, a real virtual environment and the real
backend against local PostgreSQL as a non-owner `leia_app` role:

| Step | Result |
| --- | --- |
| Deliver to an unmigrated database | Stopped at preflight, nothing switched |
| Apply migrations as a non-superuser owner, deliver the same commit again | Healthy |
| 36-step HTTP smoke test (registration, sign-in, refresh, role separation, create/edit listing, room photos, 360° upload, submit, admin approval, public search, gallery, tour, chat, history) | 36 / 36 |
| Deliver a release that crashes on start | Exit 1, rolled back, previous release answering |
| Deliver with `COOKIE_SECURE=false` | Stopped at preflight |
| Stop PostgreSQL under the running service | `/health` 200, `/health/ready` 503, recovers |
| Nginx example with a self-signed certificate | HTTP→HTTPS redirect; HSTS; `/docs` 404; 14 MB upload 413; 12 bad sign-ins with a different forged `X-Forwarded-For` each → blocked from the 11th; cookie refresh works over HTTPS |
| `systemd-analyze verify` on the unit · `visudo -c` on the sudoers file | syntax valid |
| `EXPO_PUBLIC_API_URL=https://… npx expo export --platform web` (agent app) | configured address present in the bundle |

**Could not be executed**

- Anything on AWS: EC2, security groups, DNS, Let's Encrypt, real SSH from a GitHub runner.
- The delivery workflow itself (not on the default branch; no environment to target).
- The systemd unit actually running the service (no systemd in the sandbox); IPv6 listeners.
- Real Groq and Google Maps calls (no keys by design): AI search returned the expected 502 and
  nearby places the expected 503 with placeholder settings. Existing tests cover them with mocks.
- iOS and Android builds; the customer app's web export.
- GitHub environment and secret configuration (API not readable here).

## 5. AWS provisioning checklist (owner)

Per environment; details and commands in `docs/EC2_RUNBOOK.md`.

- [ ] Decide: separate instances for staging and production (recommended), or one instance with two PostgreSQL clusters
- [ ] Ubuntu 24.04 LTS instance, ≥ 2 GB RAM, encrypted gp3 volume sized for images in the database
- [ ] Elastic IP attached; DNS `A` record for the API domain
- [ ] Security group: 443 and 80 open; 5432 closed; SSH approach decided (finding M2)
- [ ] OS updates, `fail2ban`, unattended upgrades; password and root SSH login disabled
- [ ] Deploy account and service account created; `/opt/leia/<environment>/{releases,shared}` with the documented ownership
- [ ] `deploy-release.sh` installed at `/opt/leia/<environment>/`; sudoers rule limited to restarting that one service
- [ ] PostgreSQL 16: owner role, `leia_app` role, database, `PUBLIC` connect revoked, local connections only
- [ ] `/opt/leia/<environment>/shared/.env` created from `backend/.env.example`, mode 640
- [ ] systemd unit installed and enabled; Nginx site installed; certificate issued; `certbot renew --dry-run` passes
- [ ] Daily `pg_dump` to off-instance storage; one restore rehearsed and timed
- [ ] External uptime check on `/health/ready`; EC2 status alarm; billing alarm; Groq and Google spending limits
- [ ] Google keys restricted to their one API and the server address

## 6. GitHub checklist (names only)

Environments `staging` and `production`, each with its own values:

| Kind | Name |
| --- | --- |
| Secret | `EC2_HOST` |
| Secret | `EC2_USER` |
| Secret | `EC2_SSH_KEY` |
| Secret | `EC2_KNOWN_HOSTS` |
| Variable | `LEIA_PORT` |

Server-side only, in `shared/.env`, never in GitHub: `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`,
`DB_PASSWORD`, `DB_SSLMODE` (remote database), `JWT_SECRET`, `GROQ_API_KEY`, `COOKIE_SECURE`,
`LEIA_ALLOWED_ORIGINS`, `GOOGLE_PLACES_API_KEY`, `GOOGLE_ROUTES_API_KEY`.

App builds: `EXPO_PUBLIC_API_URL`, and `EXPO_PUBLIC_GOOGLE_MAPS_EMBED_KEY` if the map embed is used.

Settings: required reviewers and `main`-only on `production`; branch limits on `staging`; branch
protection on `main` requiring the three "CRM checks" jobs.

## 7. Remaining production blockers

1. Get the product onto `main` through a reviewed pull request, then protect `main` (C1, H6).
2. Run the read-only comparison against the real development database and confirm
   "Schema matches." (`docs/MAIN_INTEGRATION_PLAN.md`, step 1). The six reported differences are
   reconciled, but the exact expressions of `email_lowercase` (`email = lower(email)`) and
   `session_expiry_valid` (`expires_at > created_at`), the type and foreign key (none) of the
   existing `chat_messages.property_id`, the CRM tables and table privileges were reconstructed,
   not read from that database (C2).
3. Provision staging, deliver to it, run the smoke test from outside AWS over HTTPS (section 5).
4. Backups with a rehearsed restore, uptime monitoring and alerts in place (H7).
5. Decide the SSH approach (M2) and the staging/production database separation (M3).
6. Pin backend dependencies in a lock file so the tested versions are the deployed versions (M5).
7. Build both apps with the real API address; test sign-in and session refresh on iOS, Android and web.
8. Repeat everything for production, with reviewer approval configured.

## 8. BTS-DMD status

| Stage | Verified | Pending | Unverified |
| --- | --- | --- | --- |
| **Build** | Backend compiles; both apps type-check and lint; agent web export builds with the API setting | Merge to `main`; dependency lock file | iOS/Android builds; customer web export |
| **Test** | 72 backend tests on PostgreSQL 16 and 39 app tests, locally and in GitHub Actions; local rehearsal and smoke test | Smoke test against a staging server | Real Groq and Google calls; any test against a deployed server |
| **Secure** | No secrets in history; sign-in, roles, uploads and limits reviewed and exercised; settings validated before go-live | Branch protection; M2–M9; lock file | GitHub environment rules; server hardening; TLS on the real domain |
| **Deploy** | Deploy script control flow, rollback and retry, locally | Workflow on default branch; server provisioning; secrets | Any EC2 deployment, staging or production |
| **Monitor** | Readiness endpoint distinguishes "process up" from "can serve" | Uptime check, alarms, log retention, backups | All of it on a real server |
| **Document** | Runbook, settings template, service/Nginx/sudoers examples, this audit | Update after the first staging deployment | — |

## 9. Update 13 November 2026 — baseline reconciled with the development schema

The owner compared `000_core_baseline.sql` with the development database (PostgreSQL 18.6) and
reported six differences. All six are reconciled in `000_core_baseline.sql`:

| # | Development schema | Baseline before | Now |
| --- | --- | --- | --- |
| 1 | `users` CHECK `email_lowercase` | missing | `CHECK (email = lower(email))` |
| 2 | `auth_sessions` CHECK `session_expiry_valid` | missing | `CHECK (expires_at > created_at)` |
| 3 | indexes `auth_sessions_user_id_idx (user_id)`, `chat_messages_conversation_idx (conversation_id, id)`, `conversations_user_updated_idx (user_id, updated_at DESC)` | missing | created `IF NOT EXISTS`, same names |
| 4 | `conversations.title TEXT` | `VARCHAR(200)` | `TEXT` |
| 5 | `conversations.id` no default | `gen_random_uuid()` | no default (the API always supplies `uuid4()`) |
| 6 | `chat_messages.property_id` already present | added by `005` | unchanged: `005` uses `ADD COLUMN IF NOT EXISTS`, so both paths end with `property_id UUID NULL`; the difference is in history only |

Compatibility: every statement in `000` is `IF NOT EXISTS`, so on the development database it
only records itself as applied; a test builds a development-shaped database with `001`–`007`
already applied, runs the migrations and checks that the schema, the rows and the tables' storage
files are unchanged. The application already satisfies the new constraints (it lower-cases email
before every insert and lookup; sessions expire 7 days after creation) and supplies every
conversation id. Titles are at most 80 characters.

Tests (sandbox, not AWS): `backend/tests` 79 passed on PostgreSQL 18.6 and on 16 (73 before + 6
new); 65 passed, 14 skipped without a database. With the previous baseline, 4 of the 6 new tests
fail. A fresh database built by the migrations produces the same schema description on 18.6 and 16.
Apps unchanged and re-verified: agent app typecheck, lint, 18 + 8 tests; customer app typecheck,
lint, 13 tests.

Not verified here: the comparison against the real development database (it was not accessed,
by instruction) and table privileges, which `schema_report.py` does not compare.

## 10. Update 14 November 2026 — development database history, backup and 007

The owner's read-only comparison of the development database printed two differences,
both from `007_room_panoramas.sql` (kind check without `panorama_room`; missing index
`property_media_room_panorama_unique`). Findings and changes:

- **Backup failure explained.** `pg_dump` does not read `backend\.env`; it uses the prompt,
  `PGPASSWORD` or `pgpass.conf` (the last two silently). The server log line names the
  exact cause. Independently of the password, `pg_dump` as `leia_app` cannot work: the
  least-privilege role has no rights on `crm_schema_migrations` (older runners never
  granted them) and `pg_dump` stops with "permission denied" — reproduced. Back up as the
  owner; no `pg_hba.conf` change is needed.
- **"No tracking table" is very probably a false negative.** `information_schema` hides
  tables a role cannot read (reproduced: 0 rows for `leia_app`, while the catalog has the
  table). `expected_schema.json` contains `crm_schema_migrations`, so a really missing
  table would have been a third difference.
- **A database built by 000–006 shows exactly the owner's two differences.**
- **New `backend/reconcile_migrations.py`.** `status` (read-only) shows owners, history,
  pending migrations, which migration's schema matches exactly, 007 compatibility of the
  media rows and exact row counts. `adopt --through 006_request_limits.sql` records
  history only if the live schema equals `tests/expected_schema_006.json`; dry run unless
  `--apply`; never runs migration SQL; refuses on any difference, missing snapshot or
  later history. 10 tests.
- **Procedure:** `docs/DEV_DATABASE_RECONCILIATION.md` (diagnose, back up, restore
  rehearsal, status, adopt, 007, verify). Rehearsed end to end here on a database shaped
  like development (PostgreSQL 16 tools; no PostgreSQL 18 `pg_dump` in the sandbox):
  backup lists all 9 tables, restore identical, adopt recorded only `000`, the runner
  applied only `007`, `Schema matches.`, row counts unchanged.
- **M11** fixed (database SSL in preflight).

Not done, by instruction or because it needs the development machine: backup and
restore of the real database, any change to it, the 007 run there.

Tests (sandbox): backend 98 passed on PostgreSQL 18.6 and on 16 (fresh database each);
74 passed, 24 skipped without a database. Agent app: typecheck, lint, 26 tests passed.
Customer app: typecheck, lint, 13 tests passed. `pip-audit`: none. `npm audit --omit=dev`:
9 / 5 moderate (L8, unchanged). Bandit: no real findings (B608 on a constant table name;
B105 on token-type labels).

### 10.1 First run on the development machine (14 Nov, 14:17)

Windows PowerShell 5.1.22621, PostgreSQL 18.6, worktree fast-forwarded to `14c45ce`.

- Backup `leia-20261114_141755-pre007.dump`: 3,870,550 bytes, 9 tables with data,
  SHA256 `00AD8FF1…36EC64` (re-verified from the file afterwards).
- Restore into `leia_restore_check_20261114_141755`: succeeded. Structured comparison:
  schema, history, pending, snapshot matches, media checks, table owners, row counts
  (126 rows in 9 tables) and all 9 content checksums identical. The only difference was
  the owner of the database itself (`leia_app` live, `postgres` on the copy): `pg_dump`
  without `-C` does not carry it and `createdb` ran as `postgres`. The script now creates
  the copy with the live owner; `compare-status --allow-database-owner-difference
  leia_app:postgres` verifies that run without accepting any other difference.
- History: `001`–`006` recorded, `000` not recorded, `007` pending; the schema equals the
  state after `006` exactly. `leia_app` cannot read the history table (as expected).
- **pg_dump login failure explained:** no `PGPASSWORD`, `PGUSER` or `pgpass.conf`; the
  failed attempts (13:05:14 and the previous evening) matched `pg_hba.conf` line 117
  (`::1/128`, scram-sha-256), the same rule the app uses with `DB_HOST=localhost`. The
  password typed at the prompt was therefore not the one in `.env`. No configuration
  change is needed; backups are taken as `postgres`.
- **New finding (M12, development database):** `leia_app` owns the database `leia` and
  the tables `users`, `auth_sessions`, `conversations`, `chat_messages`. An owner can
  alter or drop them and is not limited by grants, so the API login has more power than
  least privilege allows there. Not changed (by instruction). Servers must use a separate
  owner role (`leia_owner_ENV`, runbook §8); for development, transfer ownership to
  `postgres` in a later, approved step.
