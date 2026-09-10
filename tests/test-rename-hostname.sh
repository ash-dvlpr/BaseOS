#!/bin/sh
# Unit tests for overlay/usr/sbin/rename-hostname and its rcS ordering.
#
# The helper writes /data/hostname through a $PERSIST.$$ temp file and traps
# its removal, so these tests also cover the atomic-replace failure paths:
# the persist target must be untouched and no temp file may survive.
set -eu

HERE="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'chmod -R u+w "$TMP" 2>/dev/null; rm -rf "$TMP"' EXIT HUP INT TERM

mkdir -p "$TMP/data" "$TMP/sdcard" "$TMP/bin"

# hostname stub: records argv, and for -F validates the file like the real
# tool would, so apply's delegation (and its failure path) is observable.
cat > "$TMP/bin/hostname" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >> "$BASEOS_TEST_HOSTNAME_CALLS"
if [ "${1:-}" = "-F" ]; then
	name="$(head -n 1 "$2" 2>/dev/null | tr -d '\r\n' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
	[ -n "$name" ] || exit 1
	case "$name" in
		-*|*-) exit 1 ;;
		*[!A-Za-z0-9-]*) exit 1 ;;
	esac
fi
exit 0
EOF
chmod 755 "$TMP/bin/hostname"

run_rename() {
	BASEOS_DATA_DIR="$TMP/data" \
	BASEOS_SDCARD_DIR="$TMP/sdcard" \
	BASEOS_HOSTNAME_BIN="$TMP/bin/hostname" \
	BASEOS_HOSTNAME_LOG="$TMP/rename.log" \
	BASEOS_TEST_HOSTNAME_CALLS="$TMP/hostname.calls" \
		sh "$HERE/overlay/usr/sbin/rename-hostname" "$@"
}

last_call() { tail -n 1 "$TMP/hostname.calls" 2>/dev/null || true; }
calls() { cat "$TMP/hostname.calls" 2>/dev/null || true; }
call_count() {
	if [ -f "$TMP/hostname.calls" ]; then
		wc -l < "$TMP/hostname.calls" | tr -d ' '
	else
		echo 0
	fi
}
no_persist_temps() {
	[ -z "$(ls -A "$TMP/data" 2>/dev/null | grep '^hostname\.' || true)" ]
}

# --- apply: nothing persisted -> no hostname call
run_rename apply
[ -z "$(calls)" ]
no_persist_temps

# --- apply: an empty persisted file is ignored
: > "$TMP/data/hostname"
run_rename apply
[ -z "$(calls)" ]

# --- apply: a valid persisted file is delegated to `hostname -F`
printf '%s\n' 'baseos-dev' > "$TMP/data/hostname"
run_rename apply
[ "$(last_call)" = "-F $TMP/data/hostname" ]
[ "$(cat "$TMP/data/hostname")" = "baseos-dev" ]

# --- apply: an invalid persisted file still delegates, the failing -F is
# logged, and the helper itself stays successful
printf '%s\n' 'bad name' > "$TMP/data/hostname"
before="$(call_count)"
run_rename apply
[ "$(call_count)" -eq $((before + 1)) ]
[ "$(last_call)" = "-F $TMP/data/hostname" ]
grep -q "could not apply persisted hostname" "$TMP/rename.log"
[ "$(cat "$TMP/data/hostname")" = "bad name" ]
no_persist_temps
printf '%s\n' 'baseos-dev' > "$TMP/data/hostname"

# --- consume: no card file -> no-op and no hostname call
before="$(calls)"
if run_rename consume; then
	echo "consume with no card file reported a rename" >&2
	exit 1
fi
[ "$(calls)" = "$before" ]
[ "$(cat "$TMP/data/hostname")" = "baseos-dev" ]
no_persist_temps

# --- consume: a valid file is persisted, applied and removed
printf '%s\n' 'rg40xxv-handheld' > "$TMP/sdcard/rename_hostname"
run_rename consume
[ "$(cat "$TMP/data/hostname")" = "rg40xxv-handheld" ]
[ ! -e "$TMP/sdcard/rename_hostname" ]
[ "$(last_call)" = "rg40xxv-handheld" ]
no_persist_temps

# --- consume: CRLF and surrounding whitespace are trimmed
printf 'my-device\r\n' > "$TMP/sdcard/rename_hostname"
run_rename consume
[ "$(cat "$TMP/data/hostname")" = "my-device" ]
[ ! -e "$TMP/sdcard/rename_hostname" ]

printf '  spaced-name  \n' > "$TMP/sdcard/rename_hostname"
run_rename consume
[ "$(cat "$TMP/data/hostname")" = "spaced-name" ]
[ ! -e "$TMP/sdcard/rename_hostname" ]

