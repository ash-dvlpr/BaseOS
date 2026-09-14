# 06 — Hardware validation and constraints

## 1. Validation coverage

Hardware validation is device-specific; a successful image build or QEMU smoke
test does not establish hardware support on every target.

| Capability | Validated coverage | Remaining checks |
| --- | --- | --- |
| Boot, storage expansion and NextUI launch | RG40XX V | Per-target first-boot and upgrade checks |
| Display, input, audio and GLES | NextUI on RG40XX V | Per-target validation |
| Static boot logo and status pills | RG40XX V; rotated panel on RG28XX | Other panel profiles |
| Suspend-to-RAM | RG40XX V | Long-duration standby current |
| Wi-Fi | RG40XX V association; RG34XX SP network access | Per-target startup and reconnect checks |
| SSH/SFTP | RG40XX V | Per-target validation |
| USB adb | RG40XX V cable-connected boot; shell and file transfer | Per-target validation |
| USB mass storage | RG40XX V TF1 data partition | Whole TF2 with real cards |
| HDMI | RG40XX V hotplug in both directions | Per-target validation |
| Bluetooth audio | Daemon startup | Pairing and playback |
| A/B updates | RG40XX V slot flip, slot B boot, `/data` persistence and trial confirmation | Hardware rollback after failed boots |
| GPT visibility attributes | RG40XX V boot | Windows drive-letter and format-prompt behavior |
| Charger-only boot and PMIC shutdown | RG SP; POWER-hold startup on RG34XX SP | Per-target charging and power behavior |
| Hostname and mDNS | RG34XX SP network validation | Per-target and host-network compatibility |

## 2. Boot and build constraints

- Root ext4 requires a journal and the vendor-kernel-compatible feature mask.
  `/init` must be a regular executable script. See
  [boot chain](00-boot-chain-and-partitions.md).
- Boot-critical scripts must be executable. Tool source hashes must match the
  build stamp before packaging. See [image build](02-image-build-and-flash.md).
- HDMI switching requires the `debugfs` display interface mounted by `rcS`.
  See [rootfs and init](01-rootfs-and-init.md).
- Framebuffer draws must finish before frontend handoff. Panel rotation comes
  from the device profile. See [boot splash](04-boot-splash.md).
- Preserve vendor USB role selection and inspect configfs attribute contents
  rather than synthetic file sizes. See [USB access](08-usb-adb-and-otg.md).
- Growing a partition while a sibling is mounted requires `BLKPG`; `gptgrow`
  handles this. See [storage expansion](03-first-boot-and-expand.md).

## 3. Development validation

QEMU tests cover generic userspace. Use hardware validation for vendor-kernel
interfaces, drivers, display orientation, suspend and power handling. After
harvest changes, check the runtime library closure on the target.

See [CONTRIBUTING.md](../CONTRIBUTING.md) for the build and test commands, and
the relevant subsystem document for its acceptance criteria.

## 4. Open work

- Read-only rootfs after initialization for power-loss resilience.
- Complete the remaining hardware checks above across supported H700 models.
- Define `/data` schema migration before storing state that cannot be regenerated.

## 5. Frontend boundary

BaseOS owns hardware support, system services, storage, updates and the session
handoff. Frontends are installed separately on the card and are not BaseOS
build dependencies.
