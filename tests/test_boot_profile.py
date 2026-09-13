#!/usr/bin/env python3
"""Profiler tests use temporary files and mocked ADB; no mounts or device access."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


HERE = Path(__file__).resolve().parent.parent
HELPER = HERE / "overlay/usr/sbin/baseos-boot-profile"
SPEC = importlib.util.spec_from_file_location("boot_profile", HERE / "tools/boot_profile.py")
PROFILE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROFILE)


class ShellProfileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = self.root / "enabled"
        self.uptime = self.root / "uptime"
        self.uptime.write_text("2.34 1.00\n")
        self.trace = self.root / "trace.tsv"
        self.env = dict(os.environ, BASEOS_BOOT_PROFILE_CONFIG=str(self.config),
                        BASEOS_BOOT_PROFILE_UPTIME=str(self.uptime),
                        BASEOS_BOOT_PROFILE_FILE=str(self.trace),
                        PROFILE_HELPER=str(HELPER), TEST_ROOT=str(self.root))

    def shell(self, program):
        return subprocess.run(["sh", "-eu", "-c", '. "$PROFILE_HELPER"\n' + program],
                              env=self.env, capture_output=True, text=True, check=True)

    def test_disabled_markers_do_not_write_but_legacy_still_does(self):
        self.shell('baseos_boot_mark disabled\nbaseos_boot_legacy_mark "$TEST_ROOT/legacy"')
        self.assertFalse(self.trace.exists())
        self.assertEqual((self.root / "legacy").read_text(), "2.34\n")

    def test_import_preserves_early_rows_and_uses_new_destination(self):
        self.config.touch()
        self.trace.write_text("0.90\tinitramfs.start\t\n")
        self.shell('baseos_boot_mark rcS.start\n'
                   'baseos_boot_profile_import "$TEST_ROOT/runtime.tsv"\n'
                   'baseos_boot_mark rcS.done status=0')
        self.assertEqual((self.root / "runtime.tsv").read_text(),
                         "0.90\tinitramfs.start\t\n2.34\trcS.start\t\n2.34\trcS.done\tstatus=0\n")
        self.assertNotIn("rcS.done", self.trace.read_text())

    def test_missing_clock_is_nonfatal_and_silent(self):
        self.config.touch()
        self.uptime.unlink()
        result = self.shell('baseos_boot_mark missing\nbaseos_boot_legacy_mark "$TEST_ROOT/legacy"')
        self.assertEqual(result.stderr, "")
        self.assertFalse(self.trace.exists())

    def test_parallel_writers_leave_complete_rows(self):
        self.config.touch()
        self.shell('worker=0\nwhile [ "$worker" -lt 12 ]; do\n'
                   '  (n=0; while [ "$n" -lt 40 ]; do\n'
                   '    baseos_boot_mark "worker.$worker" "sample=$n"\n'
                   '    n=$((n + 1))\n  done) &\n'
                   '  worker=$((worker + 1))\ndone\nwait')
        events, warnings = PROFILE.parse_trace(self.trace.read_text())
        self.assertEqual(warnings, [])
        self.assertEqual(len(events), 480)
        self.assertEqual(len({(event["stage"], event["detail"]) for event in events}), 480)

    def test_real_session_handoff_includes_log_and_preserves_first_exec(self):
        self.config.touch()
        run = self.root / "run"
        run.mkdir()
        card = self.root / "card"
        launch = card / ".system/h700/paks/MinUI.pak/launch.sh"
        launch.parent.mkdir(parents=True)
        launch.write_text("exit 0\n")
        (self.root / "mali0").touch()
        binaries = self.root / "bin"
        binaries.mkdir()
        for name, content in {
            "mountpoint": "#!/bin/sh\nexit 0\n",
            "date": '#!/bin/sh\nprintf "%s 1.00\\n" "$TEST_LOG_UPTIME" > "$BASEOS_BOOT_PROFILE_UPTIME"\nprintf "00:00:00\\n"\n',
        }.items():
            file = binaries / name
            file.write_text(content)
            file.chmod(0o755)
        session = (HERE / "overlay/usr/sbin/nextui-session").read_text()
        session = session.replace(". /usr/sbin/baseos-boot-profile", '. "$PROFILE_HELPER"')
        session = session.replace("SD=/mnt/sdcard", 'SD="$TEST_ROOT/card"')
        session = session.replace("LOG=/tmp/nextui-session.log", 'LOG="$TEST_ROOT/session.log"')
        session = session.replace("/run/", str(run) + "/")
        session = session.replace("/dev/mali0", str(self.root / "mali0"))
        session = session.replace("/usr/sbin/baseos-update", str(self.root / "absent-update"))
        program = self.root / "nextui-session"
        program.write_text(session)
        self.env["PATH"] = str(binaries) + ":" + self.env["PATH"]
        self.env["TEST_LOG_UPTIME"] = "2.41"
        subprocess.run(["sh", str(program)], env=self.env, check=True, timeout=10)
        self.assertEqual((run / "boot-frontend-exec").read_text(), "2.41\n")
        self.uptime.write_text("8.00 1.00\n")
        self.env["TEST_LOG_UPTIME"] = "8.07"
        subprocess.run(["sh", str(program)], env=self.env, check=True, timeout=10)
        self.assertEqual((run / "boot-frontend-exec").read_text(), "2.41\n")
        events, warnings = PROFILE.parse_trace(self.trace.read_text())
        self.assertEqual(warnings, [])
        self.assertEqual([event["uptime_s"] for event in events if event["stage"] == "frontend.exec"],
                         [2.41, 8.07])


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def sample(self, name, label, frontend, boot_id):
        directory = self.root / name
        directory.mkdir()
        (directory / "sample.json").write_text(json.dumps({"label": label, "serial": "device1"}))
        (directory / "boot-id.txt").write_text(boot_id)
        (directory / "boot-profile.tsv").write_text(
            "2.00\trcS.start\t\n2.10\tgpu.insmod.start\t\n"
            "2.20\tdata.mount.start\t\n2.80\tgpu.insmod.done\tstatus=0\n"
            "2.50\tdata.mount.done\t\n2.95\trcS.done\t\n"
            f"{frontend:.2f}\tfrontend.exec\tlaunch.sh\n99.00\tfrontend.exec\tlaunch.sh\n")
        return directory

    def test_concurrent_trace_uses_timestamps_and_first_exec(self):
        sample = PROFILE.sample_summary(self.sample("one", "baseline", 3.00, "first"))
        self.assertEqual(sample["frontend_exec_s"], 3.0)
        self.assertEqual(sample["phases_s"]["gpu.insmod"], 0.7)
        self.assertEqual(sample["phases_s"]["data.mount"], 0.3)
        self.assertEqual(sample["phases_s"]["rcS.total"], 0.95)

    def test_malformed_nonfinite_and_negative_times_are_rejected(self):
        events, warnings = PROFILE.parse_trace("broken\nNaN\tx\t\ninf\tx\t\n-1\tx\t\n2.34\tvalid\t\n")
        self.assertEqual(len(warnings), 4)
        self.assertEqual([event["stage"] for event in events], ["valid"])

    def test_grouped_medians_exclude_repeated_boot_ids(self):
        directories = [self.sample("one", "baseline", 3.0, "a"),
                       self.sample("two", "baseline", 3.2, "b"),
                       self.sample("duplicate", "baseline", 3.0, "a"),
                       self.sample("fast", "stripped", 2.8, "c")]
        report = PROFILE.compare([PROFILE.sample_summary(path) for path in directories])
        self.assertEqual(report["groups"]["baseline"]["kernel_to_frontend"]["n"], 2)
        self.assertEqual(report["groups"]["baseline"]["kernel_to_frontend"]["median"], 3.1)
        self.assertEqual(report["groups"]["stripped"]["kernel_to_frontend"]["median"], 2.8)
        self.assertTrue(any("duplicate boot ID" in warning for warning in report["samples"][2]["warnings"]))

    def test_legacy_only_sample_and_journal_recovery(self):
        (self.root / "boot-frontend-exec").write_text("3.02\n")
        (self.root / "boot-rcS-start").write_text("2.00\n")
        (self.root / "boot-rcS-done").write_text("2.98\n")
        (self.root / "dmesg.txt").write_text("[ 2.4] EXT4-fs (mmcblk0p6): recovery complete\n")
        sample = PROFILE.sample_summary(self.root)
        self.assertEqual(sample["frontend_exec_s"], 3.02)
        self.assertEqual(sample["phases_s"]["rcS.legacy"], 0.98)
        self.assertIn("ext4 journal recovery: mmcblk0p6", sample["warnings"])

    def test_mixed_firmware_with_one_label_is_not_aggregated(self):
        one = self.sample("one", "baseline", 3.0, "a")
        two = self.sample("two", "baseline", 3.2, "b")
        (one / "runtime-sha256.txt").write_text("a" * 64 + "  /lib/modules/mali_kbase.ko\n")
        (two / "runtime-sha256.txt").write_text("b" * 64 + "  /lib/modules/mali_kbase.ko\n")
        with self.assertRaisesRegex(ValueError, "different firmware hashes"):
            PROFILE.compare([PROFILE.sample_summary(one), PROFILE.sample_summary(two)])

    def test_health_and_boot_partition_resolution(self):
        (self.root / "frontend-pids.txt").write_text("1515\n")
        (self.root / "gpu-device.txt").write_text("crw------- 1 root root 10, 51 /dev/mali0\n")
        (self.root / "wifi-link.txt").write_text("3: wlan0: <BROADCAST,MULTICAST,UP>\n")
        (self.root / "battery-capacity.txt").write_text("48\n")
        (self.root / "battery-voltage-uv.txt").write_text("3800000\n")
        (self.root / "battery-status.txt").write_text("Charging\n")
        self.assertEqual(PROFILE.health_snapshot(self.root), {
            "frontend_pids": [1515], "gpu_device_present": True, "wifi_interface_present": True,
            "battery_capacity_percent": 48, "battery_voltage_uv": 3800000, "battery_status": "Charging",
        })
        self.assertEqual(PROFILE.boot_partition("root=/dev/mmcblk0p5 partitions=env@mmcblk0p3:boot@mmcblk0p4:rootfs@mmcblk0p5"),
                         "/dev/mmcblk0p4")
        self.assertIsNone(PROFILE.boot_partition("partitions=boot@../../sensitive"))

    def test_collect_uses_simple_readonly_adb_calls_and_refuses_overwrite(self):
        fake_adb = self.root / "adb"
        fake_adb.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
with open(os.environ['ADB_TEST_LOG'], 'a') as log:
    log.write(json.dumps(args) + '\\n')
if args[:1] == ['-s']:
    args = args[2:]
if args == ['get-serialno']:
    print('mock-device')
elif args[:1] == ['shell']:
    command = args[1]
    if command == 'cat /run/boot-profile.tsv 2>/dev/null':
        print('2.00\\trcS.start\\t\\n2.80\\trcS.done\\t\\n2.85\\tfrontend.exec\\tlaunch.sh')
    elif command == 'cat /proc/sys/kernel/random/boot_id 2>/dev/null':
        print('boot-a')
    elif command == 'dmesg':
        print('[ 0.0] Linux version test')
    elif not command.startswith('cat '):
        raise SystemExit(5)
else:
    raise SystemExit(5)
''')
        fake_adb.chmod(0o755)
        destination = self.root / "sample"
        log = self.root / "adb.log"
        env = dict(os.environ, ADB_TEST_LOG=str(log))
        command = ["python3", str(HERE / "tools/boot_profile.py"), "collect", "--adb", str(fake_adb),
                   "--output-dir", str(destination), "--label", "baseline", "--json"]
        result = subprocess.run(command, env=env, capture_output=True, text=True, check=True)
        report = json.loads(result.stdout)
        self.assertEqual(report["groups"]["baseline"]["kernel_to_frontend"]["median"], 2.85)
        invocations = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertTrue(all(call[:2] == ["-s", "mock-device"] for call in invocations[1:]))
        result = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("File exists", result.stderr)
        result = subprocess.run(["python3", str(HERE / "tools/boot_profile.py"), "summarize",
                                 str(self.root), "--json"], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)["groups"]["baseline"]["kernel_to_frontend"]["n"], 1)


if __name__ == "__main__":
    unittest.main()
