#!/bin/sh
# Unit tests for overlay/usr/sbin/rename-hostname and its rcS ordering.
#
# The helper writes /data/hostname through a $PERSIST.$$ temp file and traps
# its removal, and regenerates the /run mirrors of /etc/hostname and
# /etc/hosts the same way — so these tests also cover the atomic-replace
# failure paths: the persisted value and the /run copies must stay untouched
# and no temp file may survive.
#
# consume exit codes under test: 1 = nothing to do / could not remove,
# 2 = invalid rename file.
set -eu

HERE="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'chmod -R u+w "$TMP" 2>/dev/null; rm -rf "$TMP"' EXIT HUP INT TERM

mkdir -p "$TMP/data" "$TMP/sdcard" "$TMP/run" "$TMP/bin"

cat > "$TMP/hosts.template" <<'EOF'
127.0.0.1 localhost @HOSTNAME@
::1 localhost
EOF
printf '%s\n' 'nextui' > "$TMP/default-hostname"

# hostname stub: records argv (non-empty only) and validates -F input like the
# real tool would (invalid content exits 1).
cat > "$TMP/bin/hostname" <<'EOF'
#!/bin/sh
if [ -n "$*" ]; then
	printf '%s\n' "$*" >> "$BASEOS_TEST_HOSTNAME_CALLS"
fi
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
	BASEOS_RUN_HOSTNAME="$TMP/run/hostname" \
	BASEOS_HOSTS_FILE="$TMP/run/hosts" \
	BASEOS_HOSTS_TEMPLATE="$TMP/hosts.template" \
	BASEOS_DEFAULT_HOSTNAME_FILE="$TMP/default-hostname" \
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

# Like run_rename, but with an unusable hosts template so write_runtime
# hostname files is forced to fail after hostname -F already succeeded.
run_rename_notemplate() {
	BASEOS_DATA_DIR="$TMP/data" \
	BASEOS_SDCARD_DIR="$TMP/sdcard" \
	BASEOS_HOSTNAME_BIN="$TMP/bin/hostname" \
	BASEOS_HOSTNAME_LOG="$TMP/rename.log" \
	BASEOS_RUN_HOSTNAME="$TMP/run/hostname" \
	BASEOS_HOSTS_FILE="$TMP/run/hosts" \
	BASEOS_HOSTS_TEMPLATE="$TMP/no-such-template" \
	BASEOS_DEFAULT_HOSTNAME_FILE="$TMP/default-hostname" \
	BASEOS_TEST_HOSTNAME_CALLS="$TMP/hostname.calls" \
		sh "$HERE/overlay/usr/sbin/rename-hostname" "$@"
}
no_persist_temps() {
	[ -z "$(ls -A "$TMP/data" 2>/dev/null | grep '^hostname\.' || true)" ]
}
no_run_temps() {
	[ -z "$(ls -A "$TMP/run" 2>/dev/null | grep -E '^(hostname|hosts)\.' || true)" ]
}
run_hostname() { cat "$TMP/run/hostname" 2>/dev/null; }

# --- apply: nothing persisted -> default applied, /run mirrors generated
run_rename apply
[ "$(call_count)" -eq 1 ]
[ "$(last_call)" = "-F $TMP/default-hostname" ]
[ "$(run_hostname)" = "nextui" ]
grep -q "127.0.0.1 localhost nextui" "$TMP/run/hosts"
if grep -q "@HOSTNAME@" "$TMP/run/hosts"; then
	echo "hosts placeholder was not substituted" >&2
	exit 1
fi
no_persist_temps
no_run_temps

# --- apply: an empty persisted file behaves like none
: > "$TMP/data/hostname"
run_rename apply
[ "$(call_count)" -eq 2 ]
[ "$(run_hostname)" = "nextui" ]

# --- apply: a valid persisted file is delegated to `hostname -F` and mirrored
printf '%s\n' 'baseos-dev' > "$TMP/data/hostname"
run_rename apply
[ "$(last_call)" = "-F $TMP/data/hostname" ]
[ "$(cat "$TMP/data/hostname")" = "baseos-dev" ]
[ "$(run_hostname)" = "baseos-dev" ]
grep -q "127.0.0.1 localhost baseos-dev" "$TMP/run/hosts"

# --- apply: an invalid persisted file falls back to the default and is logged
printf '%s\n' 'bad name' > "$TMP/data/hostname"
before="$(call_count)"
run_rename apply
[ "$(call_count)" -eq $((before + 2)) ]
[ "$(last_call)" = "-F $TMP/default-hostname" ]
grep -q "could not apply persisted hostname" "$TMP/rename.log"
[ "$(cat "$TMP/data/hostname")" = "bad name" ]
[ "$(run_hostname)" = "nextui" ]
grep -q "127.0.0.1 localhost nextui" "$TMP/run/hosts"
no_persist_temps
no_run_temps

