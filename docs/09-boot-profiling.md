# 09 — Boot optimization and profiling

The regular rootfs build removes debug data from `mali_kbase.ko`. The optional
`build-boot.sh` builds a smaller, profiled initramfs from a target's own vendor boot
partition. It does not replace the kernel or DTB, change the prepared prefix, or
automatically install a boot partition into an image or device.

## GPU stripping

`build-rootfs.sh` invokes `tools/strip_gpu_module.py` after extracting the stock
harvest. The helper runs `strip --strip-debug` on a temporary copy and compares
all allocated sections, module metadata/version CRCs, runtime symbols, and runtime
relocations before accepting it. Signed modules are refused. The original
`stock-harvest.tar` is preserved and `/work/gpu-strip-report.json` records the result.

On the RG34XXSP firmware examined on 2026-09-13 the GPU module shrank from
17,593,992 to 723,544 bytes. This is a file-size result, not a boot-time estimate:
the GPU loads concurrently with other startup work, and only repeated device
measurements establish the handoff saving.

## Build an experimental boot partition

```sh
./fetch-prepared.sh rg34xxsp
./build-boot.sh rg34xxsp baseline
./build-boot.sh rg34xxsp minimal
```

Outputs are `work/rg34xxsp/boot-{baseline,minimal}.img`, corresponding JSON reports,
and `initramfs-{baseline,minimal}.cpio`. Each `.img` is a complete partition-4
image, not a whole-card image or `.bosupd` update. The baseline preserves all
vendor ramdisk files and inserts timing markers into `/init`.

For a device running different firmware, first back up its own p4 and build from
that exact backup:

```sh
python3 tools/minimal_initramfs.py \
  --input-boot work/device-backup/boot.img \
  --output-boot work/device-backup/boot-minimal.img \
  --variant minimal \
  --output-cpio work/device-backup/minimal.cpio \
  --report work/device-backup/minimal.json
```

The builder validates Android boot headers v0–v2 and their component SHA1, preserves
the kernel, DTB, other non-ramdisk components and padding, and leaves the partition
tail at its original absolute offset. It refuses unsupported layouts, damaged
inputs and growth into that tail. The new Android component SHA1 is regenerated.

The minimal archive retains the original BusyBox, e2fsck, required libraries,
loader cache and directory aliases, device nodes and recovery tools. It drops
unrelated binaries and data such as ALSA configurations and the file-magic database.
The init script uses shell builtins to parse the command line and 50 ms bounded
storage polling. It keeps the vendor ext4 mount flags and runs `e2fsck -y` before
mounting: results 0/1 proceed, 2/3 reboot, and other failures enter serial recovery.

Loader path aliases are part of the runtime contract. The vendor loader searches
`/lib64` and `/usr/lib64`; keeping just the ELF dependency files under `/lib` and
`/usr/lib` is insufficient. On-device chroot testing caught this during development.

Before installing any p4 candidate, back up and hash the current p4 and runtime
files on the host and card; test the extracted candidate in a fresh device chroot
with `sh -n /init`, BusyBox `usleep`, `e2fsck -V`, and the loader's `--list` checks.
Verify the staged image and complete partition read-back before rebooting. The
shared p4 is outside rootfs A/B rollback, and `.bosupd` updates do not deliver it.

## Collect profiles

The baseline and minimal initramfs variants write early markers to
`/dev/.baseos-boot-profile.tsv`, which survives `switch_root` on devtmpfs. On a
rootfs containing the profiling helper, enable runtime tracing with:

```sh
touch /etc/baseos-boot-profile
```

At the next boot `rcS` imports the early trace into `/run/boot-profile.tsv`, then
records mounts, state restoration, GPU/Wi-Fi work, update checks, frontend setup,
logging and actual handoff. Markers use shell builtins and small tmpfs appends.
Removing the configuration file disables detailed runtime tracing; the three
historical `/run/boot-*` markers remain. Early ramdisk markers are part of the
experimental boot images themselves.

The first frontend marker is retained across session respawns and is recorded
after the handoff log, immediately before `exec`. Compare variants with identical
instrumentation because this includes work omitted by the historical marker.

