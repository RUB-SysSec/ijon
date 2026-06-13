#!/usr/bin/env python3
"""Mario Part A — autonomous annotation (blind), on the IJON paper's own game demo.

Strip the authors' annotation (`ijon_max(pos_y/16, world_pos)`) from the game
loop, hand the model the cleaned loop + plateau telemetry (plain AFL stuck near
the level start), and record what it proposes. This is the paper's iconic
benchmark turned into a class-1 IJON_MAX human-vs-LLM datapoint.

The model never sees the answer: make_clean_source() strips the #ifdef block and
any 'ijon' line and asserts none survives.
Reproduce: python scripts/mario_annotation.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness.build import make_clean_source
from harness.fuzzer import Snapshot
from harness.model import AnalystModel
from harness.agent import propose_annotation

WS = REPO / "workspace" / "mario"
MAIN = WS / "build" / "src" / "Main.cpp"
OUT = REPO / "experiments" / "mario"

GROUND_TRUTH = {
    "primitive": "ijon_max (IJON_MAX)",
    "state": "world_pos (horizontal level progress = screen*255+pos), bucketed by "
             "pos_y/16 (vertical tile row)",
    "placement": "in mainLoop, each frame after world_pos/pos_y are computed",
    "verbatim": "ijon_max(pos_y/16, world_pos);   // Main.cpp (#ifdef _USE_IJON)",
}


def extract_mainloop(text: str) -> str:
    """The mainLoop function: where the fuzzer 'plays' and world_pos is computed."""
    lines = text.splitlines()
    start = next(i for i, l in enumerate(lines) if "static void mainLoop(" in l)
    # to the closing of mainLoop (next line that is just '}' at column 0 after start)
    end = next(i for i in range(start + 1, len(lines))
               if lines[i].rstrip() == "}")
    return "\n".join(lines[start:end + 1])


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    loop = extract_mainloop(MAIN.read_text(errors="replace"))
    clean = make_clean_source(loop)
    assert "ijon" not in clean.lower()
    assert "ijon_max" not in clean.lower()

    snap = Snapshot({"execs_done": 20_000_000, "edges_found": 1500,
                     "total_edges": 65536, "corpus_count": 300,
                     "time_wo_finds": 3600, "pending_favs": 0,
                     "saved_crashes": 0}, OUT)
    model = AnalystModel()
    prop = propose_annotation(
        model, clean, snap,
        source_name="game main loop (the fuzzer drives the controller via stdin)")

    # success heuristic: did it pick IJON_MAX on the horizontal-progress state?
    code = prop.annotation.code.lower()
    st = (prop.relevant_state + " " + prop.annotation.code).lower()
    is_max = "ijon_max" in code or prop.macro.upper().startswith("IJON_MAX")
    on_progress = any(k in st for k in ("world_pos", "world pos", "screen", "pos",
                                        "progress", "x position", "horizontal",
                                        "distance", "rightmost", "furthest"))
    rec = {
        "ground_truth": GROUND_TRUTH,
        "llm_proposal": {
            "failure_class": prop.failure_class,
            "why_stuck": prop.why_stuck,
            "relevant_state": prop.relevant_state,
            "macro": prop.macro,
            "annotation_code": prop.annotation.code,
            "after_substring": prop.annotation.after_substring,
            "placement_reason": prop.placement_reason,
        },
        "match": {"used_ijon_max": is_max, "on_progress_state": on_progress,
                  "reproduces_human": bool(is_max and on_progress)},
        "model": prop.llm.model,
        "clean_loop_lines": len(clean.splitlines()),
    }
    (OUT / "annotation.json").write_text(json.dumps(rec, indent=2))

    print("=== Mario Part A: autonomous annotation (blind) ===")
    print(f"  model={prop.llm.model}; model saw {rec['clean_loop_lines']} clean loop lines")
    print(f"  HUMAN : {GROUND_TRUTH['primitive']} | {GROUND_TRUTH['state']}")
    print(f"  LLM   : [{prop.failure_class}] {prop.macro} | state={prop.relevant_state}")
    print(f"          code: {prop.annotation.code}")
    print(f"          after: {prop.annotation.after_substring!r}")
    print(f"  -> used IJON_MAX={is_max}, on progress-state={on_progress}, "
          f"reproduces human={rec['match']['reproduces_human']}")
    print(f"  -> {(OUT/'annotation.json').relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
