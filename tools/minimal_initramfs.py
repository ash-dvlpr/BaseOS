#!/usr/bin/env python3
"""Build a profiled, smaller initramfs from a target's own Android boot image.

No target binaries are rebuilt: BusyBox, e2fsck, their ELF dependencies, and the
kernel/DTB are copied from the input. Android boot v0-v2 layout and SHA1 follow
https://android.googlesource.com/platform/system/tools/mkbootimg/+/refs/tags/platform-tools-30.0.2/

The output is a partition-sized copy. Bytes after the original packed boot image
stay at their original offsets. Refuse growth into that opaque tail, unknown
headers, invalid component checksums, and unsupported archive/ELF layouts.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath
import posixpath
import stat
import struct
import sys

ROOT = Path(__file__).resolve().parent.parent


def align(value: int, size: int) -> int:
    return (value + size - 1) // size * size


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class BootImage:
    raw: bytes
    page: int
    version: int
    parts: dict[str, bytes]
    offsets: dict[str, int]
    end: int

    @classmethod
    def read(cls, raw: bytes) -> "BootImage":
        if len(raw) < 2048 or raw[:8] != b"ANDROID!":
            raise ValueError("input must start with a complete Android boot header")
        kernel_size, _, ramdisk_size, _, second_size, _, _, page, version, _ = struct.unpack_from("<10I", raw, 8)
        if page not in (2048, 4096, 8192, 16384, 32768, 65536) or version not in (0, 1, 2):
            raise ValueError(f"unsupported Android header version/page: {version}/{page}")
        if not kernel_size or not ramdisk_size:
            raise ValueError("kernel and ramdisk must be present")
        sizes = {"kernel": kernel_size, "ramdisk": ramdisk_size, "second": second_size}
        recovery_offset = 0
        if version >= 1:
            recovery_size, recovery_offset, header_size = struct.unpack_from("<IQI", raw, 1632)
            if header_size != {1: 1648, 2: 1660}[version]:
                raise ValueError("unknown Android header extension size")
            sizes["recovery_dtbo"] = recovery_size
        if version >= 2:
            sizes["dtb"] = struct.unpack_from("<I", raw, 1648)[0]
            if not sizes["dtb"]:
                raise ValueError("Android v2 image has no DTB")
        parts, offsets = {}, {}
        position = page
        for name, size in sizes.items():
            if name == "recovery_dtbo" and recovery_offset != (position if size else 0):
                raise ValueError("nonstandard recovery_dtbo offset")
            if position + align(size, page) > len(raw):
                raise ValueError(f"truncated Android component: {name}")
            offsets[name] = position
            parts[name] = raw[position:position + size]
            position += align(size, page)
        if parts["kernel"][56:60] != b"ARMd":
            raise ValueError("expected the uncompressed ARM64 kernel Image")
        if parts["ramdisk"][:2] != b"\x1f\x8b":
            raise ValueError("expected a gzip-compressed initramfs")
        if version == 2:
            dtb = parts["dtb"]
            if len(dtb) < 40 or dtb[:4] != b"\xd0\x0d\xfe\xed" or struct.unpack_from(">I", dtb, 4)[0] != len(dtb):
                raise ValueError("unsupported DTB representation")
        image = cls(raw, page, version, parts, offsets, position)
        if raw[576:596] != image.digest(parts) or any(raw[596:608]):
            raise ValueError("Android boot SHA1 mismatch or unknown image ID format")
        return image

    @staticmethod
    def digest(parts: dict[str, bytes]) -> bytes:
        digest = hashlib.sha1()
        for data in parts.values():
            digest.update(data)
            digest.update(struct.pack("<I", len(data)))
        return digest.digest()

    def repack(self, ramdisk: bytes) -> bytes:
        parts = dict(self.parts, ramdisk=ramdisk)
        new_end = self.page + sum(align(len(data), self.page) for data in parts.values())
        if new_end > self.end:
            raise ValueError("new boot payload would overwrite the opaque partition tail")
        out = bytearray(self.raw)
        out[self.page:self.end] = bytes(self.end - self.page)
        struct.pack_into("<I", out, 16, len(ramdisk))
        out[576:596] = self.digest(parts)
        position = self.page
        for name, data in parts.items():
            if name == "recovery_dtbo" and data:
                struct.pack_into("<Q", out, 1636, position)
            out[position:position + len(data)] = data
            if name != "ramdisk":
                # Preserve original component padding as well as its data.
                old = self.offsets[name] + len(data)
                padding = align(len(data), self.page) - len(data)
                out[position + len(data):position + len(data) + padding] = self.raw[old:old + padding]
            position += align(len(data), self.page)
        result = bytes(out)
        check = BootImage.read(result)
        for name, data in self.parts.items():
            if name != "ramdisk" and check.parts[name] != data:
                raise ValueError(f"repack changed {name}")
        if result[self.end:] != self.raw[self.end:] or len(result) != len(self.raw):
            raise ValueError("repack changed the opaque tail or partition size")
        return result


def canonical_name(name: str) -> str:
    if name.startswith("/") or ".." in PurePosixPath(name).parts or "\x00" in name:
        raise ValueError(f"unsafe cpio member name: {name!r}")
    cleaned = posixpath.normpath(name)
    if not name or cleaned.startswith("../"):
        raise ValueError("empty or escaping cpio name")
    return cleaned


@dataclass
class Entry:
    name: str
    fields: list[int]
    data: bytes

    @property
    def mode(self) -> int:
        return self.fields[1]


def read_cpio(blob: bytes) -> dict[str, Entry]:
    entries = {}
    position = 0
    while position + 110 <= len(blob):
        header = blob[position:position + 110]
        if header[:6] != b"070701":
            raise ValueError("only the Linux newc cpio format is supported")
        try:
            fields = [int(header[i:i + 8], 16) for i in range(6, 110, 8)]
        except ValueError as exc:
            raise ValueError("invalid newc numeric field") from exc
        size, namesize = fields[6], fields[11]
        if not namesize or position + 110 + namesize > len(blob):
            raise ValueError("truncated cpio name")
        encoded_name = blob[position + 110:position + 110 + namesize]
        if encoded_name[-1:] != b"\0" or b"\0" in encoded_name[:-1]:
            raise ValueError("invalid cpio name terminator")
        name = canonical_name(encoded_name[:-1].decode("utf-8"))
        position = align(position + 110 + namesize, 4)
        if position + size > len(blob):
            raise ValueError("truncated cpio data")
        data = blob[position:position + size]
        position = align(position + size, 4)
        if name == "TRAILER!!!":
            if size or any(blob[position:]):
                raise ValueError("nonzero data after cpio trailer")
            return entries
        if name in entries:
            raise ValueError(f"duplicate cpio path: {name}")
        if stat.S_ISREG(fields[1]) and fields[4] != 1:
            raise ValueError(f"regular-file hardlinks are unsupported: {name}")
        if stat.S_ISLNK(fields[1]) and (not data or b"\0" in data):
            raise ValueError(f"invalid symlink: {name}")
        entries[name] = Entry(name, fields, data)
    raise ValueError("cpio trailer missing")


def write_cpio(entries: dict[str, Entry]) -> bytes:
    output = bytearray()
    all_entries = list(entries.values()) + [Entry("TRAILER!!!", [0] * 13, b"")]
    for entry in all_entries:
        fields = entry.fields.copy()
        name = entry.name.encode() + b"\0"
        fields[6], fields[11], fields[12] = len(entry.data), len(name), 0
        output.extend(b"070701" + b"".join(f"{field:08x}".encode() for field in fields))
        output.extend(name)
        output.extend(bytes(-len(output) % 4))
        output.extend(entry.data)
        output.extend(bytes(-len(output) % 4))
    output.extend(bytes(-len(output) % 512))
    return bytes(output)


def resolve(entries: dict[str, Entry], path: str) -> tuple[str, list[str]]:
    """Resolve archive symlinks within the archive root, including parent links."""
    pending = path.lstrip("/").split("/")
    resolved, visited = [], []
    links = 0
    while pending:
        part = pending.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            if not resolved:
                raise ValueError(f"symlink escapes archive root: {path}")
            resolved.pop()
            continue
        resolved.append(part)
        name = "/".join(resolved)
        if name not in entries:
            raise ValueError(f"missing initramfs dependency: {name}")
        entry = entries[name]
        visited.append(name)
        if stat.S_ISLNK(entry.mode):
            links += 1
            if links > 40:
                raise ValueError(f"symlink loop: {path}")
            target = entry.data.decode()
            resolved.pop()
            if target.startswith("/"):
                resolved = []
            pending = target.split("/") + pending
        elif pending and not stat.S_ISDIR(entry.mode):
            raise ValueError(f"non-directory archive path component: {name}")
    return "/".join(resolved) or ".", visited


def elf_dependencies(data: bytes) -> tuple[str | None, list[str], list[str]]:
    """Read PT_INTERP / DT_NEEDED without executing foreign binaries or ldd."""
    if len(data) < 64 or data[:6] != b"\x7fELF\x02\x01" or struct.unpack_from("<H", data, 18)[0] != 183:
        raise ValueError("expected a little-endian AArch64 ELF64 binary")
    phoff = struct.unpack_from("<Q", data, 32)[0]
    phsize, phnum = struct.unpack_from("<HH", data, 54)
    if phsize != 56 or phoff + phsize * phnum > len(data):
        raise ValueError("invalid ELF program headers")
    loads, dynamic = [], None
    interpreter = None
    for index in range(phnum):
        kind, _, offset, address, _, size, _, _ = struct.unpack_from("<IIQQQQQQ", data, phoff + index * phsize)
        if offset + size > len(data):
            raise ValueError("truncated ELF segment")
        if kind == 1:
            loads.append((address, offset, size))
        elif kind == 2:
            dynamic = data[offset:offset + size]
        elif kind == 3:
            interpreter = data[offset:offset + size].rstrip(b"\0").decode()
    if dynamic is None:
        return interpreter, [], []
    tags = []
    for offset in range(0, len(dynamic), 16):
        tag, value = struct.unpack_from("<qQ", dynamic, offset)
        if tag == 0:
            break
        tags.append((tag, value))
    string_address = next((value for tag, value in tags if tag == 5), None)
    needed = [value for tag, value in tags if tag == 1]
    if not needed:
        return interpreter, [], []
    string_offset = next((offset + string_address - address for address, offset, size in loads
                          if string_address is not None and address <= string_address < address + size), None)
    if string_offset is None:
        raise ValueError("ELF dynamic string table is not file-backed")
    def string(offset: int) -> str:
        begin = string_offset + offset
        end = data.find(b"\0", begin)
        if not 0 <= begin < end <= len(data):
            raise ValueError("invalid ELF dynamic string")
        return data[begin:end].decode()
    names = []
    for offset in needed:
        name = string(offset)
        if "/" in name:
            raise ValueError("ELF DT_NEEDED must use a library basename")
        names.append(name)
    search_paths = []
    for tag, value in tags:
        if tag in (15, 29):
            for directory in string(value).split(":"):
                if not directory.startswith("/") or "$" in directory or ".." in PurePosixPath(directory).parts:
                    raise ValueError("unsupported relative or variable ELF RPATH/RUNPATH")
                search_paths.append(directory.lstrip("/"))
    return interpreter, names, search_paths


def minimal_entries(source: dict[str, Entry], init: bytes) -> tuple[dict[str, Entry], dict[str, list[str]]]:
    selected = set()
    dependencies = {}
    queue = ["bin/busybox", "usr/sbin/e2fsck", "bin/usleep", "bin/sh", "sbin/switch_root"]
    # Applet links cost very little and keep the original recovery shell useful.
    for name, entry in source.items():
        if stat.S_ISLNK(entry.mode):
            try:
                target, _ = resolve(source, name)
            except ValueError:
                continue
            if target == "bin/busybox":
                queue.append(name)
    # The vendor loader's default directories are target-specific. Preserve its
    # cache and multiarch directory aliases before the /bin/sh interpreter runs;
    # an LD_LIBRARY_PATH assignment inside /init would already be too late.
    for name in ("etc/ld.so.cache", "etc/ld.so.conf", "lib64", "usr/lib64",
                 "lib/aarch64-linux-gnu", "usr/lib/aarch64-linux-gnu",
                 "etc/e2fsck.conf", "etc/passwd", "etc/group", "etc/nsswitch.conf", "lib/libnss_files.so.2"):
        if name in source:
            queue.append(name)
    queue.extend(name for name in source if name.startswith("lib/modules/"))
    visited_binaries = set()
    while queue:
        name = queue.pop()
        target, paths = resolve(source, name)
        selected.update(paths)
        entry = source[target]
        if not entry.data.startswith(b"\x7fELF") or target in visited_binaries:
            continue
        visited_binaries.add(target)
        # Relocatable NAND modules are copied verbatim, not treated as programs.
        if target.endswith(".ko"):
            continue
        interpreter, needed, search_paths = elf_dependencies(entry.data)
        dependencies[target] = needed + ([interpreter] if interpreter else [])
        if interpreter:
            queue.append(interpreter)
        for library in needed:
            matches = []
            for directory in search_paths + ["lib", "usr/lib", "lib64", "usr/lib64", "lib/aarch64-linux-gnu", "usr/lib/aarch64-linux-gnu"]:
                candidate = f"{directory}/{library}"
                try:
                    resolved, _ = resolve(source, candidate)
                except ValueError:
                    continue
                matches.append((candidate, resolved))
            if not matches:
                raise ValueError(f"missing {library}, required by {target}")
            if len({sha256(source[resolved].data) for _, resolved in matches}) != 1:
                raise ValueError(f"ambiguous library resolution for {library}")
            queue.append(matches[0][0])
    for name in (".", "dev", "proc", "sys", "mnt", "tmp", "run", "root"):
        if name in source:
            _, paths = resolve(source, name)
            selected.update(paths)
            selected.add(name)
    result = {name: entry for name, entry in source.items() if name in selected}
    if "init" not in source:
        raise ValueError("source archive has no /init")
    result["init"] = replace(source["init"], data=init)
    # These also work before devtmpfs is mounted, including the kernel's initial
    # console open before executing /init. No privileged host mknod is needed.
    for name, major, minor in (("dev/console", 5, 1), ("dev/null", 1, 3)):
        fields = [0, stat.S_IFCHR | 0o600, 0, 0, 1, 0, 0, 0, 0, major, minor, len(name) + 1, 0]
        result[name] = Entry(name, fields, b"")
    # Kernel mountpoints must exist even if the vendor's archive omitted them.
    for name in ("dev", "proc", "sys", "mnt", "tmp"):
        if name not in result:
            raise ValueError(f"source archive has no {name} directory")
    for name in ("bin/usleep", "bin/sh", "sbin/switch_root", "usr/sbin/e2fsck"):
        resolve(result, name)
    return result, dependencies


MARKER_FUNCTION = '''boot_mark()
{
\tread -r boot_uptime boot_idle < /proc/uptime
\tprintf '%s\\t%s\\t%s\\n' "$boot_uptime" "$1" "${2:-}" >> /dev/.baseos-boot-profile.tsv
}
boot_mark initramfs.start baseline
'''


def baseline_init(original: bytes) -> bytes:
    """Instrument the known vendor init while retaining its control flow."""
    script = original.decode()
    replacements = [
        ("exec < /dev/console > /dev/console 2>&1", MARKER_FUNCTION + "\nexec < /dev/console > /dev/console 2>&1"),
        ("\t\tif [ -b $1 ]; then\n\t\t\treturn 0", "\t\tif [ -b $1 ]; then\n\t\t\tboot_mark initramfs.devices.ready \"$1\"\n\t\t\treturn 0"),
        ("\te2fsck -y $1", "\tboot_mark initramfs.fsck.start \"$1\"\n\te2fsck -y $1\n\tfsck_rc=$?\n\tboot_mark initramfs.fsck.done \"rc=$fsck_rc device=$1\""),
        ("\tmount -o rw,noatime,nodiratime,norelatime,noauto_da_alloc,barrier=0,data=ordered -t ext4 $1 /mnt\n\tif [ $? -ne 0 ]; then", "\tboot_mark initramfs.root.mount.start \"$1\"\n\tmount -o rw,noatime,nodiratime,norelatime,noauto_da_alloc,barrier=0,data=ordered -t ext4 $1 /mnt\n\tmount_rc=$?\n\tboot_mark initramfs.root.mount.done \"rc=$mount_rc device=$1\"\n\tif [ $mount_rc -ne 0 ]; then"),
        ("[ -x /mnt/init ] && mount", "[ -x /mnt/init ] && boot_mark initramfs.switch_root \"$ROOT_DEVICE\"\n[ -x /mnt/init ] && mount"),
    ]
    for old, new in replacements:
        if script.count(old) != 1:
            raise ValueError("unrecognized vendor /init; baseline instrumentation requires review")
        script = script.replace(old, new)
    return script.encode()


def build(input_path: Path, output_path: Path, variant: str, cpio_path: Path | None = None) -> dict:
    original = BootImage.read(input_path.read_bytes())
    original_cpio = gzip.decompress(original.parts["ramdisk"])
    entries = read_cpio(original_cpio)
    if "init" not in entries or not stat.S_ISREG(entries["init"].mode):
        raise ValueError("vendor /init must be a regular file")
    if variant == "baseline":
        result = dict(entries)
        result["init"] = replace(entries["init"], data=baseline_init(entries["init"].data))
        dependencies = {}
    else:
        result, dependencies = minimal_entries(entries, (ROOT / "initramfs/init").read_bytes())
    cpio = write_cpio(result)
    if set(read_cpio(cpio)) != set(result):
        raise ValueError("cpio round-trip changed the archive member set")
    ramdisk = gzip.compress(cpio, compresslevel=9, mtime=0)
    output = original.repack(ramdisk)
    rebuilt = BootImage.read(output)
    report = {
        "variant": variant, "input_boot": str(input_path.resolve()), "output_boot": str(output_path.resolve()),
        "input_sha256": sha256(original.raw), "output_sha256": sha256(output),
        "partition_bytes": len(output), "header_version": original.version, "page_bytes": original.page,
        "original_payload_bytes": original.end, "output_payload_bytes": rebuilt.end,
        "original_ramdisk_bytes": len(original.parts["ramdisk"]), "output_ramdisk_bytes": len(ramdisk),
        "original_cpio_bytes": len(original_cpio), "output_cpio_bytes": len(cpio),
        "kernel_sha256": sha256(original.parts["kernel"]),
        "dtb_sha256": sha256(original.parts["dtb"]) if "dtb" in original.parts else None,
        "preserved_tail_offset": original.end, "preserved_tail_sha256": sha256(original.raw[original.end:]),
        "retained_members": list(result), "removed_members": sorted(set(entries) - set(result)),
        "elf_dependencies": dependencies,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(output)
    if cpio_path:
        cpio_path.parent.mkdir(parents=True, exist_ok=True)
        cpio_path.write_bytes(cpio)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-boot", type=Path, required=True)
    parser.add_argument("--output-boot", type=Path, required=True)
    parser.add_argument("--variant", choices=("minimal", "baseline"), default="minimal")
    parser.add_argument("--output-cpio", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    paths = [path.resolve() for path in (args.input_boot, args.output_boot, args.output_cpio, args.report) if path]
    if len(set(paths)) != len(paths):
        parser.error("input and output paths must all be distinct")
    try:
        report = build(args.input_boot, args.output_boot, args.variant, args.output_cpio)
    except (ValueError, OSError, EOFError, struct.error, UnicodeError) as exc:
        parser.exit(1, f"minimal-initramfs: {exc}\n")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key not in ("retained_members", "removed_members", "elf_dependencies")}, indent=2))


if __name__ == "__main__":
    main()
