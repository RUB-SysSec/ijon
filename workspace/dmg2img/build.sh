#!/usr/bin/env bash
# Build dmg2img with AFL + ASAN to study the XMLLength integer-overflow (the
# IJON paper's IJON_MAX example). NOTE (honest result): with the minimal seed
# from make_seed.py, plain AFL finds this crash in ~150s, so it is NOT a
# roadblock and does not demonstrate IJON_MAX necessity — see
# docs/architecture-design.md. Kept for reproducibility.
set -euo pipefail
export TMPDIR="${TMPDIR:-/tmp}"; mkdir -p "$TMPDIR"
AFL="${AFL_ROOT:?set AFL_ROOT to your AFL++ (with IJON) build}"
export PATH="$AFL:$PATH"
WS="$(cd "$(dirname "$0")" && pwd)"; B="$WS/build"; mkdir -p "$B" "$WS/targets" "$WS/in"

[ -d "$B/dmg2img" ] || git clone --depth 1 https://github.com/Lekensteyn/dmg2img.git "$B/dmg2img"
# dmg2img needs bzlib.h (often no -dev installed); fetch the public header and
# link the runtime libbz2 directly. lzfse is #ifdef-guarded, so we omit it.
[ -f "$B/bzlib.h" ] || curl -fsSL -o "$B/bzlib.h" https://gitlab.com/bzip2/bzip2/-/raw/master/bzlib.h
cp -f "$B/bzlib.h" "$B/dmg2img/bzlib.h"
BZ2=$(ls /lib/x86_64-linux-gnu/libbz2.so.1.0 /usr/lib/x86_64-linux-gnu/libbz2.so* 2>/dev/null | head -1)

( cd "$B/dmg2img" && AFL_USE_ASAN=1 AFL_QUIET=1 afl-clang-fast -g -O1 -I. \
    dmg2img.c base64.c adc.c -lz "$BZ2" -o "$WS/targets/dmg2img-afl" )
echo "built: targets/dmg2img-afl (AFL+ASAN). Overflow site: dmg2img.c:240."
python3 "$WS/make_seed.py"
