# 10 — Boot SD-card I/O audit

Audited on RG34XXSP on 2026-09-13. `/run`, `/tmp`, `/var` and `/dev` are
RAM-backed; `/proc` and `/sys` are kernel interfaces. The detailed boot trace
formerly used RAM, not the SD card. The root filesystem, `/data` and the frontend
card are persistent. The tested vendor initramfs mounts the root writable.

## BaseOS changes

- Removed routine boot/ADB breadcrumbs from `baseos-boot.log` and session-log
  mirroring to `baseos-session.log` on the card. Session diagnostics stay in
  `/tmp/nextui-session.log`.
- Removed detailed stage traces and service timing files. Only the first
  `/run/boot-frontend-exec` timestamp remains, on tmpfs.
- Stopped persisting the normal "already expanded" message. Actual expansion
  and errors still leave recovery information in `/data/expand.log`.
- Baked `/mnt/SDCARD` into the rootfs and guarded its compatibility repair,
  avoiding a rootfs symlink rewrite every boot.
- Added `noatime` to fallback frontend mounts, matching normal mounts.

Keep the expansion geometry check: its unchanged path reads only 1,536 bytes.
Keep entropy restoration and shutdown saving, machine identity, SSH keys,
preferences, update verification/rollback and shutdown flushing. These serve
specific persistence or recovery needs. No extra cache or persistent completion
flag is needed.

These are reductions in logical file operations. Buffered writes can coalesce,
and repeated reads can hit cache; physical block-write savings were not measured.

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
