mkdir workdir/nv2
cd workdir/nv2 
AFL_PRELOAD="/data/projects/ijon/tpm_fuzzing/feeder/derand.so /data/projects/ijon/tpm_fuzzing/feeder/feeder.so" /data/projects/ijon/2019_ijon_space_explorer/afl-fuzz -m 300 -S s_plain -i ../seed -o ../sync/afl -- ../../src/tpm_server_afl -rm
