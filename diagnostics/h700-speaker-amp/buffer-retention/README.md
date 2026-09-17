# RG SP line-output buffer retention trial

Experimental variant of the parent speaker amplifier module, for the same exact
verified RG SP kernel only. This is a BaseOS-only change; NextUI and ALSA userspace
configuration are unchanged. It is not the persistent boot module.

## Behaviour

On ordinary LINEOUT shutdown, reproduces the vendor's ramp-register updates and
clock-dependent wait but omits clearing register 0x310 bits13/11 (mask0x2800).
The normal DAC/mixer power-down then proceeds. Startup still uses the original
LINEOUT callback. Existing speaker-amplifier sequencing is unchanged.

The vendor DT stores ramp-time-down as a one-based value; dev_probe subtracts one.
This device's value5 means hardware step4, waiting101ms at49.152MHz or110ms at
45.1584MHz, matching the recovered vendor table. The clock is acquired through
of_clk_get(np,1), verified against codec_1x. All register operations use exported
snd_soc_read/update_bits functions; no new vendor-private structure offsets or
inline current-task accesses are introduced.

The PM preparation notifier disables retained idle buffers before device suspend;
active audio shutdown during PM preparation uses the original full callback.
Resume clears the preparation flag. Unload releases retained buffers and restores
callbacks. A conventional reboot notifier also releases idle buffers, but NextUI's
PMIC reboot/poweroff path bypasses conventional kernel shutdown: acoustic behaviour
at full power removal is not validated. Never use the device's normal reboot path
for recovery; use NextUI reboot_next.

## Build and checks

Built in baseos-speaker-amp-build with the prepared vendor-compatible 4.9.170 tree.
All28 imported symbol CRCs match the verified Image's export table. Existing ABI
layout assertions are retained; notifier_block offsets are asserted too. Generated
object checked for the incompatible SP_EL0 access involved in the earlier rejected
diagnostic; none is present.

SHA256: dac2375c0e6e0070e277a912f27b9fd606b64b054ecf9a94ede98523da8d7f16
srcversion: 55FFC102E579F9019B1F335

The following checks passed on-device:

- Inspection-only load, unload, active load.
- Four two-second silence cycles: direct48k, default44.1k, default48k, direct44.1k.
- Each running snapshot: PCM running, digital/analogue DACs enabled, amp high.
- Each idle snapshot: PCM closed, DACs off, ramp down, buffers still enabled,
  amp low. Register0x310 changed from0015fd00 to00153d00, not00151500.
- Kernel freezer-only suspend dry run: PM preparation released idle buffers
  (00151500), automatic return after5seconds, PM-post callback completed.
  This tests notifier dispatch/cleanup, NOT actual hardware suspend/resume.
- Playback recovered after the dry run.
- Unload cleared the retained bits; reload and playback worked afterward.
- pm_test restored to none.

See verification.json and device-validation.dmesg.

The consolidated RG SP listening test passed on 2026-09-17: headphones connected,
game launched at volume 0, volume raised to confirm audio, returned to 0, then
game exited. The user reported: "No exit pop; audio works".

Final readback after the manual test confirmed PCM closed, headphone GPIO 259
high, speaker-amplifier GPIO 261 low, digital DAC register 0x000 = 0x00000000,
analogue register 0x310 = 0x00153d00 (output buffers retained; analogue DACs off),
and ramp register 0x31c = 0x00000040. Loaded srcversion remained
55FFC102E579F9019B1F335. This supports buffer shutdown as the source of the exit
transient in this tested case; no analogue waveform was measured.

Idle-power impact, full hardware suspend/resume, full power-off acoustics and
other device/kernel targets remain unvalidated. This is a successful temporary
RG SP trial, not a production qualification.

## Device state / restore

Temporarily loaded from /tmp/h700_speaker_amp_retention.ko. Persistent
/lib/modules/h700_speaker_amp.ko and its background boot hook are unchanged.
A reboot returns to the original speaker-only module. With PCM closed:

```
rmmod h700_speaker_amp
insmod /lib/modules/h700_speaker_amp.ko apply=1
```

No other module with this name can be loaded at the same time. Do not remove or
unbind the underlying codec, sound card or GPIO controller while attached.
