# RG SP kernel compression assessment

## Result and scope

The experimental Android v2 image with a gzip-compressed kernel and a narrowly
patched vendor U-Boot booted successfully on RG SP. The user's 240 fps video
comparison reported approximately 60 frames, or **250 ms**, saved from LED-on
to first UI frame. This is the first hardware result; repeated-trial count,
absolute boot durations and variability were not reported.

The normal BaseOS log starts from kernel uptime and cannot measure the reduced
U-Boot load/decompression interval. This result is not a new kernel-to-frontend
handoff figure for the v1.3.0 release.

The tested device runs vendor AArch64 Linux 4.9.170 and ARM32 Thumb U-Boot
2018.05, built June 24, 2026. Exact matching vendor source/config was not
located. This is a pinned binary patch, not a source rebuild.

| Input | SHA-256 |
| --- | --- |
| Original U-Boot, 1,048,576 bytes | `a11271e1276173cee48bd5705d068fd8446cfc0ffb43fca70b3b6808c2e9d0db` |
| Original kernel, 17,686,536 bytes | `3328c2fab9f19f7ea6a80e34b0a76238cf594bc631f77e05fec32f8adabad042` |

The RG SP binary differs from the RG34XX SP binary investigated elsewhere;
these offsets must not be applied to another target or firmware revision.

## Why this path

| Option | Finding |
| --- | --- |
| Compressed Android kernel with untouched U-Boot | The Android handler forces `IH_COMP_NONE`; compressed bytes are copied to the kernel entry address. Negative-control emulation confirms this does not reconstruct the kernel. |
| Android image with existing gzip decoder | Selected. One instruction changes compression selection while retaining the established Android flow, original kernel load address, ramdisk and DTB. |
| Legacy gzip uImage / multi-image | Generic decompression exists, but the vendor `bootm` command first copies a ramdisk using Android header fields. A conventional uImage reaches an invalid copy to `0x840` before generic handling. |
| FIT kernel | The image recognizer recognizes FIT, but this binary's OS-selection path handles legacy and Android formats, not FIT. The Android-specific command preamble is a further obstacle. |
| LZMA | Existing decoder works, but approximately 1.00 s decode time outweighs its additional reduction in SD reads. |
| Self-decompressing AArch64 wrapper with LZ4 | Promising cached decode time, but a much slower uncached-memory proxy exposes the need for early-boot cache/MMU work and handoff validation. Wider scope than the selected approach. |
| Replacement U-Boot | Requires broader platform bring-up; no reproducible source/config matching this vendor binary was established. |
| Parallel decompression | Dropped at the user's request before any multicore device test; independent chunks and secondary-core startup add complexity. |

