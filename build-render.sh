#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "=== VERIFY TOOLCHAIN ==="
python --version
node --version
npm --version

echo "=== INSTALL BACKEND DEPENDENCIES ==="
python -m pip install -r backend/requirements.txt

echo "=== INSTALL PINNED FFPROBE (Digital Studio) ==="
bash scripts/render/install-ffprobe.sh backend/bin

echo "=== INSTALL AGENT CRM DEPENDENCIES ==="
cd agent-app
npm ci --include=dev

echo "=== BUILD AGENT CRM ==="
: "${EXPO_PUBLIC_API_URL:?API URL must be configured}"

npx --no-install expo export \
    --platform web \
    --output-dir ../backend/agent_web \
    --clear

echo "=== VERIFY GENERATED FILES ==="
test -s ../backend/agent_web/index.html
test -d ../backend/agent_web/_expo

echo "=== RENDER BUILD COMPLETE ==="
