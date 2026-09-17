// SPDX-License-Identifier: GPL-2.0
/* Experimental runtime repair for ONE verified RG SP vendor kernel.
 * No kernel text, device-tree, pin mux, or boot partition changes.
 * Default mode only inspects. See README.md before using apply=1.
 */
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/kallsyms.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/gpio/consumer.h>
#include <linux/gpio/driver.h>
#include <linux/gpio.h>
#include "gpiolib.h"
#include <linux/delay.h>
#include <linux/clk.h>
#include <linux/clk-provider.h>
#include <linux/suspend.h>
#include <linux/reboot.h>
#include <sound/soc.h>
#include "h700_kernel_profile.h"

static bool apply;
module_param(apply, bool, 0400);
MODULE_PARM_DESC(apply, "Attach the temporary fix; default is inspection only");

typedef int (*event_fn)(struct snd_soc_dapm_widget *, struct snd_kcontrol *, int);
static struct mutex *soc_mutex;
static struct snd_soc_card *target_card;
static struct snd_soc_dapm_widget *speaker, *lineout;
static event_fn old_speaker_event, old_lineout_event;
static struct gpio_desc *amp;
static bool attached, lineout_ready;
static int initial_pin;
static unsigned int settle_ms;
static struct snd_soc_codec *target_codec;
static struct clk *codec_clk;
static unsigned int down_step;
static bool buffers_retained, power_preparing;
#define DAC_ANALOG 0x310
#define RAMP_REG 0x31c
#define OUTPUT_ENABLE 0x2800

/* Exact vendor ramp_msleep_time table, recovered from the verified Image.
 * Use exported codec/clock APIs, not the vendor driver's private structure.
 */
static const unsigned int ramp_wait_ms[2][8] = {
	{ 8, 15, 28, 56, 110, 138, 218, 273 },
	{ 8, 14, 26, 51, 101, 126, 202, 252 },
};

static int ramp_down_keep_buffers(void)
{
	unsigned long rate = clk_get_rate(codec_clk);
	int family, ret;
	if (rate == 45158400)
		family = 0;
	else if (rate == 49152000)
		family = 1;
	else
		return -EINVAL;
	if ((snd_soc_read(target_codec, DAC_ANALOG) & OUTPUT_ENABLE) != OUTPUT_ENABLE)
		return -EINVAL;
	ret = snd_soc_update_bits(target_codec, DAC_ANALOG, BIT(9), 0);
	if (ret < 0) return ret;
	ret = snd_soc_update_bits(target_codec, DAC_ANALOG, BIT(8), BIT(8));
	if (ret < 0) return ret;
	ret = snd_soc_update_bits(target_codec, RAMP_REG, 0x70, down_step << 4);
	if (ret < 0) return ret;
	ret = snd_soc_update_bits(target_codec, RAMP_REG, BIT(0), 0);
	if (ret < 0) return ret;
	msleep(ramp_wait_ms[family][down_step]);
	buffers_retained = true;
	pr_info("h700_retain: ramp-down rate=%lu step=%u wait=%u analog=%08x digital=%08x\n",
		rate, down_step, ramp_wait_ms[family][down_step],
		snd_soc_read(target_codec, DAC_ANALOG), snd_soc_read(target_codec, 0));
	return 0;
}

/* Called under the card DAPM mutex after our ramp has already completed. */
static int release_idle_buffers(void)
{
	int ret;
	if (!buffers_retained)
		return 0;
	ret = snd_soc_update_bits(target_codec, DAC_ANALOG, OUTPUT_ENABLE, 0);
	if (ret < 0)
		return ret;
	buffers_retained = false;
	pr_info("h700_retain: idle buffers released analog=%08x\n",
		snd_soc_read(target_codec, DAC_ANALOG));
	return 0;
}

