# RG SP boot timing results — 2026-09-17

The device is left running NextUI with the original uncompressed kernel and
original U-Boot, both restored and readback-verified. The new timing logger
remains installed. Main/v1.3.0 is untouched.

## Direct first-handoff comparison

| Boot path | Warm boots | Pre-kernel median (range), s | Post-kernel median (range), s | Combined median (range), s |
|---|---:|---:|---:|---:|
| Gzip + patched U-Boot | 3 | 3.416 (3.372–3.423) | 2.334 (2.319–2.347) | 5.735 (5.719–5.757) |
| Uncompressed + original U-Boot | 5 | 3.715 (3.489–3.774) | 2.348 (2.323–2.405) | 6.054 (5.818–6.179) |

Each column is an independent median; medians of parts need not sum to the
median total. Per-boot values below agree to the 1 ms display rounding.

**Median differences:** gzip saved 299 ms before Linux’s clock origin and
319 ms to handoff. Post-kernel medians differ by 14 ms, within observed boot
variation. The original pre-kernel range was 285 ms; all five original readings
are retained. This is a warm-reboot comparison, separate from the user’s
approximately 250 ms LED-on → first-UI-frame video result.

| Sample | Pre-kernel, s | Post-kernel, s | Combined, s | Boot ID |
|---|---:|---:|---:|---|
| gzip-logged-1 | 3.372 | 2.347 | 5.719 | `18431cdd-f1f0-4cdd-8824-b704141965da` |
| gzip-logged-2 | 3.416 | 2.319 | 5.735 | `a692fb74-ea9a-4106-82c2-d94fbdc41cba` |
| gzip-logged-3 | 3.423 | 2.334 | 5.757 | `f47db42e-236f-48be-b3a9-986b014e177e` |
| original-logged-1 | 3.715 | 2.339 | 6.054 | `ed0beb54-c881-41a1-abf2-773ac783e1e3` |
| original-logged-2 | 3.734 | 2.365 | 6.100 | `3241226d-6653-4e32-83ee-c11b93d94885` |
| original-logged-3 | 3.494 | 2.323 | 5.818 | `480b5feb-6b0d-4229-a217-f11f94fdefa2` |
| original-logged-4 | 3.489 | 2.348 | 5.837 | `38cdf591-e139-457f-9c27-bbb2fe9a9c05` |
| original-logged-5 | 3.774 | 2.405 | 6.179 | `6e9271c6-a192-449c-b4e2-5d344cc1efbf` |

## Clock stability and scope

All 13 accepted fresh boots (five initial gzip, three gzip with the logger,
five original with the logger) reported 24 MHz. On each boot, sixteen bracketed
counter/clock samples, separated into two groups roughly nine seconds apart,
are consistent with a constant raw-clock offset. Intersections of the sixteen
offset intervals are 1.6–2.4 microseconds wide. New boot IDs and small raw
counter values show the counter reset on every tested NextUI warm restart.

The five preliminary gzip boots had a median inferred pre-kernel interval of
3.395 s (3.372–3.416 s); handoff markers were 2.31–2.39 s. These older markers
have 10 ms granularity, so the primary table uses the later direct handoff logger.

The long-running initial boot showed an approximately 326 ms difference between
the adjusted BOOTTIME offset and the unadjusted RAW offset. The logger therefore
uses RAW for the new split and retains BOOTTIME only for the legacy marker.

Combined means **hardware-counter origin → frontend handoff**. Neither LED-on
alignment nor the first rendered frame is measured here. The kernel boundary is
Linux timekeeping zero; the earliest kernel work is included before it.

The accidental SELECT/ES boot is excluded. The pre-existing FAT dirty-volume
warning persisted through NextUI’s normal lazy-unmount path; no accepted boot
had ext4 journal recovery or a kernel fault. See [method](README.md) and the
complete [samples](evidence/measurements.json).

## Validation

The static helper passed an ARM64 OrbStack smoke test and independent on-device
counter checks. The frontend-session container suite passes, including first
handoff retention, respawns, missing helper and failed-helper fallback. Shell
syntax and git whitespace checks pass. No full release image was built.

The kernel restore wrote only p4 and the original TOC1 package at LBA 32800;
it did not rewrite GPT, rootfs, userdata or frontend files. [Readback verification](evidence/original-pair-restore.log)
and [artifact hashes](evidence/summary.json) are retained.
