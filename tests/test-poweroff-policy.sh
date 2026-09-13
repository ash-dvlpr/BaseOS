#!/bin/sh
# Behavioral tests in an isolated host-native container; no hardware access.
set -eu
HERE="$(cd "$(dirname "$0")/.." && pwd)"
. "$HERE/tools/docker-platform.sh"
docker run --rm --platform "$BASEOS_DOCKER_PLATFORM_HOST" \
  -v "$HERE":/repo:ro alpine:3.20 sh -euc '
  apk add -q build-base linux-headers python3 busybox-static
  gcc -static -O2 -Wall -Wextra -Werror -o /tmp/axp-test /repo/tests/axp-off-test.c
  gcc -static -O2 -D_GNU_SOURCE -Wall -Wextra -Werror -o /tmp/input-test /repo/tests/charger-wait-test.c
  mkdir -p /tmp/axp-fixture/bus/i2c/drivers/axp20x-i2c/5-0034
  echo axp2202 > /tmp/axp-fixture/bus/i2c/drivers/axp20x-i2c/5-0034/name
  /tmp/axp-test > /tmp/axp-test.log 2>&1 || { cat /tmp/axp-test.log; exit 1; }
  echo "axp-off transaction and filesystem tests passed"
  timeout 15 /tmp/input-test
  python3 /repo/tests/poweroff-policy.py
'
