#include <assert.h>
#include <stdarg.h>
#include <sys/ioctl.h>
#include <sys/wait.h>
static int display_ioctl(int fd, unsigned long request, ...);
#define ioctl display_ioctl
#define main charger_wait_main
#include "../src/charger-wait.c"
#undef main
#undef ioctl

static int brightness = 180, brightness_error;
static int display_ioctl(int fd, unsigned long request, ...)
{
	assert(fd == 1000);
	va_list args;
	va_start(args, request);
	unsigned long *params = va_arg(args, unsigned long *);
	assert(params[0] == 0 && params[2] == 0 && params[3] == 0);
	va_end(args);
	if (request == DISP_LCD_GET_BRIGHTNESS)
		return brightness_error ? -1 : brightness;
	assert(request == DISP_LCD_SET_BRIGHTNESS && params[1] <= 255);
	brightness = params[1];
	return 0;
}

static void send_event(int fd, unsigned type, unsigned code, int value)
{
	struct input_event event = { .type = type, .code = code, .value = value };
	assert(write(fd, &event, sizeof event) == sizeof event);
}

static void scenario(int dropped)
{
	int fds[2];
	assert(pipe2(fds, O_NONBLOCK | O_CLOEXEC) == 0);
	/* A queued hold must be drained, not treated as fresh boot intent. */
	send_event(fds[1], EV_KEY, KEY_POWER, 1);
	pid_t child = fork();
	assert(child >= 0);
	if (!child) {
		close(fds[0]);
		usleep(600000);
		send_event(fds[1], EV_KEY, KEY_POWER, 0);
		usleep(20000);
		send_event(fds[1], EV_KEY, KEY_POWER, 1);
		if (dropped) {
			usleep(600000);
			send_event(fds[1], EV_SYN, SYN_DROPPED, 0);
			send_event(fds[1], EV_SYN, SYN_REPORT, 0);
		} else {
			usleep(20000); /* A short tap must not boot. */
		}
		send_event(fds[1], EV_KEY, KEY_POWER, 0);
		usleep(20000);
		send_event(fds[1], EV_KEY, KEY_POWER, 1);
		usleep(600000);
		send_event(fds[1], EV_KEY, KEY_POWER, 0);
		usleep(100000);
		_exit(0);
	}
	close(fds[1]);
	struct timespec start, end;
	assert(clock_gettime(CLOCK_MONOTONIC, &start) == 0);
	assert(wait_for_power(fds[0]) == 0);
	assert(clock_gettime(CLOCK_MONOTONIC, &end) == 0);
	assert(end.tv_sec - start.tv_sec + (end.tv_nsec - start.tv_nsec) / 1e9 >
		(dropped ? 1.7 : 1.1));
	close(fds[0]);
	int status;
	assert(waitpid(child, &status, 0) == child && status == 0);
}

int main(void)
{
	assert(blank_display(1000) == 180 && brightness == 0);
	assert(set_brightness(1000, 180) == 0 && brightness == 180);
	brightness_error = 1;
	assert(blank_display(1000) == -1 && brightness == 180);
	scenario(0);
	scenario(1);
	assert(wait_for_power(123456) == 1);
	puts("charger input tests passed");
	return 0;
}
