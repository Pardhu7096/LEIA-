#!/usr/bin/env bash
# Install on EC2 as /opt/leia/<environment>/deploy-release.sh.
# Run as a dedicated deploy user; service restart is allowed via narrowly scoped sudoers.
set -euo pipefail
SHA="${1:?commit sha required}"
PORT="${2:?port required}"
[[ "$SHA" =~ ^[a-f0-9]{40}$ ]] || exit 2
[[ "$PORT" =~ ^[0-9]+$ ]] || exit 2
ENVIRONMENT="$(basename "$(dirname "$(realpath "$0")")")"
[[ "$ENVIRONMENT" == staging || "$ENVIRONMENT" == production ]] || exit 2
ROOT="/opt/leia/$ENVIRONMENT"
SERVICE="leia-$ENVIRONMENT.service"
ARCHIVE="/tmp/leia-$ENVIRONMENT-$SHA.tgz"
RELEASE="$ROOT/releases/$SHA"
test -f "$ARCHIVE"
test -f "$ROOT/shared/.env"
mkdir -p "$ROOT/releases"
PREVIOUS="$(readlink -f "$ROOT/current" || true)"
mkdir -p "$RELEASE"
tar -xzf "$ARCHIVE" -C "$RELEASE"
python3 -m venv "$RELEASE/.venv"
"$RELEASE/.venv/bin/pip" install --disable-pip-version-check -r "$RELEASE/requirements-runtime.txt"
ln -s "$ROOT/shared/.env" "$RELEASE/.env"
ln -sfn "$RELEASE" "$ROOT/current.next"
mv -Tf "$ROOT/current.next" "$ROOT/current"
rollback() {
  if [[ -n "$PREVIOUS" && -d "$PREVIOUS" ]]; then
    ln -sfn "$PREVIOUS" "$ROOT/current.next"
    mv -Tf "$ROOT/current.next" "$ROOT/current"
    sudo systemctl restart "$SERVICE" || true
  fi
}
if ! sudo systemctl restart "$SERVICE"; then
  rollback
  exit 1
fi
for attempt in $(seq 1 15); do
  if curl --fail --silent --show-error --max-time 3 "http://127.0.0.1:$PORT/health" >/dev/null; then
    echo "Release $SHA healthy in $ENVIRONMENT"
    rm -f "$ARCHIVE"
    exit 0
  fi
  sleep 2
done
echo "Health check failed; rolling back" >&2
rollback
exit 1
