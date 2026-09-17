import sys,struct
from pathlib import Path
import os
from capstone import *
b=(Path(os.environ['RGSP_GZIP_WORK'])/'u-boot.bin').read_bytes(); base=0x4a000000
start,end=(int(x,0) for x in sys.argv[1:3]); start-=base if start>=base else 0; end-=base if end>=base else 0
md=Cs(CS_ARCH_ARM,CS_MODE_THUMB);md.skipdata=True
for i in md.disasm(b[start:end],base+start):
 line=f'{i.address:08x}: {i.mnemonic:8} {i.op_str}'
 if i.mnemonic.startswith('ldr') and '[pc, #' in i.op_str:
  off=int(i.op_str.split('[pc, #')[1].split(']')[0],0)+((i.address+4)&~3)-base
  if 0<=off<len(b)-4:
   v=struct.unpack_from('<I',b,off)[0];line+=f' ; {v:08x}'
   if base<=v<base+len(b):
    s=b[v-base:b.find(b'\0',v-base)]
    if len(s)<200 and s and all(c in (9,10,13) or 32<=c<127 for c in s):line+=' '+repr(s.decode())
 print(line)
