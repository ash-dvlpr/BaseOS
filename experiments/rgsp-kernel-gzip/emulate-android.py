from emulate import *
import hashlib
for mode in ['original','gzip-without-patch','gzip-with-patch']:
 e=Emulator();u=e.u;images=B+0xe7348
 raw=(ROOT/('boot-original.img' if mode=='original' else 'boot-gzip-analysis.img')).read_bytes();u.mem_write(IMG,raw)
 fdt=(ROOT/'live-fdt.dtb').read_bytes();u.mem_write(0x44000000,fdt);u.mem_write(GD+0x5c,struct.pack('<I',0x44000000));u.mem_write(GD+0x68,struct.pack('<I',len(fdt)));u.mem_write(B+0x9d874,struct.pack('<I',0x44000000))
 # No real peripherals in this function-level model: intercept hardware-facing
 # board reservations, bootstage logging, IRQ state, cache maintenance,
 # board bootargs and live-device-tree mutation. This is NOT a whole-SoC boot.
 for a in [0x1430,0xea8c,0x1600,0x15e4,0x1814,0x8680,0xfda4,0x5734]:e.hook(a,lambda:0)
 if mode=='gzip-with-patch':u.mem_write(B+0xf698,bytes.fromhex('40f20123')) # movw r3,#0x201
 arg=e.store('45000000');av=0x4f002000;u.mem_write(av,struct.pack('<I',arg))
 # Emulate command's Android preamble independently up to bootm_run_states.
 def stop_states():
  e.command_reached=True;u.emu_stop();return 0
 e.command_reached=False
 h=u.hook_add(UC_HOOK_CODE,lambda u,a,s,d:stop_states(),begin=B+0xf43c,end=B+0xf43c)
 cmd=e.store('bootm');fullav=av+32;u.mem_write(fullav,struct.pack('<II',cmd,arg))
 try:
  e.call(0x7e70,[0,0,2,fullav])
 except Exception as x:
  if not e.command_reached:
   print("preamble fail",x,"PC",hex(u.reg_read(UC_ARM_REG_PC)),"LR",hex(u.reg_read(UC_ARM_REG_LR)),e.logs,flush=True);raise
 u.hook_del(h)
 print(mode,'preamble',e.logs,'environment',e.env,flush=True)
 for flags in [1,2,4,8]:
  try:
   r=e.call(0xf43c,[0,0,1,av,flags,images,0]);print('state',flags,'result',hex(r),flush=True)
  except Exception as x:
   print('FAIL',x,'PC',hex(u.reg_read(UC_ARM_REG_PC)),'LR',hex(u.reg_read(UC_ARM_REG_LR)),flush=True);raise
  assert r==0, (mode,flags,r)
 k=bytes(u.mem_read(0x40080000,len((ROOT/'Image').read_bytes())))
 print('comp',u.mem_read(images+0x5c,1).hex(),'kernel_sha256',hashlib.sha256(k).hexdigest(),flush=True)
 ramdisk_ok=bytes(u.mem_read(0x42000000,len((ROOT/'ramdisk.gz').read_bytes())))==(ROOT/'ramdisk.gz').read_bytes()
 print('ramdisk_equals_original',ramdisk_ok,flush=True)
 assert ramdisk_ok
 assert (k==(ROOT/'Image').read_bytes()) == (mode!='gzip-without-patch')
 assert u.mem_read(images+0x5c,1)==(b'\x01' if mode=='gzip-with-patch' else b'\x00')
