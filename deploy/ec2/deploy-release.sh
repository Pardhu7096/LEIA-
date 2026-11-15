#!/usr/bin/env bash
# Install on EC2 as /opt/leia/<environment>/deploy-release.sh.
# Run as a dedicated deploy user; service restart is allowed via narrowly scoped sudoers.
# Reinstall this copy on the server whenever the file changes in the repository.
set -euo pipefail
SHA="${1:?commit sha required}"
PORT="${2:?port required}"
[[ "$SHA" =~ ^[a-f0-9]{40}$ ]] || exit 2
[[ "$PORT" =~ ^[0-9]+$ ]] || exit 2
# LEIA_DEPLOY_BASE and LEIA_DEPLOY_TMP exist for the automated tests only. The
# delivery workflow never sets them, so a server always uses /opt/leia and /tmp.
BASE="${LEIA_DEPLOY_BASE:-/opt/leia}"
INCOMING="${LEIA_DEPLOY_TMP:-/tmp}"
ENVIRONMENT="$(basename "$(dirname "$(realpath "$0")")")"
[[ "$ENVIRONMENT" == staging || "$ENVIRONMENT" == production ]] || exit 2
ROOT="$BASE/$ENVIRONMENT"
[[ "$(dirname "$(realpath "$0")")" == "$(realpath "$ROOT")" ]] || exit 2
SERVICE="leia-$ENVIRONMENT.service"
ARCHIVE="$INCOMING/leia-$ENVIRONMENT-$SHA.tgz"
RELEASE="$ROOT/releases/$SHA"
test -f "$ARCHIVE"
test -f "$ROOT/shared/.env"
mkdir -p "$ROOT/releases"
# readlink -f also prints a path when "current" does not exist yet, so only an
# existing link counts as a release that can be restored.
PREVIOUS=""
if [[ -L "$ROOT/current" ]]; then
  PREVIOUS="$(readlink -f "$ROOT/current" || true)"
fi
mkdir -p "$RELEASE"
tar -xzf "$ARCHIVE" -C "$RELEASE"
python3 -m venv "$RELEASE/.venv"
"$RELEASE/.venv/bin/pip" install --disable-pip-version-check -r "$RELEASE/requirements-runtime.txt"
# -f keeps a rerun of the same commit (after a fixed .env or migration) working.
ln -sfn "$ROOT/shared/.env" "$RELEASE/.env"
# Refuse to go live with incomplete settings, an unreachable database or pending
# migrations. Nothing has been switched yet, so the running release is untouched.
if ! (cd "$RELEASE" && "$RELEASE/.venv/bin/python" preflight.py --deployed); then
  echo "Preflight failed; the live $ENVIRONMENT release was not changed" >&2
  exit 1
fi
ln -sfn "$RELEASE" "$ROOT/current.next"
mv -Tf "$ROOT/current.next" "$ROOT/current"
healthy() {
  local path="$1"
  for _ in $(seq 1 15); do
    if curl --fail --silent --show-error --max-time 3 "http://127.0.0.1:$PORT$path" >/dev/null; then
      return 0
    fi
    sleep "${LEIA_HEALTH_INTERVAL:-2}"
  done
  return 1
}
rollback() {
  if [[ -n "$PREVIOUS" && -d "$PREVIOUS" && "$PREVIOUS" != "$RELEASE" ]]; then
    ln -sfn "$PREVIOUS" "$ROOT/current.next"
    mv -Tf "$ROOT/current.next" "$ROOT/current"
    # Releases older than the readiness probe only answer /health.
    if sudo systemctl restart "$SERVICE" && healthy /health; then
      echo "Rolled back to $(basename "$PREVIOUS"); it is responding" >&2
    else
      echo "ROLLBACK FAILED: $(basename "$PREVIOUS") is not responding. $ENVIRONMENT is DOWN" >&2
    fi
  else
    echo "No earlier release to roll back to. $ENVIRONMENT is DOWN" >&2
  fi
}
if ! sudo systemctl restart "$SERVICE"; then
  echo "Service restart failed; rolling back" >&2
  rollback
  exit 1
fi
if healthy /health/ready; then
  echo "Release $SHA healthy in $ENVIRONMENT"
  rm -f "$ARCHIVE"
  exit 0
fi
echo "Health check failed; rolling back" >&2
rollback
exit 1
