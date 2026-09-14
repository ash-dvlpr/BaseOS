# Base OS diagnostics

## Boot timing

Read `/run/boot-frontend-exec` after the frontend starts. This single tmpfs marker
records kernel uptime at the first frontend handoff and survives frontend
respawns. Keep cold starts and clean warm reboots separate; the marker excludes
bootloader and frontend rendering time. See
[boot performance](../docs/09-boot-profiling.md) for the measurement procedure.
The handoff time is also recorded in `baseos-boot.log` at the root of the active
frontend card (TF2, otherwise TF1). See [boot I/O](../docs/10-boot-io-audit.md)
for early buffering and fallback destinations.

## sleep-drain — measure suspend battery drain

Estimates average suspend drain from the AXP2202 charge counter, sampled by
NextUI hooks immediately before sleep and after resume. Confirm suspend state
separately through kernel logs; low drain alone does not prove suspend-to-RAM.

From the copied `diagnostics/` directory on the device, install the hooks:

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
2026-07-19 15:40:02 slept=1834s dQ=2000uAh cap=61%->61% avg=3926uA proj_standby=815h
```

`avg` is the mean current over the interval; `proj_standby` is battery capacity
divided by that current. The hooks use `date +%s`, so avoid changing system time
during a measurement. Use an unplugged device and a long interval to reduce
charge-counter quantization error. A zero delta means the counter did not
resolve a change; the hook's `deep-sleep-confirmed` label is not independently
sufficient evidence of the sleep state.
