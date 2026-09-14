#!/usr/bin/env python3
"""Exercise cache script gates with real manifests and tiny prepared archives."""

import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]


class PreparedCacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.prepared = self.root / "manifest/prepared"
        self.prepared.mkdir(parents=True)
        (self.root / "tools").mkdir()
        for filename in ("cache-pack.sh", "fetch-prepared.sh"):
            shutil.copy2(REPO / filename, self.root / filename)
        for filename in (
            "device_profile.py", "docker-platform.sh", "prepare_stock.py",
            "source_manifest.py", "verify_harvest.py",
        ):
            shutil.copy2(REPO / "tools" / filename, self.root / "tools" / filename)
        profiles = json.loads((REPO / "devices.json").read_text())
        profiles["targets"] = profiles["targets"][:1]
        self.target = profiles["targets"][0]["id"]
        (self.root / "devices.json").write_text(json.dumps(profiles))
        (self.root / "manifest/harvest.list").write_text("/usr/lib32/libmali.so.0\n")
        self.work = self.root / "work" / self.target
        self.work.mkdir(parents=True)
        self.payload = self.root / "payload"
        self.payload.mkdir()
        self.bundle = self.root / "bundle.tar.zst"
        self.bundle.write_bytes(b"synthetic bundle; Docker extraction is stubbed")
        checksum = hashlib.sha256(self.bundle.read_bytes()).hexdigest()
        (self.prepared / "bundle.sha256").write_text(f"{checksum}  {self.bundle.name}\n")
        (self.prepared / "bundle.url").write_text("https://example.invalid/bundle\n")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        # Isolate transport/compression; all source and harvest verification is real.
        self.executable(self.bin / "docker", """#!/bin/sh
set -eu
touch "$TEST_ROOT/docker-called"
cp "$TEST_ROOT/payload/boot-prefix.img" "$TEST_WORK/boot-prefix.img"
cp "$TEST_ROOT/payload/stock-harvest.tar" "$TEST_WORK/stock-harvest.tar"
""")
        self.executable(self.root / "verify-target.sh", "#!/bin/sh\nexit 0\n")
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}",
                        TEST_ROOT=str(self.root), TEST_WORK=str(self.work))

    @staticmethod
    def executable(path, content):
        path.write_text(content)
        path.chmod(0o755)

    def make_payload(self, complete):
        (self.payload / "boot-prefix.img").write_bytes(b"boot prefix")
        with tarfile.open(self.payload / "stock-harvest.tar", "w") as archive:
            if complete:
                member = tarfile.TarInfo("usr/lib32/libmali.so.0")
                member.size = 1
                archive.addfile(member, io.BytesIO(b"x"))
        record = {"schema": 1, "provenance": "stockmod-image", "target": self.target,
                  "layout": {"partitions": [{"name": "special"}]}}
        for filename, key in (("boot-prefix.img", "boot_prefix"),
                              ("stock-harvest.tar", "harvest")):
            data = (self.payload / filename).read_bytes()
            record[key] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        (self.prepared / f"{self.target}.json").write_text(json.dumps(record))

    def install_payload(self):
        for filename in ("boot-prefix.img", "stock-harvest.tar"):
            shutil.copy2(self.payload / filename, self.work / filename)
        shutil.copy2(self.prepared / f"{self.target}.json", self.work / "source.json")

    def run_script(self, script, *args, **environment):
        return subprocess.run([str(self.root / script), *args], env=self.env | environment,
                              text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def test_current_complete_cache_is_a_noop(self):
        self.make_payload(complete=True)
        self.install_payload()
        result = self.run_script("fetch-prepared.sh", self.target)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("up to date:", result.stdout)
        self.assertFalse((self.root / "docker-called").exists())

    def test_matching_hashes_cannot_hide_incomplete_harvest(self):
        self.make_payload(complete=False)
        self.install_payload()
        result = self.run_script("fetch-prepared.sh", "--from", str(self.bundle), self.target)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("up to date:", result.stdout)
        self.assertTrue((self.root / "docker-called").exists())
        self.assertIn("missing required paths", result.stdout)
        self.assertIn("libmali.so.0", result.stdout)

    def test_restored_complete_harvest_passes(self):
        self.make_payload(complete=True)
        (self.work / "rootfs.tar").write_bytes(b"stale rootfs")
        result = self.run_script("fetch-prepared.sh", "--from", str(self.bundle), self.target)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("harvest paths OK:", result.stdout)
        self.assertFalse((self.work / "rootfs.tar").exists())

    def test_pack_rejects_incomplete_harvest_with_or_without_full_gates(self):
        self.make_payload(complete=False)
        self.install_payload()
        for skip in ("0", "1"):
            with self.subTest(skip_verify=skip):
                result = self.run_script("cache-pack.sh", "20260914", BASEOS_SKIP_VERIFY=skip)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("missing required paths", result.stdout)
                self.assertFalse((self.root / "docker-called").exists())


if __name__ == "__main__":
    unittest.main()