static int power_notify(struct notifier_block *nb, unsigned long action, void *data)
{
	int ret = 0;
	if (!attached)
		return NOTIFY_DONE;
	mutex_lock(&target_card->dapm_mutex);
	switch (action) {
	case PM_SUSPEND_PREPARE:
	case PM_HIBERNATION_PREPARE:
	case PM_RESTORE_PREPARE:
		power_preparing = true;
		ret = release_idle_buffers();
		break;
	case PM_POST_SUSPEND:
	case PM_POST_HIBERNATION:
	case PM_POST_RESTORE:
		power_preparing = false;
		break;
	}
	mutex_unlock(&target_card->dapm_mutex);
	pr_info("h700_retain: PM action=%lu result=%d\n", action, ret);
	return ret < 0 ? NOTIFY_BAD : NOTIFY_OK;
}

static int reboot_notify(struct notifier_block *nb, unsigned long action, void *data)
{
	int ret;
	if (!attached)
		return NOTIFY_DONE;
	mutex_lock(&target_card->dapm_mutex);
	power_preparing = true;
	ret = release_idle_buffers();
	mutex_unlock(&target_card->dapm_mutex);
	return ret < 0 ? NOTIFY_BAD : NOTIFY_OK;
}

static struct notifier_block power_nb = { .notifier_call = power_notify };
static struct notifier_block reboot_nb = { .notifier_call = reboot_notify };

/* All callbacks run under the SAME card DAPM mutex, including mixer changes.
 * Gate LINEOUT as well as SPK: equal-priority endpoint ordering must never
 * allow the codec's ramp-down to run before the amplifier is disconnected.
 */
static int set_amp(bool enabled)
{
	if (gpiod_get_raw_value_cansleep(amp) == enabled)
		return 0;
	if (enabled)
		msleep(settle_ms);
	gpiod_set_raw_value_cansleep(amp, enabled);
	if (gpiod_get_raw_value_cansleep(amp) != enabled) {
		pr_err("h700_amp: GPIO readback failed enabled=%d\n", enabled);
		return -EIO;
	}
	if (!enabled)
		msleep(settle_ms);
	pr_info("h700_amp: amplifier=%d\n", enabled);
	return 0;
}

static int speaker_event(struct snd_soc_dapm_widget *w,
			 struct snd_kcontrol *k, int event)
{
	if (event == SND_SOC_DAPM_PRE_PMD)
		return set_amp(false);
	if (event == SND_SOC_DAPM_POST_PMU && lineout_ready)
		return set_amp(true);
	return 0;
}

static int lineout_event(struct snd_soc_dapm_widget *w,
			 struct snd_kcontrol *k, int event)
{
	int ret;
	if (event == SND_SOC_DAPM_PRE_PMD) {
		lineout_ready = false;
		ret = set_amp(false);
		if (ret)
			return ret;
		if (!power_preparing) {
			ret = ramp_down_keep_buffers();
			if (!ret)
				return 0;
			pr_warn("h700_retain: ramp failed=%d; using vendor shutdown\n", ret);
		}
	}
	ret = old_lineout_event(w, k, event);
	if (ret)
		return ret;
	if (event == SND_SOC_DAPM_POST_PMU) {
		buffers_retained = false;
		lineout_ready = true;
		if (speaker->new_power && speaker->connected)
			return set_amp(true);
	}
	return 0;
}

