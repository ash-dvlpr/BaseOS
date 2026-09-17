# Configurable headphone fix and boot regression, 2026-09-17

## Implementation

`headphone_pop_fix` is parsed as plain data by `baseos-config`. Missing or
invalid values default to false; true opts in. The setting is copied to the
effective `/run/baseos.conf`, not sourced as shell. The loader translates it
to numeric module parameter `headphone_pop_fix=0` or `1`; this vendor kernel
rejects the literal string `true` as a boolean module argument.

The current combined module source is `h700_speaker_amp.c` in this directory.
The historical `buffer-retention/` variant is the earlier always-on trial.
Both modes retain the speaker repair. Off mode uses the original vendor
LINEOUT shutdown and skips retention clock acquisition and PM/reboot notifier
registration. On mode retains output buffers after ramp-down and releases them
before kernel suspend or module unload. Configuration is read at load time;
users restart after editing TF1's `baseos.conf`.

The boot hook starts in the background after config and update processing,
rather than the previous device-local hook immediately after GPU loading.
USB-storage boots skip it. Builds lacking a validated module skip loading it;
release module compilation/packaging and other kernel profiles remain separate
work. There are no NextUI changes.

## Validation

- Configuration suite passed: missing/default, true, false, whitespace/CRLF,
  comments, malformed values, duplicate keys and shell-looking values.
- Loader suite passed: absent module/config, both settings, invalid values,
  shell-looking data and insertion failure propagation.
- Existing mDNS lifecycle suite passed; shell syntax and diff checks passed.
- Compiled for the verified RG SP kernel; all 28 imported symbol CRCs match.
  Disassembly contains no incompatible SP_EL0 current-task access.
- Six silent playback cycles: 44.1/48 kHz for omitted, enabled and explicitly
  disabled module parameters. Running PCM had DACs and speaker amplifier on.
  After close, both DACs and the amplifier were off. Register 0x310 was
  0x00151500 for omitted/false and 0x00153d00 for true. Thus default-off actually
  powers down the output buffers, rather than merely changing a setting label.
- Saved playback snapshots: [config-verification.json](config-verification.json).
- The earlier user listening test with retained buffers reported
  "No exit pop; audio works". No further acoustic test was requested.

Compiled module srcversion: `B81F7A6D23DADCF3C45B7D1`.

## Boot comparison

Same RG SP, BaseOS 1.2.1, card, frontend and USB connection. Nine alternating
warm reboots through NextUI `reboot_next`, never the problematic normal reboot
path. Times are kernel uptime at frontend exec, not power-on-to-visible-UI time.
Resolution is 10 ms. Every boot has a distinct boot ID in
[boot-results.json](boot-results.json).

The device runs an older config parser than current BaseOS main (before the
SSH-password extension). Only the headphone key handling was backported for
these device measurements; unrelated password/shadow changes were not deployed.
The current repository parser was tested independently by its regression suite.

| Variant | Three boots | Median | Range |
| --- | --- | --- | --- |
| Previous early speaker-only loader | 2.14, 2.18, 2.21 s | 2.18 s | 2.14–2.21 s |
| Reordered loader, headphone fix off | 2.17, 2.24, 2.20 s | 2.20 s | 2.17–2.24 s |
| Reordered loader, headphone fix on | 2.25, 2.16, 2.21 s | 2.21 s | 2.16–2.25 s |

Median differences were +20 ms and +30 ms, within overlapping run ranges.
All runs stayed below the requested 2.50-second ceiling. Three samples per
variant do not establish a precise causal cost or exclude smaller regressions.

The old module completed at uptime 2.02–2.09 s; the reordered module completed
at 2.25–2.34 s, after frontend handoff but before game audio. Moving later lets
the stock driver enable the amplifier first, so attachment can include the
existing 100 ms amplifier-off settling delay. It remains in the background;
insertion succeeded on every boot. No kernel Oops/BUG was observed.

`/data` was remounted read-only before each reboot; no journal recovery was
reported. The active frontend prevented remounting the FAT card read-only,
even with swap disabled. Every measured boot reported the same existing FAT
not-properly-unmounted warning. Therefore these are controlled comparisons on
this device, not clean-filesystem release qualification. No filesystem repair
or cold-start testing was performed.

## Final device state

The configurable module and reordered hook remain installed on the RG SP.
Timing instrumentation was removed from the loader. TF1 originally had no
baseos.conf; it now has the commented settings template. The effective setting
and loaded parameter are false/N, so headphone retention is off. Speaker repair
remains active. Set `headphone_pop_fix=true` in that file and restart to opt in.

Idle-current measurement, actual hardware suspend/resume and other device/kernel
profiles remain unvalidated. This change does not claim production qualification
of the module for every BaseOS target.
