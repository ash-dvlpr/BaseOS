#define _GNU_SOURCE
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#define main cached_bench_main
#include "lz4-bench.c"
#undef main
struct alloc_data {size_t len,align;unsigned heap_id_mask,flags;int handle;};
struct fd_data {int handle,fd;};
int main(int argc,char**argv){
 if(argc!=4)return 1;size_t cn,kn;void*c=readfile(argv[1],&cn),*k=readfile(argv[2],&kn);
 int dev=open("/dev/ion",O_RDWR|O_CLOEXEC);if(dev<0){perror("ion open");return 2;}
 size_t roundcn=(cn+4095)&~4095UL,total=roundcn+((kn+4095)&~4095UL);
 struct alloc_data a={.len=total,.align=4096,.heap_id_mask=1,.flags=strtoul(argv[3],0,0)};
 if(ioctl(dev,_IOWR('I',0,struct alloc_data),&a)){perror("ion alloc");return 3;}
 struct fd_data f={a.handle,-1};if(ioctl(dev,_IOWR('I',2,struct fd_data),&f)){perror("ion map");return 4;}
 unsigned char*m=mmap(0,total,PROT_READ|PROT_WRITE,MAP_SHARED,f.fd,0);if(m==MAP_FAILED){perror("mmap");return 5;}
 memcpy(m,c,cn);unsigned char*b=m+roundcn;
 for(int i=0;i<3;i++){double t=now();int r=decode(m,cn,b,kn);t=now()-t;if(r!=(int)kn||memcmp(k,b,kn)){fprintf(stderr,"decode mismatch %d\n",r);return 6;}printf("ion_flags=%u,%d,%zu,%.6f,verified\n",a.flags,i,cn,t);fflush(stdout);}
 munmap(m,total);close(f.fd);close(dev);free(c);free(k);return 0;
}
