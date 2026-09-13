# Charger shutdown follow-up to PR #14

PR #14 was merged with its original author commits intact. This follow-up keeps
its PMIC shutdown mechanism, device discovery, power-intent shims, MENU bypass,
and persistent opt-out. It changes the filesystem gate, register reads, and
the outcome of an unsuccessful automatic shutdown.

## Changes and checks

- `axp-off` reads register `0x27` with one combined I2C transfer. Its tests reject
  unsupported adapters and short transfers, verify preservation of unrelated
  bits, and require a settling delay even after an ambiguous write error.
- `rcK` stops userspace writers before a PMIC cut. The binary remounts every
  persistent filesystem read-only, including the vendor's writable rootfs,
  and rereads the mount table. Simulated busy root/data/card mounts and a
  falsely successful remount all prevent the power write. Active swap and
  exported USB storage also prevent a direct cut.
- Charger handling runs before GPU loading and normal boot services. The
  behavioral tests run the real shell scripts under BusyBox in a disposable
  chroot, with mount, process-signal, hardware and power commands replaced.
  They cover normal boot, exact command-line matching, unavailable boot-mode
  sysfs, failed PMIC shutdown/probe, a 30-second replug, future/expired RTC
  timestamps, bad clock/data, read-only data, and input-helper failure.
- MENU remains a storage-mode request after the button is released. A
  persistent opt-out starts normally. Reboot never invokes a PMIC cut.
- The exceptional charging wait blanks the display and selects `powersave`.
  A blocking evdev reader requires a fresh POWER hold and release. Pipe-based
  tests exercise real `poll()`/`read()` behavior, rejecting queued holds, short
  taps and dropped event sequences. Display ioctl tests check brightness
  save/blank/restore. The shell tests check restoration of CPU policy and
  generic sysfs brightness before normal startup.

Commands run:

```sh
./build-tools.sh
./tests/test-axp-off.sh
./tests/test-poweroff-policy.sh
./tests/test-power-intent.sh
./tests/test-boot-menu-held.sh
./tests/test-usb-storage-mode.sh
./build-rootfs.sh rg34xxsp
./test-boot-qemu.sh rg34xxsp
```

The C regression harnesses and final ARM64 helpers compile with
`-O2 -Wall -Wextra -Werror`. The rootfs dependency-closure check passes.
The shared tool source stamp matches the final binaries. QEMU reached
`BASEOS-USERSPACE-BOOT-OK-rg34xxsp`: BusyBox init, inittab, and rcS completed.

## Read-only RG34XXSP observations

The connected device was already running a normal frontend session. Temporary
probe binaries were copied to `/tmp`; neither was invoked in a power-changing
mode. No shutdown/reboot, brightness change, or CPU-policy change was performed.

- Latched `boot_mode`: `0`.
- PMIC discovery: `axp20x-i2c/5-0034`, name `axp2202`, address `0x34`.
- The combined-I2C probe succeeded: `REG27H 0x08 -> 0x09`, **not armed**.
- `/` was ext4 mounted `rw,noatime,nodiratime,nobarrier,noauto_da_alloc,data=ordered`.
- The available CPU governors include `powersave` and `performance`.
- `/sys/class/backlight` was absent. The Allwinner disp2
  `DISP_LCD_GET_BRIGHTNESS` (`0x103`) ioctl on `/dev/disp` returned `28`.
  The brightness ABI is documented in the
  [Allwinner display header](https://github.com/allwinner-zh/linux-3.4-sunxi/blob/master/include/video/sunxi_display2.h);
  the H700 frontend already uses the matching set ioctl (`0x102`).

## Physical validation still needed

These checks do not measure charging current or demonstrate actual power cuts
with the follow-up. The original contributor's RG SP shutdown measurements
remain in the August 18 report; they are not new measurements of this version.

On hardware, check a cold USB insertion, a replug within 120 seconds, POWER
hold/release in the exceptional wait, MENU storage recovery, explicit shutdown
under a writable-file workload, and reboot with USB attached. Measure both
PMIC-off and fallback current, plus repeated RG40XX V normal boots against the
existing 3.00-second `boot-frontend-exec` ceiling. The fallback deliberately
avoids suspend-to-RAM because USB attach/detach wake is not established on the
SP vendor driver.
