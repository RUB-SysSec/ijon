/*
 * Minimal binary format with a header checksum.
 *
 * Layout: [4-byte little-endian header checksum][payload...]. The format is
 * valid only when the header value equals an FNV-1a hash computed over the
 * payload bytes. Inputs that satisfy the check reach the handler below.
 *
 * Build:
 *   afl-clang-fast -fsanitize=fuzzer -o checksum-guard checksum-guard.c
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

  if (size < 8) return 0;

  uint32_t stored = (uint32_t)data[0] | ((uint32_t)data[1] << 8) |
                    ((uint32_t)data[2] << 16) | ((uint32_t)data[3] << 24);
  uint32_t actual = fnv1a(data + 4, size - 4);

#ifdef IJON_CMP
  IJON_CMP(stored, actual);
#endif

  if (stored == actual) {

    abort();  /* valid format accepted */

  }

  return 0;

}
