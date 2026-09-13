Base OS — add a frontend to this card
======================================

This card runs Base OS: a minimal Linux that boots your Allwinner H700
handheld and hands off to a frontend. Base OS itself ships no frontend, so
this data partition is empty and ready for you to add one.

To install NextUI:

  1. Copy NextUI's release files onto the root of this card:
        MinUI.zip
        (and any nextui.*.pakz files from the same release)
  2. Optionally add your Roms / Bios / Saves later — the installer creates
     those folders on first run.
  3. Put the card back in the handheld (TF1 slot) and power on.

Base OS detects MinUI.zip, runs NextUI's installer, and launches it. On every
boot after that it goes straight to NextUI in a few seconds.

To install Slot (a GBA frontend designed for the Anbernic RG SP):

  1. Extract Slot's H700 release on your computer.
  2. Copy the contents of its slot-<version> folder to the root of this card,
     so System/slot, System/mgba_libretro.so and System/gpsp_libretro.so
     are directly on the card alongside the other release folders.
  3. Add games to Games/ and an optional gba_bios.bin to BIOS/, then reboot.

Base OS launches Slot directly. No launch.sh or on-device Slot installer is
needed. To update Slot, replace System/ with that folder from a new release.

Both frontends can also live on a FAT32/exFAT card in TF2. A usable TF2 card
takes priority over this card's data partition. If both frontends are on the
selected card, NextUI wins; its MinUI.zip and *.pakz installers run first too.
Use a card without NextUI's launcher or installer files for a Slot-only setup.

Updating Base OS
----------------

You never need to reflash to move to a new Base OS version. Copy the release's
.bosupd file onto this card and power the handheld on. Base OS installs it to
its spare system slot, checks it, switches over and restarts — about a minute.

Your Roms, Bios, Saves and settings are not touched, and the previous version
stays on the card: if the new one cannot start, Base OS returns to it by
itself. You can leave the .bosupd file here; it is only ever applied once.

This card's whole capacity is available now — Base OS expanded it to fill the
card on first boot.

USB cable access
----------------

adb is available automatically over a data-capable USB-C cable.

To start writable USB-storage mode, hold MENU while powering on and keep it
held until the USB-storage message appears. With a second card inserted, Base
OS exports that complete card; otherwise it exports this card's data volume.
adb remains available at the same time.

Safely eject the disk on the computer, then restart without MENU to return to
normal. Never restart or unplug the cable while the computer is writing.
