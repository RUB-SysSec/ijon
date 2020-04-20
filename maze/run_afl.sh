AFL_BENCH_UNTIL_CRASH=1 ./afl-fuzz -S afl_$1 -m 200 -t 250 -i in  -o workdir_afl_$1  -- ./afl.$1