After each boot has settled, collect a uniquely named sample:

```sh
python3 tools/boot_profile.py collect \
  --serial DEVICE_SERIAL --output-dir work/boots/original-01 \
  --label original --note 'clean warm reboot; cable connected'
python3 tools/boot_profile.py summarize work/boots --events
python3 tools/boot_profile.py summarize work/boots --json > work/boots/summary.json
```

The collector is read-only. It saves raw traces, boot IDs, dmesg, identity,
runtime/boot hashes and frontend/GPU/Wi-Fi/battery snapshots. Summaries reject mixed
firmware under one label, detect duplicate boots, flag ext4 journal recovery, and
show per-phase and total medians/ranges. GPU and Wi-Fi intervals overlap foreground
work and must not be added to the total.

All marker times are kernel uptime, with 10 ms resolution on the vendor kernel.
They exclude bootloader time and frontend first-frame time. Keep cold starts,
clean warm reboots and filesystem-recovery boots separate. Measure at least three
clean boots per variant using the same device, SD card and profiling files.

The regression checks for this workflow are `tests/test-strip-gpu-module.py`,
`tests/test-minimal-initramfs.py` and `tests/test_boot_profile.py`. On-device tests
are still required before claiming a boot improvement or support on another model.

## RG34XXSP measurements, 2026-09-13

Measured on BaseOS 1.1.0 (`b9adb6f`) with the target's vendor 4.9.170 kernel and
the same TF1 card, frontend, USB connection and runtime profiling scripts.
Each row contains three clean warm boots with distinct boot IDs and verified
boot/runtime hashes. All nine samples had a running NextUI process, GPU node and
Wi-Fi interface, with no filesystem-recovery or profile warnings.

| Variant | Handoff median (range), s | GPU load median, s | rcS median, s | Initramfs script median, s |
| --- | ---: | ---: | ---: | ---: |
| Original GPU, profiled vendor initramfs | 3.01 (2.93–3.03) | 0.78 | 0.99 | 0.04 |
| Stripped GPU, profiled vendor initramfs | 2.26 (2.25–2.28) | 0.05 | 0.29 | 0.04 |
| Stripped GPU, minimal initramfs | 2.19 (2.18–2.21) | 0.06 | 0.29 | 0.03 |

The combined median improvement was **0.82 seconds (27%)**. GPU stripping supplied
most of it: although GPU loading is backgrounded, the foreground rcS duration fell
by 0.70 seconds too, consistent with reduced storage contention. Adding the minimal
initramfs lowered the median a further 0.07 seconds. Its compressed archive shrank
from the original 2,606,339 bytes to 1,383,787 bytes; uncompressed cpio shrank from
8,068,096 to 2,923,008 bytes. Filesystem checking remained enabled and took 0.01
seconds in every sample.

These are kernel-uptime handoff measurements, excluding bootloader and NextUI
rendering time. The profiled baseline repacked the original ramdisk to 2,575,633
compressed bytes and retained every file, changing only `/init` to add markers.
An untouched warm boot measured 3.02 seconds. An initial filesystem-recovery boot
and a manually recovered power-cycle boot were kept outside the warm comparison.

The final device passed all 36 checks from `validate-on-device.sh`, run through ADB,
and all 32 new regression tests passed. The minimal candidate also passed on-device
BusyBox/e2fsck/loader chroot checks before flashing. Full p4 read-back matched the
candidate SHA256, and the prepared-input `build-boot.sh` output matched the candidate
built from the device's own backup. Only RG34XXSP has this hardware validation;
the minimal p4 remains an opt-in build output.

Session-local raw samples, comparison JSON and recovery notes are in
`work/boot-profile-rg34xxsp/`. During measurement one baseline reboot left the device
dark and absent from USB; a physical power reset recovered it without a firmware
restore. The cause was not established. Later automated runs stopped the idle
frontend with SIGSTOP before a normal BusyBox reboot to isolate a possible frontend
signal-handler poweroff race. Another healthy boot exceeded the original 55-second
host polling window, which was extended. Host polling delays are not used as
boot-speed measurements.
