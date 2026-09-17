#define _GNU_SOURCE
#include <sys/mman.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <time.h>
#include <errno.h>
#include <sched.h>
static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t); return t.tv_sec+t.tv_nsec/1e9; }
static void *readfile(const char *p,size_t *n) { FILE *f=fopen(p,"rb"); if(!f){perror(p);exit(1);} fseek(f,0,SEEK_END);*n=ftell(f);rewind(f);void *v=malloc(*n);if(!v||fread(v,1,*n,f)!=*n)exit(2);fclose(f);return v; }
static void silent(void) {}
static void patch(uint8_t *b,size_t o,void *f) { if(o%4)abort();uint8_t ins[]={0xdf,0xf8,0x00,0xf0};memcpy(b+o,ins,4);uint32_t p=(uintptr_t)f;memcpy(b+o+4,&p,4); }
int main(int argc,char **argv) {
 if(argc<4) {fprintf(stderr,"Usage: %s u-boot.bin Image compressed...\n",argv[0]);return 1;}
 cpu_set_t cp;CPU_ZERO(&cp);CPU_SET(0,&cp);sched_setaffinity(0,sizeof cp,&cp);
 size_t bn,kn;void *b=readfile(argv[1],&bn),*k=readfile(argv[2],&kn);
 void *m=mmap((void*)0x4a000000,0x200000,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
 if(m!=(void*)0x4a000000||bn!=0x100000){fprintf(stderr,"map/size failure\n");return 2;}
 memcpy(m,b,bn);free(b);patch(m,0x11728,malloc);patch(m,0x115cc,free);patch(m,0x6a7bc,silent);
 __builtin___clear_cache(m,(char*)m+bn);if(mprotect(m,0x200000,PROT_READ|PROT_EXEC))return 3;
 int (*gunzip_fn)(void*,int,void*,unsigned long*)=(void*)0x4a066711;
 int (*lzma_fn)(void*,size_t*,void*,size_t)=(void*)0x4a061709;
 void *out=malloc(32*1024*1024);if(!out)return 4;
 for(int a=3;a<argc;a++) {
  size_t cn;void *in=readfile(argv[a],&cn);int lzma=strstr(argv[a],"lzma")!=NULL;
  for(int i=0;i<10;i++) {
   memset(out,0,kn);size_t n=lzma?32*1024*1024:cn;
   double t=now();int r=lzma?lzma_fn(out,&n,in,cn):gunzip_fn(out,32*1024*1024,in,&n);t=now()-t;
   if(r||n!=kn||memcmp(out,k,kn)){fprintf(stderr,"FAIL %s r=%d n=%zu\n",argv[a],r,n);return 5;}
   printf("%s,%d,%zu,%.6f,verified\n",argv[a],i,cn,t);fflush(stdout);
  }free(in);
 }
 free(out);free(k);return 0;
}
