"""Test the compatibility shim against processes with delayed shutdown."""
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time

SHIM = Path(__file__).resolve().parents[1] / 'overlay/usr/sbin/systemctl'
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    (root / 'pidof').write_text('#!/bin/sh\n[ -n "$SUPPLICANT_PID" ] || exit 1\nprintf "%s\\n" "$SUPPLICANT_PID"\n')
    (root / 'sleep').write_text('#!/bin/sh\necho sleep >> "$TEST_DIR/sleeps"\nexec /bin/sleep "$@"\n')
    for name in ('pidof', 'sleep'):
        (root / name).chmod(0o755)
    env = dict(os.environ, PATH=str(root) + ':' + os.environ['PATH'],
               TEST_DIR=str(root), SUPPLICANT_PID='')
    result = subprocess.run(['/bin/sh', str(SHIM), 'stop', 'wpa_supplicant'], env=env)
    assert result.returncode == 0 and not (root / 'sleeps').exists()
    print('PASS: absent supplicant adds no shutdown wait')

    for unit in ('wpa_supplicant', 'wpa_supplicant.service', 'wpa_supplicant@wlan0.service'):
        ready = root / 'ready'
        cleaned = root / 'cleaned'
        ready.unlink(missing_ok=True)
        cleaned.unlink(missing_ok=True)
        daemon = subprocess.Popen(['/bin/sh', '-c',
            'trap \'sleep 0.15; echo done > "$TEST_DIR/cleaned"; exit 0\' TERM; '
            'echo ready > "$TEST_DIR/ready"; while :; do sleep 0.01; done'],
            env=dict(os.environ, TEST_DIR=str(root)), start_new_session=True)
        reaper = threading.Thread(target=daemon.wait)
        reaper.start()
        try:
            deadline = time.monotonic() + 2
            while not ready.exists():
                assert time.monotonic() < deadline
                time.sleep(0.005)
            result = subprocess.run(['/bin/sh', str(SHIM), 'stop', 'wpa_supplicant', unit],
                                    env=dict(env, SUPPLICANT_PID=str(daemon.pid)), timeout=3)
            assert result.returncode == 0
            assert cleaned.exists() and daemon.poll() is not None
            print('PASS: waits for control-socket cleanup:', unit)
        finally:
            if daemon.poll() is None:
                os.killpg(daemon.pid, signal.SIGKILL)
            reaper.join(timeout=2)

    ready.unlink(missing_ok=True)
    daemon = subprocess.Popen(['/bin/sh', '-c',
        'trap "" TERM; echo ready > "$TEST_DIR/ready"; while :; do sleep 1; done'],
        env=dict(os.environ, TEST_DIR=str(root)), start_new_session=True)
    try:
        deadline = time.monotonic() + 2
        while not ready.exists():
            assert time.monotonic() < deadline
            time.sleep(0.005)
        result = subprocess.run(['/bin/sh', str(SHIM), 'stop', 'wpa_supplicant'],
                                env=dict(env, SUPPLICANT_PID=str(daemon.pid)), timeout=4)
        assert result.returncode != 0, 'Reported successful stop while daemon was still alive'
        print('PASS: unresponsive supplicant fails within a bounded wait')
    finally:
        os.killpg(daemon.pid, signal.SIGKILL)
        daemon.wait()
