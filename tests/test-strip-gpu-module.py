#!/usr/bin/env python3
"""Fault-injection checks for the GPU debug-strip ABI gate (no cross tool needed)."""

from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from strip_gpu_module import SIGNATURE, strip_module, verify


def fixture(*, debug=True, text=b"\x00" * 8, modinfo=b"vermagic=test aarch64\0",
            crc=123, relocation_symbol="printk", addend=0, import_name="printk"):
    """ELF64 ET_REL with independently renumbered symbols/sections after strip."""
    names = ["", ".text", ".bss", ".modinfo", "__versions", ".gnu.linkonce.this_module"]
    if debug:
        names += [".debug_info", ".rela.debug_info"]
    names += [".rela.text", ".symtab", ".strtab", ".shstrtab"]
    indices = {name: i for i, name in enumerate(names)}
    symnames = b"\0source.c\0printk\0kmalloc\0init_module\0__this_module\0"
    # A changed imported name must preserve ELF string-table consistency.
    symnames = symnames.replace(b"printk", import_name.encode())

    def symname(name):
        if name == "printk":
            name = import_name
        return symnames.index(name.encode() + b"\0") if name else 0

    symbols = [("", 0, 0, 0, 0, 0)]
    if debug:
        symbols += [("source.c", 4, 0, 0xfff1, 0, 0),
                    ("", 3, 0, indices[".debug_info"], 0, 0)]
    symbols += [("", 3, 0, indices[".text"], 0, 0)]
    first_global = len(symbols)
    symbols += [("printk", 16, 0, 0, 0, 0), ("kmalloc", 16, 0, 0, 0, 0),
                ("init_module", 18, 0, indices[".text"], 0, 8),
                ("__this_module", 17, 0, indices[".gnu.linkonce.this_module"], 0, 16)]
    reloc_index = next(i for i, symbol in enumerate(symbols) if symbol[0] == relocation_symbol)
    symbol_bytes = b"".join(struct.pack("<IBBHQQ", symname(name), info, other, section, value, size)
                            for name, info, other, section, value, size in symbols)
    shstrings = b"\0" + b"".join(name.encode() + b"\0" for name in names[1:])
    sections = {
        "": (0, 0, b"", 0, 0, 0, 0),
        ".text": (1, 6, text, 0, 0, 4, 0),
        ".bss": (8, 3, b"\0" * 16, 0, 0, 8, 0),
        ".modinfo": (1, 2, modinfo, 0, 0, 1, 0),
        "__versions": (1, 2, struct.pack("<Q", crc) + b"printk\0" + b"\0" * 49, 0, 0, 8, 0),
        ".gnu.linkonce.this_module": (1, 3, b"\0" * 16, 0, 0, 8, 0),
        ".debug_info": (1, 0, b"debug symbols" * 16, 0, 0, 1, 0),
        ".rela.debug_info": (4, 0, b"", indices[".symtab"], indices.get(".debug_info", 0), 8, 24),
        ".rela.text": (4, 0, struct.pack("<QQq", 0, (reloc_index << 32) | 283, addend),
                       indices[".symtab"], indices[".text"], 8, 24),
        ".symtab": (2, 0, symbol_bytes, indices[".strtab"], first_global, 8, 24),
        ".strtab": (3, 0, symnames, 0, 0, 1, 0),
        ".shstrtab": (3, 0, shstrings, 0, 0, 1, 0),
    }
    data = bytearray(64)
    headers = []
    for name in names:
        kind, flags, payload, link, info, alignment, width = sections[name]
        while alignment and len(data) % alignment:
            data.append(0)
        offset = len(data)
        if kind != 8:
            data.extend(payload)
        headers.append(struct.pack("<IIQQQQIIQQ", shstrings.index(name.encode() + b"\0") if name else 0,
                                   kind, flags, 0, offset, len(payload), link, info, alignment, width))
    table = len(data)
    data.extend(b"".join(headers))
    ident = b"\x7fELF\x02\x01\x01" + b"\0" * 9
    data[:64] = struct.pack("<16sHHIQQQIHHHHHH", ident, 1, 183, 1, 0, 0, table, 0,
                            64, 0, 0, 64, len(names), indices[".shstrtab"])
    return bytes(data)


class StripGpuTests(unittest.TestCase):
    def test_accepts_repacked_debug_removal(self):
        report = verify(fixture(), fixture(debug=False))
        self.assertGreater(report["saved_bytes"], 0)
        self.assertTrue(report["modinfo_unchanged"])
        self.assertTrue(report["modversions_unchanged"])
        self.assertEqual(report["runtime_relocations_unchanged"], 1)

    def test_rejects_allocated_code_or_metadata_change(self):
        for change in ({"text": b"\x01" * 8}, {"crc": 456}, {"modinfo": b"vermagic=bad aarch64\0"}):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "allocated section"):
                verify(fixture(), fixture(debug=False, **change))

    def test_rejects_import_change(self):
        with self.assertRaisesRegex(ValueError, "runtime symbol"):
            verify(fixture(), fixture(debug=False, import_name="pr_intk"))

    def test_rejects_relocation_target_or_addend_change(self):
        for change in ({"relocation_symbol": "kmalloc"}, {"addend": 4}):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "runtime relocation"):
                verify(fixture(), fixture(debug=False, **change))

    def test_rejects_debug_data_left_behind(self):
        with self.assertRaisesRegex(ValueError, "debug sections remain"):
            verify(fixture(), fixture())

    def test_rejects_signed_module_before_running_strip(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "input.ko", Path(directory) / "output.ko"
            source.write_bytes(fixture() + b"signature" + SIGNATURE)
            with patch("strip_gpu_module.subprocess.run") as run:
                with self.assertRaisesRegex(ValueError, "signed module"):
                    strip_module(source, output, "strip")
                run.assert_not_called()
            self.assertFalse(output.exists())

    def test_never_overwrites_original(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "mali_kbase.ko"
            source.write_bytes(fixture())
            with self.assertRaisesRegex(ValueError, "input and output must differ"):
                strip_module(source, source, "strip")

    def test_installs_only_after_validation_and_keeps_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "input.ko", Path(directory) / "output.ko"
            source.write_bytes(fixture())
            source.chmod(0o640)
            output.write_bytes(b"existing output")

            def run_bad(argv, **kwargs):
                self.assertEqual(argv[:2], ["aarch64-linux-gnu-strip", "--strip-debug"])
                Path(argv[2]).write_bytes(fixture(debug=False, text=b"\xff" * 8))

            with patch("strip_gpu_module.subprocess.run", side_effect=run_bad):
                with self.assertRaises(ValueError):
                    strip_module(source, output, "aarch64-linux-gnu-strip")
            self.assertEqual(output.read_bytes(), b"existing output")

            def run_good(argv, **kwargs):
                self.assertEqual(argv[:2], ["aarch64-linux-gnu-strip", "--strip-debug"])
                Path(argv[2]).write_bytes(fixture(debug=False))

            with patch("strip_gpu_module.subprocess.run", side_effect=run_good):
                strip_module(source, output, "aarch64-linux-gnu-strip")
            self.assertEqual(output.read_bytes(), fixture(debug=False))
            self.assertEqual(output.stat().st_mode & 0o777, 0o640)
            self.assertEqual(source.read_bytes(), fixture())


if __name__ == "__main__":
    unittest.main()
