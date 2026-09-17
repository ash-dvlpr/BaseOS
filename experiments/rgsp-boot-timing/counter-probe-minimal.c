/* Read-only ARM64 probe without libc, compatible with the vendor Linux 4.9. */
#include <stdint.h>
#include <asm/unistd.h>
struct ktimespec { int64_t sec, nsec; };
static char line[512];
static unsigned used;
static long syscall3(long nr, long a, long b, long c) {
    register long x8 __asm__("x8") = nr;
    register long x0 __asm__("x0") = a;
    register long x1 __asm__("x1") = b;
    register long x2 __asm__("x2") = c;
    __asm__ volatile("svc #0" : "+r"(x0) : "r"(x8), "r"(x1), "r"(x2) : "memory", "cc");
    return x0;
}
static void text(const char *s) { while (*s) line[used++]=*s++; }
static void number(uint64_t n) {
    char rev[24]; unsigned i=0;
    do { rev[i++]='0'+n%10; n/=10; } while(n);
    while(i) line[used++]=rev[--i];
}
static void seconds(int64_t ns) {
    if(ns<0) {text("-");ns=-ns;}
    number((uint64_t)ns/1000000000);
    text(".");
    uint64_t fraction=ns%1000000000;
    for(uint64_t place=100000000;place;place/=10) line[used++]='0'+(fraction/place)%10;
}
static void flush(void) {
    text("\n"); syscall3(__NR_write,1,(long)line,used); used=0;
}
static uint64_t counter(void) {
    uint64_t value;
    __asm__ volatile("isb; mrs %0, cntvct_el0" : "=r"(value) : : "memory");
    return value;
}
static int64_t counter_ns(uint64_t ticks,uint64_t frequency) {
    return (ticks/frequency)*1000000000+(ticks%frequency)*1000000000/frequency;
}
static int64_t ts_ns(struct ktimespec t) {return t.sec*1000000000+t.nsec;}
void _start(void) {
    uint64_t frequency;
    __asm__ volatile("mrs %0, cntfrq_el0" : "=r"(frequency));
    if(!frequency) goto fail;
    text("counter_frequency_hz=");number(frequency);flush();
    for(int i=0;i<8;++i) {
        struct ktimespec raw,boot;
        uint64_t a=counter();
        if(syscall3(__NR_clock_gettime,4,(long)&raw,0))goto fail; /* MONOTONIC_RAW */
        uint64_t b=counter();
        uint64_t c=counter();
        if(syscall3(__NR_clock_gettime,7,(long)&boot,0))goto fail; /* BOOTTIME */
        uint64_t d=counter();
        text("sample=");number(i);
        text(" counter_seconds=");seconds(counter_ns(b,frequency));
        text(" monotonic_raw=");seconds(ts_ns(raw));
        text(" raw_origin_offset=[");seconds(counter_ns(a,frequency)-ts_ns(raw));
        text(",");seconds(counter_ns(b,frequency)-ts_ns(raw));text("]");
        text(" boottime=");seconds(ts_ns(boot));
        text(" boot_origin_offset=[");seconds(counter_ns(c,frequency)-ts_ns(boot));
        text(",");seconds(counter_ns(d,frequency)-ts_ns(boot));text("]");flush();
        struct ktimespec delay={0,100000000};
        syscall3(__NR_nanosleep,(long)&delay,0,0);
    }
    syscall3(__NR_exit,0,0,0);
    __builtin_unreachable();
fail:
    text("counter probe failed");flush();syscall3(__NR_exit,1,0,0);
    __builtin_unreachable();
}
