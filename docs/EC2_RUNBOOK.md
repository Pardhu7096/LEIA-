# LEIA on AWS EC2 — operator runbook

How to prepare a server, deliver a release, watch it and recover it. Everything in
angle brackets or capitals (`ENV`, `API_DOMAIN`, `PORT`) is a placeholder: no real
host names, addresses, keys or passwords belong in this repository.

**Status.** Nothing in this runbook has been run on AWS. The deploy script, the
settings and database checks, the Nginx file and the systemd unit were exercised
locally (see `docs/EC2_READINESS_AUDIT.md`). The first staging deployment is the
real test; treat any surprise there as a finding and update this file.

`ENV` is `staging` or `production`. Do every step once per environment.

## 1. What runs where

| Piece | Where | Notes |
| --- | --- | --- |
| FastAPI backend (Uvicorn) | EC2, `127.0.0.1:PORT`, systemd unit `leia-ENV.service` | never exposed directly |
| Nginx + TLS | same EC2, ports 80/443 | the only public entry point |
| PostgreSQL 16 | same EC2 (or RDS) | also stores photos and panoramas |
| Releases | `/opt/leia/ENV/releases/<commit>` , live one at `/opt/leia/ENV/current` | |
| Settings and secrets | `/opt/leia/ENV/shared/.env` | never in git, never in GitHub |
| Customer and agent apps | built separately with `EXPO_PUBLIC_API_URL` | not deployed by this pipeline |

## 2. AWS prerequisites (owner)

- Ubuntu Server 24.04 LTS instance (Python 3.12 and PostgreSQL 16, the versions CI
  tests). At least 2 GB RAM: a single photo upload can need ~100 MB while it is
  re-encoded. Encrypted gp3 volume sized for the database, which holds all media.
- **Separate instances for staging and production** (recommended). The migrations
  grant table access to a role named `leia_app`, and PostgreSQL roles are shared by
  every database in a cluster, so two environments in one cluster would share one
  API login. If one instance must host both, give each its own PostgreSQL cluster
  (`pg_createcluster 16 ENV -p <other-port>`).
- Elastic IP per instance, and a DNS `A` record `API_DOMAIN` pointing at it. Without
  an Elastic IP the address changes on stop/start and breaks DNS, `EC2_HOST` and the
  pinned host key entry.
- Security group: inbound 443 and 80 from anywhere; **never** 5432. Port 22 is the
  hard one — GitHub-hosted runners have no fixed address, so the delivery workflow
  cannot work with SSH limited to a few known addresses. Choose one and write the
  choice down: (a) open 22 to the internet with key-only login for the deploy user
  and `fail2ban`; (b) a self-hosted runner inside the VPC and 22 closed to the
  internet; (c) replace SSH with AWS Systems Manager. (b) and (c) are changes to the
  pipeline, not just settings.
- Instance role: none is needed by the application. Add one only for backups to S3
  (`s3:PutObject` on one bucket prefix).
- Billing alarm, and spending limits on the Groq and Google Maps accounts.

## 3. Server setup (Ubuntu)

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-pip postgresql nginx certbot python3-certbot-nginx curl fail2ban unattended-upgrades

# Deploy account (SSH login, runs the deploy script) and service account (runs the API).
sudo adduser --disabled-password --gecos "" leia-deploy-ENV
sudo adduser --system --group --no-create-home leia-ENV
sudo usermod -aG leia-deploy-ENV leia-ENV        # service account may read releases and .env

