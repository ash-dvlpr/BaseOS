/*
 * axp-off — cut power at the AXP2202/AXP717 PMIC, bypassing PSCI and BL31.
 *
 * The kernel's own power off — reboot(RB_POWER_OFF) -> PSCI SYSTEM_OFF -> BL31
 * -> the PMU — does not stay off on this board: with VBUS present the device
 * comes back within about seven seconds, every time, reporting
 * bootreason=charger. Measured repeatedly on an RG SP. That made "plugged in
 * and idle" a boot loop, because a frontend's doze timeout ends in a poweroff.
 *
 * Writing the PMU's own soft-poweroff bit instead stays off, on a live charger,
 * from both button and charger boots. The AXP717 datasheet (6.15.2.26) gives
 * REG 27H as:
 *
 *   7:4 reserved (RO)
 *   3   PWROK pin pull low to restart the system  0=disable 1=enable  default 0
 *   2   PWRON 16s to shutdown the PMIC            0=disable 1=enable  default 1
 *   1   restart the system                        write 1 = restart   (RWAC)
 *   0   soft PWROFF                               write 1 = power off (RWAC)
 *
 * 27H is not invariant on this hardware: measured twice on the same RG SP, a
 * POWER-button boot read 0x08 — both configurable bits inverted from
 * default — and a later boot, after two reboots, read 0x00, back at default
 * on bit 3 and still inverted on bit 2. Vendor firmware reprograms the
 * register on every boot and evidently not always to the same value; the
 * reading tracked bootreason both times, which looks like a correlation,
 * but that is one data point per boot type and not established. Bit 3 was
 * suspected of causing the restart and exonerated by measurement; it has
 * now been seen both set and clear, and the tool handled both correctly, so
 * this preserves whatever it finds rather than assuming a value.
 *
 * Two callers, and both gate on this program's own unarmed probe: with no
 * --now it writes nothing and exits 0 only after discovery succeeded, the
 * address and the part name matched, /dev/i2c-N opened and REG27H read, which
 * makes it an exact "can this work on this board" test. rcK runs it last on a
 * poweroff, after unmounting /data and the card — but those unmounts run with
 * the frontend still alive, because BusyBox init runs its shutdown action
 * before the SIGTERM sweep, so they usually degrade to a read-only remount
 * rather than completing. Read "quiesced", not "unmounted". rcS runs it to end
 * a charger boot, where nothing but /data has been mounted yet. Nothing
 * downstream of a write that lands runs at all.
 *
 * Where the chip lives, and what it is, are discovered rather than assumed.
 * The register map is a property of the silicon and travels to every board
 * carrying this part; the i2c bus number is a property of the devicetree and
 * does not. Linux has already probed and bound the chip — which is exactly why
 * the write below needs I2C_SLAVE_FORCE — so this reads the kernel's answer,
 * bus, address and part name alike, out of sysfs.
 *
 * Safety rules, in order of how badly they end if broken:
 *
 *  - Register 0x27 is a compile-time constant. There is no general poke path
 *    and no register argument: 0x10-0x2f is dense with DCDC and LDO enables and
 *    voltages, and a stray byte there drops a rail with the case shut.
 *  - Bit 1 is masked off every write. Setting it restarts instead of powering
 *    off, which is the exact failure this tool exists to avoid.
 *  - The client must sit at 0x34 *and* name a part this has been measured on.
 *    Two independent checks, and they buy different things. The address is the
 *    cheaper disqualifier, but on its own it is a location check: every AXP in
 *    this family sits at 0x34, so it catches a board that moved the chip and
 *    not a board carrying a *different* AXP there — against which the rule
 *    above applies with full force, since 0x27 in another register map need
 *    not be "soft poweroff" at all. The name is the identity check, read from
 *    the client's own sysfs attribute, and it is what makes the wrong-part
 *    case fail closed. What it cannot do is verify the silicon: on a
 *    devicetree system that name comes from the node's compatible, so it is
 *    the kernel's claim about the part rather than the part's own answer. See
 *    KNOWN_PARTS.
 *  - --now is mandatory, so the binary is safe to run just to read state.
 *
 * I2C_SLAVE_FORCE is required: the axp20x-i2c driver holds the address, so the
 * polite ioctl returns EBUSY. Register 0x27 is outside the driver's regmap
 * cache (which covers 0x79-0x9f), so this neither reads nor leaves stale state.
 */
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

