#!/usr/bin/env python3
"""Contract tests for boot repacking, archive closure, and fsck recovery."""

import gzip
from pathlib import Path
import random
import re
import stat
import struct
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from minimal_initramfs import (BootImage, Entry, ROOT, align, baseline_init,
                               elf_dependencies, minimal_entries, read_cpio,
                               resolve, write_cpio)


def entry(name, data=b"", mode=stat.S_IFREG | 0o755):
    fields = [1, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(name) + 1, 0]
    return Entry(name, fields, data)


def elf(needed=(), interpreter=None, rpath=None):
    """A small ELF with real program/dynamic tables, no host ldd required."""
    out = bytearray(2048)
    out[:7] = b"\x7fELF\x02\x01\x01"
    struct.pack_into("<HHI", out, 16, 3, 183, 1)
    struct.pack_into("<Q", out, 32, 64)
    struct.pack_into("<HHH", out, 52, 64, 56, 3 if interpreter else 2)
    struct.pack_into("<IIQQQQQQ", out, 64, 1, 5, 0, 0x400000, 0, len(out), len(out), 4096)
    strings = bytearray(b"\0")
    tags = [(5, 0x400000 + 1024)]
    for name in needed:
        tags.append((1, len(strings)))
        strings.extend(name.encode() + b"\0")
    if rpath is not None:
        tags.append((15, len(strings)))
        strings.extend(rpath.encode() + b"\0")
    tags.append((0, 0))
    struct.pack_into("<IIQQQQQQ", out, 120, 2, 6, 512, 0x400000 + 512, 0, len(tags) * 16, len(tags) * 16, 8)
    for index, tag in enumerate(tags):
        struct.pack_into("<qQ", out, 512 + index * 16, *tag)
    out[1024:1024 + len(strings)] = strings
    if interpreter:
        encoded = interpreter.encode() + b"\0"
        struct.pack_into("<IIQQQQQQ", out, 176, 3, 4, 1800, 0x400000 + 1800, 0, len(encoded), len(encoded), 1)
        out[1800:1800 + len(encoded)] = encoded
    return bytes(out)


def boot_fixture(version=2):
    page = 2048
    kernel = bytearray(b"K" * 4097)
    kernel[56:60] = b"ARMd"
    parts = {"kernel": bytes(kernel), "ramdisk": gzip.compress(random.Random(0).randbytes(10000), mtime=0), "second": b"SECOND" * 17}
    if version >= 1:
        parts["recovery_dtbo"] = b"RECOVERY" * 19
    if version >= 2:
        parts["dtb"] = b"\xd0\x0d\xfe\xed" + struct.pack(">I", 64) + bytes(56)
    header = bytearray(page)
    header[:8] = b"ANDROID!"
    struct.pack_into("<10I", header, 8, len(kernel), 0x40080000, len(parts["ramdisk"]), 0x42000000, len(parts["second"]), 0x41000000, 0x40000100, page, version, 0)
    header[48:64] = b"target-name".ljust(16, b"\0")
    cmdline = b"preserve cmdline!!!\0"
    extra_cmdline = b"extra cmdline!!!!\0\0"
    header[64:64 + len(cmdline)] = cmdline
    header[608:608 + len(extra_cmdline)] = extra_cmdline
    if version >= 1:
        recovery_offset = page + sum(align(len(parts[name]), page) for name in ("kernel", "ramdisk", "second"))
        struct.pack_into("<IQI", header, 1632, len(parts["recovery_dtbo"]), recovery_offset, 1648 if version == 1 else 1660)
    if version >= 2:
        struct.pack_into("<IQ", header, 1648, len(parts["dtb"]), 0x44000000)
    header[576:596] = BootImage.digest(parts)
    output = header
    for data in parts.values():
        output += data + bytes([0xA5]) * (align(len(data), page) - len(data))
    output += b"OPAQUE VENDOR TAIL" * 41
    return bytes(output)


