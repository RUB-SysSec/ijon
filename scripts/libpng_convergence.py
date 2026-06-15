#!/usr/bin/env python3
"""Thesis demo on a REAL target (libpng): does the autonomous IJON loop unlock
state exploration that plain AFL cannot, and does it do so over time?

This is the IJON paper's own evaluation shape (coverage/diversity over time,
AFL vs AFL+IJON), on the class-2 metric the paper uses for stateful targets:
distinct chunk-type SEQUENCES explored (cf. paper Table IV).

Two parts:
  (A) AUTONOMOUS PROPOSAL — the analyst model, given only the localized read-path
      source + plateau telemetry (no answer), proposes one annotation. We record
      whether it independently arrives at the chunk-SEQUENCE state annotation
      (the same idiom the IJON authors used for TPM commands). Closes "the agent
      applies class-2 autonomously", not just diagnoses it.
  (B) TIME-SERIES A/B — plain AFL vs AFL+IJON(chunk-seq annotation), both
      CRC-disabled so the ONLY variable is chunk sequencing. We sample the queue
      every N seconds and plot distinct chunk-sequences over wall-clock.

Honest scope (see docs/architecture-design.md 7b): libpng's *deeper function*
coverage is format-gated, not IJON-unlockable, so the clear win is on the class-2
sequence metric, not raw function count. We report both.

Control arm reuses targets/libpng_crc_off (plain AFL, CRC disabled, pristine
libpng — built by build.sh). Treatment is rebuilt here from chunk_seq_log.patch.
Reproduce: python scripts/libpng_convergence.py --budget 360
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
from harness.fuzzer import Snapshot, parse_fuzzer_stats
from harness.agent import propose_annotation
from harness.model import AnalystModel
from harness.localize import load_fi, load_cov, build_localization_context
from harness.coverage import CoverageProbe
from chunk_seq_diversity import analyse           # the class-2 metric

AFL = Path(os.environ.get("AFL_ROOT", "/opt/AFLplusplus"))
LLVM = Path(os.environ.get("LLVM_BIN", "/usr/lib/llvm/bin"))
WS = REPO / "workspace" / "libpng"
LP = WS / "build" / "libpng"
ZINST = WS / "build" / "zlib" / "install"
CRCOFF_CC = WS / "build" / "libpng_crcoff_fuzzer.cc"   # CRC-disabled harness (build.sh)
PATCH = WS / "patches" / "chunk_seq_log.patch"
OUT = REPO / "experiments" / "libpng_convergence"
os.environ.setdefault("TMPDIR", "/tmp")


def sh(cmd, **kw):
    env = dict(os.environ)
    env["PATH"] = f"{AFL}:{env.get('PATH','')}"
    env["AFL_PATH"] = str(AFL / "include")
    env.update(kw.pop("env", {}))
    return subprocess.run(cmd, env=env, shell=isinstance(cmd, str),
                          capture_output=True, text=True, **kw)


# ---------------------------------------------------------------- part A
def autonomous_proposal(model: AnalystModel) -> dict:
    """Blind: localize the read path, ask the model for one annotation."""
    fi = load_fi(WS / "fi_out" / "fuzzerLogFile-libpng_crc_fuzzer.data.yaml")
    cov = load_cov(WS / "build" / "cov" / "coverage.json")
    hint, src = build_localization_context(fi, cov)
    focused = "\n\n".join(src.values())
    snap = Snapshot({"execs_done": 5_000_000, "edges_found": 980,
                     "total_edges": 65536, "corpus_count": 463,
                     "time_wo_finds": 3600, "pending_favs": 0,
                     "saved_crashes": 0}, OUT)
    prop = propose_annotation(model, focused, snap,
                              source_name="libpng read path (coverage frontier)",
                              localization=hint)
    # the chunk-SEQUENCE idiom = accumulating the ORDER of chunk types over the
    # read loop. Matching a single chunk name (IJON_CMP(chunk_name, png_iCCP)) is
    # a class-3 magic-value annotation, NOT the sequence -- don't count it.
    blob = (prop.relevant_state + " " + prop.annotation.code + " " +
            prop.why_stuck).lower()
    seqish = (any(k in blob for k in ("sequence", "chunk_seq", "rolling",
                                      "order of", "ordered", "history of",
                                      "previous chunk", "chain of chunks"))
              and "ijon_cmp" not in prop.annotation.code.lower())
    return {
        "failure_class": prop.failure_class,
        "relevant_state": prop.relevant_state,
        "macro": prop.macro,
        "annotation_code": prop.annotation.code,
        "after_substring": prop.annotation.after_substring,
        "why_stuck": prop.why_stuck,
        "looks_like_sequence_state": seqish,
    }


# ---------------------------------------------------------------- part B
def build_treatment() -> Path:
    """CRC-off harness + libpng patched with the chunk-seq annotation + IJON."""
    # apply the annotation patch (idempotent: skip if already in tree)
    if "chunk_seq_log" not in (LP / "pngread.c").read_text(errors="replace"):
        r = sh(["git", "apply", str(PATCH)], cwd=str(LP))
        if r.returncode != 0:
            print("git apply failed:", r.stderr); return None
    # the tree may carry stale ASAN-instrumented objects from the bug-hunt
    # session (they leak __asan_report_* symbols into libpng16.a). Clean-rebuild
    # ONLY the static library (no test programs) with afl-clang-fast + IJON.
    sh(["make", "clean"], cwd=str(LP))
    r = sh(["make", "-j4", "libpng16.la"], cwd=str(LP),
           env={"AFL_LLVM_IJON": "1", "AFL_QUIET": "1"})
    if r.returncode != 0:
        print("libpng IJON make failed:", r.stderr[-800:]); return None
    out = WS / "targets" / "libpng_conv_ijon"
    r = sh([str(AFL / "afl-clang-fast++"), "-g", "-O2", "-fsanitize=fuzzer",
            f"-I{LP}", f"-I{ZINST}/include", str(CRCOFF_CC),
            str(LP / ".libs" / "libpng16.a"), str(ZINST / "lib" / "libz.a"),
            "-o", str(out)],
           env={"AFL_LLVM_IJON": "1", "AFL_QUIET": "1"})
    if r.returncode != 0:
        print("treatment link failed:", r.stderr[-800:]); return None
    return out


def run_with_sampling(target: Path, tag: str, budget: float,
                      sample_every: float) -> list:
    """Fuzz from the single seed; sample chunk-sequence diversity over time."""
    cfg = AflConfig()
    out_dir = WS / "out" / tag
    if out_dir.exists():
        sh(["rm", "-rf", str(out_dir)])
    fc = FuzzerController(target, WS / "in_single", out_dir, cfg, cwd=WS,
                          stop_on_crash=False)
    queue = out_dir / "default" / "queue"
    traj = []
    fc.start()
    t0 = time.time()
    try:
        while time.time() - t0 < budget:
            time.sleep(sample_every)
            if not queue.exists():
                continue
            a = analyse(queue)
            snap = fc.snapshot()
            traj.append({
                "t": round(time.time() - t0, 1),
                "files": a["files"], "parsed": a["parsed"],
                "sequences": a["sequences"], "trigrams": a["trigrams"],
                "types": a["types"],
                "edges_found": snap.edges_found if snap else None,
            })
            last = traj[-1]
            print(f"  [{tag} t={last['t']:>5}s] corpus={last['files']:>4} "
                  f"seqs={last['sequences']:>4} trigrams={last['trigrams']:>5} "
                  f"alpha={last['types']:>2} edges={last['edges_found']}")
    finally:
        fc.stop()
    return traj


def final_function_coverage(tag: str) -> int:
    """Replay an arm's final queue through the fixed llvm-cov build."""
    probe = CoverageProbe(WS / "targets" / "libpng_crc_cov", LLVM,
                          Path(os.environ["TMPDIR"]) / f"conv_cov_{tag}")
    q = WS / "out" / tag / "default" / "queue"
    if not q.exists():
        return -1
    return probe.measure(q, tag=tag).n_functions


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, default=360, help="seconds per arm")
    ap.add_argument("--sample-every", type=float, default=20)
    ap.add_argument("--skip-build", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    print("=== PART A: autonomous proposal (blind) ===")
    model = AnalystModel()
    propA = autonomous_proposal(model)
    print(f"  model={model.model}")
    print(f"  [{propA['failure_class']}] {propA['macro']} on {propA['relevant_state']!r}")
    print(f"  code: {propA['annotation_code']}")
    print(f"  -> looks like chunk-SEQUENCE state? {propA['looks_like_sequence_state']}")

    print("\n=== PART B: time-series A/B (plain AFL vs AFL+IJON, CRC-off) ===")
    treatment = WS / "targets" / "libpng_conv_ijon"
    if not args.skip_build:
        print("building treatment (patched libpng + IJON + CRC-off harness) ...")
        treatment = build_treatment()
        if treatment is None:
            print("BUILD FAILED — aborting part B"); return 1
        # leave the build tree clean for the next run
        sh(["git", "checkout", "pngread.c"], cwd=str(LP))
    control = WS / "targets" / "libpng_crc_off"   # plain AFL, CRC-off (build.sh)

    print(f"\n-- arm: plain (control) {control.name} --")
    plain = run_with_sampling(control, "conv_plain", args.budget, args.sample_every)
    print(f"\n-- arm: ijon (treatment) {treatment.name} --")
    ijon = run_with_sampling(treatment, "conv_ijon", args.budget, args.sample_every)

    print("\nmeasuring final source-function coverage (both arms) ...")
    cov_plain = final_function_coverage("conv_plain")
    cov_ijon = final_function_coverage("conv_ijon")

    result = {
        "model": model.model,
        "autonomous_proposal": propA,
        "budget_s": args.budget,
        "trajectory": {"plain": plain, "ijon": ijon},
        "final": {
            "plain": {"sequences": plain[-1]["sequences"] if plain else 0,
                      "trigrams": plain[-1]["trigrams"] if plain else 0,
                      "functions": cov_plain},
            "ijon": {"sequences": ijon[-1]["sequences"] if ijon else 0,
                     "trigrams": ijon[-1]["trigrams"] if ijon else 0,
                     "functions": cov_ijon},
        },
    }
    (OUT / "result.json").write_text(json.dumps(result, indent=2))
    f = result["final"]
    sx = (f["ijon"]["sequences"] / f["plain"]["sequences"]) if f["plain"]["sequences"] else 0
    print("\n================= SUMMARY =================")
    print(f"  autonomous proposal looked like sequence-state: "
          f"{propA['looks_like_sequence_state']}")
    print(f"  distinct chunk-sequences  plain={f['plain']['sequences']:>4}  "
          f"ijon={f['ijon']['sequences']:>4}  ({sx:.1f}x)")
    print(f"  distinct 3-chunk windows  plain={f['plain']['trigrams']:>4}  "
          f"ijon={f['ijon']['trigrams']:>4}")
    print(f"  source functions covered  plain={f['plain']['functions']:>4}  "
          f"ijon={f['ijon']['functions']:>4}  (format-gated; expect ~equal)")
    print(f"  -> {OUT.relative_to(REPO)}/result.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
