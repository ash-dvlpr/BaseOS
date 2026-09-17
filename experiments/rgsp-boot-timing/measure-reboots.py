#!/usr/bin/env python3
"""Read-only clock samples around explicitly requested clean NextUI reboots."""
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import time

ROOT = Path(os.environ['RGSP_BOOT_TIMING_WORK']).resolve()
OUT = ROOT / 'reboots'
OUT.mkdir(exist_ok=True)
ADB = ['adb', '-s', 'ac001089c89588720d2']
HELPER_SHA = '3f1b76894b84da54d2a2df14f8982f146bc43d4ab61e2e81edc1b0749e78c4b1'

def shell(command, timeout=15):
    p = subprocess.run(ADB + ['shell', command], capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError(p.stderr or p.stdout)
    return p.stdout.replace('\r', '').strip()

def probe():
    output = shell('ulimit -c 0; /tmp/baseos-counter-probe')
    assert 'counter_frequency_hz=24000000' in output, output
    rows = []
    for line in output.splitlines()[1:]:
        m = re.fullmatch(r'sample=(\d+) counter_seconds=([\d.]+) monotonic_raw=([\d.]+) raw_origin_offset=\[([\d.-]+),([\d.-]+)\] boottime=([\d.]+) boot_origin_offset=\[([\d.-]+),([\d.-]+)\]', line)
        assert m, line
        v = list(map(float, m.groups()))
        rows.append(dict(sample=int(v[0]), counter_s=v[1], raw_s=v[2], raw_offset_bounds=v[3:5], boot_s=v[5], boot_offset_bounds=v[6:8]))
    assert len(rows) == 8
    return dict(output=output, samples=rows)

def install_probe():
    subprocess.run(ADB + ['push', str(ROOT / 'counter-probe-minimal'), '/tmp/baseos-counter-probe'], check=True, capture_output=True)
    assert 'PROBE_OK' in shell('chmod 755 /tmp/baseos-counter-probe && echo PROBE_OK')

def sample(label):
    state = shell('cat /proc/sys/kernel/random/boot_id; cat /run/boot-frontend-exec; cat /proc/uptime; pidof nextui.elf').splitlines()
    assert len(state) == 4 and state[3].isdigit(), state
    install_probe()
    early = probe()
    time.sleep(8)
    late = probe()
    dmesg = shell('dmesg')
    (OUT / (label + '.dmesg')).write_text(dmesg + '\n')
    warnings = [s for s in dmesg.splitlines() if any(p in s.lower() for p in ['recovery complete', 'recovering journal', 'ext4-fs error', 'kernel panic', 'oops:', 'not properly unmounted'])]
    health = shell('test -e /dev/mali0 && echo GPU_OK; test -d /sys/class/net/wlan0 && echo WIFI_OK; cat /sys/devices/system/clocksource/clocksource0/current_clocksource; cat /sys/class/power_supply/axp2202-battery/capacity; cat /sys/class/thermal/thermal_zone0/temp; pidof ntpd')
    assert 'GPU_OK' in health and 'WIFI_OK' in health and 'arch_sys_counter' in health, health
    raw_bounds = [s['raw_offset_bounds'] for p in [early, late] for s in p['samples']]
    # Use the unadjusted origin even when sampling an old, NTP-disciplined
    # boot. The retained handoff marker was captured early, before that slew.
    pre = statistics.median(sum(b) / 2 for b in raw_bounds)
    post = float(state[1])
    data = dict(label=label, boot_id=state[0], handoff_s=post, observed_uptime_s=float(state[2].split()[0]), nextui_pid=state[3], early=early, late=late, raw_offset_intersection_s=[max(b[0] for b in raw_bounds), min(b[1] for b in raw_bounds)], pre_kernel_estimate_s=pre, combined_handoff_estimate_s=pre + post, health=health, recovery_warnings=warnings, method='24 MHz CNTVCT_EL0 bracketed around CLOCK_MONOTONIC_RAW and CLOCK_BOOTTIME; first /proc/uptime handoff marker. Warm NextUI reboot_next; clean remounts. Counter origin is not calibrated to power LED.')
    handoff = shell('if test -s /run/boot-clock-handoff; then cat /run/boot-clock-handoff; fi')
    if handoff:
        values = list(map(float, handoff.split()))
        assert len(values) == 4 and values[0] == post, handoff
        data['handoff_stages_s'] = dict(zip(['legacy', 'pre_kernel', 'post_kernel_raw', 'combined'], values))
        assert abs(values[1] - statistics.median(sum(b) / 2 for b in raw_bounds)) <= .000501
        assert abs(values[1] + values[2] - values[3]) <= .00101
        data['installed_timing_sha256'] = shell('sha256sum /usr/sbin/boot-clock /usr/sbin/frontend-session')
        (OUT / (label + '.boot.log')).write_text(shell('cat /mnt/sdcard/baseos-boot.log') + '\n')
    dest = OUT / (label + '.json')
    assert not dest.exists(), dest
    dest.write_text(json.dumps(data, indent=2) + '\n')
    shell('rm -f /tmp/baseos-counter-probe')
    print(json.dumps({k:data[k] for k in ['label', 'boot_id', 'handoff_s', 'pre_kernel_estimate_s', 'combined_handoff_estimate_s', 'raw_offset_intersection_s', 'recovery_warnings']}), flush=True)
    if handoff:
        print('Handoff ' + json.dumps(data['handoff_stages_s']), flush=True)
    # NextUI keeps card log descriptors open. Its normal helper lazy-unmounts
    # the FAT volume, so the existing dirty flag can persist. Rootfs/data must
    # nevertheless come back without journal recovery or other kernel errors.
    if label != 'initial-existing-boot':
        assert not [w for w in warnings if 'FAT-fs' not in w], warnings
    return data

def reboot():
    release = shell('cat /etc/baseos-release')
    assert 'BASEOS_TARGET=rgsp\n' in release and 'BASEOS_VERSION=1.3.0\n' in release
    assert shell('sha256sum /mnt/sdcard/.system/h700/bin/reboot_next').split()[0] == HELPER_SHA
    # The first attempt after an accidental SELECT boot may start from ES;
    # the measured boot must still return to NextUI below.
    old = shell('cat /proc/sys/kernel/random/boot_id')
    command = '''cd /;
/bin/dd if=/dev/urandom of=/data/random-seed bs=512 count=1 2>/dev/null || exit 1;
/bin/sync;
if ! /bin/mount -o remount,ro /data; then echo NEXTUI_RESTART_FAILED_DATA; exit 1; fi;
if ! /bin/mount -o remount,ro /; then /bin/mount -o remount,rw /data; echo NEXTUI_RESTART_FAILED_ROOT; exit 1; fi;
unset SHARED_USERDATA_PATH;
LD_LIBRARY_PATH=/mnt/sdcard/.system/h700/lib:/usr/lib:/usr/lib/aarch64-linux-gnu:/lib/aarch64-linux-gnu /mnt/sdcard/.system/h700/bin/reboot_next;
/bin/mount -o remount,rw /; /bin/mount -o remount,rw /data; echo NEXTUI_RESTART_FAILED'''
    try:
        result = shell(command, 10)
        assert 'NEXTUI_RESTART_FAILED' not in result and 'every shutdown method failed' not in result, result
    except (RuntimeError, subprocess.TimeoutExpired):
        pass  # USB transport disappears on successful restart.
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            state = shell('cat /proc/sys/kernel/random/boot_id; cat /run/boot-frontend-exec; pidof nextui.elf', 4).splitlines()
            if len(state) == 3 and state[0] != old and state[2].isdigit() and float(state[1]) > 0:
                return
        except (RuntimeError, ValueError, subprocess.TimeoutExpired):
            pass
        time.sleep(0.5)
    raise RuntimeError('No fresh boot with running NextUI after 120s; stopping all further reboots')

if __name__ == '__main__':
    if not (OUT / 'initial-existing-boot.json').exists():
        sample('initial-existing-boot')
    for n in range(1, 6):
        if (OUT / f'nextui-{n}.json').exists():
            continue
        print(f'NextUI reboot {n}/5', flush=True)
        reboot()
        sample(f'nextui-{n}')
    print('Five NextUI reboot measurements complete; device left in NextUI.', flush=True)
