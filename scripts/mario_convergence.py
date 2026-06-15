#!/usr/bin/env python3
"""Mario Part B — the "fuzzer plays the level" A/B, driven by the AGENT's own
annotation.

Two AFL campaigns from the same seed, headless, no ROM:
  - plain AFL (targets/mario_plain): no IJON feedback.
  - AFL+IJON (built here with the agent's Part-A annotation IJON_MAX(world_pos)).
Metric = max world_pos (how far right Mario got) reached in the corpus over
wall-clock. Plain plateaus near the first obstacle; IJON hill-climbs rightward.

Reproduce: python scripts/mario_convergence.py --budget 600
(Run scripts/mario_annotation.py first for the Part-A annotation record.)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from chunk_seq_diversity import analyse  # noqa  (unused; keeps scripts/ importable)

AFL = Path(os.environ.get("AFL_ROOT", "/opt/AFLplusplus"))
WS = REPO / "workspace" / "mario"
SRC = WS / "build" / "src"
RUN = WS / "build" / "run"
OUT = REPO / "experiments" / "mario"
os.environ.setdefault("TMPDIR", "/tmp")
TRACE_RE = re.compile(rb"^(\d+),(\d+)$", re.M)


def sh(cmd, **kw):
    env = dict(os.environ); env["PATH"] = f"{AFL}:{env.get('PATH','')}"
    env["AFL_PATH"] = str(AFL / "include"); env["AFL_QUIET"] = "1"
    env["AFL_DONT_OPTIMIZE"] = "1"; env.update(kw.pop("env", {}))
    return subprocess.run(cmd, env=env, shell=isinstance(cmd, str),
                          capture_output=True, text=True, **kw)


def build_ijon_llm() -> Path:
    """Rebuild the IJON target carrying the agent's annotation IJON_MAX(world_pos)
    in place of the authors' ijon_max(pos_y/16, world_pos)."""
    main = SRC / "Main.cpp"
    orig = main.read_text()
    patched = orig.replace("ijon_max(pos_y/16, world_pos);",
                           "IJON_MAX(world_pos);  /* agent (Part A) */")
    if patched == orig:
        print("  WARN: could not find authors' annotation to replace")
    main.write_text(patched)
    try:
        obj = WS / "build" / "obj_ijon_llm"; obj.mkdir(exist_ok=True)
        cf = (f"-std=c++11 -O0 -g -Wno-narrowing -I{SRC} "
              f"{subprocess.check_output(['sdl2-config','--cflags']).decode().strip()} "
              f"-D_USE_IJON")
        for f in SRC.rglob("*.cpp"):
            o = obj / (f"{f.name}.o")
            r = sh([str(AFL / "afl-clang-fast++"), *cf.split(), "-c", str(f),
                    "-o", str(o)], env={"AFL_LLVM_IJON": "1"})
            if r.returncode != 0:
                print("  build error:", r.stderr[-300:]); return None
        libs = subprocess.check_output(['sdl2-config', '--libs']).decode().split()
        out = WS / "targets" / "mario_ijon_llm"
        r = sh([str(AFL / "afl-clang-fast++"), "-O0", *[str(p) for p in obj.glob("*.o")],
                *libs, "-o", str(out)], env={"AFL_LLVM_IJON": "1"})
        return out if r.returncode == 0 and out.exists() else None
    finally:
        main.write_text(orig)  # restore staged source


def max_world_pos(target: Path, input_file: Path) -> int:
    """Replay one input headless (trace) and return the max world_pos it reaches."""
    try:
        p = subprocess.run([str(target), "0", "trace"], stdin=open(input_file, "rb"),
                           cwd=str(RUN), capture_output=True, timeout=20)
    except subprocess.TimeoutExpired:
        return 0
    best = 0
    for m in TRACE_RE.finditer(p.stdout):
        best = max(best, int(m.group(1)))
    return best


def run_arm(target: Path, tag: str, budget: float, sample_every: float) -> list:
    out_dir = RUN / f"afl_{tag}"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    seeds = RUN / "seeds"
    env = dict(os.environ)
    env["PATH"] = f"{AFL}:{env.get('PATH','')}"; env["AFL_PATH"] = str(AFL / "include")
    env["AFL_SKIP_CPUFREQ"] = "1"; env["AFL_NO_UI"] = "1"; env["AFL_QUIET"] = "1"
    env["AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES"] = "1"; env["AFL_BENCH_UNTIL_CRASH"] = "0"
    proc = subprocess.Popen(
        [str(AFL / "afl-fuzz"), "-i", str(seeds), "-o", str(out_dir),
         "--", str(target), "0"],
        cwd=str(RUN), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    queue = out_dir / "default" / "queue"
    seen, best, traj = set(), 0, []
    t0 = time.time()
    try:
        while time.time() - t0 < budget:
            time.sleep(sample_every)
            if not queue.exists():
                continue
            for q in queue.glob("id:*"):
                if q.name in seen:
                    continue
                seen.add(q.name)
                best = max(best, max_world_pos(target, q))
            traj.append({"t": round(time.time() - t0, 1),
                         "queue": len(seen), "max_world_pos": best})
            print(f"  [{tag} t={traj[-1]['t']:>5}s] queue={len(seen):>4} "
                  f"max_world_pos={best}")
    finally:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    return traj


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, default=600)
    ap.add_argument("--sample-every", type=float, default=20)
    ap.add_argument("--skip-build", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    ijon = WS / "targets" / "mario_ijon_llm"
    if not args.skip_build:
        print("building IJON target with the agent's annotation IJON_MAX(world_pos) ...")
        ijon = build_ijon_llm()
        if ijon is None:
            print("BUILD FAILED"); return 1
    plain = WS / "targets" / "mario_plain"

    print(f"\n-- arm: plain AFL ({plain.name}) --")
    plain_traj = run_arm(plain, "plain", args.budget, args.sample_every)
    print(f"\n-- arm: AFL+IJON, agent annotation ({ijon.name}) --")
    ijon_traj = run_arm(ijon, "ijon", args.budget, args.sample_every)

    fp = plain_traj[-1]["max_world_pos"] if plain_traj else 0
    fi = ijon_traj[-1]["max_world_pos"] if ijon_traj else 0
    result = {"budget_s": args.budget,
              "annotation": "IJON_MAX(world_pos)  (agent, Part A)",
              "trajectory": {"plain": plain_traj, "ijon": ijon_traj},
              "final": {"plain_max_world_pos": fp, "ijon_max_world_pos": fi,
                        "fold": round(fi / fp, 2) if fp else None}}
    (OUT / "convergence.json").write_text(json.dumps(result, indent=2))
    print("\n================= SUMMARY =================")
    print(f"  max world_pos (how far Mario got):  plain={fp}  ijon={fi}"
          + (f"  ({result['final']['fold']}x)" if fp else ""))
    print(f"  -> {(OUT/'convergence.json').relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