sudo install -d -o root -g root -m 755 /opt/leia
sudo install -d -o leia-deploy-ENV -g leia-deploy-ENV -m 750 /opt/leia/ENV /opt/leia/ENV/releases /opt/leia/ENV/shared
sudo install -o root -g root -m 755 deploy/ec2/deploy-release.sh /opt/leia/ENV/deploy-release.sh
```

Reinstall `deploy-release.sh` on the server whenever it changes in the repository;
the workflow runs the server's copy, not the one in the commit being deployed.

SSH: in `/etc/ssh/sshd_config.d/` set `PasswordAuthentication no` and
`PermitRootLogin no`. Create one key pair per environment on your own machine
(`ssh-keygen -t ed25519 -N "" -f leia-ENV-deploy`) and put the public half in
`/home/leia-deploy-ENV/.ssh/authorized_keys` prefixed with `restrict ` (no
forwarding, no terminal). The private half goes only into the GitHub environment
secret `EC2_SSH_KEY`; delete the local copy afterwards.

Restart permission: install `deploy/ec2/sudoers-leia.example` as
`/etc/sudoers.d/leia-deploy` with the real account names (`visudo -cf` first). The
deploy account must not have any other sudo right.

## 4. PostgreSQL

```sql
-- sudo -u postgres psql
CREATE ROLE leia_owner_ENV LOGIN PASSWORD '<owner-password>';   -- runs migrations only
CREATE ROLE leia_app LOGIN PASSWORD '<app-password>';           -- used by the API
CREATE DATABASE leia_ENV OWNER leia_owner_ENV;
REVOKE ALL ON DATABASE leia_ENV FROM PUBLIC;
GRANT CONNECT ON DATABASE leia_ENV TO leia_app;
```

- Keep `listen_addresses = 'localhost'` and `scram-sha-256` in `pg_hba.conf`.
- The API role must be called `leia_app`: the migrations grant it exactly the table
  rights the API needs and nothing more. Different passwords per environment.
- Database on another host (RDS): set `DB_SSLMODE=require` (or `verify-full` with
  the RDS CA bundle) in `.env`, and allow 5432 only from the instance's security group.
- The API opens a new connection per request. That is fine at launch scale; raise
  `max_connections` or add PgBouncer before heavy traffic.

## 5. Settings file

Copy `backend/.env.example` to `/opt/leia/ENV/shared/.env`, fill it in, then:

```bash
sudo chown leia-deploy-ENV:leia-deploy-ENV /opt/leia/ENV/shared/.env && sudo chmod 640 /opt/leia/ENV/shared/.env
```

| Name | Required | Purpose |
| --- | --- | --- |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | yes | API database login (`leia_app`) |
| `DB_SSLMODE` | remote database only | `require`, `verify-ca` or `verify-full`; preflight refuses a non-local `DB_HOST` without one |
| `JWT_SECRET` | yes | 32+ characters, unique per environment. Changing it signs everyone out and resets request-limit counters |
| `GROQ_API_KEY` | yes | AI assistant |
| `COOKIE_SECURE` | yes, `true` | session cookies over HTTPS only |
| `LEIA_ALLOWED_ORIGINS` | yes | comma-separated HTTPS origins of the customer web app and agent CRM |
| `GOOGLE_PLACES_API_KEY`, `GOOGLE_ROUTES_API_KEY` | optional | nearby places and walking estimates; restrict each key in Google Cloud |

Every deployment runs `python preflight.py --deployed` against this file and the
database before anything is switched. It prints the names of wrong or missing
settings, never their values.

## 6. Service

Install `deploy/ec2/leia.service.example` as `/etc/systemd/system/leia-ENV.service`
with `ENVIRONMENT`, `PORT` and `SERVICE_USER` (`leia-ENV`) replaced, then
`sudo systemctl daemon-reload && sudo systemctl enable leia-ENV.service`. Do not
start it yet: there is no release until the first deployment.

```bash
systemctl status leia-ENV.service          # state and last lines of output
sudo systemctl restart leia-ENV.service    # the only command the deploy account may run with sudo
```

## 7. Nginx and HTTPS

```bash
sudo certbot certonly --nginx -d API_DOMAIN          # needs DNS and port 80 already working
sudo install -m 644 deploy/ec2/nginx-leia.conf.example /etc/nginx/sites-available/leia-ENV   # then replace ENVIRONMENT, API_DOMAIN, PORT
sudo ln -s /etc/nginx/sites-available/leia-ENV /etc/nginx/sites-enabled/ && sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
sudo certbot renew --dry-run                         # confirms automatic renewal
```

Three lines in that file matter to the application and must survive edits:
`client_max_body_size 13m` (panoramas are up to 12 MB; the 1 MB default rejects
them), `proxy_set_header X-Forwarded-For $remote_addr` (login and registration
limits are per client address; appending instead of overwriting lets a caller pick
their own address), and the block that hides `/docs` and `/openapi.json`.

## 8. GitHub

The delivery workflow can only be started once `.github/workflows/leia-ec2-delivery.yml`
is on the default branch (`main`).

Create environments `staging` and `production` (Settings → Environments), each with:

| Kind | Name | Value |
| --- | --- | --- |
| Secret | `EC2_HOST` | `API_DOMAIN` or the Elastic IP |
| Secret | `EC2_USER` | `leia-deploy-ENV` |
| Secret | `EC2_SSH_KEY` | private half of that environment's deploy key |
| Secret | `EC2_KNOWN_HOSTS` | the server's host key line, see below |
| Variable | `LEIA_PORT` | the `PORT` used in the systemd unit and Nginx |

`EC2_KNOWN_HOSTS`: run `ssh-keyscan -t ed25519 <EC2_HOST value>` from your own
machine and compare its fingerprint (`ssh-keygen -lf <file>`) with the one printed
in the instance's first-boot system log in the EC2 console before saving it. The
host part must match `EC2_HOST` exactly.

Protection rules (cannot be set from the workflow file):

- `production`: required reviewers, deployment branches limited to `main`.
- `staging`: deployment branches limited to the branches you intend to test.
- `main`: branch protection requiring the `agent-app`, `crm-api` and
  `customer-frontend` checks; no direct pushes.

No application secret (database, JWT, Groq, Google) is ever stored in GitHub.

## 9. First deployment

1. Sections 2–8 done; `.env` in place; service enabled but not started.
2. Actions → *LEIA EC2 delivery* → Run workflow → `staging`.
3. Expected first result: **failure at preflight** with "Missing tables … run
   migrate_crm.py". The release is unpacked but nothing is live. This is by design:
   the pipeline never changes the database.
4. On the server, build the schema as the database owner (prompts for its password):
   ```bash
   cd /opt/leia/ENV/releases/<commit> && .venv/bin/python migrate_crm.py --owner leia_owner_ENV
   ```
5. Run the workflow again for the same commit. It should end with
   `Release <commit> healthy in ENV`.
6. Create the first staff accounts: register through the app, then promote in the database
   (there is deliberately no public way to become staff):
   ```sql
   UPDATE users SET role = 'admin' WHERE email = '<address>';   -- or 'agent'
   ```
7. Smoke test over HTTPS from outside AWS: `/health/ready`, register, sign in, agent
   sign-in, create and publish a listing with photos and a panorama, customer search,
   AI chat, nearby places. Record the result; only then consider production.

## 10. Routine deployment and migrations

- Start the workflow from the branch to deliver. Production additionally needs `main`,
  the typed confirmation `DEPLOY-LEIA-PRODUCTION` and reviewer approval.
- Order inside a run: backend tests on PostgreSQL → environment approval → upload →
  install into a fresh virtual environment → **preflight** (settings, database
  reachable, every shipped migration applied) → switch `current` → restart →
  `/health/ready`, retried for roughly 30–75 s → on failure switch back, restart, exit non-zero.
- A release that ships a new migration stops at preflight with "Pending migrations".
  Take a backup (section 13), run `migrate_crm.py` from that release's folder as in
  9.4, then run the workflow again.
- **Migrations must be additive** (new tables, new nullable or defaulted columns).
  The running release keeps serving while the migration is applied, and a rollback
  puts older code on the newer schema. Remove or rename things only in a later
  release, once no deployable release uses the old shape.
- Old releases are not removed automatically. Occasionally keep the newest five:
  `cd /opt/leia/ENV/releases && ls -1dt */ | tail -n +6` lists the candidates —
  check `readlink -f ../current` is not among them before deleting.

## 11. Health monitoring

| Check | Meaning |
| --- | --- |
| `GET /health` | the process answers |
| `GET /health/ready` | the database is reachable and has every migration this release ships; `503` otherwise |

- Point an external uptime monitor (a free tier is enough) at
  `https://API_DOMAIN/health/ready` every 1–5 minutes with alerts to your phone/email.
