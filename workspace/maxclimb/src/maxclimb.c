/*
 * Synthetic value-maximization roadblock.
 *
 * Each input position i has a fixed expected byte (a pseudo-random function of
 * i). The score is how many positions match their expected byte. The goal is
 * reached only when the score climbs to TARGET.
 *
 * The score has no effect on which branches execute (the same compare runs for
 * every position), so a coverage-guided fuzzer gets no signal that a higher
 * score is closer to the goal, and the pseudo-random expected bytes can't be
 * guessed in bulk. The interesting state is the score itself -- a value to push
 * as high as possible -- which lives in a variable, not in coverage.
 *
 * Build (plain, plateaus):
 *   afl-clang-fast -fsanitize=fuzzer -o maxclimb maxclimb.c
 */
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>

#define WIDTH  256
#define TARGET 32

static uint8_t expected_byte(uint32_t pos) {
  uint32_t h = (pos + 1u) * 2654435761u;   /* multiplicative hash */
  return (uint8_t)(h >> 24);
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {

  uint32_t score = 0;
  for (size_t i = 0; i < size && i < WIDTH; i++) {
    if (data[i] == expected_byte((uint32_t)i)) score++;
  }

#ifdef IJON_MAX
  IJON_MAX(score);   /* expose the score so the fuzzer pushes it higher */
#endif

  if (score >= TARGET) {

    abort();   /* climbed to the goal */

  }

  return 0;

}
