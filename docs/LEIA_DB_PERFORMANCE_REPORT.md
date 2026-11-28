# LEIA database performance: connection pooling and /chat round trips

Branch `feature/pilot-db-performance` (from `staging/render-leia` @ `16b0377`). **Not merged, not
deployed, no migration, no live database change, no Groq calls, no EAS build.**

Status labels: **Measured on staging** (live, before this change) · **Measured locally** (benchmark
on this machine with simulated network delay) · **Tested locally / CI** · **Pending** (needs deploy).

## 1. Root cause

`database.get_connection()` called `psycopg.connect()` on **every** call. Each new connection to Render
PostgreSQL costs a TCP handshake, a TLS handshake and SCRAM-SHA-256 authentication (about 7–8 network
round trips) before the first query. One deterministic `/chat` opened **5** of them (user lookup, two
rate-limit buckets, the chat transaction, and the search, which opened a second connection while the
first was still held). A no-result search opened **up to 6 more** for the "listings exist if you…"
suggestions. The queries themselves take under 1 ms (section 6). The time went into connection setup
and round trips.

Evidence (Measured on staging, `perf_baseline.py`, 2026-11-28 00:15 UTC, client in Canada via Cloudflare):

| Request | n | p50 | p95 | max |
|---|---|---|---|---|
| `/health` (no DB) | 20 | 242 ms | 320 ms | 679 ms |
| `/properties` (1 connection) | 20 | 717 ms | 1,600 ms | 1,678 ms |
| `/auth/me` (1 connection) | 20 | 766 ms | 1,607 ms | 1,699 ms |
| `/chat` deterministic, with results | 10 | 2,937 ms | 4,691 ms | 4,691 ms |
| `/chat` deterministic, empty result | 10 | 4,784 ms | 7,550 ms | 7,550 ms |

`/properties` minus `/health` ≈ **0.4–0.5 s per new connection + one query on the server**.

### Configuration audit
- Driver: psycopg 3.3.6 (sync). FastAPI runs the sync endpoints in its threadpool, and async upload
  endpoints already move database work to it (`run_in_threadpool`), so the event loop is never blocked.
- Session state: only `SET LOCAL` (transaction-scoped), so pooled connections are safe to reuse.
- Render PostgreSQL (read-only check): PostgreSQL 18.6, `max_connections=100`,
  `superuser_reserved_connections=3`, about 10 connections in use across the cluster, all TLS, SCRAM.
- Region: the database is in **Singapore**. The local `backend/.env` uses the **external** hostname
  (needed from outside Render). Claude can't read the Render service's own `DATABASE_URL` or its region.
  The measured ~0.45 s per connection matches about 40 ms per round trip in the local simulation.
  An internal same-region connection would be about 1 ms. **Owner check (free):** in Render, confirm the
  backend runs in Singapore and that its `DATABASE_URL` is the database's **Internal** URL.
- Workers: the start command lives in the Render dashboard (not in the repo). The pool is per process,
  so total connections = workers × `LEIA_DB_POOL_MAX_SIZE`.

## 2. Architecture

**Before:** every `get_connection()` opened a new connection. In `/chat`, the conversation row lock and
connection were held while waiting for Groq (AI path) or Google Maps (listing walks). The search
opened a second, nested connection.

**After:**
- **Per-process pool** (`psycopg_pool`) behind the unchanged `with get_connection() as connection:` API.
  The connection commits on success and rolls back on error, as before. It is then reset (idle,
  non-autocommit) and returned to the pool instead of being closed.
  - Opened at app startup without blocking on the database, and closed at shutdown (FastAPI lifespan).
  - Idle connections are pinged before reuse, so killed connections from a database restart are replaced.
  - Connections are recycled after 30 minutes, and idle extras are closed after 5 minutes.
  - When no connection is free within the pool timeout, the request returns **HTTP 503 with
    `Retry-After: 2`**. It never queues forever.
- **`/chat` in two phases:**
  1. Phase A: work that waits on the network, with **no connection held**. Groq extraction (with the
     budget guard unchanged), or the listing reads (short autocommit block), then Google Maps.
  2. Phase B: **one short transaction**. It locks the conversation, runs the search **on the same
     connection** (no nesting), and saves both messages plus the filters in **one statement**.

  If the filters changed between the phases (a concurrent message in the same conversation), the AI
  answer is discarded and the request falls back to ordinary search. The Groq call is still in the ledger.
  - Deterministic messages skip phase A entirely (the parser needs only the text).
