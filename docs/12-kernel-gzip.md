# 12 — Gzip kernel boot and one-update migration

All supported H700 images use a gzip-compressed vendor kernel. The decompressed
kernel bytes and module ABI are unchanged. `manifest/kernel-gzip.json` pins each
target's original boot package, U-Boot and full boot-partition SHA-256 hashes.
Unknown firmware is refused by the builder; a new vendor build requires a new
audit and profile rather than a speculative binary patch.
This is a one-time format conversion, not a general kernel updater. A later
vendor-kernel change also needs an explicit migration/versioning plan: existing
`boot-gzip-v1/complete` markers intentionally prevent repeated firmware writes,
and rootfs modules must continue matching the installed kernel.

## Build contract

`tools/kernel_gzip.py` derives a matched pair from the pristine prepared inputs:

- One Thumb instruction changes Android's forced compression selector from
  raw to gzip. Only that instruction and the U-Boot/TOC1 checksums change.
- The Android v2 image keeps its load addresses, ramdisk and DTB. The kernel is
  gzip level 9 with a fixed timestamp; its size and Android image ID are updated.
  Unused partition-tail bytes remain unchanged.

The patched loader **requires gzip**. Do not replace only one member of the
pair. Recovery must restore both the original TOC1 package and original p4.

`build-image.sh` installs and verifies the pair in fresh images. `build-rootfs.sh`
also includes the target's pair and manifest in `/usr/share/baseos/boot-gzip/`,
so the unchanged `baseos-update/1` archive format can deliver the migration to
existing installations. The payload adds roughly 11 MiB to the rootfs.

## User experience

Copy the target's normal `.bosupd` onto the frontend card and restart. The
existing updater installs the rootfs. Its first boot shows an optimising-startup
message, verifies and backs up the original firmware, installs the pair, and
continues to the frontend, confirming the rootfs trial. The next ordinary boot
uses gzip. The running kernel is identical to the decompressed Image, so an
extra migration restart is unnecessary. There is no manual card reflash, second
download or settings action.
Confirmation also removes the exact applied `.bosupd` if an older updater left
it behind, avoiding a repeated payload scan on subsequent boots.

Keep power connected during the update. The migration accepts external power
or at least 50% battery; otherwise it defers and boots normally with the original
pair, retrying at a later boot. Charging-only and USB-storage sessions do not
migrate. Fresh gzip images recognize the installed pair and finish without an
extra restart.

## Checks, failure handling and recovery

Before writing, `baseos-boot-migrate` checks target identity, p4 geometry,
replacement hashes, exact current firmware hashes, persistent writable userdata
and backup space. Original
p4 (64 MiB) and TOC1 (1.25 MiB) are saved, verified and synced under
`/data/boot-gzip-v1/`. It then writes the pair and verifies direct readback.
GPT, environment, frontend storage and user settings are not modified by this
migration. The ordinary rootfs updater still performs its normal GPT slot switch.

A failed installation attempts to restore both originals and verify them.
If restoration cannot be verified, frontend startup and trial confirmation stop;
the display asks to keep power on and ADB remains available. Do not reboot in
that condition. Unknown firmware, incorrect geometry, corrupt assets, or unusable
backups/space leave the original pair untouched and record a skipped result.

**The paired firmware update is not power-loss-safe.** Shared boot sectors are
outside rootfs A/B protection. Losing power between writes can prevent Linux
from booting and require restoring the saved pair from another computer or
reflashing the card. Backups remain on userdata for recovery; use the matching
target manifest's offsets and verify hashes before any offline writes.

`/data/boot-gzip-v1/migration.log` records the operation; `complete` contains
`installed`, `already-installed`, `rolled-back`, or a `skipped:` reason. Completed
boots execute just one shell-builtin existence check. They launch no migration
process and perform no firmware reads, hashes, backup checks or forced delays.
Low-battery deferral deliberately leaves the marker absent. After manually
restoring an original pair, remove the completion marker only if a retry is wanted.

## Validation and timing

The actual vendor Android parser and gzip decoder are exercised with Unicorn
for every pinned target. Hardware-facing operations are stubbed: this is
function-level instruction emulation, not a complete H700/QEMU boot. RG SP is
the physical test device; other models still need physical acceptance tests.

`tests/test-boot-migration.sh` runs the Python tests for paired writes, direct verification, backup
refusals, low-power deferral, restored failures and unrecoverable failures using
ordinary files in a disposable Alpine container. `tests/test-frontend-session.sh`
checks migration order, completion bypass, maintenance bypass and failure blocking.
The standard image verifier checks the exact derived pair and preserved regions;
`test-update-roundtrip.sh` checks the actual `.bosupd` A/B application.

With all prepared inputs available, run `python3 tests/test-kernel-gzip.py` for
artifact and corruption gates. Run `tests/test-kernel-gzip-emulation.py` under
Linux with `unicorn==2.1.4` and `capstone==5.0.9` for vendor instruction tests,
including the SD read-size calculation and an unpatched-loader negative control.
Both accept an optional list of target IDs.

All targets install the [standard boot-clock logger](09-boot-profiling.md).
Compression affects pre-kernel time; initramfs/rootfs work affects post-kernel
time. Combined time runs from hardware-counter origin to frontend handoff,
which is distinct from LED-on to the first rendered UI frame.
