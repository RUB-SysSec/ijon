#!/usr/bin/env bash
# Build a source-coverage (llvm-cov) variant of the libpng CRC harness, for
# replaying the AFL corpus to get source-level coverage (the localization lens).
# Clean clang (NO AFL instrumentation) + -fprofile-instr-generate -fcoverage-mapping.
# libFuzzer driver (-fsanitize=fuzzer) so `./bin <files...>` replays them.
set -euo pipefail
export TMPDIR="${TMPDIR:-/home/sanjay/san-home/tmp}"; mkdir -p "$TMPDIR"
LLVM="${LLVM_BIN:-/home/sanjay/san-home/research/llvm-stuff/llvm-project/build/bin}"
export PATH="$LLVM:$PATH"
WS="$(cd "$(dirname "$0")" && pwd)"
BUILD="$WS/build"; COV="$BUILD/cov"; mkdir -p "$COV" "$WS/targets"
COVFLAGS="-fprofile-instr-generate -fcoverage-mapping -g -O0"

# 1. zlib (clean clang, static) — no coverage needed, just to link
if [ ! -f "$COV/zlib/install/lib/libz.a" ]; then
  cp -r "$BUILD/zlib" "$COV/zlib"; ( cd "$COV/zlib" && make distclean >/dev/null 2>&1 || true
    CC=clang ./configure --static --prefix="$COV/zlib/install" && make -j4 && make install )
fi
ZINST="$COV/zlib/install"

# 2. libpng with source-coverage instrumentation (static)
if [ ! -f "$COV/libpng/.libs/libpng16.a" ]; then
  cp -r "$BUILD/libpng" "$COV/libpng"; ( cd "$COV/libpng" && make distclean >/dev/null 2>&1 || true
    CC=clang CFLAGS="$COVFLAGS" CPPFLAGS="-I$ZINST/include" LDFLAGS="-L$ZINST/lib" \
      ./configure --disable-shared --enable-static && make -j4 )
fi
LIBPNG="$COV/libpng"

# 3. harness coverage binary (libFuzzer driver replays corpus files)
clang++ $COVFLAGS -fsanitize=fuzzer -I"$LIBPNG" -I"$ZINST/include" \
  "$WS/src/libpng_crc_fuzzer.cc" "$LIBPNG/.libs/libpng16.a" "$ZINST/lib/libz.a" \
  -o "$WS/targets/libpng_crc_cov"
echo "built: targets/libpng_crc_cov (llvm-cov coverage build)"