class BootTests(unittest.TestCase):
    def test_repack_preserves_components_header_and_absolute_tail(self):
        for version in (0, 1, 2):
            with self.subTest(version=version):
                before = BootImage.read(boot_fixture(version))
                result = before.repack(gzip.compress(b"smaller ramdisk", mtime=0))
                after = BootImage.read(result)
                self.assertEqual(len(before.raw), len(result))
                self.assertLess(after.end, before.end)
                self.assertEqual(before.raw[before.end:], result[before.end:])
                for name in before.parts:
                    if name != "ramdisk":
                        self.assertEqual(before.parts[name], after.parts[name])
                changed = {index for index in range(before.page) if before.raw[index] != result[index]}
                permitted = set(range(16, 20)) | set(range(576, 596))
                if version >= 1:
                    permitted |= set(range(1636, 1644))
                self.assertLessEqual(changed, permitted)

    def test_reject_corrupt_checksum(self):
        data = bytearray(boot_fixture())
        data[2048 + 200] ^= 1
        with self.assertRaisesRegex(ValueError, "SHA1"):
            BootImage.read(bytes(data))

    def test_reject_unknown_header_and_offset(self):
        for offset, value in ((40, 3), (1644, 999), (1636, 1)):
            data = bytearray(boot_fixture())
            struct.pack_into("<I", data, offset, value)
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                BootImage.read(bytes(data))

    def test_refuse_growth_into_opaque_tail(self):
        before = BootImage.read(boot_fixture())
        with self.assertRaisesRegex(ValueError, "opaque"):
            before.repack(gzip.compress(random.Random(1).randbytes(20000), mtime=0))


class ArchiveTests(unittest.TestCase):
    def test_newc_round_trip_preserves_metadata_symlinks_and_devices(self):
        source = {".": entry(".", mode=stat.S_IFDIR | 0o755), "target": entry("target", b"payload"),
                  "link": entry("link", b"/target", stat.S_IFLNK | 0o777),
                  "console": entry("console", mode=stat.S_IFCHR | 0o600)}
        source["console"].fields[9:11] = [5, 1]
        decoded = read_cpio(write_cpio(source))
        for name in source:
            self.assertEqual(source[name].data, decoded[name].data)
            self.assertEqual(source[name].fields, decoded[name].fields)

    def test_reject_traversal_duplicate_hardlink_and_missing_trailer(self):
        bad = [
            write_cpio({"x": entry("../escape")}),
            write_cpio({"a": entry("dup"), "b": entry("dup")}),
            b"garbage",
        ]
        hardlink = entry("hard")
        hardlink.fields[4] = 2
        bad.append(write_cpio({"hard": hardlink}))
        for data in bad:
            with self.subTest(data=data[:16]), self.assertRaises(ValueError):
                read_cpio(data)

    def test_resolve_absolute_and_parent_symlinks(self):
        source = {name: entry(name, mode=stat.S_IFDIR | 0o755) for name in ("bin", "usr", "usr/bin")}
        source["bin/busybox"] = entry("bin/busybox", b"payload")
        source["usr/bin/sh"] = entry("usr/bin/sh", b"../../bin/busybox", stat.S_IFLNK | 0o777)
        source["bin/sh"] = entry("bin/sh", b"/bin/busybox", stat.S_IFLNK | 0o777)
        self.assertEqual(resolve(source, "usr/bin/sh")[0], "bin/busybox")
        self.assertEqual(resolve(source, "bin/sh")[0], "bin/busybox")
        source["bin/bad"] = entry("bin/bad", b"../../escape", stat.S_IFLNK | 0o777)
        source["bin/loop"] = entry("bin/loop", b"loop", stat.S_IFLNK | 0o777)
        for name in ("bin/bad", "bin/loop"):
            with self.assertRaises(ValueError):
                resolve(source, name)

    def test_elf_loader_and_transitive_dependency_closure(self):
        dirs = (".", "bin", "sbin", "usr", "usr/sbin", "usr/lib", "lib", "dev", "proc", "sys", "mnt", "tmp", "etc")
        source = {name: entry(name, mode=stat.S_IFDIR | 0o755) for name in dirs}
        source["init"] = entry("init", b"original")
        source["bin/busybox"] = entry("bin/busybox", elf(["libc.so.6"], "/lib/ld-linux-aarch64.so.1"))
        source["usr/sbin/e2fsck"] = entry("usr/sbin/e2fsck", elf(["libext2fs.so.2"], "/lib/ld-linux-aarch64.so.1"))
        source["usr/lib/libext2fs.so.2"] = entry("usr/lib/libext2fs.so.2", b"libext2fs.so.2.4", stat.S_IFLNK | 0o777)
        source["usr/lib/libext2fs.so.2.4"] = entry("usr/lib/libext2fs.so.2.4", elf(["libc.so.6"], rpath="/absent/vendor/build"))
        source["lib/libc.so.6"] = entry("lib/libc.so.6", elf(["ld-linux-aarch64.so.1"]))
        source["lib/ld-linux-aarch64.so.1"] = entry("lib/ld-linux-aarch64.so.1", elf())
        source["usr/lib/unneeded.so"] = entry("usr/lib/unneeded.so", elf())
        source["etc/ld.so.cache"] = entry("etc/ld.so.cache", b"vendor-loader-cache")
        source["lib/aarch64-linux-gnu"] = entry("lib/aarch64-linux-gnu", b".", stat.S_IFLNK | 0o777)
        for name in ("bin/sh", "bin/usleep", "sbin/switch_root"):
            source[name] = entry(name, b"/bin/busybox", stat.S_IFLNK | 0o777)
        result, dependencies = minimal_entries(source, b"new init")
        for name in ("bin/busybox", "usr/sbin/e2fsck", "usr/lib/libext2fs.so.2", "usr/lib/libext2fs.so.2.4", "lib/libc.so.6", "lib/ld-linux-aarch64.so.1"):
            self.assertIn(name, result)
        self.assertNotIn("usr/lib/unneeded.so", result)
        self.assertEqual(result["init"].data, b"new init")
        self.assertIn(".", result)
        self.assertEqual(result["etc/ld.so.cache"].data, b"vendor-loader-cache")
        self.assertEqual(resolve(result, "lib/aarch64-linux-gnu/libc.so.6")[0], "lib/libc.so.6")
        self.assertTrue(stat.S_ISCHR(result["dev/console"].mode))
        self.assertEqual(result["dev/console"].fields[9:11], [5, 1])
        self.assertEqual(dependencies["lib/libc.so.6"], ["ld-linux-aarch64.so.1"])
        del source["lib/libc.so.6"]
        with self.assertRaisesRegex(ValueError, "missing libc"):
            minimal_entries(source, b"new init")

    def test_reject_relative_rpath(self):
        with self.assertRaisesRegex(ValueError, "RPATH"):
            elf_dependencies(elf(["libc.so.6"], rpath="$ORIGIN"))


