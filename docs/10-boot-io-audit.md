# 10 — Boot SD-card I/O

`/run`, `/tmp`, `/var` and `/dev` are RAM-backed; `/proc` and `/sys` are kernel
interfaces. The root filesystem, `/data` and frontend card are persistent.
The vendor initramfs mounts root read-write.

## Runtime policy

- Boot, session/installer, ADB and system-update diagnostics append to a single
  `/mnt/sdcard/baseos-boot.log` on the active frontend card (TF2, otherwise TF1).
  Each boot starts a new heading; existing history is retained.
- The first kernel-to-frontend handoff time is recorded in that log and retained
  in `/run/boot-frontend-exec` across session respawns. Verbose profiling is off.
- Update checks and storage expansion run before the card is mounted. They use
  `/data/baseos-boot.log` as an early buffer (persistent when p6 mounted);
  rcS or the next successful session mount appends it to the card log and removes
  it only after success.
  The routine “already expanded” check also logs its result.
- Session and ADB logs fall back to `/tmp/baseos-boot.log` for missing or
  unwritable cards. System-update diagnostics use the persistent `/data` buffer
  in that case. USB storage mode uses RAM and never writes to the host-owned
  frontend volume. The early `/data` buffer remains available for recovery if
  no card can be mounted.
- Frontend-owned logs, Slot's `/tmp/slot.log`, and the NTP daemon's RAM log keep
  their own destinations. Existing separate log files are not deleted or migrated.
- `/mnt/SDCARD` is baked into the rootfs; compatibility repair runs only when needed.
- Frontend mounts use `noatime`, including fallback mounts.

Keep the expansion geometry check, entropy restoration and shutdown saving,
machine identity, SSH keys, preferences, update verification/rollback and
shutdown flushing. These provide required persistence and recovery behavior.

## Reviewing I/O changes

Keep useful boot diagnostics accessible. Avoid rewriting unchanged settings.
Flush at save, update and shutdown boundaries; logging itself adds no `sync`.

Frontend initialization occurs after the BaseOS handoff marker. Review its
logging, settings and recent-game writes in the frontend project, and measure
the effect on time to the first usable frame separately.

File operations do not map one-to-one to physical SD-card I/O: buffered writes
can coalesce and repeated reads can hit cache. Measure block I/O before
claiming physical write savings.
