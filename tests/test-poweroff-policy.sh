#!/bin/sh
# Static policy checks for the power-off path. Each invariant here fails
# silently when broken — a shutdown that loops forever, a reboot that becomes a
# shutdown, a build that overwrites busybox — so pin the direction statically
# rather than waiting to spot it on a device.
#
# Comments discuss the very constructs being checked, so strip them before
# judging code.
set -eu

HERE="$(cd "$(dirname "$0")/.." && pwd)"
OVERLAY="$HERE/overlay"

fail() { echo "$1" >&2; exit 1; }
strip() { sed 's/#.*//' "$1"; }
lineof() { strip "$1" | grep -n -- "$2" | head -1 | cut -d: -f1; }

# cstrip removes C comments (/* ... */, including multi-line, and //) while
# printing exactly one output line per input line — clineof's line numbers
# feed the -lt ordering checks below, so collapsing or dropping a line would
# silently corrupt that ordering. A wholly-commented line prints empty rather
# than vanishing. strip()'s '#.*' rule is a shell-comment rule and does not
# touch /* */, so axp-off.c needs this separate stripper: without it, deleting
# a real guarded construct while leaving its name inside a /* */ comment would
# keep the checks below passing.
cstrip() {
	awk '
	{
		line = $0
		out = ""
		i = 1
		n = length(line)
		while (i <= n) {
			if (incomment) {
				idx = index(substr(line, i), "*/")
				if (idx == 0) {
					i = n + 1
				} else {
					i = i + idx + 1
					incomment = 0
				}
			} else if (substr(line, i, 2) == "/*") {
				incomment = 1
				i += 2
			} else if (substr(line, i, 2) == "//") {
				i = n + 1
			} else {
				out = out substr(line, i, 1)
				i += 1
			}
		}
		print out
	}
	' "$1"
}
clineof() { cstrip "$1" | grep -n -- "$2" | head -1 | cut -d: -f1; }

# --- 1. busybox is reached by explicit path, never the bare applet ---------
# /usr/sbin/poweroff and /usr/sbin/reboot are now our shims and /sbin is a
# symlink to usr/sbin, so a bare `poweroff` inside these scripts resolves back
# to the shim and loops until the battery is flat.
for s in baseos-poweroff baseos-reboot; do
	found="$(strip "$OVERLAY/usr/sbin/$s" | grep -c 'exec "\$BUSYBOX"' || true)"
	[ "$found" -ge 1 ] || fail "$s must exec busybox through \$BUSYBOX"
done

# The scan for a bare applet name covers the shims as well, even though they
# hold no busybox call of their own. They are the two files where it would loop
# *immediately*: /usr/sbin/poweroff running `poweroff` is a call to itself.
for s in baseos-poweroff baseos-reboot poweroff reboot; do
	bare="$(strip "$OVERLAY/usr/sbin/$s" \
		| grep -nE '^[[:space:]]*(exec[[:space:]]+)?(poweroff|reboot)([[:space:]]|$)' || true)"
	if [ -n "$bare" ]; then
		echo "$s invokes the bare applet name, which resolves back to the shim:" >&2
		printf '%s\n' "$bare" >&2
		exit 1
	fi
done

# --- 2. rcK writes the PMIC only behind the marker guard ------------------
RCK="$OVERLAY/etc/init.d/rcK"
guard="$(lineof "$RCK" 'poweroff-requested')"
write="$(lineof "$RCK" 'axp-off --now')"
[ -n "$guard" ] || fail "rcK no longer tests for the poweroff marker"
[ -n "$write" ] || fail "rcK no longer cuts power at the PMIC"
[ "$guard" -lt "$write" ] || fail "rcK writes the PMIC before testing the marker: every reboot becomes a poweroff"

# --- 3. no `umount -a` between the guard and the write --------------------
# `umount -a` takes devtmpfs with it, and the i2c character device has to
# outlive that step or the write fails with ENOENT and the device restarts.
between="$(strip "$RCK" | sed -n "${guard},${write}p" | grep -n 'umount -a' || true)"
if [ -n "$between" ]; then
	echo "rcK unmounts everything before the PMIC write; /dev/i2c-* will be gone:" >&2
	printf '%s\n' "$between" >&2
	exit 1
