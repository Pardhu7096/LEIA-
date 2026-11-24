# Digital Studio roadmap

V1 goal: a 20–35 s vertical (1080 × 1920) property reel per approved listing,
built only from verified LEIA data and authentic media, approved by a human
before it is posted to Instagram and Facebook.

Out of scope for V1: drone footage, plot polygon selection or tracking, OpenCV
motion tracking, automated aerial highlighting, paid Google Flow generation.

| Milestone | Scope | Depends on |
|---|---|---|
| **M1 (done)** | Outbox event, idempotent marketing job on approval, failure isolation, reconciler, status API, RBAC, tests | — |
| **M2 Data prerequisites** | Walkthrough video upload (new media kind, stored in object storage, not in PostgreSQL); verified agent phone on `users`; nearby places captured with source and date into the snapshot; official LEIA emerald logo as a versioned asset | M1 |
| **M3 Content** | Template catalogue per property type; 3C script (Context, Content, Call to action) generated only from snapshot fields; Telugu-English voiceover via a pluggable provider; Telugu and English captions with timing; spec highlights; a validator that rejects any number or claim not present in the snapshot | M2 |
| **M4 Render worker** | Separate process from this repo; claims jobs with `FOR UPDATE SKIP LOCKED` and `locked_until` leases; FFmpeg composition with transitions and audio sync; 1080 × 1920, 20–35 s; output to object storage; automatic retries with backoff up to `max_attempts`; `processing → awaiting_review` or `failed` | M3 |
| **M5 Studio dashboard** | Agent CRM screens: job queue, status and failures, preview, regenerate (`awaiting_review → queued`), approve or cancel | M4 |
| **M6 Publishing** | Meta Graph API for Instagram Reels and Facebook; tokens in secrets only; `approved → publishing → published`, failures tracked; never publishes without M5 approval | M5 |
| **M7 AI Marketing Agent** | Orchestrates M3–M4 tools (template choice, media selection, script, render, quality check), stops at `awaiting_review` | M6 |

## Open decisions

- Separate `marketing` role, or keep studio actions admin-only (current).
- Object storage provider for footage and renders (Render disk, S3, R2).
- Voiceover provider for natural Telugu-English speech, and its cost.
- Whether agents may approve their own listings' videos.
