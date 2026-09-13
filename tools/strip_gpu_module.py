#!/usr/bin/env python3
"""Remove GPU module debug data only after checking the module's loadable ABI.

Run with an AArch64-capable GNU/LLVM strip tool. The stock input is never
modified; the output is installed atomically only after validation succeeds.
Signed modules are rejected because stripping would invalidate their signature.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile


SIGNATURE = b"~Module signature appended~\n"
SHF_ALLOC = 2
SHT_SYMTAB, SHT_STRTAB, SHT_RELA, SHT_NOBITS, SHT_REL = 2, 3, 4, 8, 9


@dataclass(frozen=True)
class Section:
    name: str
    kind: int
    flags: int
    address: int
    size: int
    link: int
    info: int
    alignment: int
    entry_size: int
    data: bytes

    def content(self) -> tuple:
        # File offsets and section indices may change when ELF is repacked.
        return (self.kind, self.flags, self.address, self.size,
                self.alignment, self.entry_size, self.data)


def is_debug(name: str) -> bool:
    if name.startswith((".rela.", ".rel.")):
        name = name[name.index(".", 1):]
    return name.startswith((".debug_", ".zdebug_", ".stab")) or name == ".gdb_index"


def cstring(data: bytes, offset: int) -> str:
    if not 0 <= offset < len(data):
        raise ValueError("invalid ELF string offset")
    end = data.find(b"\0", offset)
    if end < 0:
        raise ValueError("unterminated ELF string")
    return data[offset:end].decode("utf-8")


class Module:
    def __init__(self, blob: bytes):
        if SIGNATURE in blob[-1024:]:
            raise ValueError("signed module: --strip-debug would invalidate its signature")
        if len(blob) < 64 or blob[:7] != b"\x7fELF\x02\x01\x01":
            raise ValueError("expected an ELF64 little-endian module")
        header = struct.unpack_from("<16sHHIQQQIHHHHHH", blob)
        ident, kind, machine, version, entry, _, table, flags, ehsize, _, phnum, stride, count, strings_index = header
        if (kind, machine, version, ehsize, phnum, stride) != (1, 183, 1, 64, 0, 64):
            raise ValueError("expected a relocatable AArch64 module without program headers")
        if not 0 < strings_index < count or table + count * stride > len(blob):
            raise ValueError("invalid ELF section table")
        self.header = (ident, kind, machine, version, entry, flags, ehsize)
        raw = [struct.unpack_from("<IIQQQQIIQQ", blob, table + i * stride)
               for i in range(count)]

        def payload(row: tuple) -> bytes:
            if row[1] == SHT_NOBITS:
                return b""
            offset, length = row[4:6]
            if offset + length > len(blob):
                raise ValueError("truncated ELF section")
            return blob[offset:offset + length]

        if raw[strings_index][1] != SHT_STRTAB:
            raise ValueError("invalid section-name string table")
        strings = payload(raw[strings_index])
        self.sections = [Section(cstring(strings, row[0]), row[1], row[2], row[3],
                                 row[5], row[6], row[7], row[8], row[9], payload(row))
                         for row in raw]
        self.by_name = {section.name: section for section in self.sections}
        if len(self.by_name) != count:
            raise ValueError("duplicate ELF section names")
        for name in (".modinfo", ".gnu.linkonce.this_module", ".symtab"):
            if name not in self.by_name:
                raise ValueError(f"missing required module section {name}")
        if b"vermagic=" not in self.by_name[".modinfo"].data:
            raise ValueError("module has no vermagic")

    def symbols(self, table: Section) -> list[tuple]:
        if table.kind != SHT_SYMTAB or table.entry_size != 24 or table.size % 24:
            raise ValueError("invalid module symbol table")
        if not 0 <= table.link < len(self.sections):
            raise ValueError("invalid symbol string-table link")
        strings = self.sections[table.link]
        if strings.kind != SHT_STRTAB:
            raise ValueError("symbol names do not link to a string table")
        symbols = []
        for pos in range(0, len(table.data), 24):
            name, info, other, index, value, size = struct.unpack_from("<IBBHQQ", table.data, pos)
            if index == 0xffff:
                raise ValueError("extended symbol indices are unsupported")
            if 0 < index < 0xff00:
                if index >= len(self.sections):
                    raise ValueError("invalid symbol section index")
                section = self.sections[index].name
            else:
                section = index  # Preserve UNDEF, ABS and COMMON semantics.
            symbols.append((cstring(strings.data, name), info, other, section, value, size))
        return symbols

    def runtime_symbols(self) -> Counter:
        symbols = self.symbols(self.by_name[".symtab"])
        # GNU strip may discard unreferenced section symbols as well as file
        # symbols. Every referenced section symbol is checked by relocations().
        return Counter(symbol for symbol in symbols
                       if symbol[1] & 0xf not in (3, 4)  # STT_SECTION, STT_FILE
                       and not (isinstance(symbol[3], str) and is_debug(symbol[3])))

    def relocations(self) -> Counter:
        result = Counter()
        for section in self.sections:
            if section.kind not in (SHT_RELA, SHT_REL):
                continue
            if not 0 <= section.info < len(self.sections) or not 0 <= section.link < len(self.sections):
                raise ValueError("invalid relocation section link")
            target = self.sections[section.info]
            if is_debug(target.name):
                continue
            symbols = self.symbols(self.sections[section.link])
            width = 24 if section.kind == SHT_RELA else 16
            if section.entry_size != width or section.size % width:
                raise ValueError("invalid relocation entries")
            for pos in range(0, len(section.data), width):
                offset, info = struct.unpack_from("<QQ", section.data, pos)
                if info >> 32 >= len(symbols):
                    raise ValueError("invalid relocation symbol index")
                addend = struct.unpack_from("<q", section.data, pos + 16)[0] if width == 24 else None
                result[(target.name, section.kind, offset, info & 0xffffffff,
                        symbols[info >> 32], addend)] += 1
        return result


def verify(original: bytes, stripped: bytes) -> dict:
    before, after = Module(original), Module(stripped)
    if before.header != after.header:
        raise ValueError("ELF architecture/header changed")
    allocated = lambda module: {s.name: s.content() for s in module.sections if s.flags & SHF_ALLOC}
    if allocated(before) != allocated(after):
        raise ValueError("allocated section data or attributes changed")
    # Compare non-debug metadata too. Symbol/relocation indices and string
    # tables are allowed to be repacked, so compare their meaning separately.
    def retained(module: Module) -> dict:
        return {s.name: s.content() for s in module.sections
                if not is_debug(s.name)
                and s.kind not in (SHT_SYMTAB, SHT_STRTAB, SHT_RELA, SHT_REL)}
    if retained(before) != retained(after):
        raise ValueError("non-debug section data or attributes changed")
    for name in (".modinfo", "__versions", ".gnu.linkonce.this_module"):
        left, right = before.by_name.get(name), after.by_name.get(name)
        if (left is None) != (right is None) or (left and left.content() != right.content()):
            raise ValueError(f"module metadata changed: {name}")
    if before.runtime_symbols() != after.runtime_symbols():
        raise ValueError("runtime symbol semantics changed")
    if before.relocations() != after.relocations():
        raise ValueError("runtime relocation semantics changed")
    if any(is_debug(s.name) for s in after.sections):
        raise ValueError("debug sections remain after --strip-debug")
    if len(stripped) > len(original):
        raise ValueError("stripped module became larger")
    info = before.by_name[".modinfo"].data.split(b"\0")
    return {
        "original_bytes": len(original), "stripped_bytes": len(stripped),
        "saved_bytes": len(original) - len(stripped),
        "original_sha256": hashlib.sha256(original).hexdigest(),
        "stripped_sha256": hashlib.sha256(stripped).hexdigest(),
        "vermagic": next(item[9:].decode() for item in info if item.startswith(b"vermagic=")),
        "allocated_sections_unchanged": len(allocated(before)),
        "runtime_symbols_unchanged": sum(before.runtime_symbols().values()),
        "runtime_relocations_unchanged": sum(before.relocations().values()),
        "modinfo_unchanged": True,
        "modversions_unchanged": "__versions" in before.by_name,
        "unsigned": True,
    }


def strip_module(source: Path, output: Path, strip_tool: str) -> dict:
    if source.resolve() == output.resolve():
        raise ValueError("input and output must differ; preserve the original module")
    original = source.read_bytes()
    Module(original)  # Reject signatures/malformed inputs before invoking strip.
    with tempfile.TemporaryDirectory(prefix=".strip-gpu-", dir=output.parent) as temporary:
        candidate = Path(temporary) / source.name
        shutil.copyfile(source, candidate)
        subprocess.run([strip_tool, "--strip-debug", str(candidate)], check=True)
        report = verify(original, candidate.read_bytes())
        candidate.chmod(source.stat().st_mode & 0o777)
        os.replace(candidate, output)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--strip-tool", default="strip", help="AArch64-capable strip executable")
    parser.add_argument("--report", type=Path, help="write validated size/ABI report as JSON")
    args = parser.parse_args()
    report = strip_module(args.source, args.output, args.strip_tool)
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(f"GPU --strip-debug: {report['original_bytes']:,} -> {report['stripped_bytes']:,} bytes; "
          f"saved {report['saved_bytes']:,}; module ABI verified")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, struct.error, subprocess.CalledProcessError) as error:
        raise SystemExit(f"strip-gpu-module: {error}")