- Enable the EC2 status-check alarm, and watch disk space: the database grows with
  every photo. `df -h /` and `sudo -u postgres psql -c "SELECT pg_size_pretty(pg_database_size('leia_ENV'))"`.
- `systemctl is-active leia-ENV.service nginx postgresql` should print `active` three times.

## 12. Logs

```bash
journalctl -u leia-ENV.service -f                      # live API output
journalctl -u leia-ENV.service --since "1 hour ago" -p warning
sudo tail -f /var/log/nginx/error.log /var/log/nginx/access.log
sudo journalctl -u ssh --since today                   # who connected
```

API logs contain request paths, status codes and client addresses; they do not
contain passwords, tokens or message text. Treat client addresses as personal data:
cap retention (`SystemMaxUse=500M` in `/etc/systemd/journald.conf`, default
logrotate for Nginx) and do not copy logs off the server casually.

## 13. Backup and restore

The database is the only state: accounts, listings, chats and every image.

```bash
# Backup (run daily from cron as a user with ~/.pgpass for the owner role)
pg_dump -Fc -h 127.0.0.1 -U leia_owner_ENV -d leia_ENV -f /var/backups/leia/leia_ENV_$(date +%F_%H%M).dump

# Restore into a scratch database first, check it, then decide
sudo -u postgres createdb -O leia_owner_ENV leia_ENV_restore
pg_restore --no-owner -h 127.0.0.1 -U leia_owner_ENV -d leia_ENV_restore /var/backups/leia/<file>.dump
```

