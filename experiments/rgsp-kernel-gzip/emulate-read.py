from emulate import *
for name in ['boot-original.img','boot-gzip-analysis.img']:
 e=Emulator();data=(ROOT/name).read_bytes();reads=[];dev=0x4f008000
 e.hook(0x17734,lambda:dev)
 def partition():
  e.u.mem_write(e.r(2),struct.pack('<IIII',303104,131072,512,0));return 0
 e.hook(0x466c,partition)
 def read():
  sector,n,dst=e.r(1),e.r(2),e.r(3);off=(sector-303104)*512;size=n*512
  reads.append({'sector':sector,'sectors':n,'bytes':size})
  assert 0<=off and size<=67108864 and off+size<=67108864
  v=data[off:off+size];e.u.mem_write(dst,v+b'\xff'*(size-len(v)));return n
 e.u.mem_write(dev+0x78,struct.pack('<I',B+0xef001));e.hook(0xef000,read)
 av=0x4f009000;args=[e.store(x) for x in ['sunxi_flash','read','45000000','boot']];e.u.mem_write(av,struct.pack('<4I',*args))
 try:
  r=e.call(0xc1e8,[0,0,4,av]);print(name,'return',r,'reads',reads,'total',sum(x['bytes'] for x in reads));assert r==0;assert sum(x['bytes'] for x in reads)==len(data)
 except Exception as x:print(name,'FAIL',x,'PC',hex(e.u.reg_read(UC_ARM_REG_PC)));raise
