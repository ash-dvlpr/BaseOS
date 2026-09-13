#include <assert.h>
#include <signal.h>
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

static long long monotonic_ns(void)
{
	struct timespec now;
	assert(clock_gettime(CLOCK_MONOTONIC, &now) == 0);
	return now.tv_sec * 1000000000LL + now.tv_nsec;
}

static struct input_event event_at(long long ns, unsigned type, unsigned code, int value)
{
	return (struct input_event) {
		.time = { .tv_sec = ns / 1000000000, .tv_usec = ns % 1000000000 / 1000 },
		.type = type, .code = code, .value = value
	};
}

static void send_event(int fd, unsigned type, unsigned code, int value)
{
	struct input_event event = event_at(monotonic_ns(), type, code, value);
	assert(write(fd, &event, sizeof event) == sizeof event);
}

static void queued_hold(int fd, int duration_ms)
{
	long long start = monotonic_ns() - 2000000000;
	struct input_event events[] = {
		event_at(start, EV_KEY, KEY_POWER, 1),
		event_at(start + duration_ms * 1000000LL, EV_KEY, KEY_POWER, 0)
	};
	/* One pipe write queues both events before the reader can see either. */
	assert(write(fd, events, sizeof events) == sizeof events);
}

static void still_waiting(int fd)
{
	struct pollfd ack = { .fd = fd, .events = POLLIN };
	assert(poll(&ack, 1, 0) == 0);
}

static void interrupted(int sig) { (void)sig; }

enum scenario { HOLD, CANCELED_HOLDS, QUEUED_HOLDS, SIGNALS_AND_REPEATS };

static void scenario(enum scenario which)
{
	int events[2], done[2];
	assert(pipe2(events, O_NONBLOCK | O_CLOEXEC) == 0);
	assert(pipe2(done, O_NONBLOCK | O_CLOEXEC) == 0);
	if (which == CANCELED_HOLDS)
		send_event(events[1], EV_KEY, KEY_POWER, 1);
	pid_t child = fork();
	assert(child >= 0);
	if (!child) {
		close(events[0]);
		close(done[1]);
		usleep(20000); /* Let the reader drain input present at entry. */
		if (which == CANCELED_HOLDS) {
			/* A key held on entry must not boot, even after the threshold. */
			usleep(1100000);
			still_waiting(done[0]);
			send_event(events[1], EV_KEY, KEY_POWER, 0);
			send_event(events[1], EV_KEY, KEY_POWER, 1);
			usleep(50000);
			send_event(events[1], EV_KEY, KEY_POWER, 0); /* Short tap. */
			send_event(events[1], EV_KEY, KEY_POWER, 1);
			usleep(100000);
			send_event(events[1], EV_SYN, SYN_DROPPED, 0);
			send_event(events[1], EV_SYN, SYN_REPORT, 0);
			send_event(events[1], EV_KEY, KEY_POWER, 2);
			usleep(1100000); /* Dropped input must cancel the deadline. */
			still_waiting(done[0]);
			send_event(events[1], EV_KEY, KEY_POWER, 0);
		} else if (which == QUEUED_HOLDS) {
			queued_hold(events[1], 100);
			usleep(50000);
			still_waiting(done[0]);
		}
		long long start = monotonic_ns();
		if (which == QUEUED_HOLDS)
			queued_hold(events[1], 1200);
		else
			send_event(events[1], EV_KEY, KEY_POWER, 1);
		/* Never release this final press. Success must arrive while held,
		 * even when repeat events and signals keep interrupting poll(). */
		for (int i = 0; i < 20; i++) {
			struct pollfd ack = { .fd = done[0], .events = POLLIN };
			int ready = poll(&ack, 1, 100);
			assert(ready >= 0);
			if (ready) {
				char result;
				assert(read(done[0], &result, 1) == 1 && result == 'Y');
				if (which != QUEUED_HOLDS)
					assert(monotonic_ns() - start >= 990000000);
				_exit(0);
			}
			if (which == SIGNALS_AND_REPEATS) {
				send_event(events[1], EV_KEY, KEY_POWER, 2);
				assert(kill(getppid(), SIGUSR1) == 0);
			}
		}
		_exit(1);
	}
	close(events[1]);
	close(done[0]);
	assert(wait_for_power(events[0]) == 0);
	assert(write(done[1], "Y", 1) == 1);
	int status;
	pid_t waited;
	do { waited = waitpid(child, &status, 0); } while (waited < 0 && errno == EINTR);
	assert(waited == child && status == 0);
	close(events[0]);
	close(done[1]);
}

int main(void)
{
	assert(blank_display(1000) == 180 && brightness == 0);
	assert(set_brightness(1000, 180) == 0 && brightness == 180);
	brightness_error = 1;
	assert(blank_display(1000) == -1 && brightness == 180);
	struct sigaction action = { .sa_handler = interrupted };
	assert(sigaction(SIGUSR1, &action, NULL) == 0);
	scenario(HOLD);
	scenario(CANCELED_HOLDS);
	scenario(QUEUED_HOLDS);
	scenario(SIGNALS_AND_REPEATS);
	assert(wait_for_power(123456) == 1);
	puts("charger input tests passed");
	return 0;
}
