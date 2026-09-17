# 02 — Firmware preparation, image build & flash

Everything runs on macOS. Linux filesystem work happens inside unprivileged
Alpine containers; the build uses neither sudo, loop mounts nor a running
handheld. Platform selection lives in `tools/docker-platform.sh`: steps that
must build or execute aarch64 device binaries (`build-tools.sh`,
`build-rootfs.sh`) pin `linux/arm64`; preparation, image packing, bootlogo
generation, the QEMU userspace smoke test, and most synthetic tests pin the
host CPU (`linux/amd64` on Intel Macs, `linux/arm64` on Apple Silicon) so
they stay native. The QEMU test still boots an aarch64 guest (kernel fetched
from Alpine's aarch64 index); only the emulator binary is host-native, which
avoids nested emulation on Intel.

## 1. Inputs and targets

Normal builds use the published prepared cache through `fetch-prepared.sh`.
Preparing new inputs requires an extracted stock or StockMod `.img`; BaseOS
does not download or unpack multipart vendor archives.

`devices.json` defines eleven target profiles, including `rgsp`. Use
`python3 tools/device_profile.py list` for the current IDs, or see the
[device table](../CONTRIBUTING.md#building). Profiles specify model identity,
filename matching, logo dimensions, rotation and radio capabilities.

Preparation validates GPT geometry and CRCs. Full stock/StockMod images require
a matching backup GPT. StockMod BASE images may omit `primary`, either ending
exactly after p7 without a backup GPT or retaining a valid full-disk backup.
Other truncation is rejected. Stock layouts are normalized to StockMod offsets;
image composition restores `primary` when absent.

Preparation copies everything before p5 into `boot-prefix.img` and extracts the
allowlisted userspace from p5. Extraction happens
through `debugfs`; a static BusyBox tar runs chrooted inside the extracted root so
absolute and relative symlinks are safely dereferenced within that root. A final
GNU-tar pass normalizes ordering, owners and timestamps.

The per-target preparation contract under `work/<target>/` is:

- `boot-prefix.img` — raw boot region plus partitions 1–4;
- `stock-harvest.tar` — deterministic, dereferenced stock userspace allowlist;
- `source.json` — target/model/capabilities, vendor filename/size/SHA-256, complete
  GPT geometry, output hashes, preserved partition hashes and logo dimensions.

Paths in `manifest/harvest.list` are required unless they belong to the WiFi/Bluetooth
sections (or the corresponding kernel module) and the target profile declares that
radio absent. Permitted omissions are recorded in `source.json`; every other missing
file fails preparation, so BaseOS never silently emits a partially functional rootfs.

## 2. Build one specific target, end to end

First extract the StockMod download with 7-Zip. For a multipart download, open or
extract the `.7z.001` file; 7-Zip reads the following volumes automatically. BaseOS
does not unpack these archives itself. Confirm that extraction produced one `.img`
whose filename matches the target profile or its configured filename alias in
`devices.json`.

From the repository root, substitute the desired target and extracted image path:

```sh
TARGET=rg40xxv
FIRMWARE=/path/to/RG40XXV-...-mod-....img

./prepare-stock.sh "$TARGET" "$FIRMWARE"
./build-tools.sh                         # shared; rerun when src/ changes
./build-rootfs.sh "$TARGET"
./test-boot-qemu.sh "$TARGET"
./build-image.sh "$TARGET"
./build-update.sh "$TARGET"                # optional: the .bosupd update payload
```

The flashable result is `work/<target>/baseos-<target>.img`. Preparation and build
artifacts stay isolated in the same target directory:

- `source.json` — source identity, hash, GPT geometry and derived hashes;
- `boot-prefix.img` and `stock-harvest.tar` — prepared StockMod inputs;
- `rootfs.tar` and `closure-report.txt` — assembled and checked userspace;
- `bootlogo.bmp` and `baseos-<target>.img` — final target-specific outputs;
- `baseos-<target>-<version>.bosupd` — the update payload, if `build-update.sh` ran.

To list removable media and then flash the image on macOS:

```sh
./flash-card.sh "$TARGET"
./flash-card.sh "$TARGET" diskN
```

The second command requires typed confirmation and destroys all data on the selected
disk. Use the whole disk identifier (`diskN`), never a partition such as `diskNs1`.

`build-rootfs.sh` verifies the preparation hashes before assembling BusyBox, the
target harvest and the BaseOS overlay. It generates `/etc/baseos-release` and the
NextUI-compatible `/mnt/vendor/bin/dmenu.bin` model stub from `devices.json`.
`BASEOS_TARGET` identifies exact hardware; `BASEOS_DEVICE` remains the frontend
family; `BASEOS_MODEL_STRING` is the stock-style compatibility value.

`build-image.sh` reads p2/p5 offsets from `source.json`, not constants. It preserves
boot0 and p1/p3 byte-for-byte, derives the exact [gzip boot pair](12-kernel-gzip.md),
retains p2 geometry while replacing only
`bootlogo.bmp`, regenerates both GPTs, creates 4.9-safe journalled ext4 filesystems,
and creates the small FAT data partition expanded on first boot. Bootlogos are rendered
per target at 480×640, 640×480, 720×480 or 720×720; runtime `fbsplash` still reads the
actual framebuffer geometry.

## 3. One-command and batch builds

Put one `.img` for every desired model in a directory. With no target list the batch
command requires every configured target; target arguments select a subset:

```sh
./build-stockmod.sh /path/to/firmware
./build-stockmod.sh /path/to/firmware rg40xxv
./build-stockmod.sh /path/to/firmware rg28xx rg40xxv rgcubexx
```

All matches are preflighted before preparation. A missing image, duplicate target, or
more than one matching `<StockMod-prefix>*.img` is an error. Shared static tools are
built once or reused; every selected target then receives its own preparation,
rootfs, QEMU smoke test, image and verification artifacts. The one-target form is the
short equivalent of the complete recipe in section 2.

## 4. Image geometry and verification

The normalized p5 start becomes rootfs slot A. The environment selects
partition number 5; GPT slot switching can move its extent to slot B. BaseOS then packs a 512 MiB
rootfs slot, an identically sized unallocated slot B, a 128 MiB userdata filesystem
and a roughly 64 MiB initial FAT32 filesystem, plus backup-GPT headroom.
The normalized image is about 1.5 GB; compressed size depends on rootfs contents. `expand-storage` grows the FAT partition to the card on
first boot.

Every image build checks:

- preparation size/SHA-256 values and target identity;
- GPT CRCs, names, GUIDs, starts and non-overlap;
- exactly seven partitions, with `UDISK` two slots past the rootfs start;
- the hidden attribute bits on everything except the user-visible `primary`;
- the raw boot region plus p1/p3/p4 against the prepared StockMod bytes;
- rootfs slot A and `UDISK` with read-only `e2fsck`, and slot B empty;
- `/init`, `gptgrow`, `gptslot`, executable `expand-storage` and `baseos-update`, and
  exact target identity in the rootfs;
- the embedded bootlogo bytes and native dimensions;
- FAT32 readability on `primary`.

The synthetic importer suite covers valid preparation, deterministic output,
dereferenced relative/absolute symlinks, directories, invalid GPT magic, incorrect
primary or backup GPT data, incorrect partition names, truncation, ambiguous firmware
matches and required-file omissions:

```sh
./test-prepare-stock.sh
```

QEMU validates generic userspace plumbing, not the vendor kernel or hardware.
See [hardware validation](06-status-and-lessons.md) for device-specific coverage.

## 5. Flash and optional device validation

```sh
./flash-card.sh rg40xxv             # list removable candidates
./flash-card.sh rg40xxv diskN       # typed confirmation, write, eject
./validate-on-device.sh rg40xxv <device-ip> # optional post-boot validation
```

The flasher refuses internal/synthesised disks and targets smaller than the selected
image. Never flash the StockMod source card. On-device validation and development SSH
remain useful after boot but are not preparation or build dependencies.
