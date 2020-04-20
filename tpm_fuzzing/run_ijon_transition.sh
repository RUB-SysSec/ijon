mkdir workdir/nv4
cd workdir/nv4 
AFL_PRELOAD="/data/projects/ijon/tpm_fuzzing/feeder/derand.so /data/projects/ijon/tpm_fuzzing/feeder/feeder.so" /data/projects/ijon/2019_ijon_space_explorer/afl-fuzz -m 300 -S s_ijon -i ../seed -o ../sync/ijon -- ../../src/tpm_server_ijon_transition -rm
