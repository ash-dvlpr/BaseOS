"""Exercise rcS's actual asynchronous WiFi block with simulated kernel events."""
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
rcs = (ROOT / 'overlay/etc/init.d/rcS').read_text()
start = rcs.index('# WiFi initialization')
end = rcs.index('# machine-id', start)
SCRIPT = rcs[start:end] + '\necho ready > /run/frontend-handoff\n'


class Radio:
    def __init__(self, power=None, stale=False):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.process = None
        self.write('bin/busybox', Path('/bin/busybox.static').read_bytes(), True)
        for name in ('sh', 'sleep', 'mkdir'):
            (self.root / 'bin' / name).symlink_to('/bin/busybox')
        for directory in ('run', 'dev', 'sys/class/net/wlan0'):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        self.write('dev/null', '')
        self.card = self.root / 'sys/bus/sdio/devices/mmc2:0001:1'
        if stale:
            self.card.mkdir(parents=True)
        self.scan = self.root / 'sys/class/misc/sunxi-wlan/rf-ctrl/scan_device'
        if power is not None:
            self.write('sys/class/misc/sunxi-wlan/rf-ctrl/power_state', str(power) + '\n')
            self.write(str(self.scan.relative_to(self.root)), '')
        self.write('sbin/insmod', '''#!/bin/sh
if [ -d /sys/bus/sdio/devices/mmc2:0001:1 ]; then
    echo stale > /run/driver-state
else
    echo clean > /run/driver-state
fi
''', True)
        for name in ('ip', 'rfkill'):
            self.write('bin/' + name, '#!/bin/sh\nexit 0\n', True)
        self.write('test.sh', SCRIPT)

    def write(self, path, contents, executable=False):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(contents, bytes):
            target.write_bytes(contents)
        else:
            target.write_text(contents)
        if executable:
            target.chmod(0o755)

    def run(self):
        self.process = subprocess.Popen(['/bin/busybox.static', 'chroot', str(self.root), '/bin/sh', '/test.sh'],
                                        env={'PATH': '/bin:/sbin'}, start_new_session=True,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def wait(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.005)
        raise AssertionError('Timed out waiting for simulated boot event')

    def driver(self):
        path = self.root / 'run/driver-state'
        return path.read_text().strip() if path.exists() else None

    def close(self):
        if self.process:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.process.wait()
        self.tmp.cleanup()


# A slow removal must delay driver registration but never frontend handoff.
r = Radio(power=0, stale=True)
try:
    r.run()
    r.wait(lambda: r.scan.read_text().strip() == '1')
    assert r.process.wait(timeout=0.3) == 0, 'WiFi wait blocked frontend handoff'
    assert (r.root / 'run/frontend-handoff').exists()
    time.sleep(0.12)
    assert r.driver() is None, 'Driver probed before the stale card disappeared'
    r.card.rmdir()  # completion of the kernel's asynchronous rescan
    r.wait(lambda: r.driver() == 'clean')
    print('PASS: stale card removed before driver; frontend does not wait')
finally:
    r.close()

for name, power, stale in [('no radio controls', None, False),
                            ('already powered', 1, True),
                            ('nothing enumerated', 0, False)]:
    r = Radio(power=power, stale=stale)
    try:
        r.run()
        r.wait(lambda: r.driver() is not None, timeout=0.5)
        if power == 1:
            assert r.scan.read_text() == '', 'Rescanned an already powered radio'
        assert r.driver() == ('stale' if stale else 'clean')
        print('PASS:', name)
    finally:
        r.close()

# An unresponsive kernel must not leave an unbounded background boot process.
r = Radio(power=0, stale=True)
try:
    r.run()
    r.wait(lambda: r.scan.read_text().strip() == '1')
    assert r.process.wait(timeout=0.3) == 0
    r.wait(lambda: r.driver() == 'stale', timeout=2)
    print('PASS: failed removal has a bounded wait')
finally:
    r.close()
