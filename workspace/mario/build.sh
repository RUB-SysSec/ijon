#!/usr/bin/env bash
# Reproducible build of the SuperMarioBros-C IJON demo target (headless).
#
# The game is the MitchellSternke C++ port (codegen'd from the SMB disassembly),
# as adapted for the IJON demo in RUB-SysSec/ijon-data. We build it HEADLESS for
# fuzzing: SDL is only initialized in video mode, and the ROM is read ONLY for
# CHR graphics (rendering) -- so the fuzzing result needs no .nes ROM (a dummy
# buffer suffices; a real ROM is needed only for the optional rendered video).
#
# CRITICAL: build at -O0 (AFL_DONT_OPTIMIZE, as the authors did). The codegen'd
# 6502 emulation has goto-heavy, flag-side-effect-sensitive code (e.g. the boot
# `VBlank1: a=M(PPU_STATUS); if(!n) goto VBlank1;` spin) that -O2 miscompiles
# into an infinite loop at reset().
#
# Usage: ./build.sh        (builds targets/mario_plain + targets/mario_ijon)
# Source staged into ./build/ (gitignored). Never writes to /tmp.
set -euo pipefail

export TMPDIR="${TMPDIR:-/home/sanjay/san-home/tmp}"; mkdir -p "$TMPDIR"
AFL="${AFL_ROOT:-/home/sanjay/san-home/research/repos/AFLplusplus}"
export PATH="$AFL:$PATH"; export AFL_PATH="$AFL/include"; export AFL_QUIET=1
export AFL_DONT_OPTIMIZE=1
WS="$(cd "$(dirname "$0")" && pwd)"
REF="${IJON_DATA_REF:-$WS/../../../ijon-data-ref}/SuperMarioBros-C"
SRC="$WS/build/src"
mkdir -p "$WS/build" "$WS/targets" "$WS/in"

# 1. stage the game source (from the read-only ijon-data clone)
[ -d "$SRC" ] || cp -r "$REF/source" "$SRC"

# 2. dummy ROM (40976 B of zeros = NES header + 2 PRG + 1 CHR page; CHR pointer
#    stays in-bounds). Only graphics; logic is in the codegen'd C++.
[ -f "$WS/build/dummy.nes" ] || head -c 40976 /dev/zero > "$WS/build/dummy.nes"

# 3. a run dir holding smbc.conf (points the game at the dummy ROM) + the ROM
RUN="$WS/build/run"; mkdir -p "$RUN"
cp -f "$WS/build/dummy.nes" "$RUN/dummy.nes"
printf '[game]\nrom_file = dummy.nes\n[audio]\nenabled = 0\n' > "$RUN/smbc.conf"

CF="-std=c++11 -O0 -g -Wno-narrowing -I$SRC $(sdl2-config --cflags)"
LIBS="$(sdl2-config --libs)"

build_variant() {  # $1 = tag, $2 = extra defines, $3 = ijon (1/0)
  local tag="$1" defs="$2" ijon="$3"
  local obj="$WS/build/obj_$tag"; mkdir -p "$obj"
  for f in $(find "$SRC" -name '*.cpp'); do
    local o="$obj/$(echo "$f" | md5sum | cut -c1-8)_$(basename "$f" .cpp).o"
    AFL_LLVM_IJON=$ijon afl-clang-fast++ $CF $defs -c "$f" -o "$o"
  done
  AFL_LLVM_IJON=$ijon afl-clang-fast++ -O0 "$obj"/*.o $LIBS -o "$WS/targets/mario_$tag"
  echo "built: targets/mario_$tag"
}

# 4. control (plain AFL, annotation compiled out) + treatment (AFL+IJON, _USE_IJON)
build_variant plain "" 0
build_variant ijon  "-D_USE_IJON" 1
echo "done. run dir: build/run (smbc.conf + dummy.nes)"
