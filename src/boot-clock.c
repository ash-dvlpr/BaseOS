/* H700 first-handoff timing. CNTVCT includes time before Linux timekeeping;
 * MONOTONIC_RAW shares Linux's initial uptime origin without NTP frequency
 * correction. Hardware reset/clock comparison was measured on RG SP; other
 * H700 targets share this counter interface and fall back if it is unavailable.
 * Output: legacy BOOTTIME centiseconds, pre/raw-post/combined seconds.
 */
#define _POSIX_C_SOURCE 200809L
#include <inttypes.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <time.h>
#include <unistd.h>

static void unavailable(int signal_number)
{
	(void)signal_number;
	_exit(1);
}

static uint64_t counter(void)
{
	uint64_t value;
	__asm__ volatile("isb; mrs %0, cntvct_el0" : "=r"(value) : : "memory");
	return value;
}

static uint64_t nanoseconds(uint64_t ticks, uint64_t frequency)
{
	return ticks / frequency * UINT64_C(1000000000) +
	       ticks % frequency * UINT64_C(1000000000) / frequency;
}

static uint64_t time_ns(struct timespec t)
{
	return (uint64_t)t.tv_sec * UINT64_C(1000000000) + t.tv_nsec;
}

static void seconds(uint64_t ns)
{
	uint64_t ms = (ns + 500000) / 1000000;
	printf("%" PRIu64 ".%03" PRIu64, ms / 1000, ms % 1000);
}

int main(void)
{
	/* A kernel may forbid userspace counter reads. Let the shell retain its
	 * original uptime-only path, without leaving a core dump or blocking boot.
	 */
	if (signal(SIGILL, unavailable) == SIG_ERR)
		return 1;
	uint64_t frequency;
	__asm__ volatile("mrs %0, cntfrq_el0" : "=r"(frequency));
	if (frequency != 24000000)
		return 1;

	struct timespec raw, boot;
	uint64_t before = counter();
	if (clock_gettime(CLOCK_MONOTONIC_RAW, &raw))
		return 1;
	uint64_t after = counter();
	if (clock_gettime(CLOCK_BOOTTIME, &boot) || after < before)
		return 1;
	/* Reject a descheduled sample rather than publish an imprecise split. */
	if (after - before > frequency / 10000)
		return 1; /* >100 us */
	uint64_t total = nanoseconds(before + (after - before) / 2, frequency);
	uint64_t post = time_ns(raw);
	if (post > total)
		return 1;
	uint64_t legacy_cs = time_ns(boot) / 10000000;
	printf("%" PRIu64 ".%02" PRIu64 " ", legacy_cs / 100, legacy_cs % 100);
	seconds(total - post);
	putchar(' ');
	seconds(post);
	putchar(' ');
	seconds(total);
	putchar('\n');
	return fflush(stdout) == 0 ? 0 : 1;
}
