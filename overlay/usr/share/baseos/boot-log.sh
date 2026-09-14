# Shared by BaseOS boot scripts. Append across boots, without forcing a sync.
# Never touch the frontend card while the USB host owns it.
select_early_boot_log() {
	LOG=/data/baseos-boot.log
	( : >> "$LOG" ) 2>/dev/null || LOG=/tmp/baseos-boot.log
}

select_boot_log() {
	LOG=/tmp/baseos-boot.log
	if [ ! -s /run/usb-storage-device ] \
		&& mountpoint -q /mnt/sdcard 2>/dev/null \
		&& ( : >> /mnt/sdcard/baseos-boot.log ) 2>/dev/null; then
		LOG=/mnt/sdcard/baseos-boot.log
	fi
}

log() {
	{ read -r log_uptime log_idle < /proc/uptime
		printf '%s %s\n' "$log_uptime" "$*" >> "$LOG"; } 2>/dev/null || :
}

# Pre-card update checks and expansion run synchronously in rcS. Preserve their
# diagnostics until rcS (or a later session mount retry) can append to the card.
flush_early_boot_log() {
	[ "$LOG" = /mnt/sdcard/baseos-boot.log ] || return 0
	if [ -f /data/baseos-boot.log ]; then
		{ cat /data/baseos-boot.log >> "$LOG" \
			&& rm -f /data/baseos-boot.log; } 2>/dev/null || :
	fi
}
