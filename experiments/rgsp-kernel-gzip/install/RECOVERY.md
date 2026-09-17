# Recovery for the tested RG SP

The experimental loader always selects gzip for Android kernels. Restore
**both** original boot components together. The scripts here refer to the
tested RG SP, serial `ac001089c89588720d2`, and its original firmware hashes.

The completed installation changed only:

- 9,842,688 bytes at the beginning of `/dev/mmcblk0p4`.
- The 1,310,720-byte Allwinner TOC1 package at raw-card offset 16,793,600
  (`0x1004000`, LBA 32800). Only one instruction and two checksum fields differ
  inside that package; the other components are identical.

## Original backups

On the host, set `RGSP_GZIP_WORK` to the original ignored
`work/kernel-compression-rgsp` artifact directory. Its `install/` directory has
the extracted `boot-package-original.bin` and these full live backups under
`backup/rgsp-gzip-install/`:

```text
boot-region-original.bin       original first 36 MiB, including GPT/boot0
boot-partition-original.bin    original full 64 MiB kernel partition
env-original.bin               original full 16 MiB environment partition
```

A second copy is on TF1 at `/mnt/sdcard/.baseos-boot-gzip-20260917/`, including
the original package and `restore-on-device.sh`. Enable hidden files when
viewing it from a desktop. Original hashes are in `../evidence/manifest.json`.

## Restore while BaseOS is running

```sh
adb -s ac001089c89588720d2 shell 'sh /mnt/sdcard/.baseos-boot-gzip-20260917/restore-on-device.sh'
```

This checks device identity, partition geometry and backup hashes, restores the
original package and kernel partition, and verifies direct-read hashes. It does
not reboot. Shut down normally and power on to return to the original path.

## Offline SD recovery

If the experiment fails to boot, power off and attach **TF1** to the Mac.
Identify it with `diskutil list`, then unmount the whole card:

```sh
diskutil unmountDisk /dev/diskN
```

Replace `N` with the actual TF1 disk number, never an internal disk. From the
checkout containing these scripts, with `RGSP_GZIP_WORK` pointing at the
retained original host artifacts:

```sh
sudo env RGSP_GZIP_WORK="$RGSP_GZIP_WORK" python3 \
  experiments/rgsp-kernel-gzip/install/restore-card.py /dev/rdiskN
diskutil eject /dev/diskN
```

The helper validates GPT CRCs, names and offsets, the pinned loader identity,
and backup hashes. It restores only the original package and p4, flushes, and
verifies both readbacks. It does not rewrite the full 36 MiB backup or GPT.
Do not disable Python assertions with `-O`.

The offline helper was syntax-checked; a physical card-reader recovery was not
performed. The on-device restore logic was tested against temporary files,
including automatic rollback after an injected verification failure. Actual
power interruption during bootloader writes has not been tested.