#define I2C_SLAVE_FORCE 0x0706
#define DRIVER_SUBPATH  "/bus/i2c/drivers/axp20x-i2c"
#define EXPECT_ADDR     0x34
#define REG             0x27   /* the only register this program will ever touch */

#define SOFT_PWROFF   (1 << 0)
#define RESTART       (1 << 1)   /* masked off every write — never set this */
#define PWROK_RESTART (1 << 3)

/*
 * The parts this program will write to, named as the kernel publishes them at
 * <client>/name. Eleven H700 models ship and one has been measured: an RG SP
 * reports "axp2202" at /sys/bus/i2c/drivers/axp20x-i2c/5-0034/name.
 *
 * Add an entry only on the strength of a measurement taken from that board.
 * Unmeasured silicon has to fail closed — that is the whole point of the list
 * — because REG 0x27 is "soft poweroff" in this part's register map and is
 * free to be a rail enable in another's. A refusal here costs nothing: the
 * caller falls back to the kernel's own power off, exactly as it did before
 * this program existed.
 */
static const char *const KNOWN_PARTS[] = { "axp2202" };

/*
 * Driver-binding entries are named <bus>-<addr>, e.g. "5-0034" — the same
 * convention that names the regmap debugfs node at
 * /sys/kernel/debug/regmap/5-0034/. Everything else in the directory (bind,
 * unbind, uevent, module) fails the conversion and is skipped. The trailing
 * %c is what rejects a name with junk after the address: a complete match
 * consumes the whole string and leaves %c nothing to read, so exactly two
 * conversions means exactly one well-formed client name.
 *
 * The whole directory is scanned rather than stopping at the first entry.
 * readdir order is a property of the underlying filesystem, not of the bus, so
 * with two clients bound the first entry is whichever one sysfs happens to
 * yield — and picking it would make the caller's address check order-dependent.
 * An entry at EXPECT_ADDR wins outright; otherwise the first well-formed entry
 * is kept, purely so the caller can name it in the "sits at 0x%02x, not the
 * expected 0x34" refusal instead of reporting nothing found.
 *
 * The part name travels back with the address rather than being filtered on
 * here, so that a refusal can say which part it found. An unreadable or empty
 * name yields an empty string, which no allowlist entry matches.
 */
static void read_part(const char *dir, const char *entry, char *part, size_t plen)
{
	char path[1024];
	ssize_t n;
	int fd;

	*part = '\0';
	snprintf(path, sizeof path, "%s/%s/name", dir, entry);
	if ((fd = open(path, O_RDONLY)) < 0)
		return;
	n = read(fd, part, plen - 1);
	close(fd);
	if (n <= 0)
		return;
	part[n] = '\0';
	part[strcspn(part, " \t\r\n")] = '\0';
}

static int find_pmic(const char *sysroot, unsigned *bus, unsigned *addr,
		     char *whence, size_t wlen, char *part, size_t plen)
{
	char dir[512];
	DIR *d;
	struct dirent *e;
	unsigned b, a;
	char tail;
	int found = 0;

	snprintf(dir, sizeof dir, "%s%s", sysroot, DRIVER_SUBPATH);
	if ((d = opendir(dir)) == NULL)
		return -1;
	while ((e = readdir(d)) != NULL) {
		if (sscanf(e->d_name, "%u-%4x%c", &b, &a, &tail) != 2)
			continue;
		if (found && a != EXPECT_ADDR)
			continue;
		*bus = b;
		*addr = a;
		snprintf(whence, wlen, "axp20x-i2c/%s", e->d_name);
		read_part(dir, e->d_name, part, plen);
		found = 1;
		if (a == EXPECT_ADDR)
			break;
	}
	closedir(d);
	return found ? 0 : -1;
}

