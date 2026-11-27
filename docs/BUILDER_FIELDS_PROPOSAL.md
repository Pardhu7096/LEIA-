# Proposal: builder, RERA, possession and amenity fields

Status: **proposal only, not applied.** Needs owner approval before any migration is created in
`backend/migrations/` (migrate_crm.py applies every file in that folder).

## 1. Audit (schema at migration 013)

`public.properties` has verified-fact fields only for **parking**, **electricity / power backup** and
**water**. Each fact has a value, a `<section>_source` (`unknown | observed | owner_reported |
document_checked`), a `<section>_checked_on` date and free-text notes. Pydantic (`property_models.py`)
and the Agent CRM form (`agent-app/src/lib/propertyFields.ts`, the `evidence(section)` helper) follow the
same pattern.

These have **no columns at all**, so LEIA can't display or filter on them and currently replies
"LEIA hasn't verified …" (see `search_parser.UNSUPPORTED_TERMS`):

| Customer asks about | Today | Needed for pilot builders |
|---|---|---|
| Builder / developer name | not stored | yes |
| RERA registration number | not stored | yes (AP RERA) |
| Possession status / date | not stored | yes |
| Construction status | not stored | yes |
| Lift | not stored | yes |
| Security / CCTV, gated community | not stored | yes |
| Gym, pool, clubhouse, garden / play area | not stored | nice to have |
| Floor number / total floors | not stored | yes for flats |
| Approvals (GVMC / VMRDA plan, OC) | not stored | yes, document-checked only |
| Nearby schools / hospitals | not stored | out of scope (no verified source) |

## 2. Proposed schema (migration 014, additive, nullable / `unknown` defaults)

```sql
-- 014_builder_project_facts.sql (PROPOSED - do not apply without approval)
CREATE TABLE public.builders (
    id UUID PRIMARY KEY,
    name VARCHAR(200) NOT NULL UNIQUE,
    created_by UUID NOT NULL REFERENCES public.users (id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE public.properties
    ADD COLUMN builder_id UUID REFERENCES public.builders (id),
    ADD COLUMN project_name VARCHAR(200),
    ADD COLUMN rera_number VARCHAR(50)
        CHECK (rera_number IS NULL OR rera_number ~ '^[A-Za-z0-9/_-]{5,50}$'),
    ADD COLUMN construction_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (construction_status IN ('unknown','under_construction','ready_to_move','not_applicable')),
    ADD COLUMN possession_date DATE,
    ADD COLUMN floor_number SMALLINT CHECK (floor_number BETWEEN -2 AND 200),
    ADD COLUMN total_floors SMALLINT CHECK (total_floors BETWEEN 1 AND 200),
    ADD COLUMN lift TEXT NOT NULL DEFAULT 'unknown'
        CHECK (lift IN ('unknown','yes','no','not_applicable')),
    ADD COLUMN gated_community TEXT NOT NULL DEFAULT 'unknown'
        CHECK (gated_community IN ('unknown','yes','no','not_applicable')),
    ADD COLUMN security TEXT NOT NULL DEFAULT 'unknown'
        CHECK (security IN ('unknown','none','guard','cctv','guard_and_cctv','not_applicable')),
    ADD COLUMN amenities TEXT[] NOT NULL DEFAULT '{}'
        CHECK (amenities <@ ARRAY['gym','pool','clubhouse','garden','play_area','community_hall']::text[]),
    ADD COLUMN project_notes TEXT CHECK (length(project_notes) <= 2000),
    ADD COLUMN project_source TEXT NOT NULL DEFAULT 'unknown'
        CHECK (project_source IN ('unknown','observed','owner_reported','document_checked')),
    ADD COLUMN project_checked_on DATE,
    -- RERA and approvals are only shown to customers when document_checked.
    ADD COLUMN rera_source TEXT NOT NULL DEFAULT 'unknown'
        CHECK (rera_source IN ('unknown','owner_reported','document_checked')),
    ADD COLUMN rera_checked_on DATE;
-- Grants to leia_app if present, as in earlier migrations.
```

Backward compatibility: every column is nullable or defaults to `unknown`/empty, so the current
release keeps working and existing records stay valid. `ADD COLUMN` with constant defaults is a
metadata-only change in PostgreSQL 18 (brief ACCESS EXCLUSIVE lock, no table rewrite); run with
`lock_timeout=5s` like 012. **Existing builder records are not modified**; values stay `unknown` until
an agent fills them. Rollback: drop the columns and the `builders` table (no other table depends on
them).

## 3. Backend plan

1. `property_models.py`: add the fields to `PropertyDraft` (Literal choices as above, RERA regex,
   `possession_date` only when `construction_status = 'under_construction'`, `floor_number <= total_floors`).
2. Admin-only `POST/GET /staff/builders` (create, list); agents choose from the list (no free-text
   builder names, which prevents spelling variants).
3. Publish rules (`crm.py` approval): RERA number is shown to customers only with
   `rera_source = 'document_checked'`; otherwise it's withheld and the reply says "not verified".
4. Customer search: add `builder`, `ready_to_move`, `possession_before`, `lift`, `gated` to
   `SearchFilters` and `filter_properties`; move these terms from `UNSUPPORTED_TERMS` to real filters in
   `search_parser.py`; an "unknown" value never matches a positive filter.
5. Listing replies and comparison (`compare_reply`) show these values with their source label.
6. Tests: model validation, approval rule for RERA, search filters, eval cases for builder/RERA/
   possession questions (expected "not verified" when the source isn't document_checked).

## 4. Agent CRM plan

1. `propertyFields.ts`: new "Project & builder" section (builder picker, project name, RERA number +
   evidence, construction status, possession date, floors) and an "Amenities" section (lift, gated,
   security, multi-select amenities + `evidence("project")`).
2. `propertyApi.ts` types and the draft form; Customer app property view shows them with the
   "Verified from documents / Owner reported / Observed" label.
3. Forms tests (`npm run test:forms`) for the new fields and validation messages.

## 5. Rollout order

Approval → migration 014 file + backend + CRM PR → CI → backup → gated runner (`run_014.py`, same
gates as 012/013) → deploy → agents backfill the six pilot builders' listings → re-run the eval.
