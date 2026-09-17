# RG SP gzip kernel experiment

This branch preserves the single-core gzip experiment for work after the v1.3.0
release. It does not change the normal image builder, prepared-artifact trust
anchors, `.bosupd` format, or release defaults.

The experimental pair booted on an RG SP. The user reported approximately
**60 frames faster at 240 fps: 250 ms saved from LED-on to the first UI frame**.
The repeated-trial count and absolute baseline duration were not supplied.
BaseOS's kernel-relative boot log excludes this improvement.

The patch changes one Thumb instruction in the pinned vendor U-Boot to select
its existing gzip decoder for Android kernels. It **forces gzip**, so an
uncompressed Android kernel no longer boots with that patched loader. Revert
the bootloader and kernel together. This is a working experiment, not a
production implementation of automatic raw/gzip detection.

- [Assessment](ASSESSMENT.md): format support, measurements, constraints and
  production follow-up.
- [Recovery](install/RECOVERY.md): original-device backups and paired restore.
- [Evidence](evidence/): small measurement logs, disassembly, hashes and the
  recorded installation result. No video is included; timing is user-reported.
- `emulate*.py`: execute the actual vendor Thumb code with hardware-facing
  functions intercepted. These are function-level tests, not a complete SoC.
- `*-bench.c`, `lz4-decode.c`: the userspace decoder and storage probes used
  during the investigation. They are not part of the installed boot path.
- `install/prepare.py`: reproduce and verify the exact installed pair from
  retained inputs and original live backups. It writes host artifacts only.
- `install/test-installer.py`: exercise install, restore and failure recovery
  against ordinary temporary files in Linux.

## Local artifacts

Firmware, kernel images, generated executables and full card backups stay in
ignored `work/` storage. The source files read their artifact directory from
`RGSP_GZIP_WORK`; they do not expect binaries beside the committed code.

The original session's directory is `work/kernel-compression-rgsp` in the main
BaseOS checkout. From a separate worktree, set the absolute path to that original
directory rather than its own empty `work/` directory. For example, from the
main checkout:

```sh
export RGSP_GZIP_WORK="$PWD/work/kernel-compression-rgsp"
```

Required artifacts for the preserved validation are:

```text
$RGSP_GZIP_WORK/
  u-boot.bin                 pinned original 1 MiB vendor binary
  Image                      original uncompressed kernel
  Image.gz9                  original gzip-9 candidate
  ramdisk.gz                 original Android ramdisk
  vendor-dtb.dtb             original appended Android v2 DTB
  live-fdt.dtb               device's live flattened device tree
  boot-original.img          complete original Android image, without p4 tail
  boot-gzip-analysis.img     complete candidate used during initial analysis
  install/
    backup/rgsp-gzip-install/
      boot-region-original.bin       first 36 MiB of the original live card
      boot-partition-original.bin    full original 64 MiB p4
      env-original.bin               full original 16 MiB p3
```

These are firmware/session inputs, not a public test fixture. The preparer
checks exact hashes, including the original card's GPT-containing boot region.
It intentionally rejects a different device, firmware, or backup rather than
guessing offsets. Do not substitute the current patched card for the originals.
Generating a production pair from the prepared vendor cache is future work.

## Re-run host validation

Run from the checkout containing this experiment. Python validation must run
without `-O`; preparation and offline recovery explicitly reject optimized
Python so their assertions cannot be disabled.

```sh
python3 experiments/rgsp-kernel-gzip/install/prepare.py
```

On Linux, install `requirements.txt` into a disposable virtual environment and
run:

```sh
python3 experiments/rgsp-kernel-gzip/emulate-install.py
python3 experiments/rgsp-kernel-gzip/emulate-read.py
python3 experiments/rgsp-kernel-gzip/install/test-installer.py
```

On the original macOS host, Unicorn crashed while mapping memory; the successful
tests ran in an ARM64 Ubuntu 22.04 container under OrbStack. Mount the checkout
read-only at `/src`, the artifact directory at `/artifacts`, and set
`RGSP_GZIP_WORK=/artifacts`. Install Python 3, pip and the pinned requirements in
that disposable container before running the commands under `/src`.

Expected checks:

1. Original loader + original image recovers the original kernel.
2. Original loader + gzip image fails to recover the kernel (negative control).
3. Final patched binary + gzip image recovers the exact kernel and ramdisk.
4. The actual flash-read command requests 20,436,992 bytes for the original
   image and 9,842,688 bytes for the gzip image.
5. Simulated installation, explicit paired restore and automatic restore after
   an injected verification failure all pass.

For Thumb disassembly, `disasm.py START END` accepts file offsets or linked
addresses based at `0x4a000000`; it requires Capstone but does not execute code.

The vendor decoder benchmark is ARM32 (`arm-linux-gnueabihf-gcc -static`) and
must receive the pinned U-Boot binary. It redirects allocation/output calls and
executes the decoder in an ordinary userspace process. The LZ4 and read probes
were built for AArch64. `read-bench.c` only reads `/dev/mmcblk0p4`; the ION probe
uses temporary allocations and a cached/uncached flag argument. Multicore
decompression was dropped at the user's request and is not included here.

## Installation scripts are a session record

`install-on-device.sh` records the completed, explicitly requested installation.
It is pinned to the original serial, boot ID, partition geometry and hashes.
After that boot session it deliberately refuses to run. It is not an updater
to distribute to other cards. The paired restore scripts and original backups
remain usable for the tested card.

No command in the host validation procedure writes a live device. The separate
restore tools do write the boot components when explicitly invoked; read the
recovery instructions first.
