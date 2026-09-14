# 08 — USB adb, mass storage, and H700 OTG

`/usr/sbin/usb-gadget-adb` provides cable-based USB access on H700 devices.

## Supported behavior

BaseOS enables USB-only adb by default alongside BaseOS's SSH/SFTP service. For reliable adb,
the data cable must be connected before the handheld is powered on. If the cable is
disconnected, restart with it connected. BaseOS does not run a reconnect watcher,
poll the port, force a USB role, or modify the vendor DTB.

A BaseOS-owned maintenance boot exposes user storage as writable
USB mass storage. Connect the cable, then hold MENU from power-on. A one-shot state
query selects the mode before frontend storage is mounted. Whole TF2 wins when
present; otherwise TF1 p7 is exported. The frontend is not involved.

This keeps the only USB-C port available for intentional OTG host devices on normal
boots while making the two supported peripheral workflows explicit and predictable.
`/data/no-adb` remains the persistent adb opt-out. A cable-only startup may
enter charger handling; use POWER for normal startup or hold MENU for storage.
See [charger-only boot](11-charger-only-boot.md).

## Verified H700 hardware contract

The RG40XXV uses the vendor Linux 4.9.170 kernel. Its relevant built-in facilities
are:

- configfs USB gadgets and FunctionFS (`CONFIG_USB_CONFIGFS_F_FS=y`);
- mass-storage configfs support (`CONFIG_USB_CONFIGFS_MASS_STORAGE=y`);
- UDC `5100000.udc-controller`;
- sunxi OTG manager at `/sys/devices/platform/soc/usbc0`, also exposed through
  `/sys/bus/platform/devices/usbc0`;
- dual-role USB-C wiring with PMU VBUS detection and no useful ID GPIO.

The prepared H700 targets configure the sunxi manager as dual-role OTG. BaseOS
preserves that policy. The DTB inside Android
boot partition p4 is not the tree Linux receives; the live tree comes from the
checksummed `sunxi-package` beside U-Boot. Neither copy is changed.

The safe runtime rule is to bind the configfs gadget to the always-present UDC and
leave the sunxi manager in auto mode. Its role-control sysfs files must not be used:

- writing `usbc0/otg_role` wedges the writer permanently in uninterruptible D-state;
- `usb_device`, `usb_host`, and `usb_null` are read-trigger controls, not status
  files, and merely reading them can switch or wedge the port;
- reading `otg_role` itself is safe for diagnosis, but BaseOS does not need it for
  normal operation.

With the cable present during power-on, the manager reliably selects peripheral
mode. A later attach can instead select host mode and source VBUS to the computer;
userspace cannot safely override that kernel decision.

## Gadget and daemon

The configfs gadget contains one FunctionFS adb interface (`ff/42/01`) with Google
VID/PID `18d1:4e42`. A pinned, static Android 4.2.2 `adbd` is built with the Debian
and Buildroot non-Android patches plus two local policies:

- stay root, matching the current root/root development-access posture;
- remove the TCP 5555 transport fallback, leaving USB as the only externally
  reachable transport.

The daemon still owns its private smart socket on `127.0.0.1:5037`, so
`usb-gadget-adb` brings up loopback before launching it. FunctionFS cannot bind until
`adbd` has supplied its descriptors, so the script performs one bounded initial UDC
bind loop. That loop handles boot ordering; it is not reconnect machinery.

## Disconnect and reconnect

On VBUS loss the vendor manager clears `usb_gadget/g1/UDC`. Rebinding cannot
reliably restore access because a later attach may select host role. Restart
with the cable connected to restore peripheral access. BaseOS preserves stock
OTG support and does not run a resident reconnect watcher.

Configfs attribute sizes are synthetic; inspect the contents of `g1/UDC` to
check whether the gadget is bound.

## Mass-storage maintenance mode

The gadget supports adb and writable mass storage together. Root adb remains
usable during storage mode unless disabled with `/data/no-adb`.

The real frontend volume cannot be shared live: BaseOS normally mounts and executes
the frontend from it, and simultaneous writable access by Linux and a USB host risks
filesystem corruption. Storage mode is therefore an exclusive maintenance boot:

1. connect the USB cable, then hold MENU from power-on;
2. `boot-menu-held` reads the DT-labelled physical GPIO level because the vendor
   driver advertises `BTN_MODE` but does not maintain the evdev current-state bitmap;
3. before mounting frontend storage, choose whole TF2 when present, otherwise TF1 p7;
4. publish only an existing, completely unmounted block device to configfs;
5. keep the frontend stopped while the host owns the device;
6. eject on the host and restart without MENU.

Exporting whole TF2 exposes its real partition table, so the host can access every
filesystem it supports and deliberately repartition or format the removable card.
TF1 remains partition-backed because the running root and `/data` make whole-TF1
export unsafe without a separate RAM-root recovery system.

Every unsafe state fails closed. A missing block device does nothing, and both the
selector and gadget reject a whole device when it or any `pN` child remains mounted.
`/data/no-adb` removes FunctionFS but leaves requested mass storage available.

## Validation

Automated checks cover gadget composition, repeat setup, missing UDC, composite
ADB+storage and storage-only layouts, storage selection, mounted-child rejection, and
the production AArch64 build. `validate-on-device.sh` checks `adbd`, FunctionFS, the
bound UDC, and absence of a TCP 5555 listener.

RG40XX V hardware validation covers cable-connected startup, root shell and
checksum-matched file transfer, and TF1 export to macOS with eject/restart.
Whole-card TF2 behavior still needs real-card validation for enumeration,
write/eject/restart and partition-table operations.
