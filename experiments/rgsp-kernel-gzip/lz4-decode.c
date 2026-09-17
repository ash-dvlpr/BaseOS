/* Small bounded decoder for the LZ4 block format; no runtime dependencies. */
typedef __SIZE_TYPE__ usize;
__attribute__((noinline)) int decode(const unsigned char *src,usize n,unsigned char *dst,usize cap) {
 const unsigned char *p=src,*end=src+n;unsigned char *out=dst,*limit=dst+cap;
 while(p<end){
  unsigned token=*p++;usize lit=token>>4,match=(token&15)+4;
  if(lit==15){unsigned v;do{if(p==end)return -1;v=*p++;if(lit>cap-v)return -2;lit+=v;}while(v==255);}
  if(lit>(usize)(end-p)||lit>(usize)(limit-out))return -3;
  for(usize i=0;i<lit;i++)*out++=*p++;
  if(p==end)return (int)(out-dst);
  if(end-p<2)return -4;
  unsigned off=p[0]|(p[1]<<8);p+=2;if(!off||off>(usize)(out-dst))return -5;
  if((token&15)==15){unsigned v;do{if(p==end)return -6;v=*p++;if(match>cap-v)return -7;match+=v;}while(v==255);}
  if(match>(usize)(limit-out))return -8;
  const unsigned char *q=out-off;
  for(usize i=0;i<match;i++)*out++=*q++;
 }
 return (int)(out-dst);
}