static int check_identity(void)
{
	struct device_node *np;
	u32 pin[7], level, count, ramp_time;
	const char *banner = (void *)h700_kernel_address(H700_LINUX_BANNER_OFFSET);
	unsigned long stub = h700_kernel_address(H700_SPK_EVENT_OFFSET);
	static const u32 stub_code[] = {0x52800000, 0xd65f03c0};
	int ret = -ENODEV;

	if (!banner || !strstr(banner, "#2 SMP PREEMPT Wed Jun 24 19:15:50 CST 2026") ||
	    !strstr(banner, "Linux version 4.9.170 ") || !stub ||
	    memcmp((void *)stub, stub_code, sizeof(stub_code))) {
		pr_err("h700_amp: unsupported kernel or nonempty speaker callback\n");
		return -ENODEV;
	}
	np = of_find_node_by_path("/soc@03000000/codec@0x05096000");
	if (!np) {
		pr_err("h700_amp: codec device-tree node not found\n");
		return -ENODEV;
	}
	if (of_property_read_u32_array(np, "pa-pin-0", pin, 7) ||
	    of_property_read_u32(np, "pa-pin-level-0", &level) ||
	    of_property_read_u32(np, "pa-pin-max", &count) ||
	    of_property_read_u32(np, "pa-pin-msleep-0", &settle_ms)) {
		pr_err("h700_amp: incomplete amplifier properties\n");
		goto out;
	}
	/* PI5, output mux, active high, one amplifier. GPIO numbering is part
	 * of this exact kernel profile, not a general mapping for H700 boards.
	 */
	if (pin[1] != 8 || pin[2] != 5 || pin[3] != 1 ||
	    level != 1 || count != 1 || !settle_ms || settle_ms > 500) {
		pr_err("h700_amp: unsupported pin=%u/%u mux=%u level=%u count=%u delay=%u\n",
		       pin[1], pin[2], pin[3], level, count, settle_ms);
		goto out;
	}
	amp = gpio_to_desc(261);
	/* This vendor GPIO chip has no get_direction operation (-EINVAL).
	 * Read the same descriptor output flag used by debugfs instead.
	 * Offset/bit verified in the vendor gpiod_get_direction disassembly.
	 */
	if (!amp || !test_bit(FLAG_IS_OUT, &amp->flags) ||
	    !test_bit(FLAG_REQUESTED, &amp->flags)) {
		pr_err("h700_amp: GPIO is not an existing requested output\n");
		goto out;
	}
	initial_pin = gpiod_get_raw_value_cansleep(amp);
	if (initial_pin != 0 && initial_pin != 1)
		goto out;
	if (!of_property_read_bool(np, "ramp-en") ||
	    of_property_read_u32(np, "ramp-time-down", &ramp_time) ||
	    !ramp_time || ramp_time > 8)
		goto out;
	/* Vendor dev_probe subtracts one from the DT ramp-time-down setting. */
	down_step = ramp_time - 1;
	codec_clk = of_clk_get(np, 1);
	if (IS_ERR(codec_clk)) {
		ret = PTR_ERR(codec_clk);
		codec_clk = NULL;
		goto out;
	}
	if (strcmp(__clk_get_name(codec_clk), "codec_1x")) {
		clk_put(codec_clk);
		codec_clk = NULL;
		goto out;
	}
	ret = 0;
out:
	of_node_put(np);
	return ret;
}

