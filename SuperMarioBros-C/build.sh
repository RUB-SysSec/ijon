#create a build folder with cmake, using CC=/your/compiler and run the following 
rm smbc_ijon
rm smbc_ijon_afl
make clean
AFL_DONT_OPTIMIZE=1 AFL_INST_RATIO=1 make 
mv smbc smbc_ijon  #if build with ijon_afl
#mv smbc smbc_ijon  #if build with normal afl
