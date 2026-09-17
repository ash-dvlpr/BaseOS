# 03 — First boot: expand-to-fill, then add a frontend

The image ships a tiny 64 MiB empty data partition (p7) and **no frontend**. On the
first normal boot p7 grows to fill the card and receives `README.txt` and a
commented `baseos.conf` example; the user then copies a frontend onto it.
Subsequent boots check the geometry and skip reformatting.

## 1. Why reformat instead of resize-in-place

In-place FAT32 growth would need `fatresize` (which pulls in libparted — no viable
static build) or a hand-rolled cluster-relocation defrag (complex, corruption-prone).
Since Base OS bakes no payload, the far simpler path is safe:

- ship p7 **small and empty**;
- on first boot, **grow the FAT partition's GPT entry to fill the card and give it a fresh, empty
  FAT32 at full size**.

Reformatting an empty partition can't lose data, so this needs no staging. It also
keeps the flashable image tiny (p7 is small in the image). BusyBox already ships
`mkfs.vfat`/`partprobe`/`blockdev`, so no extra tools are needed.

## 2. `gptgrow` — GPT growth + kernel live-resize (`tools/gptgrow.c`)

A zero-dependency static C tool: `gptgrow /dev/mmcblk0`.

1. reads the primary GPT, finds the highest-numbered non-empty partition (p7);
2. if it already reaches `last_usable` → **exit 1** (idempotent no-op);
3. else sets its ending LBA to `total-4`, rewrites **both** GPT headers + entry tables
   (inline CRC32) and the protective MBR; `fsync`; → **exit 0**;
4. **`ioctl(fd, BLKPG, BLKPG_RESIZE_PARTITION)`** to live-resize p7 in the kernel.

Step 4 is essential: a full partition-table reread (`partprobe`) **fails with EBUSY**
because the rootfs (p5) is mounted on the same disk. `BLKPG_RESIZE_PARTITION` updates
just p7's size in the kernel while its siblings stay mounted, so `/dev/mmcblk0p7`
reflects the new size immediately — no reread needed. (Run against a plain file, the
ioctl fails `ENOTTY` and is skipped; the GPT is still rewritten — that's the offline
test path.)

`test-expand-storage.sh` covers GPT growth and repeat-run behavior on synthetic
images. See [hardware coverage](06-status-and-lessons.md) for device checks.

## 3. `expand-storage` — the first-boot orchestrator (`overlay/usr/sbin/expand-storage`)

Runs from `rcS`, **before** the card is mounted:

1. run `gptgrow /dev/mmcblk0` (grow + BLKPG). **Its exit code is the idempotency key:**
   - **exit 1** (already fills the disk, i.e. an already-set-up card) → log and exit,
     leaving p7 completely untouched and showing no expansion message. So a frontend
     the user has copied on is never reformatted;
   - **exit 2** (GPT/device error) → log the failure and skip formatting; a write
     error can occur after partial GPT changes;
   - **exit 0** (freshly grown) → continue;
2. paint `baseos-splash --important 45 "EXPANDING STORAGE"` now that a real resize is
   confirmed;
3. fallback `partprobe`/`blockdev --rereadpt` (harmless EBUSY if BLKPG already did it);
4. `mkfs.vfat -F 32 -n BASEOS /dev/mmcblk0p7` (fresh empty FAT32, full size);
5. mount p7 and copy `README.txt` and the commented `baseos.conf` example from
   `/usr/share/baseos/`; `sync`; unmount.

It logs to `/data/baseos-boot.log` (persistent, on p6) before the frontend card
is mounted. rcS appends that buffer to `/mnt/sdcard/baseos-boot.log` once the
card is writable, then removes the buffer. If mounting fails, the buffer remains
for recovery after power-off. Keying
idempotency on "does p7 already fill the disk" (rather than a `/data` flag or content
check) prevents normal boots from reformatting existing frontend content.
If formatting fails after GPT growth, the next geometry check will still report
that p7 fills the disk; automatic formatting is not retried.

## 4. Adding a frontend (the hand-off)

BaseOS ships no frontend, so after expansion the card contains only setup files and
`frontend-session` shows **`ADD FRONTEND TO SD CARD`** and waits (init respawns it). The
user then:

1. mounts the card on a computer — it now presents the full-capacity `BASEOS` volume
   with `README.txt` and `baseos.conf`;
2. copies a frontend onto it — for NextUI, `MinUI.zip` (+ any `nextui.*.pakz`);
3. reboots the handheld.

On the next normal boot, `frontend-session` bootstraps `.tmp_update/h700.sh`
from `MinUI.zip` when needed and runs that frontend installer. Pending `*.pakz`
files also trigger it. BaseOS displays a static installation/update pill;
see [boot splash](04-boot-splash.md). Slot's extracted release needs no installer.

Slot uses an extracted `System/slot` binary instead of the NextUI installer.
See [frontend entry points](01-rootfs-and-init.md#4-frontend-session) for launch
selection and the [README](../README.md#installation) for card setup.

## 5. Boot-to-boot behaviour

| boot | expand-storage | frontend | net |
|---|---|---|---|
| 1st normal boot | grows and formats p7, copies setup files | add-frontend prompt unless TF2 has a frontend | expand, then select frontend |
| after user copies a frontend | geometry check, no format | NextUI installer or direct Slot launch | frontend-dependent |
| every later boot | no-op | `MinUI.zip`/pakz consumed → launch only | a few seconds to the frontend |

## 6. Edge cases & caveats

- If the card is exactly the image size, `gptgrow` is a no-op and the initial
  FAT filesystem stays roughly 64 MiB; the expansion path does not copy setup files.
- Because p7 is reformatted on first boot, the user must add the frontend **after** the
  first boot, not before — anything dropped on the tiny empty p7 pre-boot is erased by
  the expansion. (Standard handheld flow: flash → boot to expand → add content.)
