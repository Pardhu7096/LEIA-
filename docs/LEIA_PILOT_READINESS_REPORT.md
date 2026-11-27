# LEIA pilot readiness report (overnight sprint, 26–27 Nov 2026)

Branch `feature/pilot-readiness-2026-11`, cut from `staging/render-leia` at `3591422`. Nothing was merged, deployed, migrated on staging, or billed.

**Status labels used in this report**
- **Implemented**: the code exists on the branch.
- **Tested locally**: automated tests pass on a disposable local PostgreSQL.
- **Mock-tested**: Groq replies were simulated.
- **Verified (real provider)**: a real Groq call was made. **None in this sprint.**
- **Verified (staging)**: checked on Render staging. **None in this sprint.**
- **Pending manual**: needs a person or a device.

---

## 1. Executive summary

- **Cost protection** (implemented, tested locally, mock-tested).
  - Every paid Groq call passes:
    - a kill switch
    - input-size limits
    - per-user and global request limits
    - a **hard daily and monthly INR cap**, enforced atomically in PostgreSQL (migration 013)
    - a 10 s timeout and at most 2 attempts with backoff
    - low-effort reasoning, with reasoning text not returned
  - Spend is settled from Groq's reported token usage.
  - If the ledger is unavailable, LEIA makes **no** paid call.
  - Default caps: ₹500 per month, ₹25 per day, 900 AI calls per day across all users.
- **Search** (implemented, tested locally).
  - "AI understands intent; PostgreSQL provides facts."
  - A deterministic parser answers simple English, Telugu and Telugu-English searches without Groq. In the 100-question suite, **Groq is called for only 7 of 100 questions**.
  - Groq output is validated against a schema, and SQL stays parameterised.
  - Any AI failure falls back to ordinary property search.
- **No fabricated facts** (implemented, tested locally). LEIA explicitly says these are not verified, instead of inventing or silently dropping them:
  - lift, possession status, RERA, builder, legal approvals
  - loan eligibility, nearby schools and hospitals
- **Evaluation**: **100/100 passed** (mock-tested, synthetic data). The suite includes draft, rejected and sold listings, and none ever appeared in a result.
- **Keyboard covering inputs** (implemented, typechecked and linted; **not device-tested**). Root cause: Android is edge-to-edge on Expo SDK 58, so the window no longer resizes, and several screens disabled `KeyboardAvoidingView` on Android.
- **Observability** (implemented, tested locally): request IDs plus one privacy-safe JSON log line per search.
- **Regression**:
  - **374 backend tests pass**, 1 skipped (the ffprobe check, which needs Linux).
  - Both apps pass type-check, lint and their unit tests.
  - GitHub CI on the branch: see §9.
- **Pilot readiness**: the search backend is ready for **staging verification**, but not for a commercial pilot yet. The blockers are in §12. The biggest product gap: the database has **no fields for builder name, RERA number, possession date or most amenities**. Builder listings for the six pilot builders can't show these as verified facts until those fields exist.

## 2. Original architecture and problems found

**Stack**
- FastAPI backend (`backend/main.py`), PostgreSQL 18 on Render staging.
- Customer app (`frontend/`) and Agent CRM (`agent-app/`): Expo SDK 58, React Native 0.88.1.
- Agent CRM web build served at `/agent`; Digital Studio at `/studio`.

**Customer search lifecycle (before)**
1. `useSavedChat.send` creates or opens a conversation (`POST /conversations`), then sends `POST /chat {message, conversation_id, property_id?}` with a bearer token.
2. `get_current_user`, then `limit_chat` (10 per minute and 100 per day per user, DB fixed window).
3. The conversation row is locked (`SELECT … FOR UPDATE`).
4. Routing:
   - listing questions → `selected_listing_reply` (DB facts)
   - temple walk → Google Routes
   - **everything else → one Groq call** (`openai/gpt-oss-20b`, 20 s timeout, no retries, 800 max completion tokens, default reasoning effort)
