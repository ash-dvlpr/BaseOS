# 10 — Boot SD-card I/O audit

Audited on RG34XXSP on 2026-09-13. `/run`, `/tmp`, `/var` and `/dev` are
RAM-backed; `/proc` and `/sys` are kernel interfaces. The detailed boot trace
formerly used RAM, not the SD card. The root filesystem, `/data` and the frontend
card are persistent. The tested vendor initramfs mounts the root writable.

## BaseOS changes

- Boot, session/installer, ADB and system-update diagnostics append to a single
  `/mnt/sdcard/baseos-boot.log` on the active frontend card (TF2, otherwise TF1).
  Each boot starts a new heading; existing history is retained.
- The first kernel-to-frontend handoff time is recorded in that log and retained
  in `/run/boot-frontend-exec` across session respawns. Verbose profiling is off.
- Update checks and storage expansion run before the card is mounted. They use
  `/data/baseos-boot.log` as a persistent early buffer; rcS or the next successful
  session mount appends it to the card log and removes it only after success.
  The routine “already expanded” message is restored.
- Session and ADB logs fall back to `/tmp/baseos-boot.log` for missing or
  unwritable cards. System-update diagnostics use the persistent `/data` buffer
  in that case. USB storage mode uses RAM and never writes to the host-owned
  frontend volume. The early `/data` buffer remains available for recovery if
  no card can be mounted.
- Frontend-owned logs, Slot's `/tmp/slot.log`, and the NTP daemon's RAM log keep
  their existing behavior. Old separate log files are not deleted or migrated.
- Baked `/mnt/SDCARD` into the rootfs and guarded its compatibility repair,
  avoiding a rootfs symlink rewrite every boot.
- Added `noatime` to fallback frontend mounts, matching normal mounts.

Keep the expansion geometry check: its unchanged path reads only 1,536 bytes.
Keep entropy restoration and shutdown saving, machine identity, SSH keys,
preferences, update verification/rollback and shutdown flushing. These serve
specific persistence or recovery needs. No extra cache or persistent completion
flag is needed.

The RG34XXSP logging A/B test used five clean boots per variant, with identical
messages and only the destination changed. Median handoff was 2.27 s for RAM
and 2.28 s for SD; median NextUI process start was 3.72 s and 3.71 s respectively.
Those differences did not establish a boot-speed benefit from RAM logging.
The original messages added only 190 logical bytes per disk boot. These results
do not measure frontend rendering or guarantee the same result on every card.

Keep useful boot diagnostics accessible. Avoid rewriting unchanged settings.
Flush at save, update and shutdown boundaries; logging itself adds no `sync`.

Buffered writes can coalesce, and repeated reads can hit cache; physical
block-write savings were not measured.

## NextUI follow-ups

These operations occur after the BaseOS handoff marker and need a separate
frontend change and first-frame measurement. BaseOS does not patch the frontend.

The deployed launcher was captured with SHA256
`2ec1433022a2f05738e635850dc16bee28924b794b87a3c7feec4a9e7e262d18`:

- It writes routine launcher, monitor and UI logs under `.userdata/h700/logs`.
  Use a RAM log directory and make the unconditional `ldd`/library-listing
  diagnostics optional.
- It calls global `sync` after touching `/tmp/nextui_exec`. The sentinel is
  already on tmpfs; remove this particular flush while retaining saves and
  update/shutdown flushes.

NextUI source revision `7637f96b55486b5619e525c094cfcfdb4748c941` has two further
findings. This checkout has not been matched to the deployed binaries:

- `workspace/h700/libmsettings/msettings.c`: initialization calls six setters
  with restored values; each setter saves the settings file and calls `sync()`.
  Keymon, audiomon and NextUI each initialize settings, requesting 18 rewrites
  and flushes before counting the initial audio-sink save. Apply restored values
  to hardware without saving unchanged state; preserve initial creation,
  migrations and genuine preference changes.
- `workspace/all/nextui/nextui.c`: reading/filtering recent games always rewrites
  `recent.txt`. Save only when the list actually changes, including filtering
  missing entries or resolving disc changes.

Battery monitoring already keeps its one-second status in RAM and persists
meaningful history changes. It is not a per-second SD database writer.

Captured scripts, detailed source references and experiment records are retained
locally in `work/governor-boot-rg34xxsp/`.
