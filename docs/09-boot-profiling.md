# 09 — Boot performance

## Build optimisation

`build-rootfs.sh` runs `tools/strip_gpu_module.py` on the harvested GPU module.
The helper removes debug data from a temporary copy and verifies allocated
sections, module metadata/version CRCs, runtime symbols and relocations before
accepting it. Signed modules are refused. The original `stock-harvest.tar`
remains unchanged; validation results are written to
`work/<target>/gpu-strip-report.json`.

The optimised module ships in the rootfs and through normal `.bosupd` updates.
Kernel compression is a separate [boot-pair optimisation](12-kernel-gzip.md):
it changes the kernel's storage format and U-Boot's compression selector,
while preserving the decompressed kernel, initramfs and DTB.
`tests/test-strip-gpu-module.py` covers the stripping checks.

## Measuring startup

`frontend-session` writes kernel uptime to `/run/boot-frontend-exec` immediately
before its first frontend `exec`. The marker is retained across frontend
respawns on tmpfs. The same value is appended once per boot to
`/mnt/sdcard/baseos-boot.log` as `BaseOS boot time: ... s (kernel start to
frontend handoff)`. This is on TF2 when it is the active frontend card, otherwise
on TF1's BASEOS partition. Open the log from a computer to read it.

After the frontend starts, read the marker and boot ID:

```sh
adb -s DEVICE_SERIAL shell 'cat /run/boot-frontend-exec; cat /proc/sys/kernel/random/boot_id'
```

The marker has 10 ms resolution on the vendor kernel. It excludes bootloader
time and frontend rendering. Measure power-on to a usable frontend separately
for user-facing startup time; USB discovery and host polling delays are not
boot timings.

### Standard counter-based stages

Every supported H700 target installs `boot-clock`. At the first handoff it reads the
24 MHz ARM generic counter and `CLOCK_MONOTONIC_RAW` together and logs:

```text
BaseOS boot stages: pre-kernel ... s; post-kernel ... s; combined ... s (counter origin to frontend handoff; raw clock)
```

`pre-kernel` is the counter value at Linux's initial timekeeping origin,
calculated as counter seconds minus raw monotonic seconds. It includes kernel
loading/decompression and the earliest kernel work before timekeeping starts.
`post-kernel` is raw monotonic time at frontend handoff: remaining kernel
initialization, initramfs and rootfs startup. `combined` is their sum, the raw
counter value at that handoff. The division is Linux's clock origin, not the
first kernel instruction. Reading the counter does not change or reset it.

The raw clock avoids NTP adjustments. The existing `/run/boot-frontend-exec`
marker and 2.50-second budget retain `CLOCK_BOOTTIME` semantics and centisecond
precision; the additional stages are rounded to milliseconds. The four values
in `/run/boot-clock-handoff` are legacy handoff, pre-kernel, raw post-kernel and
combined seconds. Records are retained across frontend respawns.

Counter reset and within-boot stability have been measured with NextUI warm
reboots on RG SP. Counter zero is **not calibrated to LED-on**, and handoff is
not the first rendered frame. Cold starts and suspend/resume still need separate
validation. Other targets use the same interface; their reset behavior still
needs physical-device validation. Missing, unavailable,
unexpected-frequency or imprecisely sampled counters also fall back to the
existing uptime-only path. See the [measurement evidence](../experiments/rgsp-boot-timing/README.md).

For performance comparisons:

- Use the same device, SD card, firmware, frontend and cable state.
- Measure at least three clean boots per variant, record distinct boot IDs,
  and compare the median and range.
- Keep cold starts and warm reboots separate. Exclude filesystem-recovery boots.
- Use the normal shutdown path so persistent filesystems are clean.

`validate-on-device.sh` enforces a 2.50-second kernel-to-frontend handoff ceiling
on all devices. See [runtime power handling](05-runtime-power-network.md) for
shutdown integration and [boot I/O](10-boot-io-audit.md) for persistence policy.
