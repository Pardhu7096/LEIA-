# LEIA EC2 delivery — operator setup (not yet deployed)

This repository includes a **manual** GitHub Actions delivery workflow, not an active production deployment. Do not add secrets until the PR has been reviewed and merged. No migration runs as part of delivery.

Step-by-step server setup, monitoring, backup and recovery are in [EC2_RUNBOOK.md](EC2_RUNBOOK.md); the readiness audit is in [EC2_READINESS_AUDIT.md](EC2_READINESS_AUDIT.md). The workflow can only be started once its file is on the default branch (`main`).

## EC2 prerequisites (each environment separately)

- Ubuntu EC2 host with Python 3.12+, outbound access for package installs, and a dedicated non-root deploy account.
- Separate PostgreSQL databases and credentials for staging and production. Never point staging at production.
- Provision `/opt/leia/staging` and `/opt/leia/production` with `releases/`, `shared/.env`, and a copy of `deploy/ec2/deploy-release.sh` installed at the respective environment root.
- Deploy account owns release directories; only allow passwordless `sudo systemctl restart leia-staging.service` or `leia-production.service` as appropriate (no blanket sudo).
- Create systemd units with `WorkingDirectory=/opt/leia/<environment>/current`, `ExecStart=/opt/leia/<environment>/current/.venv/bin/uvicorn main:app --host 127.0.0.1 --port <port>`, `Restart=always`, and a dedicated service user. Configure environment-specific ports.
- The `shared/.env` file must define JWT_SECRET (32+ characters; the API will not start without it), GROQ_API_KEY, DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, `COOKIE_SECURE=true` and LEIA_ALLOWED_ORIGINS (the HTTPS web origins). GOOGLE_PLACES_API_KEY and GOOGLE_ROUTES_API_KEY are optional. Start from `backend/.env.example`. Protect the file with filesystem permissions; never commit it.
- Use Nginx + TLS for public endpoints; keep Uvicorn bound to loopback. GitHub-hosted runners have no fixed address, so SSH cannot be limited to known addresses while this workflow is used from them; see the options in the runbook (key-only SSH with fail2ban, a self-hosted runner, or AWS Systems Manager).
- Run migrations separately after backup, using the correct environment's database owner credentials (`migrate_crm.py --owner <owner-role>` from the unpacked release). `000_core_baseline.sql` creates the account, session and chat tables, so an empty database can be built from this repository. Keep migrations additive so a rollback can run older code on the newer schema.
- For each environment, configure GitHub **Environment** `staging` or `production` with secrets `EC2_HOST`, `EC2_USER`, `EC2_SSH_KEY`, `EC2_KNOWN_HOSTS` and variable `LEIA_PORT`. Capture and verify the host key out-of-band; do not use `ssh-keyscan` blindly in the workflow.
- Restrict deployment branches for both environments. Set **required reviewers for production** in GitHub Environment settings (subject to GitHub plan/repository availability). This cannot be configured by the workflow file itself.
- Production requires manual workflow dispatch from `main` with exact confirmation `DEPLOY-LEIA-PRODUCTION`. Staging can be dispatched manually after CI and review.

## Release behavior

1. GitHub Actions runs Python compile checks and PostgreSQL integration tests.
2. The target environment's approval gate is evaluated.
3. The workflow packages the backend without local `.env` and transfers it to the EC2 host over verified SSH.
4. The deploy script installs dependencies in a per-commit virtualenv and runs `preflight.py --deployed`: settings complete and HTTPS-only, database reachable, every migration shipped with the release applied. If that fails the deployment stops and the live release is not touched.
5. It then atomically switches `current`, restarts the environment-specific systemd unit, and checks `127.0.0.1:<port>/health/ready` (database reachable and migrated), not just that the process answers.
6. On failed restart or readiness check, it switches back to the prior release when one exists, restarts it, reports whether it answers, and exits non-zero. A failed **first deployment** has no prior release to restore and is reported as down.
7. The same commit can be delivered again after fixing settings or applying migrations.
8. Rollback does not revert database migrations or external side effects. Database migrations are intentionally outside this workflow.

## Before enabling live deployments

Confirm exact EC2 hosts, DNS names, service users, SSH restrictions, ports, SSL certificates, separate databases, migration backup plan, monitoring/alerting, and GitHub Environment protection. Confirm the deployment branch has the required customer-frontend CI checks; this workflow currently reruns backend tests but does not itself rerun customer UI tests. Do not treat this as complete end-to-end CD validation until a staging deployment and smoke test succeed.
