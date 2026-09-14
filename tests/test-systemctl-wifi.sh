#!/bin/sh
set -eu
HERE="$(cd "$(dirname "$0")/.." && pwd)"
. "$HERE/tools/docker-platform.sh"
docker run --rm --platform "$BASEOS_DOCKER_PLATFORM_HOST" \
  -v "$HERE:/repo:ro" alpine:3.20 sh -euc '
  apk add -q python3
  python3 /repo/tests/systemctl-wifi.py
'
