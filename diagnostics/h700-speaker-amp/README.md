# Experimental RG SP speaker and optional headphone pop fixes

This is a kernel-specific diagnostic prototype. The BaseOS loader supports it
when installed; release builds do not yet package this module automatically.
It targets the RG SP vendor kernel **4.9.170 #2, June 24 2026 19:15:50 CST**.
Load only on that independently verified kernel Image; do not probe unknown
kernels with this profile. Default mode only inspects the live objects.

The vendor's `sunxi_spk_event` is an empty callback. This module replaces the
runtime SPK widget's callback and wraps the LINEOUT callback while holding the
card's DAPM mutex. The wrapper disconnects the amplifier before the vendor
lineout shutdown/ramp and reconnects it after startup settles. Both callbacks
share that mutex, including callbacks triggered by mixer changes. By default the vendor codec/ramp implementation remains in place. The optional
headphone mode retains output buffers after the same vendor ramp-down sequence. No kernel instructions, device-tree
properties, pin direction/mux or boot partitions are changed.

The two callbacks are needed because SPK and LINEOUT are parallel endpoints;
relying on their relative execution order would not ensure mute-before-ramp.
The device-tree amplifier delay is used in both directions (100 ms on the
tested device). GPIO 261 is the already requested active-high PI5 output.

Latest validation and installed device state: [configuration and boot regression](configuration-and-boot.md).

## Configuration

The current module defaults to speaker-only repair. Add the following to
TF1's `baseos.conf` and restart to enable the headphone exit-pop workaround:

```ini
headphone_pop_fix=true
```

Missing, false or invalid values disable headphone retention. The speaker fix
remains enabled. `baseos-config` normalizes the value into `/run/baseos.conf`;
`h700-speaker-amp-load` starts in the background after settings and the update
scan. It passes `apply=1 headphone_pop_fix=0` or `1` to the installed module.
The vendor kernel does not accept the word `true` as a bool module parameter.
The parameter is read-only after loading; changes require a restart.

Off mode delegates LINEOUT shutdown to the original driver and skips retention
clock acquisition and PM/reboot notifier registration. On mode retains output
buffers while leaving normal DAC shutdown intact; it releases retained idle
buffers before kernel suspend and on unload. Idle-power cost and actual hardware
suspend/resume are still unmeasured. The standalone `buffer-retention/` directory
records the earlier always-on acoustic trial, not the configurable build.

## Compatibility evidence

The saved Image and the device's boot kernel were compared byte for byte by
SHA-256: `3328c2fab9f19f7ea6a80e34b0a76238cf594bc631f77e05fec32f8adabad042`.
The compile-time offsets were checked against that image's disassembly. Module
structure size (768 bytes), init/exit relocation offsets (0x158/0x2c8), vermagic,
and imported symbol CRCs were compared against the vendor module/kernel.

This is deliberately dependent on vendor internals: `codec_list`,
`client_mutex`, and the writable DAPM widget callbacks. It is not a stable
kernel API. Recovered symbol CRCs alone are **not** enough to support another
kernel. Kernel text, structure offsets, GPIO identity and runtime callback
pointers must all be verified first.

## Building

Use Linux 4.9.170 source and an ARM64 GCC toolchain. The prototype was compiled
with `ghcr.io/loveretro/h700-toolchain:latest` (GCC 8.3), using the device's
`/proc/config.gz`. `bc` is required by the kernel build. Modern host GCC needs
`HOSTCFLAGS="-O2 -fcommon"` for this old tree's dtc sources.

1. Save the exact device config as the kernel tree's `.config`.
2. Run `make ARCH=arm64 CROSS_COMPILE=aarch64-nextui-linux-gnu- olddefconfig`.
3. Run `make ARCH=arm64 CROSS_COMPILE=aarch64-nextui-linux-gnu- HOSTCFLAGS="-O2 -fcommon" modules_prepare`.
4. Supply symbol versions from the **matching vendor Image**, using BaseOS's
   `tools/kernel_abi.py exports rgsp`. An upstream kernel's `Module.symvers`
   does not describe the vendor kernel. For this GPL prototype, generate
   records `<CRC>\t<name>\tvmlinux\tEXPORT_SYMBOL` in the kernel tree's
   `Module.symvers`. Independently verify every resulting import CRC.
