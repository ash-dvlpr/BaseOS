#!/bin/sh
# Exercise absent sounds, TF1 selection, private-mount cleanup and player errors.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT HUP INT TERM
export BASEOS_SOUND_MOUNTS="$TMP/mounts" BASEOS_SOUND_RUN="$TMP/run"
export SOUND_TEST_LOG="$TMP/log" SOUND_TEST_TMP="$TMP"
mkdir -p "$TMP/bin" "$TMP/run" "$TMP/tf1" "$TMP/tf2"
export PATH="$TMP/bin:$PATH"
cat > "$TMP/bin/amixer" <<'SH'
#!/bin/sh
printf 'mixer\n' >> "$SOUND_TEST_LOG"
SH
cat > "$TMP/bin/aplay" <<'SH'
#!/bin/sh
printf 'play %s\n' "$*" >> "$SOUND_TEST_LOG"
exit "${SOUND_TEST_FAIL:-0}"
SH
cat > "$TMP/bin/timeout" <<'SH'
#!/bin/sh
[ "$1" = 30 ] || exit 99
shift
exec "$@"
SH
cat > "$TMP/bin/mount" <<'SH'
#!/bin/sh
printf 'mount %s\n' "$*" >> "$SOUND_TEST_LOG"
[ "${SOUND_TEST_MOUNT_FAIL:-0}" = 0 ] || exit 1
for last; do :; done
[ ! -f "$SOUND_TEST_TMP/private.wav" ] || cp "$SOUND_TEST_TMP/private.wav" "$last/startup_sound.wav"
SH
cat > "$TMP/bin/umount" <<'SH'
#!/bin/sh
printf 'umount %s\n' "$*" >> "$SOUND_TEST_LOG"
SH
chmod +x "$TMP/bin/"*
helper="$ROOT/overlay/usr/sbin/baseos-startup-sound"
printf '/dev/mmcblk0p7 %s vfat rw 0 0\n' "$TMP/tf1" > "$TMP/mounts"
sh "$helper"
[ ! -e "$TMP/log" ]
: > "$TMP/tf1/startup_sound.wav"
sh "$helper"
[ ! -e "$TMP/log" ]
printf sound > "$TMP/tf1/startup_sound.wav"
printf decoy > "$TMP/tf2/startup_sound.wav"
printf '/dev/mmcblk1p1 %s vfat rw 0 0\n' "$TMP/tf2" >> "$TMP/mounts"
sh "$helper"
grep -q "play -q -D default $TMP/tf1/startup_sound.wav" "$TMP/log"
! grep -q -E 'mount|tf2' "$TMP/log"
cp "$TMP/log" "$TMP/first-log"
sh "$helper"
cmp "$TMP/log" "$TMP/first-log"
rmdir "$TMP/run/startup-sound.once"
printf '/dev/mmcblk1p1 %s vfat rw 0 0\n' "$TMP/tf2" > "$TMP/mounts"
: > "$TMP/log"
sh "$helper"
grep -q 'mount -t vfat -o ro /dev/mmcblk0p7' "$TMP/log"
grep -q '^umount ' "$TMP/log"
! grep -q -E '^mixer|^play' "$TMP/log"
printf sound > "$TMP/private.wav"
: > "$TMP/log"
status=0
SOUND_TEST_FAIL=7 sh "$helper" || status=$?
[ "$status" = 7 ]
grep -q "play -q -D default $TMP/run/startup-sound-tf1/startup_sound.wav" "$TMP/log"
grep -q '^umount ' "$TMP/log"
: > "$TMP/log"
SOUND_TEST_MOUNT_FAIL=1 sh "$helper"
! grep -q -E '^mixer|^play|^umount' "$TMP/log"
python3 - "$ROOT" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1])
s = (root / 'overlay/etc/init.d/rcS').read_text()
hook = s[s.index('if [ "$USB_STORAGE_MODE" -eq 0 ] && [ -x /usr/sbin/baseos-startup-sound'):]
hook = hook.split('\nfi', 1)[0]
assert '>/run/startup-sound.log 2>&1 &' in hook
assert s.index('/usr/sbin/h700-speaker-amp-load >/run/') < s.index(hook)
PY
echo 'startup sound tests passed'
