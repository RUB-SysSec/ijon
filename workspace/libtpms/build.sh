#!/usr/bin/env bash
# Reproducible build of the libtpms (TPM 2.0) IJON fuzz target.
#
# libtpms is the modern, maintained vTPM library (behind swtpm) -- unlike the
# 2018 IBM TPM, it builds cleanly on OpenSSL 3. We build it with afl-clang-fast +
# ASAN (to catch memory bugs) and link the command-stream harness (src/tpm_seq_fuzzer.c)
# in two variants: IJON (the command-sequence annotation, the "star") and plain
# (workers). They share one sync dir; IJON cracks state barriers, plain exploits.
#
# Deps (apt): libtool gawk  (+ openssl already present). See docs/installed-system-deps.md.
# Usage: ./build.sh        Source tree in ./build/ (gitignored). Never writes /tmp.
set -euo pipefail

export TMPDIR="${TMPDIR:-/home/sanjay/san-home/tmp}"; mkdir -p "$TMPDIR"
AFL="${AFL_ROOT:-/home/sanjay/san-home/research/repos/AFLplusplus}"
export PATH="$AFL:$PATH"; export AFL_PATH="$AFL/include"; export AFL_QUIET=1
WS="$(cd "$(dirname "$0")" && pwd)"
LT="$WS/build/libtpms"
mkdir -p "$WS/targets"

# 1. source (pinned to current master via shallow clone)
[ -d "$LT/.git" ] || git clone --depth 1 https://github.com/stefanberger/libtpms.git "$LT"

# 2. libtpms with afl-clang-fast + ASAN (TPM2 only, static)
cd "$LT"
[ -f configure ] || { NOCONFIGURE=1 ./autogen.sh; }
[ -f Makefile ]  || CC=afl-clang-fast ./configure --without-tpm1 --with-openssl \
                      --disable-shared --enable-static
LIB="$LT/src/.libs/libtpms.a"
if [ ! -f "$LIB" ]; then
  AFL_USE_ASAN=1 make clean >/dev/null 2>&1 || true
  AFL_USE_ASAN=1 make -j4
fi
ls -la "$LIB"

# 3. harness binaries (share the ASAN libtpms.a)
INC="-I$LT/include"
build_harness() {  # $1=tag $2=defines $3=ijon(1=on)
  # AFL enables IJON if AFL_LLVM_IJON is SET (any value, incl. 0) -> must UNSET it
  # for the plain control, not set it to 0.
  if [ "$3" = "1" ]; then export AFL_LLVM_IJON=1; else unset AFL_LLVM_IJON; fi
  AFL_USE_ASAN=1 afl-clang-fast -g -O1 $2 $INC \
    "$WS/src/tpm_seq_fuzzer.c" "$LIB" -lcrypto -o "$WS/targets/tpm_fuzz_$1"
  echo "built: targets/tpm_fuzz_$1"
}
build_harness ijon  "-D_USE_IJON" 1     # the IJON star (command-sequence state)
build_harness plain ""             0     # plain workers (truly plain)
echo "done."