5. Build with `make -C KERNEL_TREE ARCH=arm64 CROSS_COMPILE=aarch64-nextui-linux-gnu- HOSTCFLAGS="-O2 -fcommon" M=ABSOLUTE_PATH_TO_THIS_DIRECTORY modules`.

## Manual experiment

Keep the original userspace audio library. Running a separate userspace GPIO
workaround at the same time would defeat the experiment's isolation.

With audio closed and the frontend idle:

```sh
insmod /tmp/h700_speaker_amp.ko         # inspect only
dmesg | grep h700_amp
rmmod h700_speaker_amp
insmod /tmp/h700_speaker_amp.ko apply=1 # temporary callback replacement
```

The active mode refuses to attach if audio is running. Test playback using
both ALSA `default` and `hw:audiocodec`, normal games, SPK mute/volume changes,
and suspend/resume. GPIO must go low before codec shutdown and stay low at
idle; it must go high only after startup settles. Listening is needed to
determine whether this eliminates the actual pop.

To restore the original behavior, close audio first and run:

```sh
rmmod h700_speaker_amp
```

Unloading restores both callback pointers and the GPIO state recorded at load.
A reboot also removes the temporary module. Do not unbind/remove the sound
card, codec driver or GPIO controller while the prototype is attached; it
does not implement those device-removal lifecycles. No automatic loading is
installed by this directory.

## RG SP test results, 2026-09-15

Tested on BaseOS 1.2.1, with the original NextUI userspace and the exact kernel
listed above. The live library did not contain PR #46's physical GPIO helper.

- Inspection-only load: passed, then unloaded without changing GPIO.
- Active load with PCM closed: passed; GPIO changed from high to low.
- Direct ALSA playback, 48 kHz stereo S16_LE silence: PCM RUNNING/GPIO high;
  after close, PCM closed/GPIO low.
- SPK mixer switch off with direct playback running: GPIO stayed low. Restoring
  SPK at idle left it low, as expected.
- ALSA `default` playback, 44.1 kHz stereo S16_LE silence: completed and returned
  to GPIO low at idle.
- Unload: original callback pointers were restored and GPIO returned high.
  Reload: passed and returned GPIO low at idle.

After being asked to launch a game, check sound and exit, the user reported:
**"Pops are gone!"** This confirms audible pop suppression in that game test
with the temporary kernel module. At that point, playback quality,
suspend/resume and headphone/output switching still required validation.
Follow-up results are recorded below. No automatic boot loading was installed.

### Follow-up listening results

The user reported no pops on a zero-volume game launch/exit. However, ADB
disconnected during this phase and the next inspection showed a different boot
ID with the module absent. That initial result could not be attributed to the
module, so a repeat with the module loaded was requested. A reboot removes this
prototype.

Reloaded successfully on boot `afaf4e4d-8da7-4f73-95ee-3e4b555083ae` at uptime
184 seconds: both widgets were off, callbacks attached, and amplifier GPIO
went low.

The user subsequently reported completing the requested tests:

| Check | User's listening result |
| --- | --- |
| Speaker game launch/exit, including volume zero | No pops. |
| Speaker sleep/wake test | No pops, included in the user's report of all speaker cases passing. |
| Speaker noise | Slight hiss, considered acceptable by the user. |
| Wired 3.5 mm headphones | Pops remain, particularly when exiting a game. |

These are listening results, not recorded analogue measurements. ADB was
unavailable at follow-up, so module continuity across these tests, the final
GPIO state, and suspend/resume logs could not be independently checked.
Playback recovery and headphone-to-speaker routing were not separately
described in the user's response.

Assessment: the speaker fix has passed the reported listening cases, but the
prototype is not a complete fix for all analogue outputs. It gates the external
speaker amplifier and preserves the vendor codec ramp; it does not implement
an independent headphone mute. The saved RG SP kernel's LINEOUT PRE_PMD
callback calls `sunxi_ramp_event` before clearing output-enable bits at register
0x310. The headphone symptom alone does not establish whether the remaining
transient is caused by that ramp, later DAC power-down, application audio, or a
regression from the added delay. Compare headphone playback/close with and
without the module before changing the codec sequence.

