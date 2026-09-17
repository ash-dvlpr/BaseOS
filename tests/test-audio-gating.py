#!/usr/bin/env python3
"""Run the production GPIO arbitration functions with a host GPIO stand-in."""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / 'src/h700-speaker-amp/h700_speaker_amp.c').read_text()

def function(name):
    start = source.index('static ', source.index(name) - 20)
    opening = source.index('{', start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]

header = r'''
#include <assert.h>
#include <stdbool.h>
#include <errno.h>
#include <stdio.h>
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,v) ((x)=(v))
#define spin_lock_irqsave(a,f) do { (void)(a); (f)=0; assert(!locked); locked=1; } while(0)
#define spin_unlock_irqrestore(a,f) do { (void)(a); (void)(f); assert(locked); locked=0; } while(0)
#define pr_err(...) ((void)0)
#define pr_debug(...) ((void)0)
struct gpio_chip { int dummy; } chip;
struct gpio_desc { int dummy; } desc;
static struct gpio_desc *amp = &desc;
static unsigned int amp_offset=261, settle_ms=100;
static bool amplifier_requested, lineout_ready;
static int state=1, *jack_speaker_state=&state;
static int locked, amp_lock, pin, last_offset, last_value, calls, waits;
static void original_set(struct gpio_chip *c, unsigned int offset, int value) {
    (void)c;
    assert(locked == (offset == amp_offset));
    last_offset=offset; last_value=value; calls++;
    if(offset==amp_offset) pin=value;
}
static int original_direction(struct gpio_chip *c, unsigned int offset, int value) {
    original_set(c,offset,value); return 17;
}
static void (*vendor_gpio_set)(struct gpio_chip *,unsigned int,int)=original_set;
static int (*vendor_gpio_direction_output)(struct gpio_chip *,unsigned int,int)=original_direction;
static void msleep(unsigned int ms) { assert(!locked); assert(ms==100); waits++; }
static int gpiod_get_raw_value_cansleep(struct gpio_desc *p) { (void)p; return pin; }
'''
body = '\n'.join(function(n) for n in ['amp_allowed_value', 'guarded_gpio_set', 'guarded_gpio_direction_output'])
body += '\nstatic void gpiod_set_raw_value_cansleep(struct gpio_desc *p,int v) {(void)p; guarded_gpio_set(&chip,amp_offset,v);}\n'
body += function('set_amp')
main = r'''
int main(void) {
    for(int requested=0;requested<2;requested++)
    for(int ready=0;ready<2;ready++)
    for(int jack=0;jack<2;jack++) {
        amplifier_requested=requested; lineout_ready=ready; state=jack;
        int wanted=requested && ready && jack;
        guarded_gpio_set(&chip,261,1); assert(pin==wanted);
        assert(guarded_gpio_direction_output(&chip,261,1)==17); assert(pin==wanted);
        guarded_gpio_set(&chip,261,0); assert(pin==0);
        guarded_gpio_set(&chip,3,1); assert(last_offset==3 && last_value==1);
        assert(guarded_gpio_direction_output(&chip,3,1)==17);
        assert(last_offset==3 && last_value==1);
    }
    pin=0; amplifier_requested=0; lineout_ready=1; state=1; waits=0;
    assert(set_amp(true)==0 && pin==1 && waits==1);
    assert(set_amp(true)==0 && pin==1 && waits==1); /* second endpoint: no extra delay */
    assert(set_amp(false)==0 && pin==0 && waits==2);
    guarded_gpio_set(&chip,261,1); assert(pin==0); /* idle jack worker */
    assert(guarded_gpio_direction_output(&chip,261,1)==17 && pin==0);
    state=0; waits=0;
    assert(set_amp(true)==0 && pin==0 && waits==0); /* headphones: no speaker delay */
    state=1; guarded_gpio_set(&chip,261,1); assert(pin==1); /* unplug during playback */
    state=0; guarded_gpio_set(&chip,261,1); assert(pin==0); /* jack suppresses speaker */
    lineout_ready=0; state=1;
    guarded_gpio_set(&chip,261,1); assert(pin==0); /* codec ramp not ready */
    puts("audio gating tests passed");
}
'''
with tempfile.TemporaryDirectory() as tmp:
    path=Path(tmp)
    (path/'test.c').write_text(header+body+main)
    subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',str(path/'test.c'),'-o',str(path/'test')],check=True)
    subprocess.run([str(path/'test')],check=True)
