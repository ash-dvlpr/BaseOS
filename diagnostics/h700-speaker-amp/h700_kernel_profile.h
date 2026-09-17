/* SPDX-License-Identifier: GPL-2.0 */
/* RG SP 4.9.170 #2, 2026-06-24 19:15:50 CST.
 * Image SHA-256: 3328c2fab9f19f7ea6a80e34b0a76238cf594bc631f77e05fec32f8adabad042
 * Offsets from exported kallsyms_lookup_name, independently verified against
 * this Image and live /proc/kallsyms. Regenerate/verify for every kernel change.
 * The exported anchor follows the kernel's relocation; no absolute VA is used.
 */
#ifndef H700_KERNEL_PROFILE_H
#define H700_KERNEL_PROFILE_H
#define H700_LINUX_BANNER_OFFSET        0x881868UL
#define H700_SPK_EVENT_OFFSET           0x62f788UL
#define H700_CLIENT_MUTEX_OFFSET        0xfd7498UL
#define H700_CODEC_LIST_OFFSET          0xfd7580UL
#define H700_LINEOUT_EVENT_OFFSET       0x630340UL

static inline unsigned long h700_kernel_address(unsigned long offset)
{
    return (unsigned long)&kallsyms_lookup_name + offset;
}
#endif
