/* Exceptional charger boot: block until a fresh POWER hold reaches one second.
 * No idle timer wakeups or service loop. The caller owns power policy.
 */
#include <errno.h>
#include <fcntl.h>
#include <glob.h>
#include <linux/input.h>
#include <poll.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>

/* Allwinner disp2 ABI, also used by the H700 frontend. These vendor kernels
 * expose no /sys/class/backlight; fb0/blank only disables the framebuffer layer.
 */
#define DISP_LCD_SET_BRIGHTNESS 0x102
#define DISP_LCD_GET_BRIGHTNESS 0x103
#define POWER_HOLD_MS 1000

static int set_brightness(int fd, unsigned brightness)
{
	unsigned long args[4] = { 0, brightness, 0, 0 };
	return ioctl(fd, DISP_LCD_SET_BRIGHTNESS, args);
}

static int blank_display(int fd)
{
	unsigned long args[4] = {0};
	int old = ioctl(fd, DISP_LCD_GET_BRIGHTNESS, args);
	if (old < 0 || old > 255 || set_brightness(fd, 0) < 0)
		return -1;
	return old;
}

static int power_input(void)
{
	glob_t paths = {0};
	int fd = -1;

	if (glob("/dev/input/event*", 0, NULL, &paths))
		return -1;
	for (size_t i = 0; i < paths.gl_pathc; i++) {
		char name[128] = {0};
		fd = open(paths.gl_pathv[i], O_RDONLY | O_NONBLOCK | O_CLOEXEC);
		if (fd < 0)
			continue;
		if (ioctl(fd, EVIOCGNAME(sizeof name), name) >= 0 &&
		    !strncmp(name, "axp", 3) && strstr(name, "-pek")) {
			/* Use event time for holds queued while we were descheduled. */
			int clock = CLOCK_MONOTONIC;
			if (ioctl(fd, EVIOCSCLOCKID, &clock) == 0)
				break;
		}
		close(fd);
		fd = -1;
	}
	globfree(&paths);
	return fd;
}

static int hold_remaining(const struct timespec *start, const struct timespec *now)
{
	long long ns = POWER_HOLD_MS * 1000000LL -
		(now->tv_sec - start->tv_sec) * 1000000000LL -
		(now->tv_nsec - start->tv_nsec);
	/* Round up: a sub-millisecond remainder must not accept a short hold. */
	return ns > 0 ? (int)((ns + 999999) / 1000000) : 0;
}

static int wait_for_power(int fd)
{
	struct input_event event;
	struct pollfd pfd = { .fd = fd, .events = POLLIN };
	struct timespec start = {0}, now;
	int pressed = 0, dropped = 0;
	ssize_t n;

	/* Ignore events queued before we entered charging mode. A key already
	 * down must be released and pressed again to count as boot intent. */
	while (read(fd, &event, sizeof event) == sizeof event)
		;
	for (;;) {
		int timeout = -1;
		if (pressed) {
			if (clock_gettime(CLOCK_MONOTONIC, &now))
				return 1;
			timeout = hold_remaining(&start, &now);
		}
		int ready = poll(&pfd, 1, timeout);
		if (ready < 0) {
			if (errno == EINTR)
				continue;
			return 1;
		}
		if (pfd.revents & (POLLERR | POLLHUP | POLLNVAL))
			return 1;
		/* A hold has a deadline; idle input does not. Check readable events
		 * first so a queued release or SYN_DROPPED can cancel the hold. */
		if (ready == 0)
			return 0;
		while ((n = read(fd, &event, sizeof event)) == sizeof event) {
			if (event.type == EV_SYN && event.code == SYN_DROPPED) {
				pressed = 0;
				dropped = 1;
			}
			if (dropped) {
				if (event.type == EV_SYN && event.code == SYN_REPORT)
					dropped = 0;
				continue;
			}
			if (event.type != EV_KEY || event.code != KEY_POWER)
				continue;
			now.tv_sec = event.time.tv_sec;
			now.tv_nsec = event.time.tv_usec * 1000;
			if (event.value == 1 && !pressed) {
				start = now;
				pressed = 1;
			} else if (event.value == 0) {
				if (pressed && hold_remaining(&start, &now) == 0)
					return 0;
				pressed = 0;
			}
		}
		if (n != -1 || (errno != EAGAIN && errno != EINTR))
			return 1;
	}
}

int main(void)
{
	int display = open("/dev/disp", O_RDWR | O_CLOEXEC);
	int brightness = display < 0 ? -1 : blank_display(display);
	for (;;) {
		int fd = power_input();
		if (fd >= 0) {
			int result = wait_for_power(fd);
			close(fd);
			if (result == 0)
				break;
		}
		fprintf(stderr, "charger-wait: power-key input unavailable; retrying in 60s\n");
		sleep(60);
	}
	if (brightness >= 0)
		set_brightness(display, (unsigned)brightness);
	if (display >= 0)
		close(display);
	return 0;
}
