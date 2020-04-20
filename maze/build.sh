CC=gcc
rm afl.maze.*
$CC								small.c -o afl.maze.small.bt
$CC -DMAZE_NO_BT	small.c -o afl.maze.small.nobt
$CC								big.c   -o afl.maze.big.bt
$CC -DMAZE_NO_BT	big.c   -o afl.maze.big.nobt