class InitTests(unittest.TestCase):
    def test_script_syntax(self):
        subprocess.run(["sh", "-n", str(ROOT / "initramfs/init")], check=True)

    def test_fsck_statuses_gate_mount_and_request_reboot(self):
        source = (ROOT / "initramfs/init").read_text()
        function = re.search(r"^do_mount\(\)\n\{.*?^\}", source, re.M | re.S).group()
        for status in (0, 1, 2, 3, 4, 8, 16):
            script = f'''boot_mark() {{ printf 'stage %s %s\\n' "$1" "$2"; }}
e2fsck() {{ return {status}; }}
mount() {{ echo mounted "$@"; return 0; }}
sync() {{ :; }}
reboot() {{ echo rebooted; }}
{function}
do_mount /dev/test
rc=$?
echo result=$rc
'''
            result = subprocess.run(["sh"], input=script, text=True, capture_output=True, check=True).stdout
            with self.subTest(status=status):
                self.assertIn(f"initramfs.fsck.done rc={status} device=/dev/test", result)
                self.assertEqual("mounted -o" in result, status in (0, 1))
                self.assertEqual("rebooted" in result, status in (2, 3))
                self.assertIn("result=0" if status in (0, 1) else "result=1", result)

    def test_baseline_refuses_unrecognized_vendor_init(self):
        with self.assertRaisesRegex(ValueError, "requires review"):
            baseline_init(b"#!/bin/sh\necho an unknown init\n")


if __name__ == "__main__":
    unittest.main()
