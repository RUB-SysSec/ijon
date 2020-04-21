#include<stdio.h>
#include<unistd.h>
#include<assert.h>
#include<stdbool.h>
#include <stdlib.h>

int main(int argc, char** argv){
  int a = 0;
  int b = 0;
  if(read(0, &a, sizeof(a)) != sizeof(a)){
    printf("failed to read input\n");
    exit(1);
  }else{
    printf("read %d\n",a);
  }


  if(read(0, &b, sizeof(b)) != sizeof(b)){
    printf("failed to read input\n");
    exit(1);
  }else{
    printf("read %d\n",b);
  }
  int o = 213456;
  int m = 13;
  printf("a+%d == %d*b (%d == %d)\n",o,m,a+o, b*m);

  IJON_DIST(a+o, m*b);

  if(a+o==m*b){
    assert(false);
  }
}