static int __init amp_init(void)
{
	struct list_head *codecs;
	struct snd_soc_codec *codec, *found = NULL;
	struct snd_soc_dapm_widget *w;
	unsigned int matches = 0;
	int ret;

	/* Offsets independently recovered from this kernel's disassembly. */
	BUILD_BUG_ON(offsetof(struct snd_soc_codec, list) != 0x10);
	BUILD_BUG_ON(offsetof(struct snd_soc_codec, component) != 0x50);
	/* The driver loads component.dev at dapm - 0xc0: 0xd8 - 0x18. */
	BUILD_BUG_ON(offsetof(struct snd_soc_component, dapm) != 0xd8);
	BUILD_BUG_ON(offsetof(struct snd_soc_dapm_context, card) != 0x20);
	BUILD_BUG_ON(offsetof(struct snd_soc_card, dapm_mutex) != 0x58);
	BUILD_BUG_ON(offsetof(struct snd_soc_card, widgets) != 0x1d8);
	BUILD_BUG_ON(offsetof(struct snd_soc_dapm_widget, list) != 0x18);
	BUILD_BUG_ON(offsetof(struct snd_soc_dapm_widget, dapm) != 0x28);
	BUILD_BUG_ON(offsetof(struct snd_soc_dapm_widget, event_flags) != 0x78);
	BUILD_BUG_ON(offsetof(struct snd_soc_dapm_widget, event) != 0x80);
	BUILD_BUG_ON(offsetof(struct gpio_desc, flags) != 8);
	BUILD_BUG_ON(offsetof(struct notifier_block, notifier_call) != 0);
	BUILD_BUG_ON(offsetof(struct notifier_block, next) != 8);
	BUILD_BUG_ON(offsetof(struct notifier_block, priority) != 16);

	ret = check_identity();
	if (ret)
		return ret;
	soc_mutex = (void *)h700_kernel_address(H700_CLIENT_MUTEX_OFFSET);
	codecs = (void *)h700_kernel_address(H700_CODEC_LIST_OFFSET);
	if (!soc_mutex || !codecs) {
		clk_put(codec_clk);
		codec_clk = NULL;
		return -ENOENT;
	}
	mutex_lock(soc_mutex);
	list_for_each_entry(codec, codecs, list) {
		pr_info("h700_amp: codec=%s\n", codec->component.name);
		if (!strcmp(codec->component.name, "5096000.codec")) {
			found = codec;
			matches++;
		}
	}
	ret = -ENODEV;
	if (matches != 1 || !found->component.dapm.card)
		goto unlock_soc;
	target_card = found->component.dapm.card;
	target_codec = found;
	mutex_lock(&target_card->dapm_mutex);
	list_for_each_entry(w, &target_card->widgets, list) {
		if (w->dapm != &found->component.dapm)
			continue;
		if (!strcmp(w->name, "SPK")) speaker = w;
		if (!strcmp(w->name, "LINEOUT")) lineout = w;
	}
	if (!speaker || !lineout || speaker->id != snd_soc_dapm_spk ||
	    lineout->id != snd_soc_dapm_line ||
	    (unsigned long)speaker->event != h700_kernel_address(H700_SPK_EVENT_OFFSET) ||
	    (unsigned long)lineout->event != h700_kernel_address(H700_LINEOUT_EVENT_OFFSET) ||
	    speaker->event_flags != (SND_SOC_DAPM_POST_PMU | SND_SOC_DAPM_PRE_PMD) ||
	    lineout->event_flags != (SND_SOC_DAPM_POST_PMU | SND_SOC_DAPM_PRE_PMD)) {
		pr_err("h700_amp: widget identity/layout verification failed\n");
		goto unlock_card;
	}
	pr_info("h700_amp: verified SPK power=%u LINEOUT power=%u GPIO=%d delay=%u apply=%d\n",
		speaker->power, lineout->power, initial_pin, settle_ms, apply);
	ret = 0;
	if (!apply)
		goto unlock_card;
	if (speaker->power || lineout->power || found->component.active) {
		pr_err("h700_amp: close audio before attaching\n");
		ret = -EBUSY;
		goto unlock_card;
	}
	old_speaker_event = speaker->event;
	old_lineout_event = lineout->event;
	ret = set_amp(false);
	if (ret)
		goto unlock_card;
	lineout_ready = false;
	speaker->event = speaker_event;
	lineout->event = lineout_event;
	attached = true;
	pr_info("h700_amp: temporary callbacks attached\n");
unlock_card:
	mutex_unlock(&target_card->dapm_mutex);
unlock_soc:
	mutex_unlock(soc_mutex);
	if (!ret && attached) {
		ret = register_pm_notifier(&power_nb);
		if (!ret) {
			ret = register_reboot_notifier(&reboot_nb);
			if (ret)
				unregister_pm_notifier(&power_nb);
		}
		if (ret) {
			mutex_lock(soc_mutex);
			mutex_lock(&target_card->dapm_mutex);
			speaker->event = old_speaker_event;
			lineout->event = old_lineout_event;
			release_idle_buffers();
			set_amp(initial_pin);
			attached = false;
			mutex_unlock(&target_card->dapm_mutex);
			mutex_unlock(soc_mutex);
		}
	}
	if (ret || !attached) {
		clk_put(codec_clk);
		codec_clk = NULL;
	}
	return ret;
}

static void __exit amp_exit(void)
{
	if (!attached)
		return;
	unregister_reboot_notifier(&reboot_nb);
	unregister_pm_notifier(&power_nb);
	mutex_lock(soc_mutex);
	mutex_lock(&target_card->dapm_mutex);
	speaker->event = old_speaker_event;
	lineout->event = old_lineout_event;
	release_idle_buffers();
	set_amp(initial_pin);
	attached = false;
	mutex_unlock(&target_card->dapm_mutex);
	mutex_unlock(soc_mutex);
	clk_put(codec_clk);
	pr_info("h700_amp: original callbacks and GPIO restored\n");
}

module_init(amp_init);
module_exit(amp_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Experimental RG SP vendor speaker power-event repair");
