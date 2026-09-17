# What is BaseOS?

BaseOS is a minimal but feature-complete operating system for Anbernic RG XX
handhelds.

If you're tired of slow boot times, high battery consumption, or less than ideal
support for hardware features on other custom firmwares, BaseOS is for you.

It is designed as a drop-in replacement for the stock OS. However, BaseOS does
not have a user interface of its own. It will boot up as fast as possible, then
hand off to your frontend of choice.

BaseOS can auto-detect and start [NextUI](https://nextui.loveretro.games),
[Slot](https://slot.kowalski.io) and [spruceOS](https://spruceui.github.io).

[Install BaseOS](https://github.com/pvaibhav/BaseOS/wiki/BaseOS-Install-Guide)

## Boot duration

BaseOS currently boots in **2.25 seconds** as of v1.2.0, down from **2.99
seconds** in v1.1.0. We have a hard limit of 2.5 sec, and every change is
regression tested against this.

Your frontend adds its own startup time. For example, NextUI adds about **4.5
seconds**, bringing the total to approximately **6.75 seconds**.

BaseOS timing is measured from kernel start to frontend handoff, excluding
bootloader time.

## What BaseOS provides

- The fastest possible boot time for Anbernic RG XX devices.
- Lowest possible resource and battery usage.
- Takes 5 sec to install.
- Full support for the handheld's display, sound, controls, networking, HDMI,
  LEDs, deep sleep and other features. No compromise on that front.
- First-boot expansion of the data partition to fill the SD card.
- Easy updates: copy one file onto the card and reboot. No reflashing, and your
  ROMs, saves and settings are untouched.
- SSH/SFTP over Wi-Fi and adb over USB active by default.
- USB storage mode (hold MENU when powering on).

## Installation

Follow
**[installation guide](https://github.com/pvaibhav/BaseOS/wiki/BaseOS-Install-Guide)**
for flashing, first boot, and NextUI setup on one-card or two-card
configurations.

To boot Slot, extract its H700 release on your computer and copy the contents of
the extracted `slot-<version>` folder to the card root. Use either BaseOS's
visible data partition on TF1 or a FAT32/exFAT card in TF2; a usable TF2 card
takes priority. The card must contain `System/slot`, `System/mgba_libretro.so`
and `System/gpsp_libretro.so`, alongside the release's other folders. Put GBA
games in `Games/` and the optional BIOS in `BIOS/`. BaseOS boots Slot directly;
it does not install a Slot zip or require a `launch.sh`.

To boot spruceOS, copy its extracted H700 release onto the card root so it
contains `spruce/scripts/runtime.sh` alongside the rest of the release.
BaseOS launches that script through `/bin/sh`; `/mnt/SDCARD` points to the
selected TF2 or TF1 data card. No NextUI-compatible launcher is required.

For a generic frontend, provide `System/launch_frontend.sh` or a glibc AArch64
binary at `System/frontend`. The binary takes priority and uses BaseOS's
system loader; the script runs through `/bin/sh`. Both run from the
card root, need no executable bit, and log output to `/tmp/generic.log`.

When multiple frontends are installed, the priority is Generic, Slot, NextUI,
then spruceOS. NextUI's pending `MinUI.zip` and `*.pakz` installers run before
frontend selection. To select a lower-priority frontend, remove the launcher
files for higher-priority frontends from the card. Update Slot by replacing
its `System` folder with the one from a new release.

## Settings

Edit `baseos.conf` at the root of TF1's visible partition, then restart normally
to apply changes. TF1 supplies these settings even when your frontend is on TF2.

```ini
hostname=my-handheld
mdns=true
```

- `hostname`: the device's network name. Defaults to its model ID, such as
  `rg34xxsp`. Use 1–63 letters, digits or hyphens, with no hyphen at either end.
- `mdns`: enables `<hostname>.local` access over Wi-Fi (for example,
  `my-handheld.local`). Defaults to `true`; set `false` to disable it.

- `headphone_pop_fix`: headphone exit-pop workaround on supported kernels.
  Defaults to `false`; set `headphone_pop_fix=true` to enable it and restart.
  Keeps analogue output buffers powered between streams, which may increase
  awake/screen-off idle consumption. Buffers are disabled before deep sleep.
  The speaker fix remains enabled independently. Each image includes its
  matching audio module; see [runtime audio support](docs/05-runtime-power-network.md#speaker-and-headphone-pop-repair).

Omitted settings use their defaults. Lines starting with `#` are comments.

SSH password: set `ssh_password=your-password` in baseos.conf and reboot.
It defaults to `root` when omitted or empty. The setting survives OS updates
because it stays on TF1. The password is plain text on the card; do not reuse
an important password. Values are unquoted, surrounding whitespace is trimmed,
and `#` is literal in passwords (no inline comments on this key).

## Supported devices

- Anbernic RG28XX
- Anbernic RG34XX
- Anbernic RG34XX SP
- Anbernic RG35XX Plus and RG35XX 2024
- Anbernic RG35XX H
- Anbernic RG35XX Pro
- Anbernic RG35XX SP
- Anbernic RG40XX H
- Anbernic RG40XX V
- Anbernic RG CubeXX
- Anbernic RG SP

Development setup, build instructions, testing, and technical documentation are
in **[CONTRIBUTING.md](CONTRIBUTING.md)**.

## USB access

For reliable adb, connect a data-capable USB-C cable before powering on. If it
is disconnected, restart with the cable connected.

For USB mass storage, press and hold the MENU key while plugging in the USB
cable. If your computer provides power, your handheld will start in mass storage
mode. If it doesn't start, press and hold power for 3-4 sec. Let go when it
start, but *keep pressing the MENU button* till you finally see "USB STORAGE:
EJECT BEFORE RESTART" on the screen. Then you can let go.

NOTE: Eject the drive on the computer before restarting the handheld. Restart
without holding MENU to return to normal.

## How does it work?

BaseOS uses the stock Anbernic bootloader, kernel and hardware drivers for
H700-based handhelds, with a minimal BusyBox-based system in place of the stock
Ubuntu userland. It provides the hardware support and services your frontend
needs with minimal background activity.

The current version is generally based on the latest stock/stockmod OS release.
