#!/bin/sh
# The poweroff/reboot intent contract. BusyBox init runs one ::shutdown: action
# for both, so the only thing that distinguishes them is what these scripts
# leave behind. Runs against a stub busybox: without BASEOS_BUSYBOX this suite
# would reboot the developer's machine.
set -eu

HERE="$(cd "$(dirname "$0")/.." && pwd)"
SBIN="$HERE/overlay/usr/sbin"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT HUP INT TERM

fail() { echo "FAIL: $1" >&2; exit 1; }

cat > "$TMP/busybox" <<'STUB'
#!/bin/sh
printf '%s\n' "$*" > "$BB_LOG"
STUB
chmod 755 "$TMP/busybox"

reset() { rm -f "$TMP/marker"; : > "$TMP/argv"; }

run() {
	script="$1"; shift
	BB_LOG="$TMP/argv" \
	BASEOS_BUSYBOX="$TMP/busybox" \
	BASEOS_SBIN="$SBIN" \
	BASEOS_POWEROFF_MARKER="$TMP/marker" \
		sh "$SBIN/$script" "$@"
}

# --- baseos-poweroff records the intent -----------------------------------
reset
run baseos-poweroff
[ -e "$TMP/marker" ] || fail "baseos-poweroff left no marker"
grep -qx 'poweroff' "$TMP/argv" || fail "did not exec busybox poweroff: $(cat "$TMP/argv")"

# Arguments are forwarded, not swallowed.
reset
run baseos-poweroff -f
grep -qx 'poweroff -f' "$TMP/argv" || fail "argv not forwarded: $(cat "$TMP/argv")"

# --- baseos-reboot clears it ----------------------------------------------
reset; : > "$TMP/marker"
run baseos-reboot
[ -e "$TMP/marker" ] && fail "baseos-reboot left the poweroff marker in place"
grep -qx 'reboot' "$TMP/argv" || fail "did not exec busybox reboot: $(cat "$TMP/argv")"

# --- the shims delegate ----------------------------------------------------
reset
run poweroff
[ -e "$TMP/marker" ] || fail "poweroff shim did not reach baseos-poweroff"
grep -qx 'poweroff' "$TMP/argv" || fail "poweroff shim did not reach busybox"

reset; : > "$TMP/marker"
run reboot
[ -e "$TMP/marker" ] && fail "reboot shim did not reach baseos-reboot"
grep -qx 'reboot' "$TMP/argv" || fail "reboot shim did not reach busybox"

echo "power intent tests passed"
