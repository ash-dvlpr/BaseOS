# 09 — Boot performance

## Build optimization

`build-rootfs.sh` runs `tools/strip_gpu_module.py` on the harvested GPU module.
The helper removes debug data from a temporary copy and verifies allocated
sections, module metadata/version CRCs, runtime symbols and relocations before
accepting it. Signed modules are refused. The original `stock-harvest.tar`
remains unchanged; validation results are written to
`work/<target>/gpu-strip-report.json`.

The optimized module ships in the rootfs and through normal `.bosupd` updates.
The vendor bootloader, kernel, initramfs and DTB remain unchanged.
`tests/test-strip-gpu-module.py` covers the stripping checks.

## Measuring startup

`nextui-session` writes kernel uptime to `/run/boot-frontend-exec` immediately
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

For performance comparisons:

- Use the same device, SD card, firmware, frontend and cable state.
- Measure at least three clean boots per variant, record distinct boot IDs,
  and compare the median and range.
- Keep cold starts and warm reboots separate. Exclude filesystem-recovery boots.
- Use the normal shutdown path so persistent filesystems are clean.

`validate-on-device.sh` enforces a 3.00-second kernel-to-frontend handoff ceiling
on RG40XX V. See [runtime power handling](05-runtime-power-network.md) for
shutdown integration and [boot I/O](10-boot-io-audit.md) for persistence policy.

## Stock-style radio initialization experiments (RG SP, 2026-09-15)

Warm reboots of the same RG SP, SD card, NextUI installation and USB cable;
kernel start to frontend handoff, three boots per row unless noted.

| Initialization | Median | Range | Boots with a `-123` Wi-Fi probe error |
| --- | ---: | ---: | ---: |
| BaseOS 1.2.1 | 2.24 s | 2.23–2.28 s | 0/3 |
| No stale-card discard; early `rtl_btlpm` | 2.29 s | 2.28–2.35 s | 3/3 |
| Above plus stock udev coldplug before remaining init | 2.90 s | 2.88–2.91 s | 2/3 |
| Above with udev startup in the background | 2.54 s | 2.47–2.55 s | 1/3 |
| Above plus driver load held until uptime 7 s, frontend gated on `wlan0` (1 boot) | 9.17 s | — | 0/1 |

Every boot connected on this unit, which never showed the reported failure.
Two findings ruled these variants out as fixes: dropping the stale-card
discard brings back the probe race that 1.2.1 fixed (it stayed dead on cold
boots of this unit before 1.2.1), and udev cost 0.25–0.6 s while still
re-powering the radio 0.5–1.2 s after the kernel disabled its supply, no
different from the default path. An affected unit running the udev variant
still failed. BaseOS keeps direct module loading and devtmpfs. Stock's own boot log later
showed the decisive difference: its driver loads 3.8 s after the supply cut,
and an affected unit recovered after a 20 s power-off but not after 1 s. The
shipped change keeps the fast path and adds a remembered power-off retry for
such units only, plus the early `rtl_btlpm` load; neither costs boot time. See
[runtime notes](05-runtime-power-network.md).