ARM64 raw `Image` has no built-in decompressor; the bootloader must decompress
it. See the [Linux ARM64 boot protocol](https://docs.kernel.org/arch/arm64/booting.html).

## Exact boot change

The active Allwinner TOC1 package starts at card offset `0x1004000`, length
`0x140000`. Its U-Boot item starts at package offset `0x800`, so the component
is at card offset `0x1004800`. U-Boot's linked base is `0x4a000000`.

At U-Boot file offset `0xf698`:

```text
4f f4 00 73    mov.w r3, #0x200    kernel type 2, compression 0
40 f2 01 23    movw  r3, #0x201    kernel type 2, compression 1 (gzip)
```

The embedded U-Boot checksum at offset `0x0c` and TOC1 checksum at offset `0x14`
are regenerated. Both original checksums reproduce with the Allwinner additive
algorithm, substituting stamp `0x5f0a6c39` for the checksum field before summing
little-endian words. See the [TOC1 format implementation](https://lists.u-boot-project.org/pipermail/u-boot/2021-October/463731.html).
Only these two four-byte fields and the one four-byte instruction change in
the package. Monitor, DTBO and DTB items are preserved byte-for-byte.

There is also stale Rockchip FIT data at `0x1000000` in the vendor prefix,
partly overwritten by TOC1. It is not the active boot package; it must not be
used to infer FIT-kernel support or to regenerate this package's integrity data.

The Android v2 image retains its 2048-byte page size, ramdisk, appended DTB and
load addresses. Its kernel-size field and component SHA-1 are updated. The same
SHA-1 algorithm reproduces the original header. The original 17.69 MB kernel
fits the vendor decoder's 32 MiB output allowance. Output at `0x40080000`,
ramdisk at `0x42000000` and compressed input at `0x45000000` do not overlap.

The raw boot partition is 64 MiB, but the `sunxi_flash read` command reads a
32 KiB prefix and derives the remaining length from Android component sizes,
including the v2 DTB. Emulation of that actual command confirms:

| Image | Bytes requested from SD |
| --- | ---: |
| Original Android image | 20,436,992 |
| Gzip Android image | 9,842,688 |
| Reduction | 10,594,304 |

Installation writes the meaningful gzip image at the beginning of p4 and
leaves bytes after its new end untouched. Those bytes are outside the image
length requested by U-Boot. Full-region and full-partition expected hashes
account for this preserved tail.

## Component measurements

Linux userspace medians exclude the first decoder iteration. Every decoded
output was compared byte-for-byte with the original kernel. The gzip/LZMA
probe executes the actual vendor decoder code with allocator/free/output calls
redirected to userspace functions; it was checked under QEMU before hardware.
It was pinned to CPU 0. Repeat gzip sampling was predominantly at 1512 MHz.

| Encoding | Kernel bytes | Decode median |
| --- | ---: | ---: |
| Raw | 17,686,536 | No decompression |
| gzip-1 | 7,593,929 | 0.272405 s |
| gzip-6 | 7,097,454 | 0.247199 s |
| gzip-9, selected | 7,092,638 | 0.247862 s |
| LZMA | 5,156,192 | 1.000566 s |
| LZ4 HC12, minimal cached decoder | 8,497,338 | 0.100031 s |

gzip-6 and gzip-9 were effectively tied for decode speed. Compression occurs
once during preparation, so gzip-9's slightly smaller image was selected.
A repeat gzip run measured a 0.247248 s median.

The same LZ4 decoder took about 4.03 s with uncached ION buffers and about
0.10 s with cached ION buffers. These are memory-attribute proxies, not a
measurement of an actual early-boot wrapper. They reject assuming the cached
userspace time would apply with the MMU disabled, without ruling out a more
complex optimized wrapper. Decoder format reference:
[LZ4 block specification](https://github.com/lz4/lz4/blob/dev/doc/lz4_Block_format.md).

Seven alternating `O_DIRECT` trials on p4 measured approximately 23.7 MB/s.
The tested read sizes predated final inclusion of the appended DTB, so the
estimate uses throughput rather than treating their timings as final-image
boot times:

```text
saved time ≈ 10,594,304 / 23,700,000 − 0.248 ≈ 0.20 seconds
```

The observed approximately 0.25 s hardware improvement is consistent with
that estimate. U-Boot clocks, caches and SD driver were not independently
timed; savings may vary with cards, firmware and devices.

## Validation and recovery limits

Unicorn under Linux/OrbStack executes the vendor Android command preamble and
bootm START, FINDOS, FINDOTHER and LOADOS states. Original/raw and
original/gzip controls accompany the patched/gzip case. The final patched
binary, including its checksum, reconstructs the exact original kernel and
ramdisk. Board reservations, FDT mutations, IRQ/cache operations, console and
allocation services are modeled or intercepted. This is not full SoC emulation.

Before live installation, the installer passed ordinary-file tests for success,
explicit paired rollback, and automatic rollback after injected verification
failure. Live installation verified original firmware hashes, saved backups on
the Mac and TF1, wrote the pair, and compared direct-read hashes of the complete
36 MiB boot region and 64 MiB boot partition. Environment, boot0, partition
tables and rootfs were unchanged. The user then confirmed boot and video timing.

`CONFIG_KEXEC` is disabled. The vendor fastboot `boot` path has incompatible
header assumptions and was not established as a safe RAM-only boot route.
No validated bootloader-level automatic fallback was found.

The installation script can compensate for a command or verification failure
while Linux is running. It cannot recover from power loss, or run after a broken
bootloader/kernel prevents Linux starting. The paired original backups permit
offline SD-card repair; the existing BaseOS rootfs A/B rollback does not cover
this shared boot pair.

## Follow-up before release integration

1. Add automatic raw/gzip selection. The current experimental patch forces gzip.
   [Upstream U-Boot v2020.01](https://github.com/u-boot/u-boot/blob/v2020.01/common/bootm.c#L141)
   already uses `android_image_get_kcomp()` in its Android path; porting that
   behavior to this vendor binary/source still needs implementation and tests.
2. Build reproducibly from verified prepared inputs, enforce exact per-firmware
   hashes and decompressed size/load bounds, and preserve original trust anchors.
3. Repeat cold boots, reboots, charging-only boots and recovery checks on RG SP.
   Validate other models independently before enabling them.
4. Enable the pair for fresh RG SP images after that validation. The v1.3.0 image
   path on `main` remains unchanged by this experiment branch.
5. Design existing-card migration separately. Current `.bosupd` archives contain
   only `manifest` and `rootfs.img.gz`; neither the builder nor updater replaces
   U-Boot/p4. Future migration support must address interruption and recovery;
   backups and readback verification alone are not a power-loss-safe commit.

Ordinary `.bosupd` updates preserve this already-installed experiment because
they leave both boot components untouched. A rootfs rollback likewise leaves
the shared boot pair in place.
