#!/bin/sh
# Experimental RG SP paired boot update. Run only after host emulation passes.
set -eu
stage=/tmp/rgsp-gzip-install
backup=/mnt/sdcard/.baseos-boot-gzip-20260917
check_hash() {
    actual=$(sha256sum "$1")
    actual=${actual%% *}
    [ "$actual" = "$2" ] || { echo "Hash mismatch: $1" >&2; exit 1; }
}
check_region() {
    dd if=/dev/mmcblk0 of="$stage/readback.bin" bs=1M count=36 iflag=direct
    check_hash "$stage/readback.bin" "$1"
    rm "$stage/readback.bin"
}
check_boot() {
    dd if=/dev/mmcblk0p4 of="$stage/readback.bin" bs=1M iflag=direct
    check_hash "$stage/readback.bin" "$1"
    rm "$stage/readback.bin"
}
. /etc/baseos-release
[ "$BASEOS_TARGET" = rgsp ]
case "$(cat /proc/cmdline)" in *androidboot.serialno=ac001089c89588720d2*) ;; *) exit 1 ;; esac
[ "$(cat /proc/sys/kernel/random/boot_id)" = 59de6727-9b8b-4fb8-862b-8cbd20754161 ]
[ "$(cat /sys/class/block/mmcblk0p4/start)" = 303104 ]
[ "$(cat /sys/class/block/mmcblk0p4/size)" = 131072 ]
check_hash "$stage/boot-region-original.bin" 2507d7955824cb56dfead33ea916b850983cdb130f9ddd47974002fe00f1e795
check_hash "$stage/boot-partition-original.bin" d42252f30730d35ca7759a651da7260ba482144130e58f05f23ea5e33270a6f5
check_hash "$stage/env-original.bin" 8dd964ef0d525071626040d7cdce6c16925aaa6108d35558a0a5f032592b78a2
check_hash "$stage/boot-package-original.bin" 957929afa2879e18ff758d93eafa98879b21aec70dedcb731d7862a2c20f075c
check_hash "$stage/boot-package-gzip.bin" fa745599c9eb95110f03c6c426e6d08e0317146d51059c6a790e25f604ea0942
check_hash "$stage/boot-gzip.img" 2b9aeb76ace6c781e21aec1edc200cab967b0bf0d162c54588a9c339caf7490c

# Persist verified recovery files first. Do not overwrite a prior backup.
[ ! -e "$backup" ]
mkdir "$backup"
for name in boot-region-original.bin boot-partition-original.bin env-original.bin boot-package-original.bin restore-on-device.sh manifest.json RECOVERY.md; do
    cp "$stage/$name" "$backup/$name"
done
sync
check_hash "$backup/boot-region-original.bin" 2507d7955824cb56dfead33ea916b850983cdb130f9ddd47974002fe00f1e795
check_hash "$backup/boot-partition-original.bin" d42252f30730d35ca7759a651da7260ba482144130e58f05f23ea5e33270a6f5
check_hash "$backup/env-original.bin" 8dd964ef0d525071626040d7cdce6c16925aaa6108d35558a0a5f032592b78a2
check_hash "$backup/boot-package-original.bin" 957929afa2879e18ff758d93eafa98879b21aec70dedcb731d7862a2c20f075c

# Refuse to overwrite changes made since the backup was taken.
check_region 2507d7955824cb56dfead33ea916b850983cdb130f9ddd47974002fe00f1e795
check_boot d42252f30730d35ca7759a651da7260ba482144130e58f05f23ea5e33270a6f5
check_hash /dev/mmcblk0p3 8dd964ef0d525071626040d7cdce6c16925aaa6108d35558a0a5f032592b78a2

# Compensate for command/verification failures while Linux is still running.
# A power loss cannot be trapped; the saved pair also supports offline repair.
writing=0
on_exit() {
    result=$1
    trap - EXIT HUP INT TERM
    if [ "$writing" = 1 ]; then
        echo 'Installation did not complete; restoring original boot pair.' >&2
        rm -f "$stage/readback.bin"
        if ! sh "$backup/restore-on-device.sh"; then
            echo 'RECOVERY FAILED. Keep this Linux session running for repair.' >&2
        fi
    fi
    exit "$result"
}
trap 'on_exit $?' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
writing=1
dd if="$stage/boot-gzip.img" of=/dev/mmcblk0p4 bs=2048 conv=notrunc,fsync
dd if="$stage/boot-package-gzip.bin" of=/dev/mmcblk0 bs=512 seek=32800 conv=notrunc,fsync
sync
check_region 39aecbbe3d7b6f6b90549930d2a25463a020a853d46cec3a8b560d49f220f697
check_boot 83b6e8a62cf1d0479dd8a9132d2656af00c8aa15c27cdd583d29da6509071233
check_hash /dev/mmcblk0p3 8dd964ef0d525071626040d7cdce6c16925aaa6108d35558a0a5f032592b78a2
writing=0
echo 'Gzip boot pair installed; full boot region, full boot partition and environment verified.'
echo 'The device has NOT been rebooted. The next boot is the first hardware boot test.'