printf '\ttabbed-name\t\n' > "$TMP/sdcard/rename_hostname"
run_rename consume
[ "$(cat "$TMP/data/hostname")" = "tabbed-name" ]
[ ! -e "$TMP/sdcard/rename_hostname" ]
no_persist_temps

# --- consume: the card file overrides an existing persisted name
printf '%s\n' 'old-name' > "$TMP/data/hostname"
printf '%s\n' 'new-name' > "$TMP/sdcard/rename_hostname"
run_rename consume
[ "$(cat "$TMP/data/hostname")" = "new-name" ]
[ ! -e "$TMP/sdcard/rename_hostname" ]
[ "$(last_call)" = "new-name" ]

# --- consume: invalid names are rejected, the file is left in place and the
# persisted name is untouched
for bad in '' 'bad name' 'my device' '-lead' 'trail-' 'a.b' \
	'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'; do
	printf '%s\n' "$bad" > "$TMP/sdcard/rename_hostname"
	before="$(cat "$TMP/data/hostname")"
	before_calls="$(calls)"
	if run_rename consume; then
		echo "invalid hostname '$bad' was consumed" >&2
		exit 1
	fi
	[ -e "$TMP/sdcard/rename_hostname" ]
	[ "$(cat "$TMP/data/hostname")" = "$before" ]
	[ "$(calls)" = "$before_calls" ]
	no_persist_temps
done
rm -f "$TMP/sdcard/rename_hostname"

# --- consume: a file that cannot be removed must not report success, or rcS
# would consume and reboot on every boot, forever
printf '%s\n' 'stuck-name' > "$TMP/sdcard/rename_hostname"
chmod 555 "$TMP/sdcard"
if run_rename consume; then
	chmod 755 "$TMP/sdcard"
	echo "consume reported success without removing the card file" >&2
	exit 1
fi
chmod 755 "$TMP/sdcard"
[ -e "$TMP/sdcard/rename_hostname" ]
[ "$(cat "$TMP/data/hostname")" = "stuck-name" ]
no_persist_temps
rm -f "$TMP/sdcard/rename_hostname"

# --- consume: a failed atomic replace leaves the previous name and the card
# file intact, applies nothing, and leaves no temp file behind
cat > "$TMP/bin/mv" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >> "$BASEOS_TEST_MV_CALLS"
exit 1
EOF
chmod 755 "$TMP/bin/mv"

run_rename_failmv() {
	PATH="$TMP/bin:$PATH" \
	BASEOS_DATA_DIR="$TMP/data" \
	BASEOS_SDCARD_DIR="$TMP/sdcard" \
	BASEOS_HOSTNAME_BIN="$TMP/bin/hostname" \
	BASEOS_HOSTNAME_LOG="$TMP/rename.log" \
	BASEOS_TEST_HOSTNAME_CALLS="$TMP/hostname.calls" \
	BASEOS_TEST_MV_CALLS="$TMP/mv.calls" \
		sh "$HERE/overlay/usr/sbin/rename-hostname" "$@"
}

printf '%s\n' 'kept-name' > "$TMP/data/hostname"
printf '%s\n' 'failed-name' > "$TMP/sdcard/rename_hostname"
before="$(call_count)"
if run_rename_failmv consume; then
	echo "consume reported success although the replace failed" >&2
	exit 1
fi
[ "$(cat "$TMP/data/hostname")" = "kept-name" ]
[ -e "$TMP/sdcard/rename_hostname" ]
[ "$(call_count)" -eq "$before" ]
[ -s "$TMP/mv.calls" ]
grep -q "could not persist hostname" "$TMP/rename.log"
no_persist_temps
rm -f "$TMP/sdcard/rename_hostname"

# --- usage
if run_rename bogus 2>/dev/null; then
	echo "unknown mode was accepted" >&2
	exit 1
fi

# --- rcS ordering: apply after machine-id, consume after apply, update apply
# after consume, the rename reboot after the update, and dev last
RCS="$HERE/overlay/etc/init.d/rcS"
line_of() { grep -n "$1" "$RCS" | tail -n 1 | cut -d: -f1; }
machine="$(line_of 'var/lib/dbus/machine-id')"
apply="$(line_of 'rename-hostname apply')"
consume="$(line_of 'rename-hostname consume')"
update="$(line_of 'baseos-update apply')"
renamed_reboot="$(grep -n '^[[:space:]]*reboot$' "$RCS" | tail -n 1 | cut -d: -f1)"
dev="$(line_of 'init.d/dev')"
[ "$apply" -gt "$machine" ]
[ "$consume" -gt "$apply" ]
[ "$update" -gt "$consume" ]
[ "$renamed_reboot" -gt "$update" ]
[ "$dev" -gt "$renamed_reboot" ]

echo "rename hostname tests passed"
