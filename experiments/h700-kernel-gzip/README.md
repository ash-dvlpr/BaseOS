# H700 gzip integration validation — 17 September 2026

The [production contract](../../docs/12-kernel-gzip.md) is the maintained guide.
This directory retains integration evidence without shipping binaries or firmware.

All 11 supported targets were derived from their hash-pinned prepared firmware.
Their actual vendor Thumb code passed Android parsing, SD read-length calculation
and gzip decompression in Linux Unicorn 2.1.4 with Capstone 5.0.9. Original pairs
recover the original kernel; gzip with an unpatched loader deliberately does not;
patched gzip pairs recover the exact original kernel and ramdisk. These tests stub
hardware operations and do not replace physical-device acceptance.

Each target's rootfs passed its ELF closure check, and each composed image passed
the exact gzip-pair, preserved-region, GPT, filesystem and identity gates.
The RG SP rootfs also passed the generic AArch64 QEMU userspace smoke test.
The actual `.bosupd` passed A/B application/readback, slot flip and user-data
preservation in the disposable image roundtrip. BusyBox migration tests cover
13 success/refusal/failure cases; frontend tests cover ordering, completion bypass,
maintenance bypass, failed recovery and both clock-log paths.

## Physical RG SP update

The starting pair matched the original catalog hashes. One ordinary `.bosupd`
was staged through the existing updater, which installed the rootfs into the
other slot. On its first boot the migration backed up and replaced the pair;
direct readback matched:

```text
TOC1: fa745599c9eb95110f03c6c426e6d08e0317146d51059c6a790e25f604ea0942
p4:   83b6e8a62cf1d0479dd8a9132d2656af00c8aa15c27cdd583d29da6509071233
```

That test revision requested another automatic restart. The user reported a
blank screen after the completion message and pressed RESET; NextUI then started
and the trial confirmed. The cause of the blank screen was not established.
The final implementation removes this unnecessary migration-only restart and its
overlapping completion pill. It continues into the frontend with the identical
already-running kernel; the next ordinary boot uses gzip. The final helper was
installed on the test device, and its completed path returned immediately.
Future migration UI uses British spelling: `OPTIMISING STARTUP: KEEP POWER ON`.

Original p4 and TOC1 backups remain in `/data/boot-gzip-v1/`. The earlier update
file was retained as `.baseos-before-gzip.bosupd.saved` on the frontend card.
No card reflash was used. The old updater left the just-applied `.bosupd` in place;
this remained present in both sides of the timing comparison to avoid changing
the known stale-payload scan cost mid-test. After those measurements it was archived
as `.baseos-gzip-tested.bosupd.saved`. The final updater now performs one-time
confirmation cleanup of the exact trial image left by older updaters; unrelated
archives and same-build archives with different image hashes are retained.
That final compatibility cleanup was regression-tested, and the final RG SP
`.bosupd` passed the image roundtrip again. The other target image gates precede
this shared updater-only addition; their gzip assets and clock/helper binaries
are the final versions recorded in `evidence/build-matrix.json`.

## Steady-state measurements

Three original-pair boots and three completed-migration boots used NextUI's
`reboot_next`. The first boot after the user's manual RESET is excluded. Values
are first-handoff samples from the standard helper, not host USB latency.

| Median | Original pair | Migrated gzip pair |
| --- | ---: | ---: |
| Pre-kernel | 3.475 s | 3.429 s |
| Raw post-kernel | 2.283 s | 2.217 s |
| Combined | 5.727 s | 5.688 s |

The gzip pre-kernel range was 3.408–3.685 s. These noisy trials do not reproduce
the earlier approximately 250–300 ms gain and should not be used as a new precise
speedup claim. Rootfs revisions also differ, so the post-kernel change is not
attributed to compression. Earlier video and repeated-stage results remain in
the original experiment directories.

The completed migration guard was separately measured with three alternating
100,000-iteration BusyBox shell loops: baseline 1.58 s each, with the existence
check 2.72/2.71/2.71 s. The median incremental cost is about **11.3 microseconds
per cached check**, not an estimate of cold SD lookup latency. No migration
process, hashes, firmware reads or delays occur on completed boots. The observed
post-kernel times show no added recurring migration delay.

After archiving the leftover test payload and installing the final updater,
three further NextUI reboots had median pre-kernel **3.417 s**, raw post-kernel
**2.168 s**, and combined **5.617 s**. This is a separate condition from the
matched-payload comparison above. Individual samples are retained in
`evidence/device-boots.json`; medians of individual stages need not sum to the
median of their combined values.

Each measured boot retained the previously observed FAT dirty-volume warning
from NextUI's lazy card unmount; no ext4 journal recovery or kernel errors were
reported. Counter origin is not calibrated to LED-on, and frontend handoff is
not the first UI frame. Only RG SP has physical migration/timing validation.
