#!/usr/bin/env python3
"""Run the autonomous analyst loop against a target until it solves or the
iteration budget runs out. Accumulates kept annotations; reverts + retries
(with feedback) ones that don't move coverage.

  python3 scripts/autonomous.py --workspace workspace/checksum \
      --src checksum-guard.c --max-iters 4
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness import AflConfig, AnalystLoop, TargetSpec
from harness.model import AnalystModel


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--src", required=True)
    ap.add_argument("--name", default=None)
    ap.add_argument("--max-iters", type=int, default=4)
    ap.add_argument("--plateau-timeout", type=float, default=95)
    ap.add_argument("--eval-timeout", type=float, default=90)
    ap.add_argument("--model", default=None, help="override IJON_LLM_MODEL")
    args = ap.parse_args()

    cfg = AflConfig(); cfg.check()
    spec = TargetSpec(workspace=REPO / args.workspace, src=args.src, name=args.name)
    model = AnalystModel(model=args.model) if args.model else AnalystModel()
    loop = AnalystLoop(cfg, spec, model=model, max_iters=args.max_iters,
                       plateau_timeout=args.plateau_timeout,
                       eval_timeout=args.eval_timeout)

    print(f"=== autonomous loop on {spec.name} (model={model.model}, "
          f"max_iters={args.max_iters}) ===")
    res = loop.run()

    print("\n" + "=" * 72)
    print("SUMMARY")
    print("=" * 72)
    for a in res.attempts:
        print(f"  iter {a.iteration}: [{a.outcome:8s}] {a.proposal.macro:11s} "
              f"edges {a.before_edges}->{a.after_edges}  ({a.note})")
    print(f"\n  kept annotations ({len(res.kept)}):")
    for ann in res.kept:
        print(f"    + {ann.code}   after {ann.after_substring!r}")
    print(f"\n  [{'SOLVED' if res.solved else 'NOT SOLVED'}] in "
          f"{res.iterations} iteration(s)")
    return 0 if res.solved else 1


if __name__ == "__main__":
    raise SystemExit(main())
