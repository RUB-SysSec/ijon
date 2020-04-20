gdb -ex 'set environment LD_PRELOAD /home/me/tpm_fuzzing/feeder/derand.so /home/me/tpm_fuzzing/feeder/feeder.so' -ex "set args < $1" ./src/tpm_server
