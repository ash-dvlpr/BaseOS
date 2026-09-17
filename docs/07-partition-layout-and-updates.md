# 07 — Partition layout and A/B system updates

BaseOS uses seven GPT partitions and two rootfs slots. Updating writes the
inactive slot, verifies it, then changes partition 5 to select that slot.
Userdata and frontend storage retain their existing extents.

## 1. Boot contract

U-Boot loads the partition named `boot`; the kernel mounts partition number 5
as root. Its start address comes from GPT, allowing a slot switch without
changing the vendor bootloader, kernel or initramfs. Partition names and order
must remain compatible with the vendor boot chain.

## 2. Layout

The normalized layout uses these LBAs; builds derive them from the prepared
source geometry and configured slot sizes.

| Entry | Name | Start LBA | Size/role |
| --- | --- | --- | --- |
| 1 | `special` | 73728 | 64 MiB vendor partition |
| 2 | `boot-resource` | 204800 | 32 MiB vendor FAT, with BaseOS bootlogo |
| 3 | `env` | 270336 | 16 MiB vendor environment |
| 4 | `boot` | 303104 | 64 MiB vendor Android boot image |
| 5 | `rootfs` | 434176 or 1482752 | Active 512 MiB slot |
| — | Inactive slot | 1482752 or 434176 | 512 MiB unallocated extent |
| 6 | `UDISK` | 2531328 | 128 MiB persistent `/data` |
| 7 | `primary` | 2793472 | FAT32 frontend storage, expanded on first boot |

Entry 8 is empty. `appfs` is omitted; `UDISK` and `primary` preserve their
vendor identities when present in the prepared input. The initial image is
2,926,592 sectors (1,498,415,104 bytes); compressed size varies with contents.

### Desktop visibility

All entries except `primary` carry attributes `0xC000000000000000`: hidden
(bit 62) and no drive letter (bit 63). `primary` remains Microsoft Basic Data
with attributes zero. This requests one Windows drive letter; verify actual
host behavior during hardware acceptance. macOS can still mount the vendor
FAT `boot-resource` volume because it does not honor those attributes.

## 3. Update payload

A `.bosupd` is an uncompressed ustar archive with `manifest` followed by
`rootfs.img.gz`. The manifest contains:

```text
format=baseos-update/1
target=<device-id>
version=<release-version>
build=<build-id>
slot-sectors=1048576
image-size=536870912
image-sha256=<sha256 of decompressed rootfs image>
```

The rootfs bytes come from the image built by `build-image.sh`. The manifest
hash verifies decompressed image integrity; payloads are not signed.
`build-all.sh` also prints download-file checksums for the release page.
Updates replace rootfs only; they do not update the bootloader or p2 artwork.

## 4. Selection and application

`baseos-update apply` runs after card mounting. It scans the active frontend
card first, then TF1's visible partition when that is a different volume.
USB-storage mode skips scanning, including manual apply/status invocations.

The first applicable payload in directory/glob order is selected. A payload
must match the target and slot geometry, be newer than the running version
(or the same version with a different build), and have no committed image hash
in `/data/update/history`. Selection does not jump directly to the highest
version. `baseos-update status` reports each candidate's eligibility.

Application proceeds as follows:

1. Validate the manifest and slot geometry.
2. Decompress into the inactive slot in chunks, updating progress as writes finish.
3. Read the slot back and verify its SHA-256.
4. Flip GPT partition 5 to the verified slot.
5. Append `<sha> <version> <build>` to `/data/update/history`, write trial state
   to `/data/update/state` and flush.
6. Delete the applied payload, flush and reboot. A read-only payload volume is
   temporarily remounted writable and then restored to read-only.

Before the GPT flip, a failed write or verification leaves the active slot
selected. The inactive slot and diagnostic logs may already have changed;
the whole card is not byte-identical to its pre-update state. The running
kernel retains its cached active-slot offsets until reboot.

Only the successfully applied payload is deleted; skipped or rejected files
remain on the card. Cleanup failures are logged without blocking the reboot.
History is recorded at commit, so a payload left behind or copied back is not
reapplied after rollback. The old `committed-sha` file is read
only to seed history on migration. Diagnostics, including confirmation, use
`baseos-boot.log`; see [boot I/O](10-boot-io-audit.md).

## 5. Trial and rollback

`rcS` calls `baseos-update boot-check` before frontend startup. It increments
an open trial's attempt count and flips back on the third boot that reaches
this check without prior confirmation. `frontend-session` confirms the trial
on session start, even without an installed frontend, then removes trial state.
Charging-only sessions do not reach either operation.

Rollback requires userspace to reach `boot-check`. It cannot recover a rootfs
that fails before that point. Manual slot switching is available through
`gptslot /dev/mmcblk0 flip` when the system can boot.

`gptslot` derives both slot extents from GPT and refuses incompatible layouts.
It writes backup entries/header and fsyncs them, then writes primary
entries/header and fsyncs again. Power loss during the primary GPT write can
still leave inconsistent metadata; the sequence is not an atomic transaction.

## 6. Build and validation

```sh
./build-image.sh rg40xxv
./build-update.sh rg40xxv
```

The payload filename uses `VERSION`; build identity comes from
`git describe --always --dirty`. The builder checks both values against the
rootfs before packaging.

- `tests/test-gpt-slot.sh`: layout, slot arithmetic, flips and invalid geometry.
- `tests/test-update-apply.sh`: selection, rejection, integrity and trial behavior.
- `test-update-roundtrip.sh <target>`: real payload application to a copied image.

Hardware validation covers RG40XX V slot switching, `/data` persistence and
trial confirmation. Failed-trial rollback and Windows volume visibility need
separate hardware checks; see [validation coverage](06-status-and-lessons.md).
