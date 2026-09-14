#!/bin/sh
# Exercise the real BusyBox session with card, GPU and frontend stand-ins.
# All absolute runtime paths belong to the disposable container; no device,
# privileged mounts, frontend download or changes to another repo are needed.
set -eu

HERE="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=tools/docker-platform.sh
. "$HERE/tools/docker-platform.sh"

docker run --rm -i --platform "$BASEOS_DOCKER_PLATFORM_HOST" \
  -v "$HERE/overlay/usr/sbin/nextui-session":/usr/sbin/nextui-session:ro \
  -v "$HERE/overlay/usr/share/baseos/boot-log.sh":/usr/share/baseos/boot-log.sh:ro \
  alpine:3.20 sh -eu <<'TEST'
mkdir -p /usr/local/bin /usr/sbin /mnt/sdcard /data
export PATH=/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
unset SLOT_ROOT
BOOT_LOG=/mnt/sdcard/baseos-boot.log
RAM_LOG=/tmp/baseos-boot.log
EXPANSION_LOG=/data/baseos-boot.log

cat > /usr/local/bin/mountpoint <<'EOF'
#!/bin/sh
echo mountpoint >> /tmp/mountpoint.log
[ ! -e /tmp/no-card ]
EOF
cat > /usr/local/bin/mount <<'EOF'
#!/bin/sh
echo mount >> /tmp/mount.log
exit 1
EOF
cat > /usr/local/bin/sleep <<'EOF'
#!/bin/sh
echo "$1" >> /tmp/sleep.log
if [ "$1" = 3600 ]; then
	# Stop the intentional USB-storage idle loop without waiting an hour.
	kill -TERM "$PPID"
elif [ "$1" = 0.1 ] && [ -f /tmp/gpu-ready-after ]; then
	if [ "$(wc -l < /tmp/sleep.log)" -ge "$(cat /tmp/gpu-ready-after)" ]; then
		: > /dev/mali0
	fi
fi
exit 0
EOF
cat > /usr/sbin/baseos-update <<'EOF'
#!/bin/sh
echo "$*" >> /tmp/update.log
EOF
cat > /usr/bin/baseos-splash <<'EOF'
#!/bin/sh
echo "$*" >> /tmp/splash.log
EOF

# An opaque, non-executable file stands in for the ELF. The loader stand-in
# checks that the session supplies the binary path, root and working directory;
# passing this file to /bin/sh or executing it directly cannot pass the test.
cat > /lib/ld-linux-aarch64.so.1 <<'EOF'
#!/bin/sh
[ "$#" -eq 1 ] && [ "$1" = /mnt/sdcard/System/slot ] || exit 90
[ "$(cat "$1")" = SLOT-ELF-FIXTURE ] || exit 91
[ -s /run/boot-frontend-exec ] || exit 92
[ "$(cat /tmp/update.log)" = confirm ] || exit 93
printf 'slot\nroot=%s\ncwd=%s\n' "$SLOT_ROOT" "$PWD" > /tmp/frontend.log
echo 'slot stdout'
echo 'slot stderr' >&2
if [ -f /tmp/slot-exit ]; then exit "$(cat /tmp/slot-exit)"; fi
EOF
cat > /tmp/nextui-launch <<'EOF'
#!/bin/sh
[ -s /run/boot-frontend-exec ] || exit 92
[ "$(cat /tmp/update.log)" = confirm ] || exit 93
printf 'nextui\nroot=%s\n' "${SLOT_ROOT-unset}" > /tmp/frontend.log
EOF
cat > /tmp/installer <<'EOF'
#!/bin/sh
echo install >> /tmp/install.log
echo 'installer stdout'
echo 'installer stderr' >&2
if [ -f /tmp/installer-exit ]; then exit "$(cat /tmp/installer-exit)"; fi
mkdir -p /mnt/sdcard/.system/h700/paks/MinUI.pak
cp /tmp/nextui-launch /mnt/sdcard/.system/h700/paks/MinUI.pak/launch.sh
EOF
cat > /usr/local/bin/unzip <<'EOF'
#!/bin/sh
[ "$#" -eq 5 ] && [ "$1" = -o ] && [ "$2" = /mnt/sdcard/MinUI.zip ] \
	&& [ "$3" = '.tmp_update/*' ] && [ "$4" = -d ] && [ "$5" = /mnt/sdcard ] \
	|| exit 94
