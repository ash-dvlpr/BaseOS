#!/usr/bin/env python3
"""Prepare the pinned RG SP experiment from live backups. Never writes a device.

The Android handler is forced to gzip: restore bootloader and boot image as a
pair. This is an experimental patch, not a raw-compatible production backport.
TOC1 layout/checksum reference:
https://lists.u-boot-project.org/pipermail/u-boot/2021-October/463731.html
"""
from pathlib import Path
import os
import gzip
import hashlib
import json
import struct

if not __debug__:
    raise SystemExit('Run without -O: this experiment requires its validation assertions.')

HERE = Path(os.environ['RGSP_GZIP_WORK']).resolve() / 'install'
ROOT = HERE.parent
BACKUP = HERE / 'backup' / 'rgsp-gzip-install'
PACKAGE_OFFSET = 0x1004000
PACKAGE_SIZE = 0x140000

def sha(data):
    return hashlib.sha256(data).hexdigest()

def le32(data, offset):
    return struct.unpack_from('<I', data, offset)[0]

def checksum(data, field):
    words = struct.unpack('<%dI' % (len(data) // 4), data)
    return (sum(words) - words[field // 4] + 0x5f0a6c39) & 0xffffffff

def checked(name, expected):
    data = (BACKUP / name).read_bytes()
    assert sha(data) == expected, (name, sha(data))
    return data

region = checked('boot-region-original.bin',
    '2507d7955824cb56dfead33ea916b850983cdb130f9ddd47974002fe00f1e795')
boot_partition = checked('boot-partition-original.bin',
    'd42252f30730d35ca7759a651da7260ba482144130e58f05f23ea5e33270a6f5')
env = checked('env-original.bin',
    '8dd964ef0d525071626040d7cdce6c16925aaa6108d35558a0a5f032592b78a2')
assert len(region) == 36 * 1024**2
assert len(boot_partition) == 64 * 1024**2
assert len(env) == 16 * 1024**2

# Find the real Allwinner TOC1, not the unrelated stale Rockchip FIT bytes
# elsewhere in the vendor prefix. There is exactly one valid package header.
assert region.count(b'sunxi-package\0\0\0' + struct.pack('<I', 0x89119800)) == 1
package = bytearray(region[PACKAGE_OFFSET:PACKAGE_OFFSET + PACKAGE_SIZE])
assert package[:16] == b'sunxi-package\0\0\0'
assert le32(package, 16) == 0x89119800
assert le32(package, 32) == 4
assert le32(package, 36) == len(package)
assert package[60:64] == b'MIE;'
assert checksum(package, 20) == le32(package, 20)
items = {}
for i in range(4):
    offset = 64 + 368 * i
    name = bytes(package[offset:offset+64]).split(b'\0')[0].decode()
    start, size, encryption, kind = struct.unpack_from('<4I', package, offset+64)
    assert encryption == 0 and kind == 3
    assert package[offset+364:offset+368] == b'IIE;'
    assert start + size <= len(package)
    items[name] = (start, size)
assert set(items) == {'u-boot', 'monitor', 'dtbo', 'dtb'}
assert items['u-boot'] == (0x800, 0x100000)
uboot_start, uboot_size = items['u-boot']
uboot = bytearray(package[uboot_start:uboot_start+uboot_size])
assert sha(uboot) == 'a11271e1276173cee48bd5705d068fd8446cfc0ffb43fca70b3b6808c2e9d0db'
assert uboot == (ROOT / 'u-boot.bin').read_bytes()
assert le32(uboot, 20) == len(uboot)
assert checksum(uboot, 12) == le32(uboot, 12)
assert uboot[0xf698:0xf69c] == bytes.fromhex('4ff40073')
uboot[0xf698:0xf69c] = bytes.fromhex('40f20123')  # movw r3, #0x201
struct.pack_into('<I', uboot, 12, checksum(uboot, 12))
assert checksum(uboot, 12) == le32(uboot, 12)
package[uboot_start:uboot_start+uboot_size] = uboot
struct.pack_into('<I', package, 20, checksum(package, 20))
assert checksum(package, 20) == le32(package, 20)
original_package = region[PACKAGE_OFFSET:PACKAGE_OFFSET + PACKAGE_SIZE]
allowed = set(range(20, 24)) | set(range(uboot_start+12, uboot_start+16)) | set(range(uboot_start+0xf698, uboot_start+0xf69c))
changed = [i for i, (a,b) in enumerate(zip(original_package, package)) if a != b]
assert set(changed) <= allowed
for name, (start, size) in items.items():
    if name != 'u-boot':
        assert package[start:start+size] == original_package[start:start+size]

# Parse and verify the Android v2 image directly from the live backup.
assert boot_partition[:8] == b'ANDROID!'
assert le32(boot_partition, 36) == 2048 and le32(boot_partition, 40) == 2
assert le32(boot_partition, 0x66c) == 1660
sizes = [le32(boot_partition, at) for at in [8, 16, 24, 0x660, 0x670]]
assert sizes == [17686536, 2606312, 0, 0, 137564]
assert [le32(boot_partition, at) for at in [12, 20, 28, 32]] == [0x40080000, 0x42000000, 0x40f00000, 0x40000100]
def align(n):
    return (n+2047) & ~2047
parts, pos = [], 2048
for size in sizes:
    parts.append(boot_partition[pos:pos+size])
    pos += align(size)
assert pos == 20436992
assert boot_partition[:pos] == (ROOT / 'boot-original.img').read_bytes()
assert boot_partition[pos:] == b'\xff' * (len(boot_partition)-pos)
def image_id(components):
    h = hashlib.sha1()
    for data in components:
        h.update(data)
        h.update(struct.pack('<I', len(data)))
    return h.digest() + b'\0' * 12
assert boot_partition[0x240:0x260] == image_id(parts)
assert parts[0] == (ROOT / 'Image').read_bytes()
assert parts[1] == (ROOT / 'ramdisk.gz').read_bytes()
assert parts[4] == (ROOT / 'vendor-dtb.dtb').read_bytes()
compressed = (ROOT / 'Image.gz9').read_bytes()
assert gzip.decompress(compressed) == parts[0]
parts[0] = compressed
header = bytearray(boot_partition[:2048])
struct.pack_into('<I', header, 8, len(compressed))
header[0x240:0x260] = image_id(parts)
boot = bytes(header) + b''.join(data + b'\0' * (align(len(data))-len(data)) for data in parts)
assert len(boot) == 9842688
assert boot == (ROOT / 'boot-gzip-analysis.img').read_bytes()
# Only write the meaningful gzip image; bytes beyond its end remain untouched.
expected_partition = boot + boot_partition[len(boot):]
expected_region = region[:PACKAGE_OFFSET] + package + region[PACKAGE_OFFSET+PACKAGE_SIZE:]

outputs = {
    'boot-package-original.bin': original_package,
    'boot-package-gzip.bin': bytes(package),
    'u-boot-gzip.bin': bytes(uboot),
    'boot-gzip.img': boot,
}
for name, data in outputs.items():
    (HERE / name).write_bytes(data)
manifest = {
    'device_serial': 'ac001089c89588720d2',
    'device_target': 'rgsp',
    'original_boot_id': '59de6727-9b8b-4fb8-862b-8cbd20754161',
    'experiment': 'Single-core gzip; Android handler forced to gzip. Restore both components together.',
    'package_disk_offset': PACKAGE_OFFSET,
    'package_bytes': PACKAGE_SIZE,
    'boot_partition_start_lba': 303104,
    'boot_partition_sectors': 131072,
    'boot_bytes_written': len(boot),
    'changed_package_byte_offsets': [hex(i) for i in changed],
    'original_region_sha256': sha(region),
    'expected_region_sha256': sha(expected_region),
    'original_boot_partition_sha256': sha(boot_partition),
    'expected_boot_partition_sha256': sha(expected_partition),
    'unchanged_env_sha256': sha(env),
    'outputs': {name: {'bytes': len(data), 'sha256': sha(data)} for name, data in outputs.items()},
}
(HERE / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(json.dumps(manifest, indent=2))
