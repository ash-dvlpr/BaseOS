import subprocess, time, json, re
from pathlib import Path
serial='ac001089c89588720d2'
def adb(cmd, timeout=15):
    p=subprocess.run(['adb','-s',serial,'shell',cmd],capture_output=True,text=True,timeout=timeout)
    if p.returncode: raise RuntimeError(p.stderr+p.stdout)
    return p.stdout.replace('\r','')
def snapshot():
    regs={int(a,16):int(v,16) for a,v in re.findall(r'^([0-9a-f]+): ([0-9a-f]+)$',adb('cat /sys/kernel/debug/regmap/5096000.codec/registers'),re.M)}
    gpio=adb('cat /sys/kernel/debug/gpio')
    return dict(digital=regs[0], analog=regs[0x310], ramp=regs[0x31c],
                amp_high=bool(re.search(r'gpio-261.*out hi',gpio)),
                pcm=adb('cat /proc/asound/card0/pcm0p/sub0/status').splitlines()[0])
rows=[]
for device,rate in [('hw:audiocodec',48000),('default',44100),('default',48000),('hw:audiocodec',44100)]:
    player=subprocess.Popen(['adb','-s',serial,'shell',f'aplay -q -D {device} -t raw -f S16_LE -r {rate} -c 2 -d 2 /dev/zero'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    time.sleep(.8)
    during=snapshot()
    out,err=player.communicate(timeout=10)
    assert player.returncode == 0,(out,err)
    after=snapshot()
    assert 'RUNNING' in during['pcm'],during
    assert during['analog']&0xe800 == 0xe800,during
    assert during['digital']&0x80000000,during
    assert during['amp_high'],during
    assert after['pcm']=='closed',after
    assert after['analog']&0x2800 == 0x2800,after
    assert after['analog']&0xc000 == 0,after
    assert after['digital']&0x80000000 == 0,after
    assert after['ramp']&1 == 0,after
    assert not after['amp_high'],after
    row=dict(device=device,rate=rate,during=during,after=after)
    rows.append(row)
    Path('/tmp/h700-buffer-retention/verification.json').write_text(json.dumps(rows,indent=2))
    print('PASS',device,rate,'idle analog=%08x digital=%08x'%(after['analog'],after['digital']),flush=True)
print('PASS four playback cycles; buffers retained with DAC off; speaker amp sequencing unchanged')
