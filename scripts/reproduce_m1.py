#!/usr/bin/env python3
"""M2 acceptance test: reproduce the M1 maze A/B *programmatically*.

  A) clean maze (no annotation)         -> harness must report PLATEAU + UNSOLVED
  B) clean maze + IJON_SET annotation   -> harness must report SOLVED

If both hold, the deterministic rails (build/patch/run/detect/evaluate) work
and are ready for the LLM to drive in M3. No model is involved here.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness import (AflConfig, Builder, Annotation, FuzzerController,
                     PlateauDetector, apply_annotation, strip_ijon_blocks)

MAZE = REPO / "workspace" / "maze"
SRC_ORIG = MAZE / "src" / "ijon-maze.c"
SRC_CLEAN = MAZE / "src" / "maze_clean.c"
SRC_PATCHED = MAZE / "src" / "maze_patched.c"
INPUT_DIR = MAZE / "in"

# The annotation a human analyst (and, in M3, the LLM) would add: expose the
# (row,col) game state to the fuzzer's feedback function.
GROUND_TRUTH = Annotation(
    code="IJON_SET(ijon_hashint(row, col));",
    after_substring="Location: move=",
)


def banner(msg: str) -> None:
    print(f"\n{'='*70}\n{msg}\n{'='*70}")


def main() -> int:
    cfg = AflConfig()
    cfg.check()
    builder = Builder(cfg)
    detector = PlateauDetector(min_stall_seconds=30)

    # 0. Produce the clean (unannotated) source the LLM will later see.
    clean = strip_ijon_blocks(SRC_ORIG.read_text())
    SRC_CLEAN.write_text(clean)
    assert "IJON_SET" not in clean, "strip failed: annotation still present"
    print(f"[0] wrote clean source -> {SRC_CLEAN.name} (IJON_SET stripped)")

    results = {}

    # --- A) clean target should plateau and never solve ---
    banner("A) CLEAN maze (no annotation): expect PLATEAU + UNSOLVED")
    ca = builder.compile(SRC_CLEAN, MAZE / "targets" / "maze_clean", ijon=False)
    print(f"    build ok={ca.ok}")
    assert ca.ok, ca.stdout
    fa = FuzzerController(ca.binary, INPUT_DIR, MAZE / "out" / "m2_clean",
                          cfg, cwd=MAZE, stop_on_crash=False)
    ra = fa.run_until(detector.is_plateau, timeout=80, poll=3.0)
    snap_a = ra.snapshot
    print(f"    stop reason={ra.reason}; {detector.explain(snap_a)}")
    results["clean_plateau"] = detector.is_plateau(snap_a)
    results["clean_solved"] = snap_a.solved

    # --- B) patched target should solve ---
    banner("B) CLEAN + IJON_SET annotation: expect SOLVED")
    patched = apply_annotation(clean, GROUND_TRUTH)
    SRC_PATCHED.write_text(patched)
    print(f"    inserted: {GROUND_TRUTH.code!r} after line containing "
          f"{GROUND_TRUTH.after_substring!r}")
    cb = builder.compile(SRC_PATCHED, MAZE / "targets" / "maze_patched", ijon=True)
    print(f"    build ok={cb.ok} header_included={cb.header_included} "
          f"header_missing={cb.header_missing}")
    assert cb.ok, cb.stdout
    assert cb.header_included and not cb.header_missing, "IJON header not wired"
    fb = FuzzerController(cb.binary, INPUT_DIR, MAZE / "out" / "m2_patched",
                          cfg, cwd=MAZE, stop_on_crash=True)
    rb = fb.run_until(lambda s: s.solved, timeout=60, poll=2.0)
    snap_b = rb.snapshot
    print(f"    stop reason={rb.reason}; {detector.explain(snap_b)} "
          f"saved_crashes={snap_b.saved_crashes}")
    results["patched_solved"] = snap_b.solved

    # --- verdict ---
    banner("VERDICT")
    checks = {
        "clean target plateaus":      results["clean_plateau"] is True,
        "clean target NOT solved":    results["clean_solved"] is False,
        "patched target SOLVED":      results["patched_solved"] is True,
    }
    for name, ok in checks.items():
        print(f"    [{'PASS' if ok else 'FAIL'}] {name}")
    all_ok = all(checks.values())
    print(f"\n    M2 {'PASS' if all_ok else 'FAIL'}: deterministic rails "
          f"{'work end-to-end' if all_ok else 'have a problem'}.")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
