# LEIA builder and project facts (migration 014)

Branch `feature/pilot-builder-fields` from `staging/render-leia` @ `e194e69`.

**Not merged, not deployed. Migration 014 is not applied to staging. No EAS build, no listing published,
no Groq call.** `LEIA_AI_MAX_ATTEMPTS=1` is untouched (an env setting; this branch doesn't change it).

Status labels: **Tested locally** (local disposable PostgreSQL) · **CI** (GitHub Actions) · **Measured
locally** (benchmark with a simulated network delay) · **Pending** (needs approval, deploy or devices).

## 1. What was built

| Area | Result |
|---|---|
| Database | Migration `014_builder_project_facts.sql`: a `builders` table plus 17 optional listing columns |
| Facts supported | builder, project name, RERA number + status + checked date + verifying admin, construction status, expected possession, floor / total floors, lift, gated community, security, amenities (gym, pool, clubhouse, garden, play area, community hall), project notes, source + checked date |
| Unknown vs no | Lift and gated community use `unknown / yes / no / not_applicable`. Security, construction status and amenities have their own values. **Unknown never matches a search.** |
| Verification | Every fact group has a source: observed, owner reported, or document checked. **Only an admin can mark RERA "verified from documents".** |
| Unchanged | Eight-direction facing, GPS coordinates and their provenance, price, area, BHK, media, nearby places, the draft → submitted → approved workflow, AI budget guard, connection pool |

## 2. Database and migration safety

- **Additive only.** A new table; new columns are nullable or have constant defaults (metadata-only
  `ADD COLUMN`, no table rewrite). It needs one brief lock on `properties`.
- **Duplicate builders are impossible:** `name_key` is a generated column (lower-case, letters and
  numbers only, Telugu included) with a UNIQUE constraint. "Raju Constructions", " raju  CONSTRUCTIONS."
  and "RAJU-constructions" are the same builder. Empty keys are rejected.
- **Database constraints:**
  - possession date only when under construction
  - floor ≤ total floors
  - a RERA status needs a RERA number
  - `rera_verified_by` only when document-checked
  - RERA format
  - amenity whitelist
  - floor ranges
- **Existing data:** `test_migration_014.py` applies 000–013 as files and creates existing listings
  (approved, draft, submitted) with every pre-014 field, private owner details, media and users. Then it
  applies 014 and checks:
  - **every existing value is byte-identical**
  - every new fact is `unknown` or empty
  - the constraints reject bad data and duplicate builders
  - the **documented rollback** (section 8) restores the pre-014 schema with every record intact

  Result: Tested locally and in CI.
- **Sessions, users, media and Studio jobs:** none of these tables is touched.

## 3. API changes (all backward compatible)

| Endpoint | Change |
|---|---|
| `POST/PUT /staff/properties`, `GET /staff/properties/{id}` | New optional fields, listed in section 1. `builder_name` is matched to a shared builder by normalized name, created once if new. |
| *Old Agent CRM builds* | A save that sends **none** of the new fields **keeps the stored values**, the same rule as GEO-M1.1 location metadata. Old payloads still validate (`extra="forbid"` unaffected). Tested. |
| `POST /staff/properties/{id}/rera-verification` (new, **admin only**) | `{version, verified}` on draft or submitted listings. Sets document-checked, today's date and the verifying admin, and bumps the version. |
| `GET /staff/builders`, `POST /staff/builders` (new, staff) | List and create builders. The response has `id` and `name` only, never the creator. Creating an existing name returns the existing record. Customers get 403. |
| RERA rule on create and update | An agent sending `rera_source=document_checked` gets **403** with a clear message, unless the number is unchanged and already verified. Changing a verified number requires downgrading it. |
| `GET /properties`, chat search results (customer) | Additive keys: `builder`, `project_name`, `construction_status`, `possession_date`, `floor_number`, `total_floors`, `lift`, `gated_community`, `security`, `amenities`, `project_source`, `rera_verified`, `rera_number` (**null unless verified**). Never: owner details, notes, the verifier, the builder's creator. Old apps ignore the extra keys. |
| Route contract | **41 → 43 paths** (`/staff/builders`, `/staff/properties/{property_id}/rera-verification`). Update the GEO live check's `EXPECTED_ROUTES` to 43 after deploying. |

## 4. Search, chat and AI

- **Deterministic filters** (English, Telugu, Telugu-English):
  - builder ("by Raju constructions", "Sri Sai builders", "builder is …"), matched by normalized-name
    prefix
  - ready to move / under construction (రెడీ టు మూవ్, నిర్మాణంలో)
  - possession by a date ("possession by March 2027", "2027 lopu possession")
  - lift (లిఫ్ట్, "lift unna")
  - gated community (గేటెడ్ కమ్యూనిటీ)
  - security (CCTV, watchman, సెక్యూరిటీ)
  - verified RERA ("RERA approved")
  - amenities (gym/జిమ్, swimming pool, clubhouse, garden, play area, community hall)

  Amenities accumulate across follow-up messages.
- **Unknown never matches;** an explicit "no" never matches either. No-result replies offer to loosen
  the new filters too (still **one** suggestion query).
- **Groq can't invent these facts:** builder, construction, possession, lift, gated, security, RERA and
  amenity keys in AI output are **dropped**. Deterministically parsed ones are kept around the AI call.
  The budget guard and caps are unchanged. Tested.
- **Questions:**
  - "Is it RERA approved?" or "what is the RERA number?" without a listing explains where verified RERA
    appears, never a value.
  - "who is the builder?" without a listing explains the same, never a guess.
- **Selected-listing answers and comparisons** show builder, project, RERA (verified only, otherwise
  "Not verified by LEIA yet"), construction and possession ("an estimate, not a guarantee"), floor, lift,
  gated, security and amenities, **each with its source**. Unknown shows as "Not listed".
- **Pooling preserved:** the search still runs on the chat transaction's connection. The builder filter
  is an `EXISTS` subquery that runs as a single pass over `builders`, not once per listing (no N+1).
  Plan: 1.5 ms.

## 5. Digital Studio

Snapshots used to include every non-private model field automatically, which would have leaked new
internal data. Now:
- `rera_source`, `rera_checked_on` and `project_notes` are private.
- The snapshot carries `rera_verified` and the RERA number **only when verified**.
- The builder name is read from the builder record.

Studio scope is unchanged (no new features). Tested in `test_builder_fields.py` (snapshot privacy) and
`test_marketing_jobs.py`.

## 6. Apps

**Agent CRM** (`agent-app`):
- New sections: "Project and builder" and "Building amenities".
  - Builder text field with suggestion chips from existing builders (case, spacing and punctuation
    insensitive).
  - RERA number and status ("Verified from documents (admin only)").
  - Construction status, expected possession and floors.
  - Lift, gated and security, an amenities multi-select (checkbox semantics), notes and source.
- Validation messages mirror the server.
- Drafts from a server without migration 014 still open.
- Admin review panel: shows builder, construction and RERA status, with "I checked the documents: mark
  RERA verified" / "Remove RERA verification".
- Keyboard avoidance and scrolling are unchanged: the fields render in the existing scroll form.

**Customer app** (`frontend`):
- Property details list Builder, Project, RERA, Construction, Floor, Lift, Gated community, Security and
  Amenities.
- Unknown shows "Not provided" and recorded values carry their source.
- RERA shows "Not verified" unless verified.
- Older saved chats or servers show nothing extra.

**New app builds are needed** for agents to edit and customers to see these fields. **EAS builds were not
triggered.** Existing APKs keep working: old CRM saves keep stored facts, and old customer apps ignore the
new keys.

## 7. Test results

| Suite | Result |
|---|---|
| Backend: CRM, customer session, RBAC, publishing, GPS, media, temple/listing chat | 99 passed |
| Studio web / marketing jobs | 3 passed + 1 skipped (ffprobe only in CI) / 21 passed |
| Chat filters, facing, **search evaluation (124 questions)** | 260 passed; eval **124/124** |
| Hybrid search, AI guard (budget, ledger, retries), connection pool | 9 / 9 / 11 passed |
| **Migration 014 on existing records and rollback** (new) | 1 passed |
| **Builder fields** (new): round trip, builder dedup (20 concurrent creates → 1 record), RBAC, validation, admin-only RERA, re-save and number change, old-app payloads, publishing + search + chat + Studio privacy, AI can't set facts | 8 passed |
| **Backend total** | **421 passed, 1 skipped, 0 failed** (local) |
| Agent CRM | typecheck ✔, lint ✔, forms **29/29** (6 new), location 19/19, staff session 9/9 |
| Customer app | typecheck ✔, lint ✔, project facts **4/4** (new), session 23/23 |
| CI | see section 10 |

**Evaluation:** the original 100 questions are all kept.
- **24 new project-fact questions** (English, Telugu, Telugu-English): builder, ready to move,
  possession dates, lift, gated, security, verified RERA, amenities, no-match suggestions, and
  "builder / RERA number?" questions.
- **Six original expectations changed** on purpose, because these are real data now. Each case records
  the change:
  - F07, F09, T12: lift and amenities are filters
  - N01: ready to move is a filter
  - N02: still never asserts RERA
  - N05: a builder question gets an explanation instead of every flat
- The synthetic inventory's draft listing has a verified lift and RERA; it never appears.

**Changed tests** (all intent preserved):
- The exact customer payload in `test_crm.py` now lists the new keys, with RERA hidden.
- The Studio private-field set includes the three new internal fields.
- The CRM round-trip test uses a valid RERA number and construction status.

## 8. Performance (Measured locally)

Same harness as the pooling work: local PostgreSQL 18 with SCRAM, a 20 ms one-way delay proxy, 1 uvicorn
worker, n=30 sequential, Groq disabled. Synthetic data with migration 014 and builder facts.
*Before* = `e194e69` (current staging code), *after* = this branch, both on the same migrated database.

| Request | Before p50 / p95 | After p50 / p95 |
|---|---|---|
| `/properties` | 63 / 71 ms | 59 / 67 ms |
| `/auth/me` | 63 / 66 ms | 50 / 51 ms |
| Chat, with results | 630 / 650 ms | 508 / 526 ms |
| Chat, empty (suggestions) | 695 / 799 ms | 550 / 650 ms |
| Chat, Telugu | 632 / 656 ms | 500 / 525 ms |
| Chat, Telugu-English | 601 / 695 ms | 550 / 566 ms |
| Chat, **lift + gym + gated** (new) | — | 551 / 576 ms |
| Chat, **builder + ready to move** (new) | — | 551 / 590 ms |
| 20 concurrent clients, n=60 | 2,009 / 2,289 ms | 2,030 / 2,246 ms, 9.1 req/s |

- **Database work per chat is unchanged:** 3 connections and 6–7 queries (Server-Timing), also for the
  new filters.
- The before/after differences in p50 are within run-to-run noise on this machine (earlier runs of the
  same code gave 500 ms). Read this as **no regression**, not as a speed-up.
- On staging, warm deterministic chat was 30–40 ms on the server, and this change adds no queries.

## 9. Deployment order (each step needs approval)

1. Open a PR into `staging/render-leia`; CI must be green; merge.
2. Take a fresh staging backup and restore-test it locally.
3. Gated runner `run_014.py` (same gates as 012/013):
   - pinned to the merge commit
   - migration SHA-256 check
   - target `leia_staging`
   - 014 the only pending migration
   - `lock_timeout=5s`

   Dry run, then apply.
4. Deploy the merge commit on Render manually. No new environment variables; keep `LEIA_AI_MAX_ATTEMPTS=1`.
5. Verify:
   - `/health` returns 200 and the route count is **43**
   - public payloads hide unverified RERA
   - deterministic searches, the AI ledger unchanged (zero Groq calls)
   - auth / GEO / Studio checks (update GEO `EXPECTED_ROUTES` to 43)
6. With test accounts only, on the existing **unpublished** TEST draft: set facts and builder, admin RERA
   verification, read back, then clean up.
7. EAS builds of the Agent CRM and Customer app, only with approval. Then agents backfill the six
   builders' listings from documents.

**Order matters:** apply 014 **before** deploying this code (the new code reads the new columns). The
current code runs fine with 014 applied.

**Rollback:**
- **Code only:** redeploy `e194e69`. It ignores the new columns and table.
- **Schema** (only if needed, after rolling back the code): run the SQL below in one transaction, then
  delete the tracker row `014_builder_project_facts.sql`. This is tested in `test_migration_014.py`.
  It **drops any builder or fact data entered after 014**, so take a backup first.

```sql
ALTER TABLE public.properties
    DROP CONSTRAINT properties_floor_within_building, DROP CONSTRAINT properties_possession_needs_status,
    DROP CONSTRAINT properties_rera_source_needs_number, DROP CONSTRAINT properties_rera_verified_by_needs_check,
    DROP COLUMN builder_id, DROP COLUMN project_name, DROP COLUMN rera_number, DROP COLUMN rera_source,
    DROP COLUMN rera_checked_on, DROP COLUMN rera_verified_by, DROP COLUMN construction_status,
    DROP COLUMN possession_date, DROP COLUMN floor_number, DROP COLUMN total_floors, DROP COLUMN lift,
    DROP COLUMN gated_community, DROP COLUMN security, DROP COLUMN amenities, DROP COLUMN project_notes,
    DROP COLUMN project_source, DROP COLUMN project_checked_on;
DROP TABLE public.builders;
```

## 10. CI

GitHub Actions on `f80c5d7` (the last code commit; this report is docs-only, outside the CI path filter):
**crm-api: success** (every step, including the new `test_migration_014.py` and `test_builder_fields.py`
steps on Ubuntu PostgreSQL 16) and **agent-app: success** (typecheck, lint, forms, location, staff session).

## 11. Remaining risks

- **Physical devices:** the new CRM sections and customer facts aren't device-tested. Use the
  `docs/DEVICE_TEST_CHECKLIST.md` CRM and customer items and add the new sections.
- **App builds:** EAS builds are needed before agents can enter the facts. Until then, fields can be set
  only by a new build (or the API).
- **Builder names:** builders are created implicitly by name. A misspelling creates a new builder
  ("Raju Constrctions"). Normalization catches case, spacing and punctuation, not typos; the suggestion
  chips reduce this. There's no merge tool yet.
- **Builder search** is a prefix match on the normalized name. A query "Sai" matches "Sai Constructions"
  and "Sai Krishna Homes" (intended), but not "Sri Sai …".
- **RERA format:** the regex is permissive. Verification stays a manual admin check against documents.
- **Approved listings** can't be RERA-verified after approval (draft or submitted only), to keep the
  approval workflow intact.
- **Floor filters** (e.g. "ground floor") aren't searchable yet; floors are display-only.
- **The Customer app isn't in CI** (unchanged from before); its checks were run locally.