fi

# --- 4. axp-off never writes unless armed ---------------------------------
# axp-off.c has no shell-style '#' comments, only C-style /* */ ones, so this
# uses cstrip/clineof rather than strip/lineof — see the comment on cstrip.
SRC="$HERE/src/axp-off.c"
armgate="$(clineof "$SRC" 'if (!arm)')"
regwrite="$(clineof "$SRC" 'REG, val')"
[ -n "$armgate" ] || fail "axp-off lost its --now gate"
[ -n "$regwrite" ] || fail "axp-off no longer writes REG27H"
[ "$armgate" -lt "$regwrite" ] || fail "axp-off writes REG27H before checking --now"

# --- 5. bit 1 is masked on every write ------------------------------------
# Setting RESTART restarts the system instead of powering it off, which is the
# exact failure this tool exists to avoid. cstrip again, for the same reason
# as check 4.
cstrip "$SRC" | grep -q '& ~RESTART' || fail "axp-off no longer masks the RESTART bit"

# --- 6. build-rootfs.sh drops both busybox symlinks before the copy -------
BR="$HERE/build-rootfs.sh"
rmln="$(lineof "$BR" 'rm -f "\$R/usr/sbin/poweroff"')"
copy="$(lineof "$BR" 'cp -R /overlay/\.')"
[ -n "$rmln" ] || fail "build-rootfs.sh does not drop the busybox poweroff symlink"
[ -n "$copy" ] || fail "build-rootfs.sh no longer copies the overlay"
[ "$rmln" -lt "$copy" ] || fail "the overlay copy runs before the symlinks are dropped: it will overwrite busybox"
strip "$BR" | sed -n "${rmln}p" | grep -q 'usr/sbin/reboot' \
	|| fail "build-rootfs.sh drops poweroff but not reboot"

# --- 7. the static binary is exempt from the ELF closure check ------------
strip "$BR" | grep -q '\*/axp-off' \
	|| fail "axp-off is not exempt from the closure check; the build will report UNRESOLVABLE"

# --- 8. the charger branch runs before the update trial counter -----------
# A charger boot never reaches a frontend session. Counted as a failed trial
# boot it would roll back a perfectly good update on a device that is merely
# charged often.
RCS="$OVERLAY/etc/init.d/rcS"
charger="$(lineof "$RCS" 'bootreason=charger')"
check="$(lineof "$RCS" 'baseos-update boot-check')"
[ -n "$charger" ] || fail "rcS no longer acts on a charger boot"
[ -n "$check" ] || fail "rcS no longer runs the update trial check"
[ "$charger" -lt "$check" ] || fail "the charger branch runs after boot-check: charged devices will roll back good updates"

# --- 9. the charger branch closes /data before it cuts power --------------
# sync alone leaves the filesystem marked in use; only umount (or a read-only
# remount) guarantees the journal is not replayed on every subsequent mount.
# This is the lesson diagnostics/results/2026-08-18-charger-boot-and-poweroff.md
# calls out by name ("sync is not an unmount") — pin it so it cannot regress.
data_close="$(lineof "$RCS" 'umount /data')"
pmic_write="$(lineof "$RCS" 'axp-off --now')"
[ -n "$data_close" ] || fail "rcS no longer closes /data before powering off"
[ -n "$pmic_write" ] || fail "rcS no longer writes the PMIC to end a charger boot"
[ "$data_close" -lt "$pmic_write" ] || fail "rcS cuts power before closing /data: the journal will replay on every subsequent mount"

# --- 10. the loop stamp is recorded before the power off is committed -----
# The stamp is the whole memory of the loop guard. Written after the PMIC cut it
# would never land on the boot that mattered, and the next charger boot would
# have nothing to compare against — an unbounded loop with no frontend and no
# adb to recover from.
stamp_write="$(lineof "$RCS" '> /data/charger-off-stamp')"
[ -n "$stamp_write" ] || fail "rcS no longer records the charger-off loop stamp"
[ "$stamp_write" -lt "$pmic_write" ] || fail "rcS cuts power before recording the loop stamp: the loop guard would have no memory"