A temporary read-only sampler was started as PID 2050 on the boot above,
writing `/tmp/h700-amp-validation.log`. Collect its log and stop that process
after reconnecting, verifying its identity first; reboot also removes it.
No persistent module loading has been installed.

## Initialization profiling and kernel-specific address profile, 2026-09-17

Five instrumented inspection loads measured 89.023–105.245 ms inside module
initialization. Almost all time was spent in six `kallsyms_lookup_name` calls:
kernel-banner lookup, speaker callback lookup, two codec-list/lock lookups,
and two callback validation lookups. In those five samples, subtracting the
individual lookup durations from total initialization left 83–94 microseconds.

The optimized module uses `h700_kernel_profile.h`: offsets relative to the
exported `kallsyms_lookup_name` address, resolved by the module loader. It does
not call that function. Offsets were generated from the saved RG SP symbol
table and checked against the live device after reconfirming the full kernel
Image SHA-256 above. All compile-time layout assertions, kernel-banner/stub
checks, device-tree/GPIO checks, widget checks, audio-idle checks and locking
remain. The configured amplifier settling delay is unchanged.

These offsets are part of the exact-kernel build profile and must be regenerated
and independently verified for a different kernel. Before loading this profile
on a device, verify the kernel image: its early identity checks themselves use
profile-derived addresses, so they are not a safe probe for arbitrary kernels.
A production loader must select only a verified matching profile.

Five optimized instrumented loads measured initialization at 206, 87, 88, 99,
and 101 microseconds. External BusyBox timing rounded every inspection load to
0.00 seconds (10 ms resolution). All 17 imported symbol CRCs matched the
verified vendor kernel. A non-instrumented build passed direct 48 kHz stereo
silence playback: amplifier high while PCM RUNNING, then low with PCM closed.
This is electrical/state validation, not a new acoustic listening result.

The device's persistent test module was replaced with this optimized build;
the existing background startup hook was retained. The earlier 89–105 ms
profiling build was temporary and is not the installed module.

### Early boot validation: unresolved jack-control overwrite

After reset recovery, six alternating baseline/optimized boots using NextUI's
`reboot_next` all returned without intervention. Avoid this device's normal
BusyBox/init reboot path: the user reported it can leave the hardware in limbo.
Median kernel-to-frontend handoff was 2.15 s baseline and 2.17 s optimized,
with overlapping ranges. Optimized module load took 40–50 ms and finished
120–140 ms before frontend handoff. These boots included /data journal replay
and a FAT dirty-volume warning, so they are not clean-filesystem benchmarks.

On all three optimized boots, GPIO 261 was low at attachment but high at idle
when ADB connected, with no module enable event. Kernel disassembly shows
`sunxi_jack_det_work -> allen_spk_ctrl -> snd_sunxi_pa_pin_enable` can enable
the amplifier independently of DAPM when headphones are absent. That is a
strong explanation for the observed overwrite; the live write was not traced.
Early module loading therefore does not yet guarantee initial idle-off or
first-playback pop suppression. Jack changes can also bypass these callbacks.
This needs coordinated amplifier ownership, not just faster initialization.

A post-boot silence playback/close returned the amplifier low. The optimized
module and background hook remain installed for the user's experiment, with
audio closed and GPIO low at the final check. The complete timing table and
boot IDs are in NextUI's docs/triage/h700-speaker-module-timing.md.

## Headphone investigation, 2026-09-17

The user confirmed acceptable speaker behavior at boot and first game launch;
further early-boot amplifier investigation was deprioritized. Headphone trials
with analogue mute and speaker-amplifier gating did not remove the exit pop.
The original speaker-only module was restored after those failed trials.

A subsequent [buffer-retention prototype](buffer-retention/README.md) passed
automatic state checks and the consolidated headphone listening test: the user
reported "No exit pop; audio works". It keeps the line-output buffers enabled
between streams while allowing the DACs to power off. No NextUI changes are
required. The prototype is currently loaded temporarily; the persistent module
and boot hook remain unchanged, so reboot restores the speaker-only version.

See [headphone-investigation.md](headphone-investigation.md) for the evidence,
earlier failed trials and remaining validation. Idle power, actual hardware
suspend/resume and other targets still need validation before shipping.