5. The JSON reply is validated (`SearchRequirements`), then `filter_properties` runs a parameterised SQL query restricted to `status='approved' AND availability='available'`.
6. User and assistant messages and the filters are saved; the response is `{reply, properties, filters, ids}`.
7. The app renders reply bubbles and cards (prices in en-IN format).

**Groq audit (before)**

| Item | Finding |
|---|---|
| Model | `openai/gpt-oss-20b` |
| Calls per customer message | 1 for every non-listing message, including "hi", "3BHK under 65 lakh" and prompt-injection attempts |
| Input tokens | ~1,000 per call: system prompt ~910 (3,636 characters) plus filters and message. Conversation history is **not** sent, so input doesn't grow with chat length. |
| Output tokens | Up to 800, reasoning tokens included. Reasoning effort was the default, not `low`. |
| Retries / timeout | SDK retries 0; timeout 20 s |
| Rate limits | Per-user chat limits only. **No global limit.** |
| Spending cap / usage tracking | **None** |
| Caching / dedup | **None** |
| Fallback | **None**: a Groq error or invalid JSON returned HTTP 502 or 429 to the customer |
| Key exposure | Server-side only (`GROQ_API_KEY` env var); not present in either app bundle |
| Unauthenticated paid calls | Not possible: `/chat` requires a signed-in user |

**Other findings**
- **No Cloudflare storage integration in code.** Digital Studio uploads use generic S3-compatible storage (which can point at Cloudflare R2 via `STUDIO_S3_ENDPOINT_URL`), and it isn't configured on staging. Property photos and panoramas are stored in PostgreSQL (`property_media.content BYTEA`, max 12 MiB each).
- **Auth and RBAC:**
  - Customer JWT access tokens plus refresh tokens: an httpOnly cookie on web, SecureStore on native, 7-day lifetime.
  - Staff refresh cookie with a CSRF header.
  - Roles: `customer`, `agent`, `admin`, enforced with `require_staff` / `require_admin`.
  - The public `GET /properties` returns approved, available listings only.
- **Render:** `build-render.sh` (pip install, pinned ffprobe, Agent CRM web export); manual deploys only.
- **Known open security follow-ups** (unchanged by this sprint):
  - access tokens still work after logout until they expire
  - no audit trail for role changes
  - no refresh-cookie reuse detection

## 3. Updated search architecture

```
POST /chat (authenticated, per-user chat limit, conversation row lock)
 ├─ property_id set ............ selected_listing_reply  (DB facts; unverified facts stated as unverified)
 ├─ temple walk question ....... temple_walk_reply        (unchanged)
 ├─ compare question ........... compare_reply            (approved DB records only, no AI)
 └─ hybrid router (default; LEIA_SEARCH_ROUTER=ai keeps the legacy AI-first path, still guarded)
     search_parser.parse(message)  ── no network, no AI
      ├─ blocked / irrelevant / abuse ...... fixed reply, no AI
      ├─ greeting / reset / show all ....... fixed reply or SQL
      ├─ clarify (ambiguous unit, two BHKs, two facings, two localities, exclusions, rent)
      ├─ search (fully understood) ......... merge with saved filters → SQL
      └─ needs_ai (words it can't account for)
           ├─ message > 500 chars → ask to shorten (no AI)
           └─ single-flight cache → ai_guard.guarded_completion → Groq
                 → JSON → SearchRequirements (schema) → SQL
                 on ANY failure → ordinary search with what was understood, or a safe "couldn't interpret" reply
```

