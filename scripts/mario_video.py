#!/usr/bin/env python3
"""Render a placeholder-CHR video of a Mario playthrough (ROM-free).

The game reads the ROM ONLY for CHR (tile graphics); the logic + palette are
compiled C++. So we synthesize a CHR (each tile a solid block in the game's real
palette; the dominant sky tile 292 and tile 0 left blank), capture the rendered
frames headless, and assemble a GIF with a live world_pos overlay. No Nintendo
data is used or produced.

Pipeline: ensure capture binary (apply patches/capture_mode.patch + build) ->
synth CHR ROM -> run `mario_capture 0 capture < playthrough.input` (writes
frames.raw + frames_wp.txt) -> GIF with overlay.
Reproduce: python scripts/mario_video.py
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parents[1]
WS = REPO / "workspace" / "mario"
SRC = WS / "build" / "src"
RUN = WS / "build" / "run"
AFL = Path(os.environ.get("AFL_ROOT", "/opt/AFLplusplus"))
W, H = 256, 240
SKY_TILE = 292          # dominant background tile (found via nametable histogram)
os.environ.setdefault("TMPDIR", "/tmp")


def sh(cmd, **kw):
    env = dict(os.environ); env["PATH"] = f"{AFL}:{env.get('PATH','')}"
    env["AFL_PATH"] = str(AFL / "include"); env["AFL_QUIET"] = "1"
    env["AFL_DONT_OPTIMIZE"] = "1"
    return subprocess.run(cmd, env=env, capture_output=True, text=True, **kw)


def ensure_capture_binary() -> Path:
    out = WS / "targets" / "mario_capture"
    if out.exists():
        return out
    if "g_capture" not in (SRC / "Main.cpp").read_text():
        # patch uses a/Main.cpp · b/Main.cpp headers -> apply with -p1 from src/
        sh(["git", "apply", "-p1", str(WS / "patches" / "capture_mode.patch")], cwd=str(SRC))
    cf = ["-std=c++11", "-O0", "-g", "-Wno-narrowing", f"-I{SRC}",
          *subprocess.check_output(["sdl2-config", "--cflags"]).decode().split()]
    obj = WS / "build" / "obj_capture"; obj.mkdir(exist_ok=True)
    for f in SRC.rglob("*.cpp"):
        sh([str(AFL / "afl-clang-fast++"), *cf, "-c", str(f),
            "-o", str(obj / f"{f.name}.o")])
    libs = subprocess.check_output(["sdl2-config", "--libs"]).decode().split()
    sh([str(AFL / "afl-clang-fast++"), "-O0", *[str(p) for p in obj.glob("*.o")],
        *libs, "-o", str(out)])
    return out


def synth_chr_rom(path: Path):
    """40976-byte ROM; CHR region = solid tiles (palette 1/2/3 cycling), with the
    sky tile and tile 0 left blank so the background reads as real sky."""
    rom = bytearray(40976); CHR = 16 + 32768
    for t in range(512):
        if t in (0, SKY_TILE):
            continue
        base = CHR + t * 16; p = 1 + (t % 3)
        lo = 0xFF if p & 1 else 0; hi = 0xFF if p & 2 else 0
        for r in range(8):
            rom[base + r] = lo; rom[base + 8 + r] = hi
    path.write_bytes(rom)


def capture(cap_bin: Path, rom: Path, input_file: Path):
    (RUN / "smbc.conf").write_text(
        f"[game]\nrom_file = {rom.name}\n[audio]\nenabled = 0\n")
    with open(input_file, "rb") as fin:
        subprocess.run([str(cap_bin), "0", "capture"], stdin=fin, cwd=str(RUN),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)


def make_gif(out: Path, step: int = 3, scale: int = 2):
    data = np.fromfile(RUN / "frames.raw", dtype=np.uint32); n = data.size // (W * H)
    wp = [int(x) for x in (RUN / "frames_wp.txt").read_text().split()]
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
        small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
    except OSError:
        font = small = ImageFont.load_default()
    start = next(i for i in range(n)
                 if len(np.unique(data[i * W * H:(i + 1) * W * H])) > 4)
    frames = []
    for i in range(start, n, step):
        fr = data[i * W * H:(i + 1) * W * H].reshape(H, W)
        rgb = np.stack([(fr >> 16) & 255, (fr >> 8) & 255, fr & 255], -1).astype(np.uint8)
        img = Image.fromarray(rgb).resize((W * scale, H * scale), Image.NEAREST).convert("RGB")
        d = ImageDraw.Draw(img)
        w = wp[i] if i < len(wp) else wp[-1]
        d.rectangle([0, 0, W * scale, 28], fill=(0, 0, 0))
        d.text((6, 3), f"world_pos: {w:>4}", font=font, fill=(255, 255, 80))
        d.text((170, 7), "AFL+IJON  -  IJON_MAX(world_pos)  [LLM, blind]",
               font=small, fill=(120, 220, 255))
        frames.append(img.convert("P", palette=Image.ADAPTIVE, colors=64))
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=60,
                   loop=0, optimize=True)
    return len(frames), wp[start], max(wp)


def main() -> int:
    inp = WS / "playthrough.input"
    if not inp.exists():
        print(f"missing {inp} (a good playthrough input)"); return 1
    cap = ensure_capture_binary()
    rom = RUN / "ph_final.nes"; synth_chr_rom(rom)
    print("capturing frames ...")
    capture(cap, rom, inp)
    out = REPO / "experiments" / "mario" / "mario_playthrough.gif"
    nf, w0, wmax = make_gif(out)
    # tidy the large raw dump
    for f in ("frames.raw", "frames_wp.txt"):
        (RUN / f).unlink(missing_ok=True)
    print(f"wrote {out.relative_to(REPO)}: {nf} frames, world_pos {w0}->{wmax}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
