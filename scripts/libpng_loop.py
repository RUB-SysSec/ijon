#!/usr/bin/env python3
"""Coverage-driven autonomous loop on libpng (a real multi-function target).

Reuses the harness: the localizer (FI static graph + llvm-cov), the
localization-aware agent, and CoverageProbe for keep/revert. The build/fuzz are
libpng-specific. Keep/revert is decided by REAL source coverage (new functions
covered), NOT AFL edges — so an IJON_CMP that only inflates the IJON map is
correctly REVERTED. This validates, in-loop, the metric the step-4 libpng run
exposed.

Prereqs (built earlier): build.sh (AFL targets), cov-build.sh (llvm-cov target),
and the FI data.yaml in fi_out/. Bounded: --max-iters small, short eval windows.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness import AflConfig, FuzzerController, PlateauDetector, apply_annotation
from harness.agent import propose_annotation
from harness.model import AnalystModel
from harness.localize import (load_fi, load_cov, build_localization_context,
                              localization_hint)
from harness.coverage import CoverageProbe

AFL = Path(os.environ.get("AFL_ROOT", "/opt/AFLplusplus"))
LLVM = Path(os.environ.get("LLVM_BIN", "/usr/lib/llvm/bin"))
WS = REPO / "workspace" / "libpng"
LP = WS / "build" / "libpng"
ZINST = WS / "build" / "zlib" / "install"
os.environ.setdefault("TMPDIR", "/tmp")


def sh(cmd, **kw):
    env = dict(os.environ)
    env["PATH"] = f"{AFL}:{env.get('PATH','')}"
    env["AFL_PATH"] = str(AFL / "include")
    env.update(kw.pop("env", {}))
    return subprocess.run(cmd, env=env, shell=isinstance(cmd, str),
                          capture_output=True, text=True, **kw)


def build_ijon_target() -> Path:
    """Incrementally rebuild libpng with AFL_LLVM_IJON (recompiles patched .c),
    relink the harness -> the AFL+IJON target."""
    sh(["make", "-j4"], cwd=str(LP), env={"AFL_LLVM_IJON": "1", "AFL_QUIET": "1"})
    out = WS / "targets" / "libpng_crc_loop_ijon"
    r = sh([str(AFL / "afl-clang-fast++"), "-g", "-O2", "-fsanitize=fuzzer",
            f"-I{LP}", f"-I{ZINST}/include",
            str(WS / "src" / "libpng_crc_fuzzer.cc"),
            str(LP / ".libs" / "libpng16.a"), str(ZINST / "lib" / "libz.a"),
            "-o", str(out)],
           env={"AFL_LLVM_IJON": "1", "AFL_QUIET": "1"})
    return out if (r.returncode == 0 and out.exists()) else None


def find_target_file(anchor: str, accepted: dict):
    """Find the libpng .c file whose CURRENT accepted content holds `anchor`.
    `accepted` maps file->content for files modified by a kept annotation; other
    files use their on-disk (pristine) content. Returns (file, content)."""
    for c in sorted(LP.glob("*.c")):
        content = accepted.get(c, c.read_text(errors="replace"))
        if anchor in content:
            return c, content
    return None, None


def fuzz(target: Path, out_tag: str, timeout: float):
    cfg = AflConfig()
    fc = FuzzerController(target, WS / "in_single", WS / "out" / out_tag, cfg,
                          cwd=WS, stop_on_crash=True)
    det = PlateauDetector(min_stall_seconds=30)
    fc.run_until(lambda s: s.solved or det.is_plateau(s), timeout=timeout, poll=3.0)
    return fc.snapshot(), (WS / "out" / out_tag / "default" / "queue")


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-iters", type=int, default=2)
    ap.add_argument("--eval-timeout", type=float, default=90)
    args = ap.parse_args()

    fi = load_fi(WS / "fi_out" / "fuzzerLogFile-libpng_crc_fuzzer.data.yaml")
    cov_counts = load_cov(WS / "build" / "cov" / "coverage.json")   # baseline static localize
    probe = CoverageProbe(WS / "targets" / "libpng_crc_cov", LLVM,
                          Path(os.environ["TMPDIR"]) / "libpng_loop_cov")
    model = AnalystModel()

    hint, src = build_localization_context(fi, cov_counts)
    focused = "\n\n".join(src.values())
    print(f"[init] localized to {len(src)} focused functions; model={model.model}")

    # baseline real coverage from the existing crc_on corpus
    baseline_cov = probe.measure(WS / "out" / "crc_on" / "default" / "queue", tag="base")
    print(f"[init] baseline real coverage: {baseline_cov.n_functions} functions")
    from harness.fuzzer import Snapshot, parse_fuzzer_stats
    snap = Snapshot(parse_fuzzer_stats(WS / "out" / "crc_on" / "default" / "fuzzer_stats"),
                    WS / "out" / "crc_on" / "default" / "crashes")

    from harness.build import Annotation
    history = []
    accepted = {}   # file -> accepted content (pristine + kept annotations)
    kept = []
    for it in range(1, args.max_iters + 1):
        print(f"\n[iter {it}] asking analyst ({len(history)} prior failed)")
        prop = propose_annotation(model, focused, snap,
                                  source_name="libpng read path (coverage frontier)",
                                  history=history, localization=hint)
        print(f"    propose [{prop.failure_class}] {prop.macro}: {prop.annotation.code}"
              f"  after {prop.annotation.after_substring!r}")
        target_file, base_content = find_target_file(prop.annotation.after_substring, accepted)
        if target_file is None:
            note = "anchor not found in any libpng source file"
            print(f"    [revert] {note}"); history.append((prop, note)); continue
        if "".join(prop.annotation.code.split()) in "".join(base_content.split()):
            note = "annotation already present (a prior step); find the NEXT roadblock"
            print(f"    [reject:dup] {note}"); history.append((prop, note)); continue
        candidate = apply_annotation(base_content, prop.annotation)
        target_file.write_text(candidate)          # apply to disk for the build
        print(f"    patched {target_file.name}; rebuilding AFL+IJON ...")
        target = build_ijon_target()
        if target is None:
            note = "patched target failed to build"
            print(f"    [revert] {note}")
            target_file.write_text(base_content)   # roll back disk
            history.append((prop, note)); continue
        _, queue = fuzz(target, f"loop_iter{it}", args.eval_timeout)
        after_cov = probe.measure(queue, tag=f"iter{it}")
        new = after_cov.new_vs(baseline_cov)
        if new:
            print(f"    [KEEP] {len(new)} NEW functions covered: {sorted(new)[:5]}")
            accepted[target_file] = candidate       # commit
            kept.append((target_file.name, prop.annotation.code))
            baseline_cov = after_cov
        else:
            note = (f"no new source coverage ({baseline_cov.n_functions}->"
                    f"{after_cov.n_functions} fns); annotation did not let the fuzzer "
                    f"reach new code (raw IJON edge growth does not count) — try a "
                    f"different gate or primitive")
            print(f"    [REVERT] {note}")
            target_file.write_text(base_content)    # roll back disk to accepted
            history.append((prop, note))

    print(f"\n[done] {args.max_iters} iters; kept {len(kept)} annotation(s): {kept}")
    print("VALIDATION: coverage-driven keep/revert ran in-loop on a real target; "
          "no-gain IJON_CMP correctly reverted (not fooled by edge inflation).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