# --- 11. the settle wait sits between the PMIC write and the remount ------
# axp-off returns as soon as the i2c write completes, milliseconds before the
# rails drop. Remounting /data rw inside that gap puts a writable filesystem
# back underneath a cut that is still landing — the exact race the unmount
# above exists to avoid.
settle="$(lineof "$RCS" 'sleep 5')"
remount="$(lineof "$RCS" 'remount,rw /data')"
[ -n "$settle" ] || fail "rcS no longer waits for the PMIC cut to land"
[ -n "$remount" ] || fail "rcS no longer puts /data back when the cut does not take"
[ "$pmic_write" -lt "$settle" ] || fail "rcS waits before it writes the PMIC, which only delays the boot"
[ "$settle" -lt "$remount" ] || fail "rcS remounts /data rw without waiting for the cut to land"

# --- 12. rcK closes its filesystems before it cuts power ------------------
# BusyBox init runs the shutdown action before its SIGTERM sweep, so the
# frontend is still live on the card here. These two unmounts are what stand
# between the rails dropping and a mounted, writable vfat/ext4 — degraded to
# read-only by -r when the mount is busy, which it normally is. -r only wins
# against a busy cwd, though: a file still open for writing refuses the
# remount too and the mount stays rw, which is part of why quiescing the
# frontend is a separate change. Delete these unmounts and every other check
# in this file stays green.
card_close="$(lineof "$RCK" 'umount -r /mnt/sdcard')"
rck_data_close="$(lineof "$RCK" 'umount -r /data')"
[ -n "$card_close" ] || fail "rcK no longer closes the card with umount -r before cutting power"
[ -n "$rck_data_close" ] || fail "rcK no longer closes /data with umount -r before cutting power"
[ "$card_close" -lt "$write" ] || fail "rcK cuts power before closing the card: the rails drop on a live vfat mount"
[ "$rck_data_close" -lt "$write" ] || fail "rcK cuts power before closing /data: the rails drop on a live ext4 mount"

# --- 13. the charger branch refuses to fire while MENU is held ------------
# MENU-held is how a powered-off device reaches the documented USB
# mass-storage recovery (connect the cable, then hold MENU from power-on).
# Without this precondition the charger branch would power the device off
# before rcS ever looks at the button, breaking that path.
menu_held="$(lineof "$RCS" 'boot-menu-held')"
[ -n "$menu_held" ] || fail "rcS no longer tests boot-menu-held before ending a charger boot"
[ "$charger" -lt "$menu_held" ] || fail "rcS tests boot-menu-held outside the charger branch"
[ "$menu_held" -lt "$pmic_write" ] || fail "rcS writes the PMIC before checking whether MENU is held"

# --- 14. both callers gate on the unarmed probe before their first unmount -
# axp-off run with no --now writes nothing and returns 0 only after it found
# the driver's client, matched the address and the part name it publishes,
# opened /dev/i2c-N and read REG27H — the guard that stands between a board
# this was never measured on and an unmounted filesystem. Losing it here still
# leaves the write itself behind --now, but every unmeasured board would
# unmount its filesystems for nothing.
probe_rcs="$(lineof "$RCS" 'axp-off >/dev/null 2>&1')"
probe_rck="$(lineof "$RCK" 'axp-off >/dev/null 2>&1')"
[ -n "$probe_rcs" ] || fail "rcS no longer probes axp-off before ending a charger boot"
[ -n "$probe_rck" ] || fail "rcK no longer probes axp-off before cutting power"
[ "$probe_rcs" -lt "$data_close" ] || fail "rcS unmounts /data before probing axp-off: an unmeasured board would lose its filesystem for nothing"
[ "$probe_rck" -lt "$card_close" ] || fail "rcK unmounts the card before probing axp-off: an unmeasured board would lose its filesystem for nothing"

echo "poweroff policy tests passed"
