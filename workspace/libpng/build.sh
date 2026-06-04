#!/usr/bin/env bash
# Reproducible build of the libpng CRC-roadblock target (M4 first real target).
#
# Builds zlib + libpng instrumented with AFL++, then the CRC-enforced fuzz
# harness (src/libpng_crc_fuzzer.cc -> PNG_CRC_ERROR_QUIT). Also builds the
# CRC-disabled contrast target for quantifying the roadblock.
#
# Usage:  ./build.sh
# Scratch/clone tree goes in ./build/ (gitignored). Never writes to /tmp.
set -euo pipefail

export TMPDIR="${TMPDIR:-/home/sanjay/san-home/tmp}"; mkdir -p "$TMPDIR"
AFL="${AFL_ROOT:-/home/sanjay/san-home/research/repos/AFLplusplus}"
export PATH="$AFL:$PATH"
WS="$(cd "$(dirname "$0")" && pwd)"
BUILD="$WS/build"; ZINST="$BUILD/zlib/install"; LIBPNG="$BUILD/libpng"
mkdir -p "$BUILD" "$WS/targets" "$WS/in"

# 1. sources (pinned)
[ -d "$BUILD/zlib" ]   || git clone --depth 1 -b v1.3.1  https://github.com/madler/zlib.git    "$BUILD/zlib"
[ -d "$LIBPNG" ]       || git clone --depth 1 -b v1.6.58 https://github.com/pnggroup/libpng.git "$LIBPNG"
# NOTE: the recorded experimental numbers (CRC A/B, frontier localization, the
# 53x chunk-sequence-diversity result) were measured on libpng 1.6.44; the pin
# is now the latest 1.6.58. The roadblock mechanisms (per-chunk CRC, chunk
# dispatch) are unchanged across 1.6.x, so the conclusions hold; only re-run if
# you want the numbers restated on 1.6.58.

# 2. zlib (instrumented, static)
( cd "$BUILD/zlib" && CC=afl-clang-fast ./configure --static --prefix="$ZINST" && \
  AFL_QUIET=1 make -j4 && make install )

# 3. libpng (instrumented, static)
( cd "$LIBPNG" && CC=afl-clang-fast CPPFLAGS="-I$ZINST/include" LDFLAGS="-L$ZINST/lib" \
  ./configure --disable-shared --enable-static && AFL_QUIET=1 make -j4 )

# 4. seeds from libpng's own test suite (if not present)
for s in basn0g01 basn2c08 basn6a08; do
  [ -f "$WS/in/$s.png" ] || cp "$LIBPNG/contrib/pngsuite/$s.png" "$WS/in/"
done
mkdir -p "$WS/in_single"; cp -n "$WS/in/basn0g01.png" "$WS/in_single/" || true

# 5. targets: CRC enforced (roadblock) and CRC disabled (contrast)
INC="-I$LIBPNG -I$ZINST/include"
LIBS="$LIBPNG/.libs/libpng16.a $ZINST/lib/libz.a"
AFL_QUIET=1 afl-clang-fast++ -g -O2 -fsanitize=fuzzer $INC \
  "$WS/src/libpng_crc_fuzzer.cc" $LIBS -o "$WS/targets/libpng_crc_plain"

# CRC-off contrast (flip the one png_set_crc_action line)
sed 's/PNG_CRC_ERROR_QUIT, PNG_CRC_ERROR_QUIT);.*/PNG_CRC_QUIET_USE, PNG_CRC_QUIET_USE);/' \
  "$WS/src/libpng_crc_fuzzer.cc" > "$BUILD/libpng_crcoff_fuzzer.cc"
AFL_QUIET=1 afl-clang-fast++ -g -O2 -fsanitize=fuzzer $INC \
  "$BUILD/libpng_crcoff_fuzzer.cc" $LIBS -o "$WS/targets/libpng_crc_off"

echo "built: targets/libpng_crc_plain (CRC enforced) + targets/libpng_crc_off (contrast)"
