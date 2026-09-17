#!/usr/bin/env python3
"""Run inside Alpine: file-backed fault tests of the actual BusyBox migrator."""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "overlay/usr/sbin/baseos-boot-migrate"


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Migration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.assets = self.root / "usr/share/baseos/boot-gzip"
        self.state = self.root / "data/boot-gzip-v1"
        for name in ("dev", "etc", "run", "tmp", "bin", "sys/class/block/mmcblk0p4",
                     "sys/class/power_supply/battery", str(self.assets.relative_to(self.root)),
                     str(self.state.relative_to(self.root))):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        (self.root / ".boot-migration-test").touch()
        (self.root / "etc/baseos-release").write_text("BASEOS_TARGET=rgsp\n")
        self.original = b"original kernel!" * 1024  # 16 KiB partition
        self.package = b"original package" * 32
        self.new_image = b"gzip kernel!" * 512
        self.new_package = b"gzip bootloader!" * 32
        self.expected = self.new_image + self.original[len(self.new_image):]
        self.boot = self.root / "dev/mmcblk0p4"
        self.disk = self.root / "dev/mmcblk0"
        self.boot.write_bytes(self.original)
        self.disk.write_bytes(b"P" * 512 + self.package + b"S" * 512)
        (self.assets / "boot.img").write_bytes(self.new_image)
        (self.assets / "boot-package.bin").write_bytes(self.new_package)
        (self.root / "sys/class/block/mmcblk0p4/start").write_text("4096\n")
        (self.root / "sys/class/block/mmcblk0p4/size").write_text("32\n")
        (self.root / "sys/class/power_supply/battery/capacity").write_text("100\n")
        fields = dict(TARGET="rgsp", PACKAGE_OFFSET=512, PACKAGE_SIZE=512,
                      BOOT_START=4096, BOOT_SECTORS=32, PACKAGE_SHA256=sha(self.new_package),
                      ORIGINAL_PACKAGE_SHA256=sha(self.package), ORIGINAL_BOOT_SHA256=sha(self.original),
                      BOOT_IMAGE_SHA256=sha(self.new_image), BOOT_PARTITION_SHA256=sha(self.expected))
        (self.assets / "manifest").write_text("".join(f"BOOT_{k}={v}\n" for k, v in fields.items()))
        self.env = dict(os.environ, BASEOS_BOOT_TESTING="1", BASEOS_BOOT_TEST_ROOT=str(self.root),
                        PATH=f"{self.root}/bin:/usr/sbin:/usr/bin:/sbin:/bin")

    def run_migration(self, expected=0):
        result = subprocess.run(["/bin/busybox", "sh", str(SCRIPT)], env=self.env,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)

    def marker(self):
        return (self.state / "complete").read_text().strip()

    def assert_original(self):
        self.assertEqual(self.boot.read_bytes(), self.original)
        self.assertEqual(self.disk.read_bytes(), b"P" * 512 + self.package + b"S" * 512)
        self.assertFalse((self.state / "test-reboot-requested").exists())

    def inject(self, body):
        wrapper = self.root / "bin/dd"
        wrapper.write_text('#!/bin/sh\n' + body + '\nexec /bin/busybox dd "$@"\n')
        wrapper.chmod(0o755)

    def test_install_and_completed_path(self):
        self.run_migration()
        self.assertEqual(self.marker(), "installed")
        self.assertEqual(self.boot.read_bytes(), self.expected)
        self.assertEqual(self.disk.read_bytes(), b"P" * 512 + self.new_package + b"S" * 512)
        self.assertEqual((self.state / "boot-original.bin").read_bytes(), self.original)
        self.assertEqual((self.state / "package-original.bin").read_bytes(), self.package)
        self.assertFalse((self.state / "test-reboot-requested").exists())
        self.assertIn("continuing startup", (self.state / "migration.log").read_text())
        # Completed path must work with zero external utilities available.
        self.env["PATH"] = "/nonexistent"
        self.run_migration()

    def test_fresh_image(self):
        self.boot.write_bytes(self.expected)
        self.disk.write_bytes(b"P" * 512 + self.new_package + b"S" * 512)
        self.run_migration()
        self.assertEqual(self.marker(), "already-installed")
        self.assertFalse((self.state / "boot-original.bin").exists())
        self.assertFalse((self.state / "test-reboot-requested").exists())

    def test_unknown_firmware(self):
        self.disk.write_bytes(b"?" * 1536)
        self.run_migration()
        self.assertIn("firmware differs", self.marker())
        self.assertEqual(self.disk.read_bytes(), b"?" * 1536)
        self.assertEqual(self.boot.read_bytes(), self.original)

    def test_target_and_geometry(self):
        for path, content in (("etc/baseos-release", "BASEOS_TARGET=rg28xx\n"),
                              ("sys/class/block/mmcblk0p4/start", "4097\n")):
            file = self.root / path
            previous = file.read_text()
            file.write_text(content)
            self.run_migration()
            self.assertTrue(self.marker().startswith("skipped:"))
            self.assert_original()
            file.write_text(previous)
            (self.state / "complete").unlink()

    def test_corrupt_assets(self):
        (self.assets / "boot.img").write_bytes(b"broken")
        self.run_migration()
        self.assertIn("assets failed", self.marker())
        self.assert_original()

    def test_low_battery_and_storage_defer(self):
        battery = self.root / "sys/class/power_supply/battery/capacity"
        battery.write_text("10\n")
        self.run_migration()
        self.assertFalse((self.state / "complete").exists())
        self.assert_original()
        battery.write_text("100\n")
        (self.root / "run/usb-storage-device").write_text("/dev/mmcblk1\n")
        self.run_migration()
        self.assertFalse((self.state / "complete").exists())
        self.assert_original()

    def test_external_power_allows_low_battery(self):
        (self.root / "sys/class/power_supply/battery/capacity").write_text("10\n")
        usb = self.root / "sys/class/power_supply/usb"
        usb.mkdir()
        (usb / "online").write_text("1\n")
        self.run_migration()
        self.assertEqual(self.marker(), "installed")

    def test_real_path_refuses_nonpersistent_userdata(self):
        # Disposable container has no /dev/mmcblk0p6 mounted at /data. Exercise
        # the production guard (without TEST_ROOT), before any block access.
        assets = Path("/usr/share/baseos/boot-gzip")
        self.assertFalse(assets.exists())
        assets.mkdir(parents=True)
        manifest = assets / "manifest"
        manifest.touch()
        self.addCleanup(assets.rmdir)
        self.addCleanup(manifest.unlink)
        env = dict(self.env)
        env.pop("BASEOS_BOOT_TEST_ROOT")
        result = subprocess.run(["/bin/busybox", "sh", str(SCRIPT)], env=env,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertIn("persistent writable userdata is unavailable", result.stderr)
        self.assertFalse(Path("/data/boot-gzip-v1").exists())

    def test_bad_backup(self):
        (self.state / "boot-original.bin").write_bytes(b"corrupt")
        self.run_migration()
        self.assertIn("backups", self.marker())
        self.assert_original()

    def test_no_backup_space(self):
        wrapper = self.root / "bin/df"
        wrapper.write_text('#!/bin/sh\necho "disk 100 99 1 99% /data"\n')
        wrapper.chmod(0o755)
        self.run_migration()
        self.assertIn("insufficient", self.marker())
        self.assert_original()

    def test_package_write_failure_restores(self):
        self.inject('case "$*" in *"/boot-package.bin"*) exit 1 ;; esac')
        self.run_migration()
        self.assertEqual(self.marker(), "rolled-back")
        self.assert_original()

    def test_bad_readback_restores(self):
        self.inject('''case "$*" in *"/boot-package.bin"*)
 /bin/busybox dd "$@" || exit 1
 printf X | /bin/busybox dd of="$BASEOS_BOOT_TEST_ROOT/dev/mmcblk0p4" conv=notrunc 2>/dev/null
 exit 0 ;; esac''')
        self.run_migration()
        self.assertEqual(self.marker(), "rolled-back")
        self.assert_original()

    def test_restore_failure_blocks_reboot(self):
        self.inject('for arg do case "$arg" in if=*/boot-package.bin|if=*/boot-original.bin) exit 1 ;; esac; done')
        self.run_migration(expected=1)
        self.assertFalse((self.state / "complete").exists())
        self.assertFalse((self.state / "test-reboot-requested").exists())
        self.assertIn("RESTORE FAILED", (self.state / "migration.log").read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
