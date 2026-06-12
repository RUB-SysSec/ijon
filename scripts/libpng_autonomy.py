#!/usr/bin/env python3
"""Autonomy closure for class 2 on libpng (real target): can the loop, over
iterations with a CLASS-MATCHED reward, escalate from its local one-shot picks to
a state-SEQUENCE annotation that unlocks state exploration?

Key design point (see experiments/libpng_convergence/REPORT.md): the class-2
keep/revert reward is STATE DIVERSITY, not function coverage. A function-coverage
loop would wrongly revert the best class-2 annotation (it adds 0 functions but 41x
state-sequences). Here the reward = distinct chunk-type SEQUENCES explored (the
paper's class-2 metric). The history feedback to the model stays GENERAL ("expose
a state that changes as the program processes input"), the class-2 guidance the
agent already has -- it never names the chunk-sequence answer. So a KEEP means the
agent reasoned its own way to a sequence annotation.

Each iteration is independent (pristine base + one candidate annotation) so we
measure whether the agent FINDS the unlocking annotation, not annotation stacking.
Reproduce: python scripts/libpng_autonomy.py --iters 4 --window 90
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from harness import AflConfig, FuzzerController
from harness.fuzzer import Snapshot
from harness.build import apply_annotation
from harness.agent import propose_annotation
from harness.model import AnalystModel
from harness.localize import load_fi, load_cov, build_localization_context
from chunk_seq_diversity import analyse

AFL = Path("/home/sanjay/san-home/research/repos/AFLplusplus")
WS = REPO / "workspace" / "libpng"
LP = WS / "build" / "libpng"
ZINST = WS / "build" / "zlib" / "install"
CRCOFF_CC = WS / "build" / "libpng_crcoff_fuzzer.cc"
OUT = REPO / "experiments" / "libpng_autonomy"
KEEP_FACTOR = 5.0   # diversity must beat plain baseline by >=5x to count as "unlocked"
os.environ.setdefault("TMPDIR", "/home/sanjay/san-home/tmp")


def sh(cmd, **kw):
    env = dict(os.environ)
    env["PATH"] = f"{AFL}:{env.get('PATH','')}"
    env["AFL_PATH"] = str(AFL / "include")
    env.update(kw.pop("env", {}))
    return subprocess.run(cmd, env=env, shell=isinstance(cmd, str),
                          capture_output=True, text=True, **kw)


def _norm(s: str) -> str:
    return " ".join(s.split())


def find_target_file(anchor: str):
    """libpng .c file (pristine on disk) holding `anchor`. Returns
    (file, content, exact_anchor) where exact_anchor is guaranteed to occur
    verbatim in the file (so apply_annotation's exact match succeeds). Tries an
    exact match, then a whitespace-normalized match against each line (the model
    often paraphrases the indentation/spacing of a line it saw in a slice)."""
    files = sorted(LP.glob("*.c"))
    for c in files:                                   # exact
        txt = c.read_text(errors="replace")
        if anchor in txt:
            return c, txt, anchor
    na = _norm(anchor)                                # whitespace-normalized
    for c in files:
        txt = c.read_text(errors="replace")
        for line in txt.splitlines():
            if na and na in _norm(line):
                return c, txt, line.strip()           # the real file line
    return None, None, None


def build_annotated(prop, prev_file) -> tuple:
    """Revert any previously-annotated file to pristine, apply this candidate,
    rebuild libpng16.la (IJON) + relink CRC-off harness. Returns (target, file, err)."""
    if prev_file is not None:
        sh(["git", "checkout", prev_file.name], cwd=str(LP))
        sh(["touch", prev_file.name], cwd=str(LP))      # force pristine recompile
    tgt_file, content, exact = find_target_file(prop.annotation.after_substring)
    if tgt_file is None:
        return None, None, "anchor not found in any libpng source file"
    if "".join(prop.annotation.code.split()) in "".join(content.split()):
        return None, None, "annotation already present"
    ann = prop.annotation
    if exact != ann.after_substring:                  # use the real file line
        from harness.build import Annotation
        ann = Annotation(code=ann.code, after_substring=exact)
    try:
        tgt_file.write_text(apply_annotation(content, ann))
    except ValueError as e:
        return None, tgt_file, f"could not place annotation: {e}"
    sh(["touch", tgt_file.name], cwd=str(LP))
    r = sh(["make", "-j4", "libpng16.la"], cwd=str(LP),
           env={"AFL_LLVM_IJON": "1", "AFL_QUIET": "1"})
    if r.returncode != 0:
        sh(["git", "checkout", tgt_file.name], cwd=str(LP))
        return None, tgt_file, "build failed: " + r.stderr.strip().splitlines()[-1][:160]
    out = WS / "targets" / "libpng_auto_ijon"
    r = sh([str(AFL / "afl-clang-fast++"), "-g", "-O2", "-fsanitize=fuzzer",
            f"-I{LP}", f"-I{ZINST}/include", str(CRCOFF_CC),
            str(LP / ".libs" / "libpng16.a"), str(ZINST / "lib" / "libz.a"),
            "-o", str(out)],
           env={"AFL_LLVM_IJON": "1", "AFL_QUIET": "1"})
    if r.returncode != 0:
        return None, tgt_file, "link failed"
    return out, tgt_file, None


def fuzz_and_diversity(target: Path, tag: str, window: float) -> int:
    out_dir = WS / "out" / tag
    if out_dir.exists():
        sh(["rm", "-rf", str(out_dir)])
    fc = FuzzerController(target, WS / "in_single", out_dir, AflConfig(),
                          cwd=WS, stop_on_crash=False)
    queue = out_dir / "default" / "queue"
    fc.start()
    t0 = time.time()
    try:
        while time.time() - t0 < window:
            time.sleep(5)
    finally:
        fc.stop()
    return analyse(queue)["sequences"] if queue.exists() else 0


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--iters", type=int, default=4)
    ap.add_argument("--window", type=float, default=90)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    model = AnalystModel()
    fi = load_fi(WS / "fi_out" / "fuzzerLogFile-libpng_crc_fuzzer.data.yaml")
    cov = load_cov(WS / "build" / "cov" / "coverage.json")
    hint, src = build_localization_context(fi, cov)
    focused = "\n\n".join(src.values())
    snap = Snapshot({"execs_done": 5_000_000, "edges_found": 1121,
                     "total_edges": 65536, "corpus_count": 800,
                     "time_wo_finds": 3600, "pending_favs": 0,
                     "saved_crashes": 0}, OUT)

    print(f"[baseline] plain AFL, {args.window}s, distinct chunk-sequences ...")
    base_seqs = fuzz_and_diversity(WS / "targets" / "libpng_crc_off",
                                   "auto_baseline", args.window)
    keep_threshold = max(int(base_seqs * KEEP_FACTOR), base_seqs + 200)
    print(f"[baseline] plain={base_seqs} sequences; KEEP threshold={keep_threshold}")

    history, log = [], []
    prev_file = None
    found = None
    for it in range(1, args.iters + 1):
        print(f"\n[iter {it}] proposing ({len(history)} prior failed) ...")
        prop = propose_annotation(model, focused, snap,
                                  source_name="libpng read path (coverage frontier)",
                                  history=history, localization=hint)
        print(f"    [{prop.failure_class}] {prop.macro}: {prop.annotation.code}")
        target, tgt_file, err = build_annotated(prop, prev_file)
        prev_file = tgt_file or prev_file
        if err:
            print(f"    [skip] {err}")
            history.append((prop, f"could not evaluate: {err}"))
            log.append({"iter": it, "code": prop.annotation.code,
                        "class": prop.failure_class, "seqs": None, "kept": False,
                        "note": err})
            continue
        seqs = fuzz_and_diversity(target, f"auto_iter{it}", args.window)
        kept = seqs >= keep_threshold
        print(f"    diversity={seqs} sequences  (baseline {base_seqs}, "
              f"threshold {keep_threshold})  -> {'KEEP' if kept else 'REVERT'}")
        log.append({"iter": it, "code": prop.annotation.code,
                    "class": prop.failure_class, "relevant_state": prop.relevant_state,
                    "seqs": seqs, "kept": bool(kept)})
        if kept:
            found = {"iter": it, "code": prop.annotation.code, "seqs": seqs,
                     "relevant_state": prop.relevant_state,
                     "fold_over_baseline": round(seqs / max(base_seqs, 1), 1)}
            break
        # feedback -- NEVER names the chunk-sequence answer. If the agent already
        # reached the right CLASS (IJON_STATE) but the state was too COARSE, push
        # on granularity; otherwise give the general class-2 redirection.
        got_state = "ijon_state" in prop.annotation.code.lower() or \
                    prop.failure_class == "known_state_changes"
        if got_state and seqs > base_seqs:
            note = (
                f"right idea (a state annotation), but the state you exposed is too "
                f"COARSE: it took only ~{seqs} distinct values across the whole "
                f"campaign ({keep_threshold}+ needed). A flag/mode field has too few "
                f"values. Pick a state variable that changes at EVERY processing "
                f"step and can take MANY more distinct values as the program advances "
                f"through its input -- and combine it with its own running history so "
                f"distinct processing ORDERS map to distinct state values.")
        else:
            note = (
                f"did NOT increase state diversity (only {seqs} distinct execution "
                f"state-trajectories vs a {keep_threshold}+ target; baseline "
                f"{base_seqs}). The fuzzer keeps revisiting the same states. This is "
                f"a class-2 'known state CHANGES' plateau: expose a STATE that "
                f"ACCUMULATES as the program processes successive parts of its input, "
                f"so different processing orders get different feedback -- not a "
                f"single value or a one-shot comparison.")
        history.append((prop, note))

    # leave the tree pristine
    if prev_file is not None:
        sh(["git", "checkout", prev_file.name], cwd=str(LP))
    result = {"model": model.model, "baseline_sequences": base_seqs,
              "keep_threshold": keep_threshold, "window_s": args.window,
              "iterations": log, "found": found}
    (OUT / "result.json").write_text(json.dumps(result, indent=2))

    print("\n================= SUMMARY =================")
    for e in log:
        s = e["seqs"] if e["seqs"] is not None else "n/a"
        print(f"  iter {e['iter']}: [{e['class']}] {e['code'][:54]:<54} "
              f"seqs={s} {'KEEP' if e['kept'] else 'revert'}")
    if found:
        print(f"\n  CLOSED: by iteration {found['iter']} the agent reached "
              f"`{found['code']}`")
        print(f"  -> {found['seqs']} distinct sequences = "
              f"{found['fold_over_baseline']}x baseline (autonomous, "
              f"diversity-reward keep)")
    else:
        print("\n  NOT closed in this budget: agent stayed on local annotations; "
              "report honestly (may need stronger localization or more iters).")
    print(f"  -> {OUT.relative_to(REPO)}/result.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
