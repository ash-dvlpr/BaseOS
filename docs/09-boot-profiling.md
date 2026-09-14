# 09 — Boot optimization and measurement

BaseOS strips debug data from the vendor GPU module during the rootfs build.
The vendor bootloader, kernel, initramfs and DTB are preserved. Boot timing uses
one marker at the first frontend handoff.

## GPU stripping

`build-rootfs.sh` invokes `tools/strip_gpu_module.py` after extracting the stock
harvest. The helper runs `strip --strip-debug` on a temporary copy and compares
allocated sections, module metadata/version CRCs, runtime symbols and runtime
relocations before accepting it. Signed modules are refused. The original
`stock-harvest.tar` is preserved; `work/<target>/gpu-strip-report.json` records
the size and validation results.

On the RG34XXSP firmware examined on 2026-09-13, `mali_kbase.ko` shrank from
**17,593,992 to 723,544 bytes**. Rebuild the rootfs and update payload to deliver
this change through the normal `.bosupd` update path. No boot-partition update
is needed.

The regression checks are in `tests/test-strip-gpu-module.py`. Repeated device
measurements establish the boot-time saving because GPU loading overlaps other
startup work.

## Measure frontend handoff

`nextui-session` writes the first field of `/proc/uptime` to
`/run/boot-frontend-exec` immediately before its first frontend `exec`, after
the handoff log. The marker is retained across frontend respawns on tmpfs.
The same value is appended once per boot to `/mnt/sdcard/baseos-boot.log` as
`BaseOS boot time: ... s (kernel start to frontend handoff)`. This is on TF2
when it is the active frontend card, otherwise on TF1's BASEOS partition. Open
the log from a computer to read it. There is no detailed stage trace.

After the frontend starts, read the marker and boot ID over ADB:

```sh
adb -s DEVICE_SERIAL shell 'cat /run/boot-frontend-exec; cat /proc/sys/kernel/random/boot_id'
```

The marker measures seconds from kernel start to frontend handoff, with 10 ms
resolution on the vendor kernel. It excludes bootloader time and frontend
rendering. Measure power-on to a usable frontend separately for total startup
time; host USB discovery and polling delays are not boot timings.

Compare at least three clean boots per variant using the same device, SD card,
firmware, frontend and cable state. Record distinct boot IDs and report the
median and range. Keep cold starts, clean warm reboots and filesystem-recovery
boots separate. The RG40XX V handoff ceiling is 3.00 seconds, checked by
`validate-on-device.sh`.

On RG34XXSP, use the installed NextUI `reboot_next` helper for controlled
restarts. Direct BusyBox `reboot` can leave the handheld in a powered-down
limbo requiring Reset, even if the frontend is stopped first. Exclude any
reset-assisted recovery from a warm-boot comparison. NextUI's `poweroff_next`
performs the PMIC power-off sequence; its restart helper uses its own flush and
restart path. Do not substitute a generic reboot or a signal to the frontend.
For these controlled tests, save entropy, sync, and successfully remount `/data`
and `/` read-only before invoking the helper: it handles the frontend card,
but does not itself cleanly close BaseOS's separate userdata partition. Abort
the restart if preparation fails, and exclude any following boot that reports
journal recovery. The vendor initramfs and `rcS` restore the normal writable
mounts on the next boot.

See the [SD-card I/O audit](10-boot-io-audit.md) for the logging comparison,
persistence policy and remaining frontend opportunities.

## RG34XXSP measurements, 2026-09-13

BaseOS 1.1.0 (`b9adb6f`) was measured with the vendor 4.9.170 kernel, the same
TF1 card, frontend and USB connection, and identical temporary instrumentation.
Each variant had three clean warm boots with distinct boot IDs and verified
boot/runtime hashes. All six samples had a running NextUI process, GPU node and
Wi-Fi interface, with no filesystem recovery.

| GPU module | Handoff median | Range |
| --- | ---: | ---: |
| Original | 3.01 s | 2.93–3.03 s |
| Debug data stripped | 2.26 s | 2.25–2.28 s |

GPU stripping reduced the median handoff time by **0.75 seconds (25%)**. Both
variants used the same temporarily instrumented vendor initramfs; these are
warm-reboot results, not cold-start measurements. The minimal-initramfs
experiment saved a further 0.07 seconds and was dropped. No kernel, bootloader
or initramfs change is shipped.

Session-local raw samples, comparison JSON and recovery notes remain in the
gitignored `work/boot-profile-rg34xxsp/` directory. They are historical evidence;
the stage-profiling runtime and experimental boot-image builder are no longer
part of the project.
