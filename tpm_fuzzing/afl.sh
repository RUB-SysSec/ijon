AFL_PRELOAD="/home/me/tpm_fuzzing/feeder/derand.so /home/me/tpm_fuzzing/feeder/feeder.so" /data/proggen/source/afl/ijon/afl-fuzz -m 300 -S slave1 -i ../seed -o ../sync -- ../../src/tpm_server -rm 
