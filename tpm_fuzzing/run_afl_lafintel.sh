mkdir workdir/nv1
cd workdir/nv1 
AFL_PRELOAD="/data/projects/ijon/tpm_fuzzing/feeder/derand.so /data/projects/ijon/tpm_fuzzing/feeder/feeder.so" /data/projects/ijon/2019_ijon_space_explorer/afl-fuzz -m 600 -S s_laf -i ../seed -o ../sync/afl -- ../../src/tpm_server_afl_lafintel -rm
