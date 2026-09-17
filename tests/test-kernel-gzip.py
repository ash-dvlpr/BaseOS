#!/usr/bin/env python3
"""Artifact gates against prepared firmware; optional target arguments."""
import gzip
import json
from pathlib import Path
import struct
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import kernel_gzip as k
from device_profile import load_profiles

catalog = json.loads(k.CATALOG.read_text())
assert set(catalog["profiles"]) == {p["id"] for p in load_profiles()}
for target in sys.argv[1:] or catalog["profiles"]:
    prefix = ROOT / "work" / target / "boot-prefix.img"
    d = k.derive(target, prefix)
    size = struct.unpack_from("<I", d["boot"], 8)[0]
    assert gzip.decompress(d["boot"][2048:2048+size]) == d["kernel"]
    assert len(d["boot"]) < len(d["original_image"])
    assert d["expected_boot"][len(d["boot"]):] == d["original_boot"][len(d["boot"]):]
    # Corruption must be rejected against the unchanged trust anchor, without
    # touching the prepared source. A sparse file needs only the audited regions.
    p = d["profile"]
    with tempfile.TemporaryDirectory() as tmp:
        damaged = Path(tmp) / "prefix"
        with damaged.open("wb") as f:
            f.seek(p["package_offset"])
            f.write(d["original_package"])
            f.seek(p["boot_partition_start"]*512)
            f.write(d["original_boot"])
        for offset, error in ((p["boot_partition_start"]*512+2048, "boot partition"),
                              (p["package_offset"]+p["uboot_offset"]+p["selector_offset"], "TOC1")):
            with damaged.open("r+b") as f:
                f.seek(offset);previous=f.read(1);f.seek(offset);f.write(bytes([previous[0]^1]))
            try:
                k.derive(target, damaged)
            except ValueError as exc:
                assert error in str(exc), exc
            else:
                raise AssertionError("accepted corrupt firmware")
            with damaged.open("r+b") as f:
                f.seek(offset);f.write(previous)
    print(f"{target}: {len(d['original_image'])} -> {len(d['boot'])} bytes; corruption gates PASS")
