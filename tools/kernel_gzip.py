#!/usr/bin/env python3
"""Derive gzip boot pairs from pinned prepared firmware; never write a device.

The vendor Android handler forces raw Image. Change its compression selector,
preserving the Android layout and every other TOC1 component. Both boot files
must be installed together. These are exact-firmware patches, not signatures
to apply speculatively to another U-Boot build.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import struct
from pathlib import Path

CATALOG = Path(__file__).resolve().parent.parent / "manifest/kernel-gzip.json"
STAMP = 0x5F0A6C39


def sha(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def word(blob: bytes, offset: int) -> int:
    return struct.unpack_from("<I", blob, offset)[0]


def checksum(blob: bytes, field: int) -> int:
    require(len(blob) % 4 == 0, "unaligned Allwinner package")
    return (sum(struct.unpack(f"<{len(blob) // 4}I", blob)) - word(blob, field) + STAMP) & 0xFFFFFFFF


def align(size: int) -> int:
    return (size + 2047) & ~2047


def image_id(parts: list[bytes]) -> bytes:
    digest = hashlib.sha1()
    for part in parts:
        digest.update(part)
        digest.update(struct.pack("<I", len(part)))
    return digest.digest() + bytes(12)


def derive(target: str, prefix: Path, catalog: Path = CATALOG) -> dict:
    profiles = json.loads(catalog.read_text())
    require(profiles["schema"] == 1, "unsupported gzip profile catalog")
    require(target in profiles["profiles"], f"no audited gzip profile for {target}")
    profile = profiles["profiles"][target]
    with prefix.open("rb") as handle:
        handle.seek(profile["package_offset"])
        original_package = handle.read(profile["package_size"])
        handle.seek(profile["boot_partition_start"] * 512)
        original_boot = handle.read(profile["boot_partition_sectors"] * 512)
    require(sha(original_package) == profile["package_sha256"], "unknown original TOC1 package")
    require(sha(original_boot) == profile["boot_partition_sha256"], "unknown original boot partition")
    package = bytearray(original_package)
    require(package[:16] == b"sunxi-package\0\0\0" and word(package, 16) == 0x89119800,
            "not the expected Allwinner TOC1")
    require(word(package, 36) == len(package) and checksum(package, 20) == word(package, 20),
            "invalid original TOC1 checksum/length")
    require(word(package, 32) == 4 and package[60:64] == b"MIE;", "unexpected TOC1 table")
    items = {}
    for i in range(4):
        pos = 64 + 368 * i
        name = bytes(package[pos:pos + 64]).split(b"\0")[0].decode("ascii")
        start, size, encryption, kind = struct.unpack_from("<4I", package, pos + 64)
        require(encryption == 0 and kind == 3 and start + size <= len(package), "invalid TOC1 item")
        require(package[pos + 364:pos + 368] == b"IIE;", "invalid TOC1 item footer")
        items[name] = (start, size)
    require(set(items) == {"u-boot", "monitor", "dtbo", "dtb"}, "unexpected TOC1 components")
    start, size = items["u-boot"]
    require((start, size) == (profile["uboot_offset"], profile["uboot_size"]), "U-Boot geometry changed")
    uboot = bytearray(package[start:start + size])
    require(sha(uboot) == profile["uboot_sha256"], "unknown U-Boot")
    require(word(uboot, 20) == len(uboot) and checksum(uboot, 12) == word(uboot, 12),
            "invalid original U-Boot checksum/length")
    selector = profile["selector_offset"]
    context = bytes.fromhex(profile["selector_context"])
    require(uboot[selector:selector + len(context)] == context, "Android selector context changed")
    require(context[:4] == bytes.fromhex("4ff40073"), "unexpected original compression selector")
    uboot[selector:selector + 4] = bytes.fromhex("40f20123")
    struct.pack_into("<I", uboot, 12, checksum(uboot, 12))
    package[start:start + size] = uboot
    struct.pack_into("<I", package, 20, checksum(package, 20))
    allowed = set(range(20, 24)) | set(range(start + 12, start + 16)) | set(range(start + selector, start + selector + 4))
    require(all(i in allowed for i, (a, b) in enumerate(zip(original_package, package)) if a != b),
            "patch changed bytes outside the instruction and checksums")

    require(original_boot[:8] == b"ANDROID!", "not an Android boot image")
    require(word(original_boot, 36) == 2048 and word(original_boot, 40) == 2 and
            word(original_boot, 0x66C) == 1660, "unsupported Android header/page size")
    require([word(original_boot, at) for at in (12, 20, 28, 32)] ==
            [0x40080000, 0x42000000, 0x40F00000, 0x40000100], "unexpected image load addresses")
    sizes = [word(original_boot, at) for at in (8, 16, 24, 0x660, 0x670)]
    require(sizes[2:4] == [0, 0] and 0 < sizes[0] < 32 * 1024 * 1024,
            "unsupported components or kernel decoder output size")
    require(0x40080000 + sizes[0] <= 0x42000000, "kernel output overlaps ramdisk")
    parts, pos = [], 2048
    for size in sizes:
        require(pos + size <= len(original_boot), "truncated Android component")
        parts.append(original_boot[pos:pos + size])
        pos += align(size)
    original_length = pos
    require(parts[0][0x38:0x3C] == b"ARM\x64", "kernel is not an uncompressed ARM64 Image")
    require(original_boot[0x240:0x260] == image_id(parts), "original Android image ID mismatch")
    kernel = parts[0]
    stream = io.BytesIO()
    with gzip.GzipFile(fileobj=stream, mode="wb", compresslevel=9, mtime=0, filename="") as out:
        out.write(kernel)
    parts[0] = stream.getvalue()
    require(gzip.decompress(parts[0]) == kernel, "gzip roundtrip failed")
    header = bytearray(original_boot[:2048])
    struct.pack_into("<I", header, 8, len(parts[0]))
    header[0x240:0x260] = image_id(parts)
    boot = bytes(header) + b"".join(part + bytes(align(len(part)) - len(part)) for part in parts)
    require(len(boot) < original_length, "compressed image did not shrink")
    # Preserve unused p4 tail exactly, matching the validated device experiment.
    expected_boot = boot + original_boot[len(boot):]
    return dict(profile=profile, package=bytes(package), uboot=bytes(uboot), boot=boot,
                original_package=original_package, original_boot=original_boot,
                expected_boot=expected_boot, kernel=kernel, ramdisk=parts[1], dtb=parts[4],
                original_image=original_boot[:original_length])


def prepare(target: str, prefix: Path, output: Path) -> dict:
    change = derive(target, prefix)
    p = change["profile"]
    output.mkdir(parents=True, exist_ok=True)
    for name, key in (("boot-package.bin", "package"), ("boot.img", "boot")):
        (output / name).write_bytes(change[key])
    manifest = {
        "target": target, "package_offset": p["package_offset"],
        "package_size": len(change["package"]), "boot_start": p["boot_partition_start"],
        "boot_sectors": p["boot_partition_sectors"], "boot_image_size": len(change["boot"]),
        "original_package_sha256": sha(change["original_package"]),
        "original_boot_sha256": sha(change["original_boot"]),
        "package_sha256": sha(change["package"]), "boot_image_sha256": sha(change["boot"]),
        "boot_partition_sha256": sha(change["expected_boot"]),
        "kernel_sha256": sha(change["kernel"]), "kernel_size": len(change["kernel"]),
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    # Trusted rootfs-owned manifest: only fixed keys, target IDs, decimal sizes
    # and hexadecimal hashes. Never source data from a card-supplied manifest.
    require(all(str(v).replace("_", "").isalnum() for v in manifest.values()), "unsafe shell manifest value")
    (output / "manifest").write_text("".join(f"BOOT_{k.upper()}={v}\n" for k, v in manifest.items()))
    return manifest


def apply(target: str, prefix: Path, image: Path) -> None:
    require(image.resolve() != prefix.resolve(), "refusing to modify prepared firmware")
    change = derive(target, prefix)
    p = change["profile"]
    with image.open("r+b") as handle:
        for offset, old, new in ((p["package_offset"], change["original_package"], change["package"]),
                                 (p["boot_partition_start"] * 512, change["original_boot"], change["boot"])):
            handle.seek(offset)
            require(handle.read(len(old)) == old, "image does not contain the pinned original boot bytes")
            handle.seek(offset)
            handle.write(new)
    verify(target, prefix, image)


def verify(target: str, prefix: Path, image: Path) -> None:
    change = derive(target, prefix)
    p = change["profile"]
    with image.open("rb") as handle:
        for offset, expected in ((p["package_offset"], change["package"]),
                                  (p["boot_partition_start"] * 512, change["expected_boot"])):
            handle.seek(offset)
            require(handle.read(len(expected)) == expected, "composed gzip boot pair differs from derivation")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "apply", "verify"))
    parser.add_argument("target")
    parser.add_argument("prefix", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        print(json.dumps(prepare(args.target, args.prefix, args.output), indent=2))
    else:
        globals()[args.command](args.target, args.prefix, args.output)
        print(f"{args.target}: gzip boot pair {args.command} OK")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, struct.error) as error:
        raise SystemExit(f"kernel-gzip: {error}")