**What the deterministic parser understands**
- **Budgets:** lakh, lac, L, crore, cr, ₹ with Indian digit grouping, and Telugu లక్ష / కోటి.
- **Budget words:** under, above, between and around (stated as ±10%). Romanised and Telugu lopu, paina, varaku, లోపు, పైన, మధ్య.
- **Size:** BHK and bedrooms (digits, English words, Telugu words); square-footage ranges.
- **Facing:** all eight directions in English, abbreviations (NE/NW/SE/SW next to "facing"), romanised Telugu (uttaram, turpu, eesanyam…) and Telugu script.
- **Type:** flat, independent house or plot (ఫ్లాట్ / ప్లాట్ / స్థలం).
- **Amenities with real data:** parking, power backup.
- **Sorting:** by price, newest or largest.
- **Localities:** about 30 Visakhapatnam localities with spelling and Telugu variants, including Vepagunta, Lakshmipuram, Chinthalagraharam/Chintalaagraharam, Pinagadi, Pedagadi, Rampuram, PM Palem / P.M. Palem, Madhurawada and MVP Colony.
- **Locality matching in SQL:** compares normalised spellings (letters and digits only) against the stored locality and city, in addition to the original exact match.

**New validated filters** (`SearchFilters`): `min_area_sqft`, `max_area_sqft`, `parking`, `power_backup`, `sort`. `ORDER BY` comes from a fixed whitelist and is never built from user text. AI output can only set fields in this schema, and SQL is never generated by AI.

## 4. Files changed and why

| File | Why |
|---|---|
| `backend/ai_guard.py` (new) | Kill switch, limits, row-locked budget ledger, retries/backoff/timeout, usage settlement, single-flight cache |
| `backend/migrations/013_ai_usage_ledger.sql` (new) | `ai_usage_periods`, the hard-cap ledger (additive) |
| `backend/search_parser.py` (new) | Deterministic English / Telugu / mixed parser, locality spellings, unsupported-fact detection |
| `backend/observability.py` (new) | Request-ID middleware, one JSON search event per request |
| `backend/main.py` | New filters and SQL; hybrid router; guarded AI path; fallback; no-result suggestions; comparisons; unverified-fact replies; safe AI clarifications; request IDs |
| `backend/tests/test_ai_guard.py` (new) | Budget, concurrency, fail-closed, limits, retries, timeouts, dedup |
| `backend/tests/test_hybrid_search.py` (new) | `/chat` end-to-end against a DB: SQL filters, fallback, metering, cache, budget, 401, request ID, comparison |
| `backend/tests/eval/*`, `backend/tests/test_search_eval.py` (new) | 100-question suite, synthetic inventory, runner and report |
| `backend/tests/test_crm.py` | Saved-chat tests now explicitly exercise the AI-first path through the real guard (fixture only; assertions unchanged) |
| `.github/workflows/crm-checks.yml` | CI also runs the chat-filter, facing, evaluation, hybrid and guard tests |
| `frontend/src/app/index.tsx` | Keyboard avoidance and safe-area insets on the search screen |
| `frontend/src/components/authForm.tsx` | Keyboard avoidance on login and registration |
| `frontend/src/components/PropertyView.tsx` | Android keyboard avoidance for property questions |
| `agent-app/App.tsx` | Android keyboard avoidance for sign-in and property forms |
| `docs/eval/search_eval_results.{md,json}` | Generated evaluation results |

## 5. Git

| Commit | Change |
|---|---|
| `c94634b` | Budget-guarded AI calls with a concurrency-safe cost ledger |
| `06f57a6` | Deterministic-first routing, AI only when needed |
| `2bc4fac` | 100-question evaluation suite; new tests in CI |
| `140add0` | Keyboard fixes (edge-to-edge Android) |
| `001eb43` | Comparison prices in Indian digit grouping |
| (this report) | Report and evaluation results |

Base: `3591422` (`staging/render-leia`). The branch is pushed so CI can run; **not merged**.

## 6. Groq cost protection

