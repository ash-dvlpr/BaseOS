#!/usr/bin/env python3
"""Exercise install + error rollback against regular files in disposable Linux."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ['RGSP_GZIP_WORK']).resolve() / 'install'
BACKUP = DATA / 'backup/rgsp-gzip-install'
manifest = json.loads((DATA/'manifest.json').read_text())
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

for inject_failure in (False, True):
    with tempfile.TemporaryDirectory(prefix='rgsp-install-test-') as root:
        root = Path(root)
        stage = root/'stage'
        stage.mkdir()
        (root/'sdcard').mkdir()
        for name in ('boot-region-original.bin', 'boot-partition-original.bin', 'env-original.bin'):
            shutil.copyfile(BACKUP/name, stage/name)
        for name in ('boot-package-original.bin', 'boot-package-gzip.bin', 'boot-gzip.img', 'manifest.json', 'RECOVERY.md'):
            shutil.copyfile((HERE if name == 'RECOVERY.md' else DATA)/name, stage/name)
        for part, source in (('mmcblk0', 'boot-region-original.bin'), ('mmcblk0p4', 'boot-partition-original.bin'), ('mmcblk0p3', 'env-original.bin')):
            shutil.copyfile(stage/source, root/part)
        (root/'release').write_text('BASEOS_TARGET=rgsp\n')
        (root/'cmdline').write_text('androidboot.serialno=ac001089c89588720d2\n')
        (root/'boot_id').write_text(manifest['original_boot_id']+'\n')
        (root/'start').write_text('303104\n')
        (root/'size').write_text('131072\n')
        replacements = {
            '/tmp/rgsp-gzip-install': str(stage),
            '/mnt/sdcard': str(root/'sdcard'),
            '/etc/baseos-release': str(root/'release'),
            '/proc/cmdline': str(root/'cmdline'),
            '/proc/sys/kernel/random/boot_id': str(root/'boot_id'),
            '/sys/class/block/mmcblk0p4/start': str(root/'start'),
            '/sys/class/block/mmcblk0p4/size': str(root/'size'),
            '/dev/mmcblk0': str(root/'mmcblk0'),
            '/tmp/rgsp-gzip-restore-readback.bin': str(root/'restore-readback'),
        }
        for name in ('install-on-device.sh', 'restore-on-device.sh'):
            script = (HERE/name).read_text()
            for old, new in replacements.items():
                script = script.replace(old, new)
            if inject_failure and name == 'install-on-device.sh':
                script = script.replace(manifest['expected_boot_partition_sha256'], '0'*64)
            assert '/dev/' not in script
            (stage/name).write_text(script)
        result = subprocess.run(['sh', str(stage/'install-on-device.sh')], text=True, capture_output=True)
        if result.returncode != (1 if inject_failure else 0):
            raise RuntimeError((result.returncode, result.stdout, result.stderr))
        prefix = 'original_' if inject_failure else 'expected_'
        assert sha(root/'mmcblk0') == manifest[prefix+'region_sha256']
        assert sha(root/'mmcblk0p4') == manifest[prefix+'boot_partition_sha256']
        assert sha(root/'mmcblk0p3') == manifest['unchanged_env_sha256']
        recovery = root/'sdcard/.baseos-boot-gzip-20260917'
        assert sha(recovery/'boot-partition-original.bin') == manifest['original_boot_partition_sha256']
        print(('Injected verification failure and automatic rollback' if inject_failure else 'Successful installation') + ': PASS', flush=True)
        if not inject_failure:
            result = subprocess.run(['sh', str(recovery/'restore-on-device.sh')], text=True, capture_output=True)
            assert result.returncode == 0, (result.stdout, result.stderr)
            assert sha(root/'mmcblk0') == manifest['original_region_sha256']
            assert sha(root/'mmcblk0p4') == manifest['original_boot_partition_sha256']
            print('Explicit paired rollback: PASS', flush=True)