# --- apply: when the runtime mirrors cannot be written the boot must still
# succeed, the previous mirrors must stay intact, and the failure is logged
printf '%s\n' 'baseos-dev' > "$TMP/data/hostname"
before_run="$(run_hostname)"
before_hosts="$(cat "$TMP/run/hosts")"
run_rename_notemplate apply
[ "$(run_hostname)" = "$before_run" ]
[ "$(cat "$TMP/run/hosts")" = "$before_hosts" ]
grep -q "could not update runtime files" "$TMP/rename.log"
no_persist_temps
no_run_temps

# --- consume: no card file -> no-op (exit 1) and no hostname call
before="$(calls)"
before_run="$(run_hostname)"
rc=0
run_rename consume || rc=$?
[ "$rc" -eq 1 ]
[ "$(calls)" = "$before" ]
[ "$(cat "$TMP/data/hostname")" = "baseos-dev" ]
[ "$(run_hostname)" = "$before_run" ]
no_persist_temps
no_run_temps

# --- consume: a valid file is persisted, applied, mirrored and removed
printf '%s\n' 'rg40xxv-handheld' > "$TMP/sdcard/rename_hostname"
run_rename consume
[ "$(cat "$TMP/data/hostname")" = "rg40xxv-handheld" ]
[ "$(run_hostname)" = "rg40xxv-handheld" ]
grep -q "127.0.0.1 localhost rg40xxv-handheld" "$TMP/run/hosts"
[ ! -e "$TMP/sdcard/rename_hostname" ]
[ "$(last_call)" = "-F $TMP/data/hostname" ]
no_persist_temps
no_run_temps

# --- consume: CRLF and surrounding whitespace are trimmed
printf 'my-device\r\n' > "$TMP/sdcard/rename_hostname"
run_rename consume
[ "$(cat "$TMP/data/hostname")" = "my-device" ]
[ "$(run_hostname)" = "my-device" ]
[ ! -e "$TMP/sdcard/rename_hostname" ]

printf '  spaced-name  \n' > "$TMP/sdcard/rename_hostname"
run_rename consume
[ "$(cat "$TMP/data/hostname")" = "spaced-name" ]
[ "$(run_hostname)" = "spaced-name" ]
[ ! -e "$TMP/sdcard/rename_hostname" ]

printf '\ttabbed-name\t\n' > "$TMP/sdcard/rename_hostname"
run_rename consume
[ "$(cat "$TMP/data/hostname")" = "tabbed-name" ]
[ "$(run_hostname)" = "tabbed-name" ]
[ ! -e "$TMP/sdcard/rename_hostname" ]
no_persist_temps
no_run_temps

# --- consume: the card file overrides an existing persisted name
printf '%s\n' 'old-name' > "$TMP/data/hostname"
printf '%s\n' 'new-name' > "$TMP/sdcard/rename_hostname"
run_rename consume
[ "$(cat "$TMP/data/hostname")" = "new-name" ]
[ "$(run_hostname)" = "new-name" ]
[ ! -e "$TMP/sdcard/rename_hostname" ]
[ "$(last_call)" = "-F $TMP/data/hostname" ]

# --- consume: invalid names are rejected with exit 2, the file is left in
# place and the persisted name and /run mirrors are untouched
for bad in '' 'bad name' 'my device' '-lead' 'trail-' 'a.b' \
	'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'; do
	printf '%s\n' "$bad" > "$TMP/sdcard/rename_hostname"
	before="$(cat "$TMP/data/hostname")"
	before_calls="$(calls)"
	before_run="$(run_hostname)"
	before_hosts="$(cat "$TMP/run/hosts")"
	rc=0
	run_rename consume || rc=$?
	[ "$rc" -eq 2 ]
	[ -e "$TMP/sdcard/rename_hostname" ]
	[ "$(cat "$TMP/data/hostname")" = "$before" ]
	[ "$(calls)" = "$before_calls" ]
	[ "$(run_hostname)" = "$before_run" ]
	[ "$(cat "$TMP/run/hosts")" = "$before_hosts" ]
	no_persist_temps
	no_run_temps
done
rm -f "$TMP/sdcard/rename_hostname"

