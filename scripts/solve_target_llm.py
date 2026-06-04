#!/usr/bin/env python3
"""One autonomous analyst turn against any libFuzzer target with a #ifdef-gated
ground-truth annotation:  strip -> fuzz to plateau -> [DeepSeek] classify +
synthesize -> patch -> rebuild -> re-fuzz -> verdict.

The model only ever sees the answer-stripped source + real plateau telemetry.
Success = its annotation makes AFL reach the goal (SIGABRT).

  python3 scripts/solve_target_llm.py --workspace workspace/checksum \
      --src checksum-guard.c
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness import (AflConfig, Builder, FuzzerController, PlateauDetector,
                     apply_annotation, make_clean_source)
from harness.agent import propose_annotation
from harness.model import AnalystModel


def banner(m: str) -> None:
    print(f"\n{'='*72}\n{m}\n{'='*72}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True, help="dir with src/ in/ targets/ out/")
    ap.add_argument("--src", required=True, help="source filename under <workspace>/src")
    ap.add_argument("--name", default=None, help="source name shown to the model")
    ap.add_argument("--plateau-timeout", type=float, default=80)
    ap.add_argument("--solve-timeout", type=float, default=90)
    ap.add_argument("--min-stall", type=int, default=30)
    args = ap.parse_args()

    ws = (REPO / args.workspace).resolve()
    src_orig = ws / "src" / args.src
    stem = Path(args.src).stem
    src_clean = ws / "src" / f"{stem}_clean.c"
    src_llm = ws / "src" / f"{stem}_llm.c"
    input_dir = ws / "in"
    name = args.name or args.src

    cfg = AflConfig(); cfg.check()
    builder = Builder(cfg)
    detector = PlateauDetector(min_stall_seconds=args.min_stall)

    clean = make_clean_source(src_orig.read_text())  # hard-fails on any leak
    src_clean.write_text(clean)
    print(f"[0] clean source ready ({len(clean.splitlines())} lines); "
          f"no 'ijon' tokens present (verified)")

    banner("1) FUZZ clean target to plateau")
    cc = builder.compile(src_clean, ws / "targets" / f"{stem}_clean", ijon=False)
    assert cc.ok, cc.stdout
    fc = FuzzerController(cc.binary, input_dir, ws / "out" / "llm_clean", cfg, cwd=ws)
    fc.run_until(detector.is_plateau, timeout=args.plateau_timeout, poll=3.0)
    snap = fc.snapshot()
    print(f"    {detector.explain(snap)}")
    if not detector.is_plateau(snap) or snap.solved:
        print("    [FAIL] no clean plateau reached"); return 1

    banner("2) ANALYST (DeepSeek) proposes an annotation")
    model = AnalystModel()
    print(f"    model: {model.model}")
    p = propose_annotation(model, clean, snap, source_name=name)
    print(f"    why_stuck      : {p.why_stuck}")
    print(f"    failure_class  : {p.failure_class}")
    print(f"    relevant_state : {p.relevant_state}")
    print(f"    macro          : {p.macro}")
    print(f"    code           : {p.annotation.code}")
    print(f"    after_substring: {p.annotation.after_substring!r}")
    print(f"    [served by {p.llm.model}, {p.llm.prompt_tokens}+"
          f"{p.llm.completion_tokens} tok, {p.llm.latency_s:.1f}s]")

    banner("3) PATCH + REBUILD with the model's annotation")
    try:
        patched = apply_annotation(clean, p.annotation)
    except ValueError as e:
        print(f"    [FAIL] could not place annotation: {e}"); return 1
    src_llm.write_text(patched)
    cp = builder.compile(src_llm, ws / "targets" / f"{stem}_llm", ijon=True)
    print(f"    build ok={cp.ok} header_included={cp.header_included} "
          f"header_missing={cp.header_missing}")
    if not cp.ok:
        print("    [FAIL] patched target did not compile:")
        print("    " + "\n    ".join(cp.stdout.splitlines()[-8:])); return 1

    banner("4) RE-FUZZ — did the annotation reach the goal?")
    fp = FuzzerController(cp.binary, input_dir, ws / "out" / "llm_patched",
                          cfg, cwd=ws, stop_on_crash=True)
    fp.run_until(lambda s: s.solved, timeout=args.solve_timeout, poll=2.0)
    snap2 = fp.snapshot()
    print(f"    {detector.explain(snap2)} saved_crashes={snap2.saved_crashes}")

    banner("VERDICT")
    solved = bool(snap2 and snap2.solved)
    print(f"    [{'PASS' if solved else 'FAIL'}] {name}: DeepSeek's "
          f"{p.macro} annotation {'SOLVED the target' if solved else 'did NOT solve'}")
    return 0 if solved else 1


if __name__ == "__main__":
    raise SystemExit(main())
