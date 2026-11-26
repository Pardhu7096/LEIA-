#!/usr/bin/env bash
# Install a pinned, checksum-verified static ffprobe for Digital Studio walkthrough checks.
# Artifact: eugeneware/ffmpeg-static release b6.1.1 (FFmpeg 6.1.1, linux x86-64, static).
# Both SHA-256 values are the digests GitHub publishes for these release assets; the
# compressed download and the unpacked binary must each match or the build fails.
set -euo pipefail

FFPROBE_URL="https://github.com/eugeneware/ffmpeg-static/releases/download/b6.1.1/ffprobe-linux-x64.gz"
FFPROBE_GZ_SHA256="25d9b6ccb05e3d9de9e04e31e2506d8dd7f9f0418981965ac6df12e8d3afd067"
FFPROBE_SHA256="4f231a1960d83e403d08f7971e271707bec278a9ae18e21b8b5b03186668450d"

dest_dir="${1:?usage: install-ffprobe.sh <destination bin directory>}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

curl -fsSL --retry 3 --proto '=https' --tlsv1.2 -o "$work/ffprobe.gz" "$FFPROBE_URL"
echo "$FFPROBE_GZ_SHA256  $work/ffprobe.gz" | sha256sum -c -
gunzip -c "$work/ffprobe.gz" > "$work/ffprobe"
echo "$FFPROBE_SHA256  $work/ffprobe" | sha256sum -c -

mkdir -p "$dest_dir"
install -m 0755 "$work/ffprobe" "$dest_dir/ffprobe"
"$dest_dir/ffprobe" -hide_banner -version | head -n 1
# The validation flags used by studio_inputs.validate_video must be supported.
"$dest_dir/ffprobe" -hide_banner -h demuxer=mov | grep -q enable_drefs
echo "ffprobe installed at $dest_dir/ffprobe"