# --- consume: a file that cannot be removed must not report success (exit 1),
# or rcS would consume and reboot on every boot, forever
printf '%s\n' 'stuck-name' > "$TMP/sdcard/rename_hostname"
chmod 555 "$TMP/sdcard"
rc=0
run_rename consume || rc=$?
chmod 755 "$TMP/sdcard"
[ "$rc" -eq 1 ]
[ -e "$TMP/sdcard/rename_hostname" ]
[ "$(cat "$TMP/data/hostname")" = "stuck-name" ]
[ "$(run_hostname)" = "stuck-name" ]
grep -q "stuck-name" "$TMP/run/hosts"
no_persist_temps
no_run_temps
rm -f "$TMP/sdcard/rename_hostname"

# --- consume: a failed atomic replace of the persisted file leaves the
# previous name and the card file intact, applies nothing, mirrors nothing,
# and leaves no temp file behind
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
	BASEOS_RUN_HOSTNAME="$TMP/run/hostname" \
	BASEOS_HOSTS_FILE="$TMP/run/hosts" \
	BASEOS_HOSTS_TEMPLATE="$TMP/hosts.template" \
	BASEOS_DEFAULT_HOSTNAME_FILE="$TMP/default-hostname" \
	BASEOS_TEST_HOSTNAME_CALLS="$TMP/hostname.calls" \
	BASEOS_TEST_MV_CALLS="$TMP/mv.calls" \
		sh "$HERE/overlay/usr/sbin/rename-hostname" "$@"
}

printf '%s\n' 'kept-name' > "$TMP/data/hostname"
printf '%s\n' 'kept-name' > "$TMP/run/hostname"
printf '%s\n' 'failed-name' > "$TMP/sdcard/rename_hostname"
before="$(call_count)"
before_run="$(run_hostname)"
before_hosts="$(cat "$TMP/run/hosts")"
rc=0
run_rename_failmv consume || rc=$?
[ "$rc" -eq 1 ]
[ "$(cat "$TMP/data/hostname")" = "kept-name" ]
[ -e "$TMP/sdcard/rename_hostname" ]
[ "$(call_count)" -eq "$before" ]
[ "$(run_hostname)" = "$before_run" ]
[ "$(cat "$TMP/run/hosts")" = "$before_hosts" ]
[ -s "$TMP/mv.calls" ]
grep -q "could not persist hostname" "$TMP/rename.log"
no_persist_temps
no_run_temps
rm -f "$TMP/sdcard/rename_hostname"

# --- consume: if applying the persisted file fails, the name is kept for the
# next boot but the card file is NOT consumed and the running mirrors stay
# untouched (the system must never be left half-renamed)
printf '%s\n' 'still-name' > "$TMP/run/hostname"
printf '%s\n' 'apply-me' > "$TMP/sdcard/rename_hostname"
before_hosts="$(cat "$TMP/run/hosts")"
rc=0
run_rename_notemplate consume || rc=$?
[ "$rc" -eq 1 ]
[ "$(cat "$TMP/data/hostname")" = "apply-me" ]
[ -e "$TMP/sdcard/rename_hostname" ]
[ "$(run_hostname)" = "still-name" ]
[ "$(cat "$TMP/run/hosts")" = "$before_hosts" ]
grep -q "could not update runtime files" "$TMP/rename.log"
no_persist_temps
no_run_temps
rm -f "$TMP/sdcard/rename_hostname"

# --- usage
rc=0
run_rename bogus 2>/dev/null || rc=$?
[ "$rc" -eq 2 ]

# --- source layout: PS1 is dynamic, no static hosts/hostname files, baked
# symlinks and templates in place
grep -q '\\h' "$HERE/overlay/etc/profile"
if grep -q 'nextui' "$HERE/overlay/etc/profile"; then
	echo "profile still hardcodes a hostname" >&2
	exit 1
fi
[ ! -e "$HERE/overlay/etc/hosts" ]
[ ! -e "$HERE/overlay/etc/hostname" ]
[ -f "$HERE/overlay/usr/share/baseos/hosts.template" ]
[ -f "$HERE/overlay/usr/share/baseos/default-hostname" ]
grep -q 'ln -sf /run/hosts' "$HERE/build-rootfs.sh"
grep -q 'ln -sf /run/hostname' "$HERE/build-rootfs.sh"
RCS="$HERE/overlay/etc/init.d/rcS"
grep -q 'cp /usr/share/baseos/default-hostname /run/hostname' "$RCS"
grep -q 'hostname -F /etc/hostname' "$RCS"

# --- rcS ordering: apply after machine-id, consume after apply, update apply
# after consume, the rename reboot after the update, and dev last
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