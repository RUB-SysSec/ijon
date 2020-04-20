mkdir workdir/nv3
cd workdir/nv3 
AFL_PRELOAD="/data/projects/ijon/tpm_fuzzing/feeder/derand.so /data/projects/ijon/tpm_fuzzing/feeder/feeder.so" /data/projects/ijon/2019_ijon_space_explorer/afl-fuzz -m 800 -S s_laf -i ../seed -o ../sync/ijon -- ../../src/tpm_server_afl_lafintel -rm