static int part_is_known(const char *part)
{
	size_t i;

	for (i = 0; i < sizeof KNOWN_PARTS / sizeof KNOWN_PARTS[0]; i++)
		if (strcmp(part, KNOWN_PARTS[i]) == 0)
			return 1;
	return 0;
}

static int rd(int fd, unsigned char *out)
{
	unsigned char reg = REG;

	if (write(fd, &reg, 1) != 1)
		return -1;
	return read(fd, out, 1) == 1 ? 0 : -1;
}

int main(int argc, char **argv)
{
	const char *sysroot;
	char bus_path[512], whence[128], part[64];
	unsigned bus = 0, addr = 0;
	int fd, i, arm = 0, drop_pwrok = 0;
	unsigned char cur, val, back;

	for (i = 1; i < argc; i++) {
		if (!strcmp(argv[i], "--now"))
			arm = 1;
		else if (!strcmp(argv[i], "--no-pwrok"))
			drop_pwrok = 1;
		else {
			fprintf(stderr, "usage: axp-off [--now] [--no-pwrok]\n");
			return 2;
		}
	}

	sysroot = getenv("BASEOS_SYS_ROOT");
	if (sysroot == NULL || *sysroot == '\0')
		sysroot = "/sys";

	if (find_pmic(sysroot, &bus, &addr, whence, sizeof whence, part, sizeof part)) {
		fprintf(stderr, "axp-off: no axp20x-i2c client under %s%s\n",
			sysroot, DRIVER_SUBPATH);
		return 1;
	}
	if (addr != EXPECT_ADDR) {
		fprintf(stderr, "axp-off: %s sits at 0x%02x, not the expected 0x%02x; refusing\n",
			whence, addr, EXPECT_ADDR);
		return 1;
	}
	if (!part_is_known(part)) {
		if (*part == '\0')
			fprintf(stderr, "axp-off: %s publishes no readable name; refusing\n",
				whence);
		else
			fprintf(stderr, "axp-off: %s is a \"%s\", not a part this has been measured on; refusing\n",
				whence, part);
		return 1;
	}
	snprintf(bus_path, sizeof bus_path, "/dev/i2c-%u", bus);
	printf("axp-off: PMIC %s at %s addr 0x%02x (%s)\n", part, bus_path, addr, whence);

	if ((fd = open(bus_path, O_RDWR)) < 0) {
		fprintf(stderr, "axp-off: open %s: %s\n", bus_path, strerror(errno));
		return 1;
	}
	if (ioctl(fd, I2C_SLAVE_FORCE, addr) < 0) {
		fprintf(stderr, "axp-off: I2C_SLAVE_FORCE 0x%02x: %s\n", addr, strerror(errno));
		return 1;
	}
	if (rd(fd, &cur)) {
		fprintf(stderr, "axp-off: read REG27H: %s\n", strerror(errno));
		return 1;
	}

	val = (unsigned char)((cur & ~RESTART) | SOFT_PWROFF);
	if (drop_pwrok)
		val &= (unsigned char)~PWROK_RESTART;

	printf("axp-off: REG27H 0x%02x -> 0x%02x\n", cur, val);
	if (!arm) {
		printf("axp-off: not armed (pass --now to cut power)\n");
		return 0;
	}

	/* Last-resort flush. The caller has already quiesced its filesystems —
	 * unmounted, or read-only where a live frontend held them — so this only
	 * covers what a mistake in the calling script would leave behind. */
	fflush(stdout);
	sync();

	if (write(fd, (unsigned char[]){ REG, val }, 2) != 2) {
		fprintf(stderr, "axp-off: write REG27H: %s\n", strerror(errno));
		return 1;
	}

	/* Reaching this point means the PMIC did not cut the rails, which is
	 * itself the result: the caller falls through to reboot(RB_POWER_OFF). */
	if (!rd(fd, &back))
		fprintf(stderr, "axp-off: still running after write, REG27H reads 0x%02x\n", back);
	else
		fprintf(stderr, "axp-off: still running after write, REG27H unreadable\n");
	return 1;
}