- **Rate limits:** all buckets for a request in **one statement**, pipelined with `BEGIN` and the 3 s
  statement timeout. Behaviour is unchanged: a bucket is only counted if the previous one accepted, and
  `429` names the full bucket. The AI guard's 4 buckets use the same single statement.
- **Suggestions:** one `count(*) FILTER (…)` query covers every loosened filter set, instead of up to 6
  full searches. Customer filters are untouched, only approved + available listings are counted, and
  all values are SQL parameters.
- **User lookup** on every authenticated request runs in autocommit (no BEGIN/COMMIT round trips).

Round trips for one deterministic `/chat`: about 40 (5 new connections plus queries) → **about 11**.
In Server-Timing: 3 short connection uses, 6–7 statements.

## 3. Pool configuration and rationale

| Variable | Default | Why |
|---|---|---|
| `LEIA_DB_POOL_ENABLED` | `true` | `false` = old behaviour, a rollback switch with no code change |
| `LEIA_DB_POOL_MIN_SIZE` | `1` | keeps one warm connection; small idle footprint |
| `LEIA_DB_POOL_MAX_SIZE` | `5` | per process. Each request now uses **at most one connection at a time**. 5 handles ~9 chat req/s at 40 ms round trips (section 5). |
| `LEIA_DB_POOL_TIMEOUT` | `5` s | bounded wait, then 503 (shed load instead of piling up threads) |
| `LEIA_DB_POOL_MAX_IDLE` | `300` s | release extra connections when quiet |
| `LEIA_DB_POOL_MAX_LIFETIME` | `1800` s | replace long-lived connections (network or proxy resets) |
| `LEIA_DB_POOL_CHECK_IDLE` | `30` s | ping only connections idle ≥ 30 s. This catches restarts without an extra round trip on busy connections. |
| `LEIA_DB_CONNECT_TIMEOUT` | `10` s | new-connection timeout |

**Connection budget:** 100 − 3 reserved = 97, and about 10 are already in use on the cluster.
Rule: `workers × LEIA_DB_POOL_MAX_SIZE + 5 (migrations/admin) ≤ 50` (half the server, leaving room for
other clients).
- 1 worker × 5 = 5
- 2 × 5 = 10
- 4 × 5 = 20

Raise `LEIA_DB_POOL_MAX_SIZE` only with that rule.

Observability (numbers only: no SQL text, parameters, messages, tokens or identities):
- `leia.search` events now include:
  - `request_latency_ms`
  - `db_connections`, `db_acquire_ms`, `db_queries`, `db_query_ms`
  - `db_pool_exhausted`
  - `ai_provider_ms` (Groq time, separate from database time)
- A `slow_request` JSON line for any request ≥ `LEIA_SLOW_REQUEST_MS` (1500), or any request that hit
  pool exhaustion, with the route template, status and the same DB numbers.
- A `db_pool_exhausted` warning with the pool stats.
- `LEIA_SERVER_TIMING=true` (default off) adds a `Server-Timing` header (total, db-acquire, db-query and
  count, connections, ai), so latency can be measured from outside on staging.

## 4. Before / after benchmark (Measured locally)

Environment:
- Windows 11 laptop; local PostgreSQL 18.6 with a SCRAM role (no TLS).
- A TCP proxy adds **20 ms each way (40 ms round trip)**, about the delay inferred from staging.
- uvicorn with 1 worker; synthetic data: 60 listings (48 approved), 12 customers.
- Groq disabled (`LEIA_AI_ENABLED=false`, fake key).
- Sequential requests after a 5-request warm-up; p95 = 95th percentile.
- Same database and proxy for both runs: *before* = `16b0377`, *after* = `5adc2c4`.

