#!/usr/bin/env python3
"""M3 acceptance: can DeepSeek, acting as the analyst, independently break the
maze plateau? The model NEVER sees the ground-truth annotation (stripped). It
gets only the clean source + real plateau telemetry, then must reason out the
annotation. Success = its annotation makes AFL solve the hard maze (SIGABRT).

This is one full turn of the autonomous loop:
  run -> detect plateau -> [LLM] classify + synthesize -> patch -> rebuild -> run
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness import (AflConfig, Builder, FuzzerController, PlateauDetector,
                     apply_annotation, strip_ijon_blocks)
from harness.agent import propose_annotation
from harness.model import AnalystModel

MAZE = REPO / "workspace" / "maze"
SRC_ORIG = MAZE / "src" / "ijon-maze.c"
SRC_CLEAN = MAZE / "src" / "maze_clean.c"
SRC_LLM = MAZE / "src" / "maze_llm_patched.c"
INPUT_DIR = MAZE / "in"


def banner(msg: str) -> None:
    print(f"\n{'='*72}\n{msg}\n{'='*72}")


def main() -> int:
    cfg = AflConfig(); cfg.check()
    builder = Builder(cfg)
    detector = PlateauDetector(min_stall_seconds=30)

    # 0. Clean (answer-stripped) source — this is ALL the model will see.
    clean = strip_ijon_blocks(SRC_ORIG.read_text())
    SRC_CLEAN.write_text(clean)
    assert "IJON_SET" not in clean
    print(f"[0] clean source ready ({len(clean.splitlines())} lines, "
          f"annotation stripped)")

    # 1. Run the clean target to a REAL plateau.
    banner("1) FUZZ clean target to plateau")
    cc = builder.compile(SRC_CLEAN, MAZE / "targets" / "maze_clean", ijon=False)
    assert cc.ok, cc.stdout
    fc = FuzzerController(cc.binary, INPUT_DIR, MAZE / "out" / "m3_clean",
                          cfg, cwd=MAZE)
    rc = fc.run_until(detector.is_plateau, timeout=80, poll=3.0)
    snap = rc.snapshot
    print(f"    {detector.explain(snap)}")
    if not detector.is_plateau(snap) or snap.solved:
        print("    [FAIL] did not reach a clean plateau; aborting")
        return 1

    # 2. Ask DeepSeek (the analyst) for an annotation.
    banner("2) ANALYST (DeepSeek) proposes an annotation")
    model = AnalystModel()
    print(f"    model: {model.model}")
    proposal = propose_annotation(model, clean, snap, source_name="maze.c")
    print(f"    why_stuck      : {proposal.why_stuck}")
    print(f"    failure_class  : {proposal.failure_class}")
    print(f"    relevant_state : {proposal.relevant_state}")
    print(f"    macro          : {proposal.macro}")
    print(f"    code           : {proposal.annotation.code}")
    print(f"    after_substring: {proposal.annotation.after_substring!r}")
    print(f"    placement      : {proposal.placement_reason}")
    print(f"    [served by {proposal.llm.model}, "
          f"{proposal.llm.prompt_tokens}+{proposal.llm.completion_tokens} tok, "
          f"{proposal.llm.latency_s:.1f}s]")

    # 3. Apply + rebuild with IJON.
    banner("3) PATCH + REBUILD with the model's annotation")
    try:
        patched = apply_annotation(clean, proposal.annotation)
    except ValueError as e:
        print(f"    [FAIL] could not place annotation: {e}")
        return 1
    SRC_LLM.write_text(patched)
    cp = builder.compile(SRC_LLM, MAZE / "targets" / "maze_llm", ijon=True)
    print(f"    build ok={cp.ok} header_included={cp.header_included} "
          f"header_missing={cp.header_missing}")
    if not cp.ok:
        print("    [FAIL] patched target did not compile:")
        print("    " + "\n    ".join(cp.stdout.splitlines()[-8:]))
        return 1

    # 4. Re-fuzz; did the model's annotation solve the maze?
    banner("4) RE-FUZZ — did the annotation solve the maze?")
    fp = FuzzerController(cp.binary, INPUT_DIR, MAZE / "out" / "m3_llm",
                          cfg, cwd=MAZE, stop_on_crash=True)
    rp = fp.run_until(lambda s: s.solved, timeout=90, poll=2.0)
    snap2 = rp.snapshot
    print(f"    {detector.explain(snap2)} saved_crashes={snap2.saved_crashes}")

    banner("VERDICT")
    solved = bool(snap2 and snap2.solved)
    print(f"    [{'PASS' if solved else 'FAIL'}] DeepSeek's annotation "
          f"{'SOLVED the hard maze' if solved else 'did NOT solve the maze'}")
    return 0 if solved else 1


if __name__ == "__main__":
    raise SystemExit(main())
