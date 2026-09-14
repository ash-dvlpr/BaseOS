# 05 — Runtime: boot timing, power/sleep, network

## 1. Boot timing

`/run/boot-frontend-exec` records kernel uptime at the first frontend handoff.
See [boot performance](09-boot-profiling.md) for measurement instructions and
the acceptance ceiling.

`rcS` loads `mali_kbase.ko` in the background to overlap storage and service
startup. `nextui-session` performs a bounded wait for `/dev/mali0` before
handoff. Ordinary boots leave the bootloader logo untouched until the frontend
renders its first frame.

## 2. Deep sleep

The frontend enters suspend-to-RAM by writing `mem` to `/sys/power/state`.
`alsactl` saves and restores the mixer across sleep.

BaseOS applies the stock Super Standby value (`16`) when the SP-only
`axp2202-battery/os_sleep` attribute exists; NextUI also applies it before
suspend. This stops USB host controllers and excludes the hall sensor from
the wake set: POWER wakes the device, while opening the lid alone does not.
Non-SP kernels do not expose `os_sleep` and use the full USB-stop path.

For standby measurements, `diagnostics/sleep-drain/` provides
`pre-sleep.d`/`post-resume.d` hooks that record the battery charge counter and
system timestamps in `/mnt/sdcard/sleep-drain.log`. Use a long sleep interval because
the counter has coarse resolution. Hook filenames must end in `.sh`.

## 3. Wi-Fi

BaseOS owns interface startup. In a background task, `rcS` first asks the
vendor WLAN controller to discard any SDIO card enumerated before its supply
was turned off by the kernel's unused-regulator cleanup. It waits up to one
second for that stale card to disappear before loading `8821cs.ko`. Without
this ordering, H700 devices can fail the driver probe with `-123` and never create
`wlan0`; merely delaying module insertion does not prevent the failed probe.
Already powered radios and kernels without these controls skip this step.

The same background task waits for `wlan0`, then unblocks radio and brings
the interface up. Neither wait delays frontend handoff. Frontends manage
network credentials, association and DHCP, and must wait if they start before
the interface appears.

The `systemctl` compatibility shim implements a synchronous
`stop wpa_supplicant` (including interface service names), with a bounded
two-second wait. This lets NextUI's existing restart script wait for the old
daemon's control-socket cleanup before launching its replacement. When no
supplicant is running, the command returns immediately.

Driver power saving uses the stock default, `rtw_power_mgnt=2`. This can add
latency to intermittent traffic. DHCP uses BusyBox `udhcpc`; its event script
writes `/run/resolv.conf`, linked from `/etc/resolv.conf`.

### Hostname and mDNS

`baseos-config` reads `baseos.conf` from TF1's visible partition, even when
TF2 holds the frontend. It recognizes `hostname` and `mdns`; defaults are the
lowercase device model ID and `true`. Hostnames accept 1–63 ASCII letters,
digits or hyphens, without a leading/trailing hyphen. Missing or invalid values
use defaults; unknown keys are ignored. Settings are data, never shell code.

The file is read once during normal boot. USB-storage mode uses defaults
without opening the exported card. See [README settings](../README.md#settings)
for editing instructions.

After Wi-Fi receives an IPv4 address, the DHCP hook starts the static Avahi
responder asynchronously through `baseos-mdns`. It publishes `<hostname>.local`
on `wlan0`, with no service advertisements or D-Bus dependency. Renewals reuse
the daemon; DHCP deconfiguration reconciles it with current interface state.
Avahi handles later interface changes without an additional polling process.

Name collisions receive an mDNS suffix, such as `rg34xxsp-2.local`, without
changing the configured hostname. Set distinct hostnames for stable addresses.
`mdns=false` disables the responder.

## 4. Bluetooth audio

`rtk_hciattach` attaches the controller UART. The `systemctl` shim starts D-Bus
and BlueZ on demand; BlueALSA supplies the audio service. Frontend scripts
configure pairing and audio through the vendor-compatible shims. Radio files
are omitted from the harvest for profiles without Bluetooth.

See [validation coverage](06-status-and-lessons.md) for hardware checks.

## 5. Power button and shutdown

BaseOS provides `poweroff` and `reboot` wrappers. `baseos-poweroff` creates
`/run/poweroff-requested`; `baseos-reboot` clears it. Both then invoke BusyBox
to request shutdown through init.

`rcK` stops other processes, saves entropy, disables swap and flushes writes.
For power-off it calls `axp-off --now`, which requires persistent filesystems
to be read-only before cutting processor power through the PMIC. Reboot skips
the PMIC cut. If the PMIC helper refuses, shutdown falls back to BusyBox's
kernel path after filesystem cleanup.

Frontends should use the BaseOS commands so shutdown intent and cleanup are
preserved. Direct BusyBox applet calls or frontend-specific helpers can bypass
that integration. A long power-button press forces hardware power-off.

Charger-origin boots use the early charging path before normal services start;
see [charger-only boot](11-charger-only-boot.md).

## 6. USB access

BaseOS starts USB-only adb in the background through `/etc/init.d/dev`.
Connect a data cable before power-on; after disconnecting, restart with the
cable connected. `/data/no-adb` disables adb.

Holding MENU during startup selects exclusive USB-storage maintenance mode.
It exports whole TF2 when present, otherwise TF1's data partition, and keeps
the frontend stopped until restart. Eject the drive on the host before
restarting without MENU.

See [USB adb, mass storage and H700 OTG](08-usb-adb-and-otg.md) for gadget
configuration, role handling, storage safety and validation.
