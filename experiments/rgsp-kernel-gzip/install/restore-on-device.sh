#!/bin/sh
# Restore BOTH original boot components, with the RG SP already running Linux.
set -eu
backup=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
check_hash() {
    actual=$(sha256sum "$1")
    actual=${actual%% *}
    [ "$actual" = "$2" ] || { echo "Hash mismatch: $1" >&2; exit 1; }
}
. /etc/baseos-release
[ "$BASEOS_TARGET" = rgsp ]
case "$(cat /proc/cmdline)" in *androidboot.serialno=ac001089c89588720d2*) ;; *) exit 1 ;; esac
[ "$(cat /sys/class/block/mmcblk0p4/start)" = 303104 ]
[ "$(cat /sys/class/block/mmcblk0p4/size)" = 131072 ]
check_hash "$backup/boot-package-original.bin" 957929afa2879e18ff758d93eafa98879b21aec70dedcb731d7862a2c20f075c
check_hash "$backup/boot-partition-original.bin" d42252f30730d35ca7759a651da7260ba482144130e58f05f23ea5e33270a6f5
dd if="$backup/boot-partition-original.bin" of=/dev/mmcblk0p4 bs=1M conv=notrunc,fsync
dd if="$backup/boot-package-original.bin" of=/dev/mmcblk0 bs=512 seek=32800 conv=notrunc,fsync
sync
dd if=/dev/mmcblk0p4 of=/tmp/rgsp-gzip-restore-readback.bin bs=1M iflag=direct
check_hash /tmp/rgsp-gzip-restore-readback.bin d42252f30730d35ca7759a651da7260ba482144130e58f05f23ea5e33270a6f5
dd if=/dev/mmcblk0 of=/tmp/rgsp-gzip-restore-readback.bin bs=512 skip=32800 count=2560 iflag=direct
check_hash /tmp/rgsp-gzip-restore-readback.bin 957929afa2879e18ff758d93eafa98879b21aec70dedcb731d7862a2c20f075c
rm /tmp/rgsp-gzip-restore-readback.bin
echo 'Original bootloader and kernel restored and verified. No reboot performed.'
