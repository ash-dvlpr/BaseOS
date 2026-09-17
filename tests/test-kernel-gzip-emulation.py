#!/usr/bin/env python3
"""Execute the pinned vendor Thumb code under Linux Unicorn 2.1.4/Capstone 5.0.9.

Requires prepared work/<target> inputs. No device access. Hardware operations
are mocked; parsing, SD read-length calculation and gzip decompression execute
the vendor instructions. Run from any directory, optionally naming targets.
The retained RG SP emulator supplies register/memory/environment plumbing.
"""
import sys,os,struct,json,hashlib
from pathlib import Path
os.chdir(Path(__file__).resolve().parents[1])
sys.path.insert(0,'tools')
from kernel_gzip import derive,CATALOG
sys.path.insert(0,'experiments/rgsp-kernel-gzip')
os.environ['RGSP_GZIP_WORK']='work/kernel-gzip-validation'
import emulate as em
from capstone import Cs,CS_ARCH_ARM,CS_MODE_THUMB
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_PC,UC_ARM_REG_LR
cs=Cs(CS_ARCH_ARM,CS_MODE_THUMB)
for target in sys.argv[1:] or json.loads(CATALOG.read_text())['profiles']:
 d=derive(target,Path('work')/target/'boot-prefix.img')
 root=Path('work/kernel-gzip-validation')/target;root.mkdir(parents=True,exist_ok=True)
 original=d['original_package'][d['profile']['uboot_offset']:d['profile']['uboot_offset']+d['profile']['uboot_size']]
 (root/'u-boot.bin').write_bytes(original);em.ROOT=root
 images=struct.unpack_from('<I',original,0xf798)[0]-4
 printf=next(cs.disasm(original[0xf4fc:0xf500],em.B+0xf4fc))
 assert printf.mnemonic=='bl'
 printfaddr=int(printf.op_str[1:],16)-em.B
 print(target,'images',hex(images),'printf',hex(printfaddr),flush=True)
 # Execute sunxi_flash's Android read-size calculation with a fake block device.
 # A compressed Android image must reduce the actual requested SD bytes.
 for raw in [d['original_image'], d['boot']]:
  e=em.Emulator();e.hook(printfaddr,lambda:0);reads=[];dev=0x4f008000
  e.hook(0x17734,lambda:dev)
  start=d['profile']['boot_partition_start'];sectors=d['profile']['boot_partition_sectors']
  def partition():
   e.u.mem_write(e.r(2),struct.pack('<IIII',start,sectors,512,0));return 0
  e.hook(0x466c,partition)
  def read():
   sector,n,dst=e.r(1),e.r(2),e.r(3);off=(sector-start)*512;size=n*512
   assert 0<=off and off+size<=sectors*512
   reads.append(size);value=raw[off:off+size]
   e.u.mem_write(dst,value+b'\xff'*(size-len(value)));return n
  e.u.mem_write(dev+0x78,struct.pack('<I',em.B+0xef001));e.hook(0xef000,read)
  av=0x4f009000;args=[e.store(x) for x in ['sunxi_flash','read','45000000','boot']]
  e.u.mem_write(av,struct.pack('<4I',*args))
  assert e.call(0xc1e8,[0,0,4,av])==0
  assert sum(reads)==len(raw),(target,reads,len(raw))
  print(target,'SD bytes',sum(reads),'PASS',flush=True)
 for mode in ['original','gzip-without-patch','gzip-with-patch']:
  e=em.Emulator();u=e.u
  e.hook(printfaddr,lambda:0)
  raw=d['original_image'] if mode=='original' else d['boot'];u.mem_write(em.IMG,raw)
  fdt=d['dtb'];u.mem_write(0x44000000,fdt);u.mem_write(em.GD+0x5c,struct.pack('<I',0x44000000));u.mem_write(em.GD+0x68,struct.pack('<I',len(fdt)))
  for a in [0x1430,0xea8c,0x1600,0x15e4,0x1814,0x8680,0xfda4,0x5734]: e.hook(a,lambda:0)
  if mode=='gzip-with-patch':u.mem_write(em.B,d['uboot'])
  arg=e.store('45000000');av=0x4f002000;u.mem_write(av,struct.pack('<I',arg))
  e.command_reached=False
  def stop_states(u,a,s,x):e.command_reached=True;u.emu_stop()
  h=u.hook_add(UC_HOOK_CODE,stop_states,begin=em.B+0xf43c,end=em.B+0xf43c)
  cmd=e.store('bootm');fullav=av+32;u.mem_write(fullav,struct.pack('<II',cmd,arg))
  try:
   try:e.call(0x7e70,[0,0,2,fullav])
   except RuntimeError:
    if not e.command_reached:raise
   assert e.command_reached
   u.hook_del(h)
   for flags in [1,2,4,8]:
    result=e.call(0xf43c,[0,0,1,av,flags,images,0]);assert result==0,(flags,result)
   kernel=bytes(u.mem_read(0x40080000,len(d['kernel'])))
   assert (kernel==d['kernel'])==(mode!='gzip-without-patch')
   assert bytes(u.mem_read(0x42000000,len(d['ramdisk'])))==d['ramdisk']
   assert bytes(u.mem_read(images+0x5c,1))==(b'\1' if mode=='gzip-with-patch' else b'\0')
   print(target,mode,'PASS',flush=True)
  except Exception:
   print('FAIL',mode,'PC',hex(u.reg_read(UC_ARM_REG_PC)),'LR',hex(u.reg_read(UC_ARM_REG_LR)),e.logs,flush=True);raise
 print(target,'kernel-sha256',hashlib.sha256(d['kernel']).hexdigest(),flush=True)
