/*
 * Two sequential gates guarding the handler.
 *
 * Layout: [4-byte stored checksum][4-byte tag][payload...].
 *   Gate 1: stored must equal an FNV-1a hash of the payload.
 *   Gate 2: the tag must equal a fixed 32-bit constant.
 * Both gates are exact equalities with no gradient, and the second is only
 * reachable once the first is satisfied. The tag is independent of the
 * checksum (which covers only the payload), so it can be set freely once gate
 * one passes.
 *
 * Build:
 *   afl-clang-fast -fsanitize=fuzzer -o twogate twogate.c
 */
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>

static uint32_t fnv1a(const uint8_t *p, size_t n) {
  uint32_t h = 0x811c9dc5u;
  for (size_t i = 0; i < n; i++) {
    h ^= p[i];
    h *= 16777619u;
  }
  return h;
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {

  if (size < 12) return 0;

  uint32_t stored = (uint32_t)data[0] | ((uint32_t)data[1] << 8) |
                    ((uint32_t)data[2] << 16) | ((uint32_t)data[3] << 24);
  uint32_t tag = (uint32_t)data[4] | ((uint32_t)data[5] << 8) |
                 ((uint32_t)data[6] << 16) | ((uint32_t)data[7] << 24);
  uint32_t actual = fnv1a(data + 8, size - 8);

#ifdef IJON_CMP
  IJON_CMP(stored, actual);
#endif
  if (stored != actual) return 0;  /* gate 1: checksum */

#ifdef IJON_CMP
  IJON_CMP(tag, 0xC0FFEE42u);
#endif
  if (tag == 0xC0FFEE42u) {         /* gate 2: tag */

    abort();  /* both gates passed */

  }

  return 0;

}
