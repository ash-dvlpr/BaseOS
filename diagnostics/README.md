# Base OS diagnostics

## Boot timing

Read `/run/boot-frontend-exec` after the frontend starts. This single tmpfs marker
records kernel uptime at the first frontend handoff and survives frontend
respawns. Keep cold starts and clean warm reboots separate; the marker excludes
bootloader and frontend rendering time. See
[boot optimization and measurement](../docs/09-boot-profiling.md) for repeated
measurements and the retained GPU improvement.

## sleep-drain — measure suspend battery drain

Distinguishes real deep sleep (µA-level, days of standby) from fake sleep
(tens of mA, hours) and quantifies it, using the AXP2202 hardware coulomb
counter sampled at the exact suspend/resume boundary via NextUI's hook system.

Install onto a running device's card:

```sh
D=/mnt/sdcard/.userdata/h700/.hooks
mkdir -p $D/pre-sleep.d $D/post-resume.d
cp sleep-drain/pre-sleep.d/10-drain.sh   $D/pre-sleep.d/
cp sleep-drain/post-resume.d/10-drain.sh $D/post-resume.d/
chmod 755 $D/pre-sleep.d/10-drain.sh $D/post-resume.d/10-drain.sh
```

Then sleep the device (tap power), leave it suspended for a while (30 min
minimum for a coarse read; longer or overnight for precision), wake it (tap
power). Each wake appends a line to `/mnt/sdcard/sleep-drain.log`:

```
2026-07-19 15:40:02 slept=1834s dQ=2000uAh cap=61%->61% avg=3926uA proj_suspend_life=815h
```

`avg` is the mean current during suspend; `proj_suspend_life` = full battery /
avg current. Single-digit-mA average ⇒ real deep sleep; tens of mA ⇒ fake.

## results — measurement write-ups

`results/` holds the write-up behind a change that needed hardware to settle.
`2026-08-18-charger-boot-and-poweroff.md` records why `poweroff` did not stay
off on a charger, the register evidence for writing the PMU directly, and the
two theories that were tested and killed on the way.