| # | Requirement | Implementation | Status |
|---|---|---|---|
| 1 | Server-side key | `GROQ_API_KEY` env var only; apps never call Groq | Implemented (was already true) |
| 2 | Per-user limits | `ai-user-minute` (5) and `ai-user-day` (40), in addition to chat limits (10/min, 100/day) | Tested locally |
| 3 | Global limits | `ai-global-minute` (25) and `ai-global-day` (900) | Tested locally |
| 4 | Daily and monthly usage | `ai_usage_periods` rows `day:YYYY-MM-DD` and `month:YYYY-MM` (UTC): requests, tokens, spent and reserved micro-INR | Tested locally |
| 5 | Token limits | Message over 500 characters → no AI; estimated input over 3,000 tokens → no AI; `max_completion_tokens` 600 | Tested locally |
| 6 | Bounded history | Only the current filters and the message are sent; never chat history | By design (unchanged) |
| 7 | Deduplication | In-process single-flight TTL cache (15 min). Identical concurrent requests wait for one call. | Tested locally (per instance only) |
| 8 | Retries | At most 2 attempts; exponential backoff with jitter; honours Retry-After up to 5 s; only timeouts, connection errors, 429 and 5xx are retried | Tested locally |
| 9 | Timeouts | 10 s per attempt (`timeout=`) | Tested locally (mock) |
| 10 | Spending cap | Worst-case cost **reserved before** each attempt, under row locks for day then month; settled with actual usage | Tested locally |
| 11 | Fallback | Any `AIUnavailable` or invalid AI output → ordinary search with the deterministically understood filters, or a safe reply. The customer sees no 5xx. | Tested locally |
| 12 | Concurrency-safe enforcement | 12 simultaneous requests against a cap sized for 5 worst cases: never more than 5 succeed; spent plus reserved never exceeds the cap | Tested locally |

