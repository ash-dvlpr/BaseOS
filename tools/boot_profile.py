#!/usr/bin/env python3
"""Collect a read-only ADB boot sample and compare repeated boot profiles.

Enable detailed markers by creating /etc/baseos-boot-profile on the device.
Collect after boot has settled so the background Wi-Fi events are included:

  python3 tools/boot_profile.py collect work/boots/baseline-01 --label baseline
  python3 tools/boot_profile.py collect work/boots/stripped-01 --label stripped
  python3 tools/boot_profile.py summarize work/boots --events

The collector does not reboot, remount, or change the device. Keep instrumentation
identical across A/B variants. Times are kernel uptime (10 ms resolution), not
power-on time or the time to the frontend's first visible frame. Concurrent
GPU/Wi-Fi intervals overlap the foreground boot and must not be added to it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path
import re
import shlex
import statistics
import subprocess
import sys
from typing import Any


FILES = {
    "boot-profile.tsv": "/run/boot-profile.tsv",
    "boot-rcS-start": "/run/boot-rcS-start",
    "boot-rcS-done": "/run/boot-rcS-done",
    "boot-frontend-exec": "/run/boot-frontend-exec",
    "boot-id.txt": "/proc/sys/kernel/random/boot_id",
    "cmdline.txt": "/proc/cmdline",
    "modules.txt": "/proc/modules",
    "baseos-release": "/etc/baseos-release",
    "battery-capacity.txt": "/sys/class/power_supply/axp2202-battery/capacity",
    "battery-status.txt": "/sys/class/power_supply/axp2202-battery/status",
    "battery-voltage-uv.txt": "/sys/class/power_supply/axp2202-battery/voltage_now",
}
HASH_PATHS = (
    "/lib/modules/mali_kbase.ko", "/lib/modules/8821cs.ko",
    "/etc/init.d/rcS", "/usr/sbin/nextui-session", "/usr/sbin/baseos-boot-profile",
)
SNAPSHOTS = {
    "dmesg.txt": "dmesg",
    "ps.txt": "ps w",
    "frontend-pids.txt": "pidof nextui.elf MinUI.elf 2>/dev/null",
    "gpu-device.txt": "ls -l /dev/mali0 2>/dev/null",
    "wifi-link.txt": "ip link show wlan0 2>/dev/null",
    "runtime-sha256.txt": "sha256sum " + " ".join(HASH_PATHS) + " 2>/dev/null",
}
EXTRA_INTERVALS = {
    "initramfs.total": ("initramfs.start", "initramfs.switch_root"),
    "initramfs.to_devices": ("initramfs.start", "initramfs.devices.ready"),
    "rcS.early_mounts": ("rcS.start", "rcS.run.ready"),
    "rcS.total": ("rcS.start", "rcS.done"),
    "session.to_exec": ("session.start", "frontend.exec"),
    "wifi.to_interface": ("wifi.request", "wifi.interface.ready"),
}


def timestamp(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError("uptime must be finite and nonnegative")
    return number


def parse_trace(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    events = []
    warnings = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split("\t", 2)
        try:
            if len(fields) < 2 or not fields[1]:
                raise ValueError("missing stage")
            events.append({
                "uptime_s": timestamp(fields[0]),
                "stage": fields[1],
                "detail": fields[2] if len(fields) == 3 else "",
                "line": line_number,
            })
        except ValueError:
            warnings.append(f"ignored malformed trace row {line_number}")
    # Parallel workers can be descheduled between reading uptime and appending.
    # Stable sorting retains append order when the 10 ms clock values tie.
    events.sort(key=lambda event: event["uptime_s"])
    return events, warnings


def first_event(events: list[dict[str, Any]], stage: str) -> float | None:
    return next((event["uptime_s"] for event in events if event["stage"] == stage), None)


def interval(events: list[dict[str, Any]], start: str, done: str) -> float | None:
    begin = first_event(events, start)
    if begin is None:
        return None
    finish = next((event["uptime_s"] for event in events
                   if event["stage"] == done and event["uptime_s"] >= begin), None)
    return round(finish - begin, 6) if finish is not None else None


def read_text(path: Path) -> str:
    return path.read_text(errors="replace") if path.is_file() else ""


def parse_hashes(text: str) -> dict[str, str]:
    return {match.group(2): match.group(1).lower() for match in re.finditer(
        r"^([0-9a-fA-F]{64})\s+\*?(.+)$", text, re.MULTILINE)}


def boot_partition(cmdline: str) -> str | None:
    for parameter in cmdline.split():
        if parameter.startswith("partitions="):
            for entry in parameter[len("partitions="):].split(":"):
                if entry.startswith("boot@"):
                    device = entry[len("boot@"):]
                    if re.fullmatch(r"(?:mmcblk\d+p\d+|nand[a-z0-9]+)", device):
                        return "/dev/" + device
    return None


def health_snapshot(directory: Path) -> dict[str, Any]:
    def number(name: str) -> int | None:
        value = read_text(directory / name).strip()
        return int(value) if value.isdigit() else None

    return {
        "frontend_pids": [int(value) for value in read_text(directory / "frontend-pids.txt").split()
                          if value.isdigit()] if (directory / "frontend-pids.txt").is_file() else None,
        "gpu_device_present": (read_text(directory / "gpu-device.txt").startswith("c")
                               if (directory / "gpu-device.txt").is_file() else None),
        "wifi_interface_present": (bool(re.search(r"\bwlan0\b", read_text(directory / "wifi-link.txt")))
                                   if (directory / "wifi-link.txt").is_file() else None),
        "battery_capacity_percent": number("battery-capacity.txt"),
        "battery_voltage_uv": number("battery-voltage-uv.txt"),
        "battery_status": read_text(directory / "battery-status.txt").strip() or None,
    }


def sample_summary(directory: Path) -> dict[str, Any]:
    metadata = json.loads(read_text(directory / "sample.json") or "{}")
    events, warnings = parse_trace(read_text(directory / "boot-profile.tsv"))
    legacy = {}
    for name in ("boot-rcS-start", "boot-rcS-done", "boot-frontend-exec"):
        value = read_text(directory / name).strip()
        if value:
            try:
                legacy[name] = timestamp(value)
            except ValueError:
                warnings.append(f"ignored invalid {name}")
    phases = {}
    for stage in dict.fromkeys(event["stage"] for event in events):
        if stage.endswith(".start"):
            name = stage[:-len(".start")]
            if name == "rcS":
                continue  # Report this once as rcS.total, beside rcS.legacy.
            elapsed = interval(events, stage, name + ".done")
            if elapsed is not None:
                phases[name] = elapsed
    for name, (start, done) in EXTRA_INTERVALS.items():
        elapsed = interval(events, start, done)
        if elapsed is not None:
            phases[name] = elapsed
    if "boot-rcS-start" in legacy and "boot-rcS-done" in legacy:
        elapsed = legacy["boot-rcS-done"] - legacy["boot-rcS-start"]
        if elapsed >= 0:
            phases["rcS.legacy"] = round(elapsed, 6)
    frontend = first_event(events, "frontend.exec")
    if frontend is None:
        frontend = legacy.get("boot-frontend-exec")
    recovery = sorted(set(re.findall(
        r"EXT4-fs \(([^)]+)\): recovery complete", read_text(directory / "dmesg.txt"))))
    if recovery:
        warnings.append("ext4 journal recovery: " + ", ".join(recovery))
    for event in events:
        if event["stage"] in ("data.mount.fallback", "wifi.interface.timeout"):
            warnings.append(event["stage"])
        if event["stage"] == "gpu.insmod.done" and event["detail"] != "status=0":
            warnings.append("GPU load: " + (event["detail"] or "missing status"))
    if not events:
        warnings.append("no detailed trace; legacy markers only")
    hashes = parse_hashes(read_text(directory / "runtime-sha256.txt"))
    hashes.update(parse_hashes(read_text(directory / "boot-sha256.txt")))
    return {
        "directory": str(directory),
        "label": metadata.get("label", directory.name),
        "serial": metadata.get("serial"),
        "boot_id": read_text(directory / "boot-id.txt").strip() or None,
        "frontend_exec_s": frontend,
        "legacy_s": legacy,
        "phases_s": phases,
        "events": events,
        "firmware_sha256": hashes,
        "health": health_snapshot(directory),
        "warnings": warnings,
        "metadata": metadata,
    }


def stats(values: list[float]) -> dict[str, float | int]:
    return {
        "n": len(values), "min": min(values), "median": statistics.median(values),
        "mean": statistics.mean(values), "max": max(values),
    }


def compare(samples: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, Any] = {}
    firmware_by_label: dict[str, dict[str, str]] = {}
    seen_boots: set[tuple[str | None, str]] = set()
    for sample in samples:
        if sample["metadata"].get("consistent_boot") is False:
            sample["warnings"].append("device rebooted during collection; excluded from aggregate")
            continue
        boot_id = sample["boot_id"]
        identity = (sample["serial"], boot_id)
        if boot_id and identity in seen_boots:
            sample["warnings"].append("duplicate boot ID; excluded from aggregate")
            continue
        if boot_id:
            seen_boots.add(identity)
        known_hashes = firmware_by_label.setdefault(sample["label"], {})
        for path, digest in sample["firmware_sha256"].items():
            if path in known_hashes and digest != known_hashes[path]:
                raise ValueError(f"label {sample['label']!r} contains different firmware hashes for "
                                 f"{path}; use distinct variant labels")
            known_hashes[path] = digest
        metrics = groups.setdefault(sample["label"], {})
        for name, value in sample["phases_s"].items():
            metrics.setdefault(name, []).append(value)
        if sample["frontend_exec_s"] is not None:
            metrics.setdefault("kernel_to_frontend", []).append(sample["frontend_exec_s"])
    return {
        "clock": "kernel uptime, 10 ms resolution; excludes bootloader and first visible frame",
        "intervals_overlap": True,
        "samples": samples,
        "groups": {label: {name: stats(values) for name, values in metrics.items()}
                   for label, metrics in groups.items()},
    }


def print_report(report: dict[str, Any], show_events: bool = False) -> None:
    print("Times in seconds from kernel start; frontend.exec precedes launch.sh and the first frame.")
    print("GPU/Wi-Fi run concurrently; phase durations overlap. Clock resolution: 0.01 s.")
    for label, metrics in report["groups"].items():
        print(f"\n{label}")
        print(f"{'metric':32s} {'n':>3s} {'median':>8s} {'mean':>8s} {'min':>8s} {'max':>8s}")
        for name in sorted(metrics, key=lambda item: (item != "kernel_to_frontend", item)):
            metric = metrics[name]
            print(f"{name:32s} {metric['n']:3d} {metric['median']:8.3f} "
                  f"{metric['mean']:8.3f} {metric['min']:8.3f} {metric['max']:8.3f}")
    for sample in report["samples"]:
        if sample["warnings"]:
            print(f"\n{sample['directory']}: " + "; ".join(sample["warnings"]))
        if show_events:
            print(f"\n{sample['directory']} events (chronological; delta is since preceding event)")
            previous = None
            for event in sample["events"]:
                delta = "   — " if previous is None else f"{event['uptime_s'] - previous:5.2f}"
                print(f"{event['uptime_s']:7.2f}  {delta}  {event['stage']:32s} {event['detail']}")
                previous = event["uptime_s"]


def adb_command(args: argparse.Namespace, command: list[str]) -> subprocess.CompletedProcess[str]:
    prefix = [args.adb]
    if args.serial:
        prefix += ["-s", args.serial]
    return subprocess.run(prefix + command, capture_output=True, text=True,
                          timeout=args.timeout, check=False)


def collect(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = args.output_dir or args.directory
    if not output_dir or (args.output_dir and args.directory):
        raise ValueError("provide one output directory, positionally or with --output-dir")
    serial_result = adb_command(args, ["get-serialno"])
    serial = serial_result.stdout.strip()
    if serial_result.returncode or not serial or serial == "unknown":
        raise RuntimeError("ADB device unavailable: " + serial_result.stderr.strip())
    # Pin subsequent calls to the selected device even if another is attached.
    args.serial = serial
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=False)
    errors = {}

    def save_command(name: str, command: str) -> None:
        # Old vendor adbd supports simple shell commands; it need not support
        # exec-out or interactive `sh -s`. All remote paths here are constants.
        result = adb_command(args, ["shell", command])
        output = result.stdout.replace("\r\n", "\n")
        if output:
            (directory / name).write_text(output)
        if result.returncode or not output:
            errors[name] = result.stderr.strip() or "unavailable or empty"

    for name, remote in FILES.items():
        save_command(name, "cat " + shlex.quote(remote) + " 2>/dev/null")
    for name, command in SNAPSHOTS.items():
        save_command(name, command)
    boot_device = boot_partition(read_text(directory / "cmdline.txt"))
    if boot_device:
        save_command("boot-sha256.txt", "sha256sum " + shlex.quote(boot_device) + " 2>/dev/null")
    else:
        errors["boot-sha256.txt"] = "boot partition absent from cmdline partitions map"
    # Include any background completion events that arrived during the health
    # snapshot; these cannot change the already-recorded first boot handoff.
    save_command("boot-profile.tsv", "cat /run/boot-profile.tsv 2>/dev/null")
    save_command("boot-id-end.txt", "cat /proc/sys/kernel/random/boot_id 2>/dev/null")
    consistent_boot = read_text(directory / "boot-id.txt") == read_text(directory / "boot-id-end.txt")
    metadata = {
        "schema": 1, "label": args.label or directory.name, "serial": serial,
        "collected_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "note": args.note or "", "unavailable_files": errors, "consistent_boot": consistent_boot,
    }
    (directory / "sample.json").write_text(json.dumps(metadata, indent=2) + "\n")
    if not consistent_boot:
        raise RuntimeError(f"device rebooted during collection; discard incomplete sample {directory}")
    sample = sample_summary(directory)
    if not sample["events"] and not sample["legacy_s"]:
        raise RuntimeError(f"no boot measurements found; diagnostics saved in {directory}")
    return compare([sample])


def find_samples(paths: list[str]) -> list[Path]:
    found = set()
    for value in paths:
        path = Path(value)
        if path.is_file() and path.name == "sample.json":
            found.add(path.parent.resolve())
        elif path.is_dir():
            if (path / "boot-profile.tsv").is_file() or (path / "boot-frontend-exec").is_file():
                found.add(path.resolve())
            found.update(metadata.parent.resolve() for metadata in path.rglob("sample.json"))
    if not found:
        raise ValueError("no boot sample directories found")
    return sorted(found)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    collector = commands.add_parser("collect", help="save one read-only ADB boot sample")
    collector.add_argument("directory", nargs="?", help="new output directory; never overwrites an existing sample")
    collector.add_argument("--output-dir", help="alias for the positional output directory")
    collector.add_argument("--label", help="A/B variant name for grouping repeated samples")
    collector.add_argument("--serial", default=os.environ.get("ANDROID_SERIAL"))
    collector.add_argument("--adb", default="adb")
    collector.add_argument("--timeout", type=float, default=20, help="timeout per ADB call in seconds")
    collector.add_argument("--note", help="e.g. cold power-on, clean reboot, card used")
    summary = commands.add_parser("summarize", help="compare samples, recursively finding sample.json")
    summary.add_argument("paths", nargs="+")
    for command in (collector, summary):
        command.add_argument("--json", action="store_true", help="print machine-readable report")
        command.add_argument("--events", action="store_true", help="also print the ordered trace")
    args = parser.parse_args(argv)
    try:
        report = collect(args) if args.command == "collect" else compare(
            [sample_summary(path) for path in find_samples(args.paths)])
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print_report(report, args.events)
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"boot_profile: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
