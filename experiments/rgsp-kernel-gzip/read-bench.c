#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <fcntl.h>
#include <unistd.h>
#include <time.h>
static double now(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+t.tv_nsec/1e9;}
int main(void){int fd=open("/dev/mmcblk0p4",O_RDONLY|O_DIRECT);if(fd<0){perror("open");return 1;}void *b; if(posix_memalign(&b,4096,1024*1024))return 2;size_t sizes[]={20295680,9701376,7764992};for(int i=0;i<7;i++)for(int j=0;j<3;j++){size_t n=(sizes[j]+4095)&~4095UL,p=0;double t=now();while(p<n){size_t c=n-p;if(c>1024*1024)c=1024*1024;ssize_t r=pread(fd,b,c,p);if(r!=(ssize_t)c){perror("read");return 3;}p+=r;}printf("%d,%zu,%.6f\n",i,n,now()-t);fflush(stdout);}close(fd);free(b);}