| Request (n=30 each) | Before p50 / p95 / max | After p50 / p95 / max |
|---|---|---|
| `/properties` | 400 / 500 / 567 ms | **50 / 59 / 105 ms** |
| `/auth/me` | 391 / 491 / 499 ms | **50 / 55 / 60 ms** |
| `/chat` English, with results | 2,275 / 2,376 / 2,401 ms | **500 / 524 / 700 ms** |
| `/chat` empty result (+ suggestions) | 3,939 / 4,036 / 4,126 ms | **550 / 642 / 660 ms** |
| `/chat` Telugu | 2,293 / 2,409 / 2,501 ms | **500 / 516 / 516 ms** |
| `/chat` Telugu-English (no match) | 3,906 / 4,150 / 4,376 ms | **550 / 566 / 576 ms** |
| `/chat`, 20 concurrent clients, n=60 | 2,754 / 3,329 / 3,580 ms, 6.7 req/s | **2,026 / 2,267 / 2,401 ms, 9.1 req/s** |

All 330 requests per run returned 200. The local "before" closely matches staging, so the simulation is
representative. Example Server-Timing (after, with results):
`total 500 ms, db-acquire 0.0 ms, db-query 297 ms (6 queries), 3 connections, ai 0 ms`. The remaining
~200 ms are the two COMMITs and the BEGIN of phase B (round trips not inside `execute`).

**Expected on staging (Pending):** warm deterministic chat ≈ 0.5 s plus ~0.25–0.3 s of network and
Cloudflare overhead ≈ **0.8 s**, under the 1 s target. With the Internal database URL in the same region,
it should be much lower (round trips ≈ 1 ms). This is a target, not a guarantee, and it is verified only
after deployment, by setting `LEIA_SERVER_TIMING=true` and re-running `runners/perf_baseline.py`.

Concurrency: with 20 simultaneous clients, a pool of 5 at 40 ms round trips gives about 9 req/s per
worker; requests then wait for a connection (no errors, no exhaustion at a 5 s timeout). Raising the
pool size or shortening the network distance raises this.

## 5. Regression and concurrency tests

Local run (same steps as CI), every suite on its own disposable local database:

| Suite | Result |
|---|---|
| `test_crm.py` + `test_customer_session.py` (auth, sessions, revocation, RBAC, publishing, listing visibility, GPS, media, temple/listing chat) | 99 passed |
| `test_studio_web.py` | 3 passed, 1 skipped (ffprobe installed only in CI) |
| `test_marketing_jobs.py` (Digital Studio) | 21 passed |
| `test_chat_filters.py`, `test_facing.py`, `test_search_eval.py` (100-question eval: English, Telugu, Telugu-English, mocked Groq) | 236 passed; eval **100/100** |
| `test_hybrid_search.py` (+3 new: one transaction per deterministic search; **no connection open during the Groq call**; conversation changed during Groq → safe fallback, call still in the ledger) | 9 passed |
| `test_ai_guard.py` (budget cap, concurrency, ledger failure, timeouts, retries) | 9 passed |
| **`test_db_pool.py` (new, real PostgreSQL)**: reuse without leaks (200 uses, 16 threads, ≤4 connections); failed transaction rollback and clean reuse; commit and autocommit reset; transaction isolation; pool exhaustion → `PoolTimeout` within the bounded wait, and **HTTP 503 + Retry-After** → recovery; killed connections replaced (database restart); unchecked stale connection fails once then recovers; **concurrent AI budget reservations through the pool never exceed the cap** (60 attempts / 24 threads / 4 connections → exactly 14 fit); **rate limits exact under concurrency** and multi-bucket; per-request metrics; pool-off switch | 11 passed (3 consecutive runs) |
| **Total** | **388 passed, 1 skipped, 0 failed** |

- **Changed tests:**
  - Two chat tests use mocks that pin the exact query order; their mock sequences were updated for the
    new order (ownership check, reads, then lock and save). Every assertion keeps its meaning, and new
    ones were added: Google is called between the two database phases, and walk results are never saved.
  - The eval runner also swaps out the new count query, as it already did for `filter_properties`.
- **Live check:** the stale GEO check expected 38 routes; it now expects the verified **41** plus the
  required GEO routes. It was re-run live: **12/12 PASS** (read-only part).
- **CI on the branch:** see section 9.

## 6. Query plans and indexes

Plans were taken with `EXPLAIN ANALYZE` on the seeded local data:
- **Search:** sequential scan, 0.2 ms.
- **Suggestion counts (4 variants in one query):** one scan, 0.16 ms.

