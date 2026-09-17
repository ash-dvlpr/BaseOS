from pathlib import Path
import os
import struct,zlib,json,sys
from unicorn import *
from unicorn.arm_const import *
ROOT=Path(os.environ['RGSP_GZIP_WORK']).resolve()
B=0x4a000000; IMG=0x45000000; GD=0x4f000000; STACK=0x4ffff000; STOP=0x4fff0000
class Emulator:
 def __init__(self):
  self.u=Uc(UC_ARCH_ARM,UC_MODE_THUMB);self.u.mem_map(0x40000000,0x10000000);self.u.mem_write(B,(ROOT/'u-boot.bin').read_bytes())
  self.u.reg_write(UC_ARM_REG_R9,GD);self.u.reg_write(UC_ARM_REG_SP,STACK)
  self.heap=0x48000000;self.logs=[];self.env={'boot_from_partion':'boot','bootargs':'root=/dev/mmcblk0p5 rootwait quiet splash init=/init','verify':'yes','bootm_low':'40000000','bootm_size':'10000000'}
  self.hook(0xe8f8,self.getenv);self.hook(0xe620,self.setenv);self.hook(0xe674,self.sethex)
  self.hook(0x11728,self.alloc);self.hook(0x115cc,lambda:0)
  for addr in [0x6a7bc,0x6a750,0x110fa,0x111e8]:self.hook(addr,lambda:0)
  self.hook(0x12104,self.copy)
  self.u.mem_write(GD,struct.pack('<I',GD+0x1000))
  self.u.mem_write(GD+0x1050,struct.pack('<QQ',0x40000000,0x40000000))
 def r(self,i): return self.u.reg_read(UC_ARM_REG_R0+i)
 def string(self,p): return bytes(self.u.mem_read(p,512)).split(b'\0')[0].decode(errors='replace') if p else ''
 def alloc(self):
  p=self.heap;self.heap+=(self.r(0)+15)&~15;return p
 def store(self,s):
  p=self.heap;self.heap+=(len(s)+16)&~15;self.u.mem_write(p,s.encode()+b'\0');return p
 def getenv(self):
  k=self.string(self.r(0));v=self.env.get(k);return self.store(v) if v else 0
 def setenv(self):self.env[self.string(self.r(0))]=self.string(self.r(1));return 0
 def sethex(self):self.env[self.string(self.r(0))]=hex(self.r(1))[2:];return 0
 def copy(self):
  dst,src,n=(self.r(i) for i in range(3));self.logs.append({'copy_dst':hex(dst),'src':hex(src),'bytes':n})
  if not (0x40000000<=dst<0x50000000 and dst+n<=0x50000000 and 0x40000000<=src<0x50000000 and src+n<=0x50000000):
   raise RuntimeError(f'Invalid copy: {self.logs[-1]}')
  self.u.mem_write(dst,bytes(self.u.mem_read(src,n)));return dst
 def hook(self,a,f):
  def h(u,addr,size,data):
   r=f();u.reg_write(UC_ARM_REG_R0,0 if r is None else r&0xffffffff);u.reg_write(UC_ARM_REG_PC,u.reg_read(UC_ARM_REG_LR))
  self.u.hook_add(UC_HOOK_CODE,h,begin=B+a,end=B+a)
 def call(self,a,args):
  self.u.reg_write(UC_ARM_REG_SP,STACK);self.u.reg_write(UC_ARM_REG_LR,STOP|1)
  for i,n in enumerate(args):
   if i<4:self.u.reg_write(UC_ARM_REG_R0+i,n)
   else:self.u.mem_write(STACK+(i-4)*4,struct.pack('<I',n))
  self.u.emu_start((B+a)|1,STOP,count=500000000)
  if self.u.reg_read(UC_ARM_REG_PC)!=STOP:raise RuntimeError(f'incomplete at {self.u.reg_read(UC_ARM_REG_PC):08x}')
  return self.r(0)
def legacy(payload,comp=1,arch=22,kind=2):
 h=struct.pack('>7I4B32s',0x27051956,0,0,len(payload),0x40080000,0x40080000,zlib.crc32(payload),5,arch,kind,comp,b'RGSP test')
 return h[:4]+struct.pack('>I',zlib.crc32(h))+h[8:]+payload
if __name__=='__main__':
 e=Emulator();k=(ROOT/'Image.gz9').read_bytes();im=legacy(k);e.u.mem_write(IMG,im)
 argv=e.store('bootm');arg=e.store('45000000');av=0x4f001100;e.u.mem_write(av,struct.pack('<II',argv,arg))
 try:
  e.call(0x7e70,[0,0,2,av]);print('completed')
 except Exception as x:print(type(x).__name__,str(x))
 print(json.dumps(e.logs,indent=2));print(e.env)
