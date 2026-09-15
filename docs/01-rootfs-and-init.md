# 01 — Root filesystem and init

The active rootfs is a 512 MiB ext4 slot containing BusyBox, harvested vendor
libraries and BaseOS tools. `/bin`, `/sbin` and `/lib` link into `/usr`.

## 1. Build inputs

`build-rootfs.sh` combines:

- Static BusyBox and tools built by `build-tools.sh`: Dropbear, SFTP server,
  curl with CA certificates, adb, Avahi, `fbsplash`, `gptgrow`, `gptslot`,
  `axp-off` and `charger-wait`.
- Vendor userspace selected by `manifest/harvest.list`: glibc, Mali/EGL/GLES,
  ALSA, wireless services, Bluetooth/BlueALSA/D-Bus, timezone data and modules.
- ARMHF compatibility libraries and loader, including stock fbdev Mali and
  SDL2 under `/usr/lib32`. These are required independently of radio support.
- `overlay/`, copied last, plus generated device identity and runtime symlinks.

Preparation dereferences harvest paths inside the extracted vendor root and
normalizes the archive. List sonames in the allowlist. Changing required paths
requires preparing the affected targets again and refreshing the cache;
`tools/verify_harvest.py` rejects incomplete archives during build, pack and restore.

The rootfs build checks the AArch64 dependency closure and the ARMHF loader/libc
required by `rtk_hciattach`. Other ARMHF applications still need their own
compatibility validation. Shipping `libudev` does not provide a udev daemon.
GPU module debug data is stripped with ABI checks; ARMHF libraries are retained
as harvested. See [boot performance](09-boot-profiling.md).

## 2. Runtime layout

| Path | Purpose |
| --- | --- |
| `/data` | Persistent p6 state: SSH keys, Bluetooth, entropy, time preferences and update state |
| `/mnt/sdcard` | Active frontend card: usable TF2 p1, otherwise TF1 p7 |
| `/mnt/SDCARD` | Compatibility symlink to `/mnt/sdcard` |
| `/mnt/system` | Temporary read-only TF1 mount for configuration and update discovery when needed |
| `/run`, `/tmp`, `/var`, `/dev/shm` | tmpfs runtime state |
| `/mnt/vendor` | Compatibility files shipped in the rootfs; no vendor partition mounted here |

The vendor initramfs mounts root read-write. Normal initialization does not
remount it read-only. If mounting p6 fails, `rcS` uses tmpfs for `/data`, so
state from that boot is not persistent. Shutdown flushes persistent mounts;
see [power handling](05-runtime-power-network.md).

## 3. Init and startup order

The vendor initramfs switches root to the regular executable `/init` script,
which executes BusyBox init. `/etc/inittab` runs `rcS`, then respawns
`nextui-session` and a serial getty. It invokes `rcK` on shutdown.

`rcS` performs this sequence:

1. Mount kernel interfaces and tmpfs filesystems. Apply SP Super Standby where
   supported. Mount `debugfs` for the HDMI display-control and MENU GPIO interfaces.
2. Handle charger-origin startup before ordinary initialization; see
   [charger-only boot](11-charger-only-boot.md).
3. Load the Bluetooth wake-handshake module, start Mali in the background,
   mount `/data`, and check the system-update trial.
4. Restore time preferences and entropy, read the RTC as UTC with `hwclock -u -s`,
   and start Wi-Fi module/interface initialization in the background (with a
   remembered power-off retry for slow-resetting radios; see [runtime notes](05-runtime-power-network.md)).
5. Restore or generate the machine ID, then run first-boot storage expansion.
6. Sample MENU through `boot-menu-held`'s GPIO check, or use the marker retained
   by charger handling. Select USB-storage mode before mounting frontend storage.
7. For normal startup, mount usable TF2 p1 or fall back to TF1 p7. Apply
   `baseos.conf` from TF1, then scan for system updates. Storage mode uses
   configuration defaults and skips card access and update application.
8. Start SSH/SFTP and USB adb through `/etc/init.d/dev` in the background.

Services requiring a network do not block frontend startup. There is no udev
or mdev; devtmpfs provides device nodes. Bluetooth starts D-Bus on demand.

## 4. Frontend session

`nextui-session` confirms an update trial when the session starts, even if no
frontend is installed. In USB-storage mode it waits for the gadget result,
displays the storage status and keeps the frontend stopped.

Normal sessions retry card mounting, run pending `MinUI.zip`/`*.pakz` installers,
then select these entry points in order:

1. `.system/h700/paks/MinUI.pak/launch.sh`, invoked through `/bin/sh`.
2. `System/slot`, invoked through `/lib/ld-linux-aarch64.so.1`.

These are the explicit launch paths in the session script. Other compatible
frontends must provide a supported entry point; there is no separate
spruceOS-name check. Only regular files on the mounted card qualify.

Slot uses `SLOT_ROOT=/mnt/sdcard` and that working directory. Its release is
extracted on a computer; BaseOS does not unpack Slot archives. Invoking the
loader supports copied binaries without an executable bit. Its optional
AGS-102 `ags-net` helper is not shipped by BaseOS.

Before the first handoff, the session performs a bounded wait for `/dev/mali0`
and records `/run/boot-frontend-exec`. The marker survives session respawns.
Missing cards, missing frontends and installer errors display a status pill;
see [boot splash](04-boot-splash.md).

## 5. Configuration and compatibility

`baseos-config` reads TF1's `baseos.conf` before network services start and
writes `/run/hostname`, `/run/hosts` and `/run/baseos.conf`. `/etc/hostname`
and `/etc/hosts` link to those generated files. See
[network identity](05-runtime-power-network.md#hostname-and-mdns).

- `systemctl` handles Bluetooth start, restart, stop and `is-active`.
  Unsupported `is-active` queries return 3; other unsupported operations succeed
  quietly. Starting Bluetooth ensures D-Bus is running first.
- `timedatectl` supports `show` for Timezone/NTP/NTPSynchronized,
  `set-timezone`, `set-ntp` and the boot-time `apply` operation. Preferences live
  in `/data/timezone` and `/data/ntp-enabled`; the runtime timezone link is
  `/run/localtime`. NTP defaults off and runs asynchronously when enabled.
- `/mnt/vendor/ctrl/setBluetooth.sh` supplies the vendor-compatible Bluetooth
  initialization interface.
- `poweroff` and `reboot` route through BaseOS shutdown wrappers; see
  [power handling](05-runtime-power-network.md).

`/etc/baseos-release` includes exact target, frontend family, model string,
panel rotation, version and build. The `dmenu.bin` model-string stub supports
frontends that detect hardware through the vendor path. Device profiles are
maintained in `devices.json`.

## 6. Logs

BaseOS boot, session, installer, update and adb diagnostics append to
`baseos-boot.log` at the root of the active frontend card. Early messages use
`/data/baseos-boot.log` until the card mounts; missing/unwritable cards and
USB-storage mode use the fallbacks described in [boot I/O](10-boot-io-audit.md).
Slot output remains in `/tmp/slot.log`, and NTP output in `/var/log/baseos-ntp.log`.
