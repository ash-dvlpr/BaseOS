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