Existing indexes cover today's pilot size: `properties (status, availability)`,
`chat_messages (conversation_id, id)`, `conversations (user_id, updated_at)` and
`request_limits (expires_at)`. **No migration is proposed now.**

Separate proposal, only if listings grow past several thousand (needs its own approval):
- a partial index `properties (reviewed_at DESC) WHERE status='approved' AND availability='available'`
- an expression index on the normalised locality used by search

## 7. Files changed

- `backend/database.py`: the pool, lifespan helpers, autocommit support, and the rollback switch.
- `backend/db_metrics.py` (new): per-request DB/AI metrics and a timed cursor.
- `backend/observability.py`: metrics per request, the slow-request log, the optional Server-Timing
  header, and DB fields in search events.
- `backend/main.py`:
  - lifespan, and a 503 handler for pool exhaustion
  - two-phase `/chat`, the single save statement, cursor reuse for search
  - one-query suggestions, Google Maps calls after the transaction
- `backend/rate_limits.py`: multi-bucket single statement (pipelined) and `LimitReached` (names the bucket).
- `backend/ai_guard.py`: the 4 AI buckets in one statement, and Groq timing.
- `backend/auth.py`: autocommit user lookup.
- `backend/requirements.txt` (`psycopg-pool==3.3.3`) and `backend/requirements-test.txt`.
- Tests:
  - `tests/conftest.py`: also guards pooled connections
  - `tests/test_db_pool.py` (new)
  - `tests/test_hybrid_search.py`, `tests/test_crm.py`, `tests/eval/runner.py`
- `.github/workflows/crm-checks.yml`: adds the pool test database and step.
- Outside the repo: `D:\LEIA-Releases\runners\geo_m1_1_live_verify.py` (route contract) and
  `perf_baseline.py` (new; live latency measurement).

## 8. Deployment and rollback

**Deploy (needs approval):**
1. Open a PR into `staging/render-leia`, wait for green CI, merge.
2. Deploy on Render manually. The build installs `psycopg-pool` from `requirements.txt`.
   No migration and no new required variables: the defaults are safe for 1 worker.
3. Optional for measurement: set `LEIA_SERVER_TIMING=true` on **staging** only.
4. Verify:
   - `/health` returns 200.
   - Re-run `runners\perf_baseline.py` (test customer, about 20 chat messages, sessions revoked).
   - The `leia.search` logs show `db_connections` = 3 for deterministic chats.
5. Owner checks: Singapore region and the **Internal** database URL (free, likely the biggest further gain).

**Rollback:**
- **Fast:** set `LEIA_DB_POOL_ENABLED=false` and restart. Connections are opened per call again; the
  rest of the change stays.
- **Full:** redeploy `16b0377`. There are no schema changes, so nothing to undo in the database.

## 9. CI

GitHub Actions on `5adc2c4` (the code commit; this report is docs-only and outside the CI path filter):
**crm-api: success** (all steps, including the new `test_db_pool.py` step running in Ubuntu with the
pinned ffprobe Studio test) and **agent-app: success**.

## 10. Remaining risks

- **Benchmarks are local simulations** (no TLS, synthetic data). Staging numbers are Pending until deployed.
- **Unknown worker count:** the pool is per process. Check the Render start command before raising the
  pool size.
- **Concurrent messages in one conversation:** if a message arrives while Groq is answering another,
  the second AI answer is discarded and ordinary search is used. This is safe but a behaviour change.
- **Listing / temple routes:** the messages are saved after the Google call. If the call fails, nothing
  is saved, as before.
- **A connection killed within 30 s of its last use** is handed out unchecked. One request then fails
  and the next succeeds (tested). Set `LEIA_DB_POOL_CHECK_IDLE=0` to ping every time (+1 round trip).
- **Pipeline mode** (rate limits) needs libpq ≥ 14, which the `psycopg[binary]` wheels bundle.
- **Under heavy concurrency** with a pool of 5, requests wait (bounded) and may return 503 after 5 s
  instead of timing out at the proxy.
- **Not done:** physical-device testing; the Studio upload E2E with a real MP4. Neither is part of this
  change.
