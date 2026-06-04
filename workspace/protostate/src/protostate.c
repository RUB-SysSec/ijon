/*
 * Synthetic stateful-protocol roadblock (IJON class 2: known state changes).
 *
 * The input is a stream of protocol commands (one per byte). The server runs a
 * state machine: in each step it expects a specific command (a pseudo-random
 * function of the step). The expected command advances the protocol; any
 * unexpected command resets it to the start (as a strict protocol would). A
 * privileged operation becomes reachable only after the full expected command
 * sequence has been accepted in order.
 *
 * Each command is dispatched to a handler, but the handlers are ordinary shared
 * code: edge coverage saturates once every command type has been seen and does
 * NOT reflect how far the protocol has advanced. A coverage-guided fuzzer has no
 * feedback about the *sequence of state changes* and cannot assemble the
 * required command sequence. The interesting state is which commands were
 * accepted, in order -- spread across operations, not a single exposed value.
 *
 * NOTE (honest): plain AFL plateaus here (0 crashes / 16M+ execs), and the agent
 * correctly diagnoses this as the known_state_changes class and proposes
 * IJON_STATE(step). However a 1-D reset-on-wrong sequence cannot be *auto-solved*
 * by AFL+IJON_STATE in a short budget -- climbing needs a contiguous correct
 * prefix that havoc corrupts faster than it extends. A robust class-2 auto-solve
 * needs maze-like 2-D/graph structure (cf. test/ijon-maze.c, which proves the
 * state-exposure mechanism). See docs/architecture-design.md.
 *
 * Build (plain, plateaus):
 *   afl-clang-fast -fsanitize=fuzzer -o protostate protostate.c
 */
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>

#define NCMD  8
#define STEPS 14

static uint8_t expected_cmd(uint32_t step) {
  return (uint8_t)((((step + 1u) * 2654435761u) >> 24) % NCMD);
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {

  static volatile uint32_t sink = 0;
  uint32_t step = 0;

  for (size_t i = 0; i < size; i++) {

    uint8_t cmd = (uint8_t)(data[i] % NCMD);

    switch (cmd) {                 /* dispatch the command to its handler */
      case 0:  sink += 1;  break;
      case 1:  sink += 3;  break;
      case 2:  sink += 7;  break;
      case 3:  sink += 11; break;
      case 4:  sink += 13; break;
      case 5:  sink += 17; break;
      case 6:  sink += 19; break;
      default: sink += 23; break;
    }

    if (cmd == expected_cmd(step)) step++;   /* command accepted: advance */
    else step = 0;                            /* unexpected command: reset */

#ifdef IJON_STATE
    IJON_STATE(step);   /* expose the protocol state change */
#endif
  }

  if (step >= STEPS) {

    abort();   /* full command sequence accepted -> privileged op reached */

  }

  return 0;

}
