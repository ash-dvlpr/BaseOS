#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <sched.h>
int decode(const unsigned char*,size_t,unsigned char*,size_t);
static double now(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+t.tv_nsec/1e9;}
static void *readfile(const char *p,size_t *n){FILE*f=fopen(p,"rb");if(!f)exit(1);fseek(f,0,SEEK_END);*n=ftell(f);rewind(f);void*b=malloc(*n);if(fread(b,1,*n,f)!=*n)exit(2);fclose(f);return b;}
int main(int argc,char**argv){if(argc!=3)return 1;cpu_set_t cp;CPU_ZERO(&cp);CPU_SET(0,&cp);sched_setaffinity(0,sizeof cp,&cp);size_t cn,kn;void*c=readfile(argv[1],&cn),*k=readfile(argv[2],&kn),*b=malloc(kn);for(int i=0;i<10;i++){memset(b,0,kn);double t=now();int r=decode(c,cn,b,kn);t=now()-t;if(r!=(int)kn||memcmp(k,b,kn))return 3;printf("%d,%zu,%.6f,verified\n",i,cn,t);}return 0;}
