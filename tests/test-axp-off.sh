#!/bin/sh
# Offline test for axp-off's PMIC discovery. Everything runs against synthetic
# sysfs trees in a host-native container, so this needs no device, no i2c bus
# and no privileges. --now is never passed, so no invocation here can write.
set -eu

HERE="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=tools/docker-platform.sh
. "$HERE/tools/docker-platform.sh"

# Host-native: the discovery path is plain C with no device dependency, so the
# host-arch build exercises exactly the same logic as the aarch64 one.
docker run --rm --platform "$BASEOS_DOCKER_PLATFORM_HOST" \
  -v "$HERE/src":/src:ro alpine:3.20 sh -euc '
  apk add -q build-base linux-headers
  gcc -static -O2 -Wall -Wextra -Werror -o /usr/local/bin/axp-off /src/axp-off.c

  fail() { echo "FAIL: $1" >&2; exit 1; }

  # A driver directory always carries these; none of them is a client. Every
  # argument becomes a client, created in the order given, so a caller can set
  # up more than one binding. An argument may also pin the part name the kernel
  # would publish at <client>/name, written as "5-0034=axp717"; a bare argument
  # gets the one part that has been measured, and a trailing "=" with nothing
  # after it writes no name file at all — a kernel that does not publish the
  # attribute.
  mkdrv() {
    rm -rf /tmp/sys
    mkdir -p "/tmp/sys/bus/i2c/drivers/axp20x-i2c"
    : > /tmp/sys/bus/i2c/drivers/axp20x-i2c/bind
    : > /tmp/sys/bus/i2c/drivers/axp20x-i2c/unbind
    : > /tmp/sys/bus/i2c/drivers/axp20x-i2c/uevent
    : > /tmp/sys/bus/i2c/drivers/axp20x-i2c/module
    for c in "$@"; do
      [ -z "$c" ] || {
        case "$c" in
          *=*) part="${c#*=}"; c="${c%%=*}" ;;
          *)   part=axp2202 ;;
        esac
        mkdir -p "/tmp/sys/bus/i2c/drivers/axp20x-i2c/$c"
        [ -z "$part" ] \
          || printf "%s\n" "$part" > "/tmp/sys/bus/i2c/drivers/axp20x-i2c/$c/name"
      }
    done
  }
  # Captures both output and exit status. Written as an if/else rather than
  # "out=$(...) || true" because under set -e a failing command substitution
  # used as an assignment aborts the script unless its failure is caught by
  # a conditional; "|| true" would catch it but also clobber $? before rc=$?
  # can read it. The if/else lets set -e see the failure as handled while
  # leaving $? from the substitution intact in the else branch.
  run() {
    if out="$(BASEOS_SYS_ROOT=/tmp/sys axp-off 2>&1)"; then
      rc=0
    else
      rc=$?
    fi
  }
  assert_rc() { [ "$rc" -eq "$1" ] || fail "expected exit $1, got $rc: $out"; }

  # --- the RG SP layout -------------------------------------------------
  mkdrv 5-0034
  run
  assert_rc 1
  echo "$out" | grep -q "/dev/i2c-5"        || fail "bus 5 not discovered: $out"
  echo "$out" | grep -q "addr 0x34"         || fail "addr 0x34 not discovered: $out"
  echo "$out" | grep -q "axp20x-i2c/5-0034" || fail "provenance not printed: $out"
  echo "$out" | grep -q "axp2202"           || fail "part name not printed: $out"
  # Discovery succeeded; the container just has no /dev/i2c-5. The failure
  # must name opening the bus, not discovery, or a later regression here
  # would be indistinguishable from a discovery bug.
  echo "$out" | grep -q "open /dev/i2c-5"   || fail "failure did not name opening the bus: $out"

  # --- a different bus: the whole point of this change ------------------
  mkdrv 3-0034
  run
  assert_rc 1
  echo "$out" | grep -q "/dev/i2c-3"      || fail "bus is still hardcoded: $out"
  echo "$out" | grep -q "open /dev/i2c-3" || fail "failure did not name opening the bus: $out"

  # --- no driver bound: fail closed, never name a bus --------------------
  rm -rf /tmp/sys; mkdir -p /tmp/sys
  run
  assert_rc 1
  echo "$out" | grep -q "/dev/i2c" && fail "named a bus with no driver bound: $out"
  echo "$out" | grep -q "no axp20x-i2c client" || fail "unclear discovery failure: $out"

  # --- driver present but nothing bound to it ---------------------------
  mkdrv ""
  run
  assert_rc 1
  echo "$out" | grep -q "/dev/i2c" && fail "named a bus with no client: $out"

  # --- wrong address: refuse, and do not open anything -------------------
  mkdrv 5-0036
  run
  assert_rc 1
  echo "$out" | grep -q "/dev/i2c" && fail "opened a bus at an unexpected address: $out"
  echo "$out" | grep -q "0x36"     || fail "did not report the unexpected address: $out"
  echo "$out" | grep -qi "refus"   || fail "did not say it refused: $out"

  # --- the right address, the wrong part ---------------------------------
  # 0x34 says where the chip is, never what it is: every AXP in the family sits
  # there. Only the name separates the one part this has been measured on from
  # another whose REG 0x27 need not mean soft poweroff at all.
  mkdrv 5-0034=axp717
  run
  assert_rc 1
  echo "$out" | grep -q "/dev/i2c" && fail "opened a bus for an unmeasured part: $out"
  echo "$out" | grep -q "axp717"   || fail "did not report the part it found: $out"
  echo "$out" | grep -qi "refus"   || fail "did not say it refused: $out"

  # --- no name published: fail closed ------------------------------------
  # A kernel that does not export the attribute leaves the part unidentified,
  # and unidentified silicon has to take the kernel poweroff path rather than a
  # register write made on the strength of an address alone.
  mkdrv 5-0034=
  run
  assert_rc 1
  echo "$out" | grep -q "/dev/i2c" && fail "opened a bus for a client with no name: $out"
  echo "$out" | grep -qi "refus"   || fail "did not say it refused: $out"
  echo "$out" | grep -q "name"     || fail "refusal did not name the missing name: $out"

  # --- two clients bound: the one at 0x34 wins ---------------------------
  # Stopping at the first entry made the address check order-dependent, because
  # readdir order is a property of the filesystem rather than of the bus. A
  # fixture cannot pin that order, so it asserts the outcome under both creation
  # orders; where entries come back in creation order, as they do here, the
  # first case is the one that catches a first-entry-wins regression.
  for order in "3-0036 5-0034" "5-0034 3-0036"; do
    mkdrv $order
    run
    assert_rc 1
    echo "$out" | grep -q "addr 0x34"       || fail "client at 0x34 not preferred ($order): $out"
    echo "$out" | grep -q "/dev/i2c-5"      || fail "bus of the 0x34 client not chosen ($order): $out"
    echo "$out" | grep -q "5-0034"          || fail "provenance names the wrong client ($order): $out"
    echo "$out" | grep -q "open /dev/i2c-5" || fail "failure did not name opening the bus ($order): $out"
  done

  # Two clients and neither at 0x34: still refuse, and still name one of them so
  # the refusal says something a human can act on.
  mkdrv 3-0036 5-0035
  run
  assert_rc 1
  echo "$out" | grep -q "/dev/i2c" && fail "opened a bus with no client at 0x34: $out"
  echo "$out" | grep -qi "refus"   || fail "did not say it refused: $out"

  # --- junk entries are not clients --------------------------------------
  mkdrv 5-0034x
  run
  assert_rc 1
  echo "$out" | grep -q "/dev/i2c" && fail "accepted a malformed client name: $out"

  echo "axp-off discovery tests passed"
'