**Details**
- **Usage:** `prompt_tokens` and `completion_tokens` from Groq's response are used. Completion tokens are treated as including reasoning tokens, billed as output. That's an assumption; Groq's reasoning page doesn't say. A timed-out attempt is charged its worst case, because the provider may still bill it.
- **Pricing (verified 2026-11-26, Groq console models table):** gpt-oss-20b costs **$0.075 per million input tokens and $0.30 per million output tokens**. It's configurable. **Assumption: USD→INR = 90** (unverified; `LEIA_AI_USD_TO_INR`).
- **Request parameters:** `reasoning_effort="low"` and `include_reasoning=false` match Groq's reasoning documentation (`reasoning_format` isn't supported on gpt-oss). **They haven't been sent to the real API yet.** If Groq rejected them, every AI call would fail and fall back safely. Verify with one staging call (§18).
- **Provider limits are not LEIA's cap.** Groq's rate-limit page lists 30 RPM / 1K RPD / 8K TPM for gpt-oss-20b (the plan isn't clear from the page); the models table lists 1K RPM / 250K TPM. On the lower tier, set `LEIA_AI_GLOBAL_PER_MINUTE=4`; provider 429s still fall back safely.
- **Known limits:**
  - The cache is per instance (staging runs one).
  - A crash between reserving and settling leaves a reservation counted, which errs towards spending less.
  - Usage periods are UTC.

## 7. Estimated AI cost (assumptions explicit)

**Assumptions**
- 6 chat messages per daily active customer.
- 30% of real messages need AI. The suite needed 7%, but real customers phrase things more freely.
- ~1,100 input tokens and ~300 output tokens per call (worst case 600).
- ₹90 per $1; no cache hits counted.
- That gives about **₹0.0155 per AI call** (worst case ₹0.024).

| Daily active customers | AI calls per day | Typical per month | Worst case per month | With default caps |
|---|---|---|---|---|
| 100 | 180 | ₹84 | ₹127 | Within ₹500 |
| 500 | 900 | ₹419 | ₹637 | Capped at ₹500 (monthly cap and 900 calls/day) |
| 1,000 | 1,800 | ₹838 | ₹1,276 | Capped at ₹500; extra AI requests fall back to ordinary search |

The legacy path, for comparison (every message to AI, up to 800 output tokens, default reasoning effort), would be about ₹3,800 per month at 1,000 daily customers, with no cap.

The ₹500 Groq budget therefore covers about 500 daily active customers. Above that, the cap degrades only the free-text interpretation, not search.

## 8. 100-question evaluation

**100/100 passed (mock-tested).** Results: `docs/eval/search_eval_results.md` and `.json`. To reproduce:

```powershell
D:\LEIA\backend\.venv\Scripts\python.exe -X utf8 -I backend/tests/eval/runner.py
```

| Category | Cases | Passed | Groq called |
|---|---|---|---|
| Budget / BHK / area | 20 | 20 | 1 |
| Locality | 15 | 15 | 1 |
| Facing / amenities | 10 | 10 | 1 |
| Telugu | 15 | 15 | 1 |
| Telugu-English mixed | 10 | 10 | 1 |
| Comparisons / follow-ups | 10 | 10 | 1 |
| Missing information / no results | 10 | 10 | 1 |
| Security / abuse | 10 | 10 | 0 |

**How each case is checked**
- Each case records: question, expected intent, expected filters, expected behaviour, whether Groq should be called, and the actual intent, filters, listings and reply, with a pass or fail and the reason.
- The runner checks intent, Groq use, filters, exact listing IDs or order, required and forbidden reply text, and that draft, rejected or sold listings never appear.
- A deliberately wrong expectation was confirmed to fail.

**Caveats**
- **Groq replies are mocked**, so this proves routing, validation and fact-safety, not real model quality.
- The cases were written alongside the parser, so they show intended behaviour rather than blind accuracy. Next, run a set of real customer questions collected during the pilot.
- One reply worth refining: "which builder built these flats?" lists all flats plus the "builder not verified" note.

## 9. Regression results

**Backend** (local disposable PostgreSQL 18; never `backend/.env`):

| Test files | Result |
|---|---|
| `test_crm.py` + `test_customer_session.py` (auth, sessions, RBAC, drafts/publishing, public listings, GPS/location, media, chat) | 99 passed |
| `test_studio_web.py` + `test_marketing_jobs.py` (Studio, walkthrough uploads, marketing jobs) | 24 passed, 1 skipped (real ffprobe needs Linux; runs in CI) |
| `test_chat_filters.py` + `test_facing.py` + `test_search_eval.py` | 236 passed |
| `test_hybrid_search.py` (fallback, metering, cache, budget, 401, request IDs, comparison) | 6 passed |
| `test_ai_guard.py` (rate limits, concurrent budget, retries, timeouts, dedup) | 9 passed |

**Apps**
- Customer: `tsc` and `expo lint` clean; session tests 23/23.
- Agent CRM: `tsc` and `eslint` clean; forms 23/23, location 19/19, staff session 9/9.

**GitHub CI** on `001eb43` (pushed branch): see the result appended at the end of this report.

**Not run:** EAS or Android builds (not authorised), web export, device tests.

**Migration safety:** 013 is additive (one new table plus a conditional grant) and was applied cleanly by `migrate_crm` in the disposable test databases. No existing table changes.

## 10. Keyboard fixes

- **Cause:** Expo SDK 58 / RN 0.88 run Android **edge-to-edge**, so `adjustResize` no longer shrinks the window. Screens using `behavior={Platform.OS === "ios" ? "padding" : undefined}` did nothing on Android, and the Customer search screen had no keyboard handling at all.
- **Customer app:**
  - Search screen: `KeyboardAvoidingView` with padding, plus `SafeAreaView` for top and bottom so the header and composer stay clear of the system bars, plus drag-to-dismiss.
  - Login and registration: keyboard avoidance and keyboard insets.
  - Property "Ask": Android padding and drag-to-dismiss.
- **Agent CRM:** sign-in and the property create/edit forms (one scroll view) get padding on Android, drag-to-dismiss and iOS keyboard insets. Multiline fields already align to the top.
- **Not changed:** the Agent CRM has no safe-area library, so the top inset on Android is unchanged (cosmetic, to verify on device).
- **Status:** implemented, typechecked and linted. **Not validated on a physical device or emulator** (checklist in §16).

## 11. Observability

- Every response carries `X-Request-ID`. A valid inbound ID is reused, otherwise one is generated.
- Each `/chat` search emits one JSON line to stdout (`leia.search` logger, visible in Render logs) with:
  - `request_id`, `route` (deterministic / ai / fallback / blocked / clarify / listing / compare), `language`
  - `deterministic_searches`, `groq_requests`, `input_tokens`, `output_tokens`, `reasoning_tokens`, `estimated_cost_inr`
  - `cache` (hit / miss), `results`, `empty_result`
  - `fallback_reason` (budget, user_rate, global_rate, provider_timeout, provider_error, invalid_ai_output, disabled, ledger_unavailable…), plus `budget_limited` and `rate_limited` flags
  - `db_error`, `unsupported`, `latency_ms`
- **Never logged:** message text, replies, conversation contents, tokens, secrets, user IDs.
- **Gap:** requests rejected earlier by the per-user chat limit (HTTP 429 before routing) aren't logged as search events.
- Daily and monthly AI spend can be read from `ai_usage_periods`. No paid observability services.

## 12. Remaining blockers

1. **Merge and deploy are not authorised.** The branch needs review, a PR into `staging/render-leia`, and a manual Render deploy.
2. **Migration 013 needs approval** (§14). Until it's applied, the AI path fails closed: every free-text query falls back, while deterministic search keeps working.
3. **No real-provider verification.** One real call is needed to confirm the request parameters and the token accounting.
4. **No device validation of the keyboard fixes** (§16).
5. **Builder data model.** No columns exist for builder name, RERA number, possession date or status, lift and other amenities, or legal approvals. These can't be searched or shown as verified. They need a schema migration and CRM fields, which need approval.
6. **Digital Studio storage** is still unconfigured on staging (from the earlier Studio work).
7. **Existing security follow-ups** (§2).

## 13. Environment variables

**Required (already set):** `GROQ_API_KEY`, `DATABASE_URL`, `JWT_SECRET`, `EXPO_PUBLIC_API_URL` (build).

**New, all optional with safe defaults:**

| Variable | Default | Purpose |
|---|---|---|
| `LEIA_SEARCH_ROUTER` | `hybrid` | `ai` = legacy AI-first routing (still guarded) |
| `LEIA_AI_ENABLED` | `true` | Kill switch for all paid AI calls |
| `LEIA_AI_MODEL` | `openai/gpt-oss-20b` | Model |
| `LEIA_AI_MONTHLY_BUDGET_INR` | `500` | Hard monthly cap |
| `LEIA_AI_DAILY_BUDGET_INR` | monthly ÷ 20 (`25`) | Hard daily cap |
| `LEIA_AI_PRICE_INPUT_USD_PER_M` | `0.075` | Groq input price |
| `LEIA_AI_PRICE_OUTPUT_USD_PER_M` | `0.30` | Groq output price |
| `LEIA_AI_USD_TO_INR` | `90` | Exchange-rate assumption |
| `LEIA_AI_USER_PER_MINUTE` | `5` | Per-user AI limit |
| `LEIA_AI_USER_PER_DAY` | `40` | Per-user AI limit |
| `LEIA_AI_GLOBAL_PER_MINUTE` | `25` | Global AI limit |
| `LEIA_AI_GLOBAL_PER_DAY` | `900` | Global AI limit |
| `LEIA_AI_TIMEOUT_SECONDS` | `10` | Per-attempt timeout |
| `LEIA_AI_MAX_ATTEMPTS` | `2` (max 3) | Retries |
| `LEIA_AI_BACKOFF_SECONDS` | `0.5` | Backoff base |
| `LEIA_AI_MAX_OUTPUT_TOKENS` | `600` | Output limit |
| `LEIA_AI_MAX_MESSAGE_CHARS` | `500` | Input limit |
| `LEIA_AI_MAX_INPUT_TOKENS` | `3000` | Input limit |
| `LEIA_AI_CACHE_SECONDS` | `900` | Dedup cache lifetime |

## 14. Migrations requiring approval

- **`013_ai_usage_ledger.sql`:** creates `public.ai_usage_periods` and grants it to `leia_app` if that role exists.
  - Additive and safe while the current release is live.
  - Apply **before** deploying this branch, with a gated runner: backup first; only 013 pending; bounded `lock_timeout`.
  - Rollback: the table can stay. Dropping it simply makes AI fail closed.

## 15. Deployment and rollback

**Deploy (each step needs owner approval)**
1. Review the branch and open a PR into `staging/render-leia`; wait for green CI.
2. Take a pre-013 backup, dry-run, then apply 013 with a gated runner (as for 009–012). Verify the tracker row and the table.
3. Merge, then run a manual Render deploy of the merge commit.
4. Smoke-test staging:
   - `/health`
   - a signed-in test customer asks "3BHK under 65 lakh" (no Groq call: `route":"deterministic"` in the logs)
   - "a quiet 3 bhk for my parents in Madhurawada" (one Groq call; `groq_requests":1`, tokens and cost logged)
   - repeat it (`cache":"hit"`)
   - check the `ai_usage_periods` rows
5. Optionally set `LEIA_AI_DAILY_BUDGET_INR=0` briefly and confirm the fallback reply.

**Rollback**
- Fastest: `LEIA_SEARCH_ROUTER=ai` (legacy routing, still capped), or `LEIA_AI_ENABLED=false` (deterministic only).
- Full: redeploy `3591422`. Migration 013 can stay.

## 16. Physical-device checklist (pending manual)

Record the device, Android version and keyboard (Gboard, Samsung) for each:

- [ ] **Customer search screen:**
  - focus the search box → keyboard opens → the composer stays fully visible above it
  - the header isn't under the status bar
  - the composer isn't under the navigation bar or gesture area
- [ ] **Customer, scrolling:** scroll the chat with the keyboard open → it dismisses on drag; the send button stays tappable.
- [ ] **Customer login and registration:** each field is visible while typing; the password field and button aren't covered; small screen (5" or less) too.
- [ ] **Customer property "Ask" tab:** the input stays above the keyboard; replies scroll.
- [ ] **Agent CRM sign-in:** the password field and Sign in button are visible with the keyboard open.
- [ ] **Agent CRM property form:**
  - focus fields near the bottom (notes, multiline) → the field scrolls into view
  - multiline text starts at the top
  - drag dismisses the keyboard
- [ ] **Agent CRM:** check the top inset (status bar overlap) after scrolling.
- [ ] **Searches on a real device:** Telugu input ("వేపగుంటలో 65 లక్షల లోపు 3 బీహెచ్కే"), Telugu-English input, and a free-text query; check the replies and cards.
- [ ] Rotate is N/A (portrait only). Large font size: inputs remain usable.

## 17. Pilot readiness assessment

| Area | Ready? | Note |
|---|---|---|
| AI cost control | Yes, after staging verification | Hard cap, fail-closed; real-call check pending |
| Search quality and fact safety | Yes for supported filters | Builder, RERA, possession and amenity data missing from the schema |
| Customer experience | Likely, pending device test | Keyboard fixes not device-verified |
| Security | Acceptable for a controlled pilot | Known follow-ups open (§2) |
| Operations | Basic | Structured logs and the ledger; no alerting |

**Overall: not yet ready for a commercial pilot.** About two working days of approved steps away, as listed in §18.

## 18. Next actions, 27–30 November

- **27 Nov:**
  - Review this branch.
  - Approve migration 013 and the staging deploy.
  - Run the §15 smoke tests, including **one real Groq call**: confirm the parameters, `usage` fields and cost logging.
  - Decide the Groq plan and limits (`LEIA_AI_GLOBAL_PER_MINUTE`).
- **28 Nov:**
  - Device-test the keyboard checklist (§16) on 2 Android phones (one small).
  - Approve a builder-data schema: builder name, RERA number, possession status and date, lift and key amenities, each with a "verified by / date" source. Add CRM fields.
- **29 Nov:**
  - Load the six builders' verified listings through the Agent CRM (drafts, then admin approval).
  - Run 30–50 real Telugu and English customer questions on staging and add any misses to the evaluation suite.
- **30 Nov:**
  - Go / no-go review against §12.
  - Configure Studio storage if marketing is needed for launch.
  - Set the production budget env vars.
  - Prepare a production deploy plan (separate approval).
