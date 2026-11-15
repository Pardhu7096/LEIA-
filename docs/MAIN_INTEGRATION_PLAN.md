# LEIA — plan to bring the product onto `main`

- **Prepared:** 13 November 2026 · follows `docs/EC2_READINESS_AUDIT.md`
- **Nothing here has been merged.** Both pull requests are drafts. No PR base was changed, nothing was
  force-pushed, no GitHub setting was changed, nothing was deployed.

## 1. Current state (verified from git and the GitHub API)

| Branch | Head | Relation |
| --- | --- | --- |
| `main` | `fce7de8` | initial commit only; unprotected; no rulesets |
| `codex/customer-quality-checks` (integration) | `e99ffc3` | `main` + 47 commits (PRs #1–#5) |
| `audit/leia-ec2-readiness` | see PR #6 | integration + the audit commits |

- The history is a straight line: `main` is an ancestor of the integration branch, which is an
  ancestor of the audit branch. Neither merge can conflict (`git merge-tree`: clean for both).
- Every other remote branch (`feature/*`, `test/*`, `codex/edit-drafts-and-utilities`) is fully
  contained in the integration branch. No work is stranded.
- Only one workflow is registered with GitHub Actions: "CRM checks". "LEIA EC2 delivery" will appear
  only when its file reaches `main`.

| PR | From → into | State |
| --- | --- | --- |
| [#6](https://github.com/Pardhu7096/LEIA-/pull/6) | `audit/leia-ec2-readiness` → `codex/customer-quality-checks` | draft, mergeable, checks green |
| [#7](https://github.com/Pardhu7096/LEIA-/pull/7) | `codex/customer-quality-checks` → `main` | draft, mergeable, checks green (without #6 yet) |

## 2. Merge plan

Why this order: `main` must never hold the deploy script as it is at `e99ffc3` (a failed first
deployment corrupts the release link; an empty database cannot be built; the apps call localhost).
Merging #6 first means the first thing `main` ever receives already has the fixes, in one reviewed
promotion, with no PR base changes.

1. **Confirm the reconciled baseline against the real development database.** The six
   differences found on 13 November are fixed on PR #6. On the development machine, from the
   PR #6 checkout (read-only; uses `backend/.env`, changes nothing):
   ```powershell
   cd D:\LEIA\backend
   .\.venv\Scripts\python.exe .\schema_report.py --compare .\tests\expected_schema.json
   ```
   Expected: `Schema matches.` Every other line is a remaining difference: say so on PR #6
   before merging; it is fixed there, not later. Do not run `migrate_crm.py` for this check.
2. **Review PR #6**, mark it *Ready for review*, and merge it into
   `codex/customer-quality-checks` with **Create a merge commit**.
   Expected: the integration branch tree becomes identical to the audit branch tree.
3. **Wait for the checks on PR #7 to re-run** (it updates itself when step 2 lands). Expected:
   `agent-app`, `crm-api` (79 passed), `customer-frontend` all green; 56 commits (55 plus the merge commit); 88 files changed.
4. **Protect `main`** with the settings in section 4 — before anything is merged into it.
5. **Create the `staging` and `production` environments** with the rules in section 4. An
   environment that does not exist is created automatically, with no protection, the first time
   the delivery workflow names it. No secrets are needed yet.
6. **Review PR #7**, mark it *Ready for review*, and merge it into `main` with **Create a merge
   commit**. Do not squash or rebase: that would give `main` different commits from the
   integration branch, make every merged side branch look unmerged, and fold the separately
   revertible audit fixes into one commit.
7. **Verify** (section 5).
8. **From then on, open pull requests against `main`.** `codex/customer-quality-checks` and the
   eight merged side branches can be deleted once step 7 passes; nothing depends on them.

If the promotion ever has to be undone: revert the merge commit through a pull request
(`git revert -m 1 <merge-commit>`). Never force-push `main`.

Not recommended: retargeting #6 to `main` and merging once. It needs a base change, mixes 47
product commits into the audit review, and leaves the integration branch behind `main`.

## 3. What was verified for the combined result

The two merges were rehearsed locally in a separate worktree (never pushed). The result's file tree
is identical to the audit branch tip, so tests on either describe the same code.

**Review of everything `main` would receive (73 files added, 15 modified, 0 deleted or renamed)**

| Check | Result |
| --- | --- |
| Merge conflicts | none (both merges) |
| Secrets in the result | `detect-secrets` (27 detectors): 15 hits, all inspected — the disposable CI database password, test fixtures and one docs sentence. No real credential. |
| Secrets in history | every commit on every branch and PR ref scanned for provider key formats, private keys, tokens and database URLs: one hit, the same disposable CI password |
| Secret-looking files ever committed | none (`backend/.env.example` holds placeholders only) |
| Stray files | no build output, dependencies folders, archives, dumps or logs; total tracked size 4 MB |
| Unrelated changes | none found: all changes are under `backend/`, `frontend/`, `agent-app/`, `deploy/`, `docs/`, `.github/`; no leftover debug statements or TODO markers |
| Files named in docs and workflows | all exist |
| Migrations | `000`–`007`, continuous |

Worth knowing, not blocking:

- The repository is **public**. Three commits carry a personal mailbox as author address, and the
  customer app contains a contact phone number. Both are already public on the integration branch;
  merging to `main` does not change that, it only makes them more visible.
- There is no root `README`, `LICENSE`, `SECURITY.md` or `CODEOWNERS`. Without a licence a public
  repository is "all rights reserved" by default — fine if intended.
- GitHub secret scanning and push protection are enabled. Dependabot security updates are disabled.

**Deployment fixes present on the audit branch** (each confirmed in the result and covered by tests):
baseline migration; preflight before the switch; `/health/ready`; first-deployment rollback fix;
same-commit retry; configurable web origins; `DB_SSLMODE`; API address from `EXPO_PUBLIC_API_URL`;
settings template; workflow time limits and fail-fast SSH; runbook and server examples.
One fix was added during this review: the checks now run on **every** pull request. They were
limited to certain paths, which would have left a docs-only pull request waiting forever once the
checks are required on `main`.

Still open and deliberately not changed (see the audit): pinned dependency lock file, SSH approach
for GitHub-hosted runners, staging/production database separation.

**Tests on the rehearsed result** (fresh virtual environment and fresh `npm ci`; local PostgreSQL 16,
Python 3.13, Node 22)

| Command | Result |
| --- | --- |
| `python -m compileall -q backend` | OK |
| `TEST_DATABASE_URL=<disposable-db> python -m pytest backend/tests -q` | 73 passed |
| `python -m pytest backend/tests -q` (no database) | 65 passed, 8 skipped |
| `agent-app`: `npm ci`, `npm run typecheck`, `npm run lint` | clean |
| `agent-app`: `npm run test:forms` · `node --experimental-strip-types --test tests/staffSession.test.mjs tests/apiUrl.test.mjs` | 18 passed · 8 passed |
| `frontend`: `npm ci`, `npx tsc --noEmit`, `npm run lint` | clean |
| `frontend`: `node --experimental-strip-types --test tests/roomTours.test.mjs tests/apiUrl.test.mjs` | 13 passed |
| `shellcheck deploy/ec2/deploy-release.sh` | clean |

**GitHub Actions ("CRM checks": Python 3.12, Node 24, PostgreSQL 16)**

| Commit / event | `agent-app` | `crm-api` | `customer-frontend` |
| --- | --- | --- | --- |
| Audit branch `0ae2b0e`, push | success | success | success |
| PR #6 `0ae2b0e`, pull request | success | success (73 passed) | success |
| PR #7 `e99ffc3` merged into `main`, pull request | success | success (31 passed) | success |

Not verified: the actual merge commits (they do not exist yet — step 3 covers the real one), and
anything on AWS. Local and CI results are not deployment validation.

## 4. GitHub protection requirements

All of this is available on a public repository in a personal account. None of it is configured
today, and none of it could be configured or fully read from this review (section 6).

**`main`** — Settings → Rules → Rulesets → New branch ruleset (or classic branch protection):

| Setting | Value |
| --- | --- |
| Target | `main`; enforcement active; no bypass list |
| Restrict deletions | on |
| Block force pushes | on |
| Require a pull request before merging | on; required approvals **0** while there is one maintainer (you cannot approve your own pull request) — raise to 1 when a second reviewer exists |
| Require status checks to pass | `agent-app`, `crm-api`, `customer-frontend` (source: GitHub Actions); require branches to be up to date |
| Require linear history | **off** (the plan uses merge commits) |

**Environments** — Settings → Environments:

| Environment | Rules |
| --- | --- |
| `production` | Required reviewers: the owner (leave "Prevent self-review" off while there is one maintainer). Deployment branches: selected → `main` only. |
| `staging` | Deployment branches: selected → `main` (add a named test branch only when needed). |

Secrets (`EC2_HOST`, `EC2_USER`, `EC2_SSH_KEY`, `EC2_KNOWN_HOSTS`) and the variable `LEIA_PORT` are
added per environment only once a server exists. Until then a delivery run stops at its first
deploy step.

**Also check** — Settings → Actions → General: workflow permissions "Read repository contents",
and approval required for workflows from first-time outside contributors (this is a public repository).

## 5. After the promotion — verify

1. `git fetch origin && git rev-parse origin/main^{tree} origin/codex/customer-quality-checks^{tree}`
   prints the same value twice.
2. Actions shows a green "CRM checks" run for the push to `main`.
3. Actions lists "LEIA EC2 delivery" with a *Run workflow* button. Do not run it until the staging
   server and its secrets exist (`docs/EC2_RUNBOOK.md`).
4. A test pull request that changes only a file in `docs/` shows the three checks and can be merged.
5. A direct `git push origin main` is rejected.

## 6. Missing permissions and manual actions

| Item | Why it is manual |
| --- | --- |
| Environments: whether `staging`/`production` exist, their rules and secret names | GitHub API path not available to this session |
| Collaborators: who can push | API path not available |
| Actions settings (workflow permissions, fork approval) | API path not available |
| Branch protection / rulesets | Readable (confirmed: none). Writing was not attempted — changing repository settings was outside this assignment |
| Marking PRs ready, merging | Owner decisions by instruction |
| Comparing the baseline migration with the development database | Needs the development machine |
| Everything on AWS | Out of scope by instruction |

## 7. BTS-DMD position

| Stage | Status |
| --- | --- |
| Build | Verified locally and in CI for the proposed result |
| Test | Verified: 79 backend + 39 app tests, locally (PostgreSQL 18.6 and 16) and in CI |
| Secure | Verified: no secrets in result or history. Pending: `main` protection, environment rules |
| Deploy | Not started. Blocked until steps 1–7 are done and a staging server exists |
| Monitor | Not started |
| Document | This plan, the audit and the runbook |