echo unzip >> /tmp/unzip.log
echo 'unzip stdout'
echo 'unzip stderr' >&2
mkdir -p /mnt/sdcard/.tmp_update
cp /tmp/installer /mnt/sdcard/.tmp_update/h700.sh
EOF
chmod 755 /usr/local/bin/* /usr/sbin/baseos-update /usr/bin/baseos-splash \
  /lib/ld-linux-aarch64.so.1
chmod 644 /tmp/nextui-launch /tmp/installer

reset() {
	rm -rf /mnt/sdcard
	mkdir -p /mnt/sdcard
	rm -f /tmp/*.log /tmp/no-card /tmp/gpu-ready-after /tmp/slot-exit /tmp/installer-exit \
		"$EXPANSION_LOG" \
		/run/boot-frontend-exec /run/usb-storage-device \
		/run/usb-storage-ready /run/usb-storage-failed
	: > /dev/mali0
}
slot_card() {
	mkdir -p /mnt/sdcard/System
	echo SLOT-ELF-FIXTURE > /mnt/sdcard/System/slot
	chmod 644 /mnt/sdcard/System/slot
}
nextui_card() {
	mkdir -p /mnt/sdcard/.system/h700/paks/MinUI.pak
	cp /tmp/nextui-launch /mnt/sdcard/.system/h700/paks/MinUI.pak/launch.sh
}
run_session() {
	status=0
	timeout 5 sh /usr/sbin/nextui-session > /tmp/console.log 2>&1 || status=$?
}
no_legacy_logs() {
	[ ! -e /tmp/nextui-session.log ] \
		&& [ ! -e /tmp/expand.log ] && [ ! -e /mnt/sdcard/baseos-session.log ]
}
card_snapshot() {
	find /mnt/sdcard -type f -exec sha256sum {} \; | sort
}
check_handoff_log() {
	boot_time=$(cat /run/boot-frontend-exec)
	grep -Fqx "$boot_time BaseOS boot time: $boot_time s (kernel start to frontend handoff)" "$1"
	[ "$(grep -c ' BaseOS boot time:' "$1")" -eq "$2" ]
}

reset
slot_card
echo 'previous boot diagnostics' > "$BOOT_LOG"
echo 'early expansion diagnostics' > "$EXPANSION_LOG"
run_session
[ "$status" -eq 0 ]
[ "$(cat /tmp/frontend.log)" = "$(printf 'slot\nroot=/mnt/sdcard\ncwd=/mnt/sdcard\n')" ]
[ ! -x /mnt/sdcard/System/slot ]
[ "$(head -n 2 "$BOOT_LOG")" = "$(printf 'previous boot diagnostics\nearly expansion diagnostics\n')" ]
[ "$(cat /tmp/slot.log)" = "$(printf 'slot stdout\nslot stderr\n')" ]
! grep -qE '^slot (stdout|stderr)$' "$BOOT_LOG"
check_handoff_log "$BOOT_LOG" 1
[ ! -e "$EXPANSION_LOG" ] && [ ! -e "$RAM_LOG" ]
[ ! -e /tmp/splash.log ] && [ ! -e /tmp/sleep.log ]
[ "$(find /mnt/sdcard -type f | wc -l)" -eq 2 ]
no_legacy_logs
echo 'ok: Slot handoff uses the shared boot log while frontend output stays in RAM'

# A respawn must preserve the first handoff timestamp, append BaseOS diagnostics,
# replace the raw Slot output, and propagate the frontend exit status to init.
first_handoff=$(cat /run/boot-frontend-exec)
echo 'earlier session diagnostics' >> "$BOOT_LOG"
echo stale >> /tmp/slot.log
echo 42 > /tmp/slot-exit
rm /tmp/update.log
run_session
[ "$status" -eq 42 ]
[ "$(cat /run/boot-frontend-exec)" = "$first_handoff" ]
check_handoff_log "$BOOT_LOG" 1
grep -qx 'earlier session diagnostics' "$BOOT_LOG"
[ "$(cat /tmp/slot.log)" = "$(printf 'slot stdout\nslot stderr\n')" ]
[ "$(grep -c ' exec /mnt/sdcard/System/slot ' "$BOOT_LOG")" -eq 2 ]
! grep -qE '^slot (stdout|stderr)$' "$BOOT_LOG"
[ "$(grep -c '^early expansion diagnostics$' "$BOOT_LOG")" -eq 1 ]
no_legacy_logs
echo 'ok: respawn preserves one boot timing record and refreshes Slot diagnostics'

# Simulate a later boot by clearing volatile state while retaining the card.
rm /run/boot-frontend-exec /tmp/update.log /tmp/slot-exit
run_session
[ "$status" -eq 0 ]
check_handoff_log "$BOOT_LOG" 2
grep -qx 'previous boot diagnostics' "$BOOT_LOG"
[ "$(grep -c ' exec /mnt/sdcard/System/slot ' "$BOOT_LOG")" -eq 3 ]
echo 'ok: a later boot appends its timing without discarding prior boot logs'

# The bind-mounted helper is read-only even for container root. An existing
# log symlink to it makes append fail without relying on chmod permissions.
reset
slot_card
readonly_hash=$(sha256sum /usr/share/baseos/boot-log.sh)
ln -s /usr/share/baseos/boot-log.sh "$BOOT_LOG"
echo 'unflushed expansion diagnostics' > "$EXPANSION_LOG"
run_session
[ "$status" -eq 0 ]
[ "$(sha256sum /usr/share/baseos/boot-log.sh)" = "$readonly_hash" ]
[ "$(readlink "$BOOT_LOG")" = /usr/share/baseos/boot-log.sh ]
[ "$(cat "$EXPANSION_LOG")" = 'unflushed expansion diagnostics' ]
[ "$(cat /tmp/slot.log)" = "$(printf 'slot stdout\nslot stderr\n')" ]
check_handoff_log "$RAM_LOG" 1
no_legacy_logs
echo 'ok: an unwritable card log falls back to RAM without losing the expansion spool'

reset
nextui_card
run_session
[ "$status" -eq 0 ]
[ "$(cat /tmp/frontend.log)" = "$(printf 'nextui\nroot=unset\n')" ]
[ ! -e /tmp/slot.log ] && [ ! -e /tmp/splash.log ]
check_handoff_log "$BOOT_LOG" 1
slot_card
rm /tmp/update.log
run_session
[ "$status" -eq 0 ]
[ "$(cat /tmp/frontend.log)" = "$(printf 'nextui\nroot=unset\n')" ]
[ ! -e /tmp/slot.log ]
check_handoff_log "$BOOT_LOG" 1
echo 'ok: NextUI shell launch and priority when both frontends are present'

reset
slot_card
: > /mnt/sdcard/MinUI.zip
run_session
[ "$status" -eq 0 ]
grep -qx nextui /tmp/frontend.log
grep -qx unzip /tmp/unzip.log
grep -qx install /tmp/install.log
grep -qx -- '--important 85 INSTALLING FRONTEND' /tmp/splash.log
grep -qx 'unzip stdout' "$BOOT_LOG"
grep -qx 'unzip stderr' "$BOOT_LOG"
grep -qx 'installer stdout' "$BOOT_LOG"
grep -qx 'installer stderr' "$BOOT_LOG"
check_handoff_log "$BOOT_LOG" 1
no_legacy_logs
echo 'ok: NextUI archive installation takes priority over an existing Slot'

reset
nextui_card
slot_card
mkdir -p /mnt/sdcard/.tmp_update
cp /tmp/installer /mnt/sdcard/.tmp_update/h700.sh
: > /mnt/sdcard/nextui.update.pakz
run_session
[ "$status" -eq 0 ]
grep -qx nextui /tmp/frontend.log
grep -qx install /tmp/install.log
[ ! -e /tmp/unzip.log ]
grep -qx -- '--important 85 UPDATING FRONTEND' /tmp/splash.log
grep -qx 'installer stderr' "$BOOT_LOG"
echo 'ok: NextUI pakz updates still run before handoff'

reset
: > /mnt/sdcard/MinUI.zip
echo 7 > /tmp/installer-exit
run_session
[ "$status" -eq 1 ]
grep -qx 'installer stdout' "$BOOT_LOG"
grep -qx 'installer stderr' "$BOOT_LOG"
grep -q ' installer exited 7$' "$BOOT_LOG"
[ "$(tail -n 1 /tmp/splash.log)" = '--important -1 INSTALL FAILED' ]
[ ! -e /run/boot-frontend-exec ] && [ ! -e /tmp/frontend.log ]
no_legacy_logs
echo 'ok: a failing installer keeps stderr and exit status in the shared log'

reset
slot_card
rm /dev/mali0
echo 3 > /tmp/gpu-ready-after
run_session
[ "$status" -eq 0 ] && [ -s /tmp/frontend.log ]
[ "$(grep -c '^0.1$' /tmp/sleep.log)" -eq 3 ]
reset
slot_card
rm /dev/mali0
run_session
[ "$status" -eq 0 ] && [ -s /tmp/frontend.log ]
[ "$(grep -c '^0.1$' /tmp/sleep.log)" -eq 30 ]
echo 'ok: Slot shares the bounded GPU readiness wait'

reset
# Directories at either entry point must not be mistaken for a frontend.
mkdir -p /mnt/sdcard/System/slot /mnt/sdcard/.system/h700/paks/MinUI.pak/launch.sh
run_session
[ "$status" -eq 1 ]
grep -qx -- '--important -1 ADD FRONTEND TO SD CARD' /tmp/splash.log
[ ! -e /run/boot-frontend-exec ] && [ ! -e /tmp/frontend.log ]
echo 'ok: missing frontend retains the add-frontend prompt'

reset
: > /mnt/sdcard/broken.pakz
run_session
[ "$status" -eq 1 ]
[ "$(tail -n 1 /tmp/splash.log)" = '--important -1 INSTALL FAILED' ]
[ ! -e /run/boot-frontend-exec ] && [ ! -e /tmp/frontend.log ]
echo 'ok: an incomplete NextUI installation retains its failure prompt'

reset
slot_card
nextui_card
: > /tmp/no-card
echo 'pending expansion diagnostics' > "$EXPANSION_LOG"
before_card=$(card_snapshot)
run_session
[ "$status" -eq 1 ]
grep -qx -- '--important -1 INSERT SD CARD' /tmp/splash.log
[ ! -e /run/boot-frontend-exec ] && [ ! -e /tmp/frontend.log ]
[ "$(grep -c '^1$' /tmp/sleep.log)" -eq 15 ]
[ "$(card_snapshot)" = "$before_card" ]
[ ! -e "$BOOT_LOG" ]
[ "$(cat "$EXPANSION_LOG")" = 'pending expansion diagnostics' ]
grep -q ' no card mounted after 15s$' "$RAM_LOG"
grep -q ' no card mounted — waiting for insertion$' "$RAM_LOG"
no_legacy_logs
echo 'ok: a missing card uses RAM logging and leaves card files and expansion diagnostics untouched'

for state in ready failed; do
	reset
	slot_card
	nextui_card
	: > /mnt/sdcard/MinUI.zip
	echo /dev/mmcblk1 > /run/usb-storage-device
	: > "/run/usb-storage-$state"
	echo 'pending expansion diagnostics' > "$EXPANSION_LOG"
	before_card=$(card_snapshot)
	run_session
	[ "$status" -eq 143 ]
	[ ! -e /tmp/frontend.log ] && [ ! -e /tmp/install.log ]
	[ ! -e /tmp/unzip.log ] && [ ! -e /run/boot-frontend-exec ]
	[ ! -e /tmp/mountpoint.log ] && [ ! -e /tmp/mount.log ]
	[ "$(card_snapshot)" = "$before_card" ]
	[ ! -e "$BOOT_LOG" ]
	[ "$(cat "$EXPANSION_LOG")" = 'pending expansion diagnostics' ]
	grep -q ' USB storage mode — frontend intentionally stopped$' "$RAM_LOG"
	no_legacy_logs
	if [ "$state" = ready ]; then
		grep -qx -- '--important -1 USB STORAGE: EJECT BEFORE RESTART' /tmp/splash.log
	else
		grep -qx -- '--important -1 USB STORAGE FAILED: POWER OFF' /tmp/splash.log
		grep -q ' USB storage gadget did not become ready$' "$RAM_LOG"
	fi
done
echo 'ok: USB storage mode blocks card access, installers and both frontends'
echo 'frontend session tests passed'
TEST