- Copy dumps off the instance (S3 bucket with versioning; the instance role needs
  only `s3:PutObject`). Keep 7 daily and 4 weekly. Add EBS snapshots if budget allows.
- Always back up immediately before running a migration.
- A backup that has never been restored is not a backup: restore one into
  `leia_ENV_restore` before go-live and time it.
- Also keep `/opt/leia/ENV/shared/.env` in a password manager; it is not in any backup.

## 14. Rollback and recovery

| Situation | What happens / what to do |
| --- | --- |
| New release fails its health check | Automatic: previous release restored and restarted, workflow fails. Read the run log and `journalctl`. |
| Preflight fails | Nothing was switched. Fix `.env` or apply migrations, run the workflow again. |
| Release is live but misbehaving | Manual rollback below, or re-run the workflow from the last good commit. |
| "ROLLBACK FAILED" or "is DOWN" in the run log | The service is down. Manual rollback below; check `journalctl` and the database. |
| First deployment fails after the switch | No earlier release exists; the service stays down until a good release is delivered. |
| A migration damaged data | Stop the service, restore the pre-migration backup (section 13), deploy the previous commit. Code rollback alone never undoes a migration. |
| Instance lost | New instance from sections 2–8, restore the latest dump, deliver the last good commit, repoint the Elastic IP. |
| Secret exposed | Rotate it at its source, update `.env`, restart. Rotating `JWT_SECRET` signs everyone out. |

Manual rollback (as the deploy account):

```bash
ls -1t /opt/leia/ENV/releases                                   # pick the previous commit
ln -sfn /opt/leia/ENV/releases/<previous-commit> /opt/leia/ENV/current.next
mv -Tf /opt/leia/ENV/current.next /opt/leia/ENV/current
sudo systemctl restart leia-ENV.service
curl -fsS http://127.0.0.1:PORT/health/ready
```

## 15. App builds

Both Expo apps read the API address at build time:

```bash
EXPO_PUBLIC_API_URL=https://API_DOMAIN npx expo export --platform web     # or an EAS build profile env
```

Release builds refuse to start without it, and refuse plain `http://` to a remote
host. For the web builds, add each site's origin to `LEIA_ALLOWED_ORIGINS`, and host
them under the same registrable domain as the API (for example `app.example.com`
and `api.example.com`); otherwise browsers will not send the session cookie and
users are signed out every 15 minutes.
