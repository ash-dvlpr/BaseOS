#!/usr/bin/env python3
"""Offline recovery only. Requires an explicitly selected, unmounted RG SP TF1."""
from pathlib import Path
import hashlib
import os
import re
import struct
import sys
import zlib

if not __debug__:
    raise SystemExit('Run without -O: this experiment requires its validation assertions.')

HERE = Path(os.environ['RGSP_GZIP_WORK']).resolve() / 'install'
if len(sys.argv) != 2 or not re.fullmatch(r'/dev/(rdisk[0-9]+|sd[a-z]+|mmcblk[0-9]+)', sys.argv[1]):
    sys.exit('Usage: sudo python3 restore-card.py /dev/rdiskN (unmounted RG SP TF1 only)')
device = sys.argv[1]
package = (HERE / 'boot-package-original.bin').read_bytes()
boot = (HERE / 'backup/rgsp-gzip-install/boot-partition-original.bin').read_bytes()
assert hashlib.sha256(package).hexdigest() == '957929afa2879e18ff758d93eafa98879b21aec70dedcb731d7862a2c20f075c'
assert hashlib.sha256(boot).hexdigest() == 'd42252f30730d35ca7759a651da7260ba482144130e58f05f23ea5e33270a6f5'
with open(device, 'r+b', buffering=0) as disk:
    disk.seek(512)
    header = bytearray(disk.read(512))
    assert header[:8] == b'EFI PART', 'Not a GPT card'
    header_size, stored_crc = struct.unpack_from('<II', header, 12)
    assert 92 <= header_size <= 512
    struct.pack_into('<I', header, 16, 0)
    assert zlib.crc32(header[:header_size]) == stored_crc, 'GPT header CRC mismatch'
    table_lba, count, entry_size, table_crc = struct.unpack_from('<QIII', header, 72)
    assert entry_size == 128 and 7 <= count <= 128
    disk.seek(table_lba * 512)
    table = disk.read(count * entry_size)
    assert zlib.crc32(table) == table_crc, 'GPT table CRC mismatch'
    entries = []
    for i in range(7):
        entry = table[i*entry_size:(i+1)*entry_size]
        start, end = struct.unpack_from('<QQ', entry, 32)
        name = entry[56:128].decode('utf-16le').split('\0')[0]
        entries.append((name, start, end-start+1))
    assert [e[0] for e in entries] == ['special', 'boot-resource', 'env', 'boot', 'rootfs', 'UDISK', 'primary'], entries
    assert entries[0][1] == 73728 and entries[3][1:] == (303104, 131072), entries
    disk.seek(0x1004000)
    current = disk.read(len(package))
    # Identify the pinned RG SP package while permitting damaged patch sectors.
    assert current[:20] == package[:20], 'Not the expected TOC1 package'
    assert current[0x10800:0x11800] == package[0x10800:0x11800], 'Bootloader identity mismatch'
    for offset, data in [(303104*512, boot), (0x1004000, package)]:
        print(f'Restoring {len(data)} bytes at {offset:#x} on {device}', flush=True)
        disk.seek(offset)
        view = memoryview(data)
        while view:
            written = disk.write(view[:1024*1024])
            if not written:
                raise OSError('Short write')
            view = view[written:]
        os.fsync(disk.fileno())
        disk.seek(offset)
        digest = hashlib.sha256()
        remaining = len(data)
        while remaining:
            chunk = disk.read(min(remaining, 1024*1024))
            if not chunk:
                raise OSError('Short read')
            remaining -= len(chunk)
            digest.update(chunk)
        assert digest.digest() == hashlib.sha256(data).digest(), 'Readback mismatch'
print('Original boot pair restored and verified. Eject TF1 before removing it.')
