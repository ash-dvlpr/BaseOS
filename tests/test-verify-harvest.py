#!/usr/bin/env python3
"""Prepared-cache regressions: manifest additions and device-specific omissions."""

import io
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from verify_harvest import verify_harvest


class VerifyHarvestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = self.root / "stock-harvest.tar"
        self.manifest = self.root / "harvest.list"
        self.profile = {"id": "rg28xx", "wifi": False, "bluetooth": False}

    def archive_paths(self, *paths):
        with tarfile.open(self.archive, "w") as output:
            for path in paths:
                member = tarfile.TarInfo("./" + path.lstrip("/"))
                member.size = 1
                output.addfile(member, io.BytesIO(b"x"))

    def verify(self):
        verify_harvest(self.archive, self.manifest, self.profile)

    def test_old_cache_rejects_new_required_library(self):
        self.archive_paths("/usr/lib/arm-linux-gnueabihf/libc.so.6")
        self.manifest.write_text("/usr/lib/arm-linux-gnueabihf/libc.so.6\n")
        self.verify()
        with self.manifest.open("a") as manifest:
            manifest.write("/usr/lib32/libmali.so.0\n")
        with self.assertRaisesRegex(ValueError, r"libmali.so.0.*prepare-stock.sh rg28xx"):
            self.verify()

    def test_hardware_omissions_do_not_hide_unconditional_armhf(self):
        self.archive_paths("/usr/lib/ld-linux-armhf.so.3")
        self.manifest.write_text(
            "## ARMHF runtime\n/usr/lib/ld-linux-armhf.so.3\n"
            "## WiFi\n/usr/sbin/wpa_supplicant\n"
            "## Bluetooth audio stack\n/usr/bin/rtk_hciattach\n"
            "## kernel modules\n/usr/lib/modules/8821cs.ko\n"
        )
        self.verify()
        self.profile["bluetooth"] = True
        with self.assertRaisesRegex(ValueError, "rtk_hciattach"):
            self.verify()
        self.profile["bluetooth"] = False
        self.archive_paths()
        with self.assertRaisesRegex(ValueError, "ld-linux-armhf"):
            self.verify()

    def test_fat_checker_remains_required_without_wifi(self):
        self.archive_paths()
        self.manifest.write_text("## WiFi\n/usr/sbin/fsck.fat\n")
        with self.assertRaisesRegex(ValueError, "fsck.fat"):
            self.verify()


if __name__ == "__main__":
    unittest.main()
