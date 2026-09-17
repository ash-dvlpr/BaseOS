# RG SP boot-stage timing

See [measured results](RESULTS.md) for the gzip/original comparison and final
device state.

This follow-up stays on `codex/rgsp-kernel-gzip`, separate from the v1.3.0 main
branch. It adds an RG SP-only rootfs helper and first-handoff log record. It
does not change the release kernel/bootloader defaults or `.bosupd` format.

The device initially still had the gzip kernel and patched U-Boot, despite its
rootfs having been updated to BaseOS 1.3.0. The user then requested restoration
of the uncompressed kernel for a timing comparison. Because the experimental
loader forces gzip, the original U-Boot package and kernel must be restored
together; the existing hash-pinned restore script performs that operation.

## What the stages measure

The current handoff marker uses the first field of `/proc/uptime`: Linux
`CLOCK_BOOTTIME`, truncated to centiseconds. This is a clock maintained by Linux,
not the full value of the hardware counter. On this device both Linux's initial
relative-clock origin and its unadjusted `CLOCK_MONOTONIC_RAW` origin start
after firmware/kernel loading.

The 24 MHz `CNTVCT_EL0` counter is readable from ordinary AArch64 userspace:

```text
pre-kernel = counter_seconds - CLOCK_MONOTONIC_RAW
post-kernel = CLOCK_MONOTONIC_RAW at frontend handoff
combined = counter_seconds at frontend handoff
```

The helper brackets the raw-clock read with two counter reads, takes their
midpoint and rejects brackets wider than 100 microseconds. The first-handoff
record is retained across frontend respawns. The legacy marker/log retain
`CLOCK_BOOTTIME` and the existing 2.50-second budget. New stages are rounded to
milliseconds; independently rounded parts can differ from the total by 1 ms.

Kernel loading and U-Boot decompression fall in the pre-kernel interval.
Remaining kernel initialization, initramfs unpacking and rootfs startup fall
after Linux's clock origin. The split is not the first kernel instruction.
Counter zero has not been calibrated against the power LED. The last endpoint
is frontend handoff, not the first UI frame.

## Validation method

- Five gzip boots established repeatability, with sixteen paired samples per
  boot: eight soon after ADB returned and eight roughly nine seconds later.
- Additional gzip and restored-original boots used the same installed logger
  to record stages at handoff; independent later probes check its pre-kernel
  value against the raw-counter offset. Three gzip boots and five original
  boots are retained: two original repeats were added after the first three
  showed a 240 ms range. No original-kernel sample was discarded.
- Every restart uses the inspected NextUI `reboot_next` binary. Rootfs and
  userdata are synced and remounted read-only first. No BusyBox reboot command
  is issued. Every measured boot must have a new boot ID and running NextUI.
- The accidental SELECT/ES boot (`2a584c4c-d641-48f5-a6bc-8050f9d1d54f`)
  is excluded. The sampler stopped when NextUI did not return; it did not
  substitute the other frontend's timing.
- The card had a FAT dirty-volume warning before testing. NextUI's open card
  logs prevent a read-only card remount; its normal helper lazy-unmounts the
  card. This warning persisted across samples. No ext4 journal recovery,
  kernel oops or panic was found in accepted boots. No filesystem repair was
  performed as part of the timing comparison.
- Warm restarts with USB attached are the validated condition. Cold power-on,
  suspend/resume, absolute oscillator accuracy and other models remain untested.

The existing long-running boot illustrates why the raw clock matters: NTP had
shifted `counter - CLOCK_BOOTTIME` by about 326 ms relative to the raw-clock
origin. Within a boot, `counter - CLOCK_MONOTONIC_RAW` remains stable at
microsecond scale. A late uptime subtraction would therefore misattribute
time synchronization to pre-kernel work.

## Code and evidence

- [`src/boot-clock.c`](../../src/boot-clock.c): static userspace reader;
  handles unavailable counter instructions and unexpected frequency by failing
  back to the original uptime-only logging path.
- [`frontend-session`](../../overlay/usr/sbin/frontend-session): retains the
  old marker and adds the pre/post/combined line once per boot.
- [`test-frontend-session.sh`](../../tests/test-frontend-session.sh): normal
  frontend behavior, counter logging, respawn retention and failure fallback.
- `counter-probe-minimal.c`: independent freestanding probe compatible with the
  vendor Linux 4.9.170 kernel. The original toolchain's libc-linked probe could
  not start on this older kernel.
- `measure-reboots.py`, `compare-pair.py`: hardware measurement harnesses. These
  actively reboot the pinned device and should only be run with explicit
  authorization. Set `RGSP_BOOT_TIMING_WORK` to an ignored artifact directory
  containing the compiled `counter-probe-minimal` binary. Existing samples are
  never overwritten. `compare-pair.py gzip|original` verifies the kernel
  partition hash before three restarts. It does not flash either variant.
- `evidence/`: readings, boot IDs, hashes, restore verification and a compact
  comparison. Larger raw boot logs and generated executables remain in ignored
  `work/boot-timer-rgsp` in the original checkout.

The new C helper was compiled with Alpine 3.20's AArch64 musl toolchain, using
`gcc -static -O2 -Wall -Wextra -Werror`, smoke-tested under OrbStack, then
checked on the device before installation. Container frontend-session tests
also pass. The normal full rootfs/release build has not been rerun for this
experimental branch.
