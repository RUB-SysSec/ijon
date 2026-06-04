"""The autonomous analyst loop.

Each iteration: ask the model for one annotation, apply it on top of the
annotations kept so far, rebuild, fuzz an evaluation window, then judge:
  - goal reached            -> SUCCESS (stop)
  - coverage advanced        -> KEEP the annotation, it becomes the new baseline
  - no change                -> REVERT it and feed the failure back to the model

This is what lets the agent self-correct: a semantically-right but badly-placed
annotation (e.g. IJON_CMP inside the branch it was meant to help reach) produces
no coverage change, gets reverted, and the next proposal is told why.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .build import Builder, Annotation, apply_annotation, make_clean_source
from .config import AflConfig
from .fuzzer import FuzzerController, Snapshot
from .model import AnalystModel
from .plateau import PlateauDetector
from .agent import propose_annotation, AnnotationProposal


@dataclass
class TargetSpec:
    workspace: Path          # dir containing src/ in/ targets/ out/
    src: str                 # source filename under workspace/src
    name: str = None         # display name shown to the model
    seeds: str = "in"        # input dir name under workspace

    def __post_init__(self):
        self.workspace = Path(self.workspace)
        self.name = self.name or self.src


@dataclass
class Attempt:
    iteration: int
    proposal: AnnotationProposal
    before_edges: int
    after_edges: int
    outcome: str             # "solved" | "kept" | "reverted"
    note: str


@dataclass
class LoopResult:
    solved: bool
    iterations: int
    attempts: list = field(default_factory=list)
    kept: list = field(default_factory=list)   # list[Annotation]
    final_source: str = ""


class AnalystLoop:
    def __init__(self, cfg: AflConfig, spec: TargetSpec,
                 model: Optional[AnalystModel] = None,
                 max_iters: int = 4, plateau_timeout: float = 95,
                 eval_timeout: float = 90, min_stall: int = 30,
                 advance_margin: int = 3, saturation_edges: int = 20000,
                 coverage_probe=None, log=print):
        self.cfg = cfg
        self.spec = spec
        self.builder = Builder(cfg)
        self.detector = PlateauDetector(min_stall_seconds=min_stall)
        self.model = model or AnalystModel()
        self.max_iters = max_iters
        self.plateau_timeout = plateau_timeout
        self.eval_timeout = eval_timeout
        self.advance_margin = advance_margin
        self.saturation_edges = saturation_edges
        # Optional CoverageProbe: when set, keep/revert uses REAL source coverage
        # (immune to IJON map inflation) instead of AFL edges_found. Required for
        # real targets where success is coverage, not a crash.
        self.coverage_probe = coverage_probe
        self.log = log

    # --- paths ---
    def _src(self, name: str) -> Path:
        return self.spec.workspace / "src" / name

    def _target(self, name: str) -> Path:
        return self.spec.workspace / "targets" / name

    def _out(self, name: str) -> Path:
        return self.spec.workspace / "out" / name

    def _build_and_fuzz(self, source: str, tag: str, ijon: bool,
                        until_solved_only: bool, timeout: float) -> tuple:
        """Returns (snapshot, queue_dir). queue_dir is where the corpus lands,
        for optional source-coverage measurement."""
        stem = Path(self.spec.src).stem
        src_path = self._src(f"{stem}_{tag}.c")
        src_path.write_text(source)
        cr = self.builder.compile(src_path, self._target(f"{stem}_{tag}"), ijon=ijon)
        if not cr.ok:
            self.log(f"    [build FAILED] {tag}:\n      " +
                     "\n      ".join(cr.stdout.splitlines()[-6:]))
            return None, None
        if ijon and cr.header_missing:
            self.log(f"    [warn] IJON header missing for {tag}")
        fc = FuzzerController(cr.binary, self.spec.workspace / self.spec.seeds,
                              self._out(tag), self.cfg, cwd=self.spec.workspace,
                              stop_on_crash=True)
        if until_solved_only:
            pred = lambda s: s.solved
        else:
            pred = lambda s: s.solved or self.detector.is_plateau(s)
        fc.run_until(pred, timeout=timeout, poll=3.0)
        return fc.snapshot(), self._out(tag) / "default" / "queue"

    def _measure_cov(self, queue_dir, tag: str):
        if self.coverage_probe is None or queue_dir is None:
            return None
        try:
            return self.coverage_probe.measure(queue_dir, tag=tag)
        except Exception as e:
            self.log(f"    [warn] coverage measure failed: {e}")
            return None

    @staticmethod
    def _norm(s: str) -> str:
        return "".join(s.split())

    def _already_present(self, working: str, code: str) -> bool:
        target = self._norm(code)
        return any(self._norm(line) == target for line in working.splitlines())

    def _classify(self, before: Snapshot, after: Snapshot,
                  before_cov=None, after_cov=None) -> tuple:
        """Return (verdict, note). verdict in solved|advanced|saturated|stalled.
        With a CoverageProbe, progress = REAL new source coverage (immune to
        IJON map inflation). Otherwise fall back to AFL edges (toy targets)."""
        if after is None:
            return "stalled", "build/run produced no telemetry"
        if after.solved:
            return "solved", "goal reached"
        if self.coverage_probe is not None and before_cov is not None \
                and after_cov is not None:
            new = after_cov.new_vs(before_cov)
            if new:
                return "advanced", (f"{len(new)} NEW source functions covered "
                                    f"(e.g. {sorted(new)[:4]}); real coverage "
                                    f"{before_cov.n_functions}->{after_cov.n_functions} fns")
            return ("stalled", f"no new source coverage "
                    f"({before_cov.n_functions}->{after_cov.n_functions} functions) — "
                    f"the annotation did not let the fuzzer reach new code "
                    f"(raw edge growth from IJON map entries does not count)")
        delta = after.edges_found - before.edges_found
        if after.edges_found >= self.saturation_edges and delta > 0:
            return ("saturated",
                    f"coverage map saturated (edges jumped to {after.edges_found}, "
                    f"~one entry per value): the annotation exposes a WIDE/unbounded "
                    f"value via IJON_SET/IJON_INC and floods the map. For matching a "
                    f"value against a target/constant use IJON_CMP, not IJON_SET")
        if delta >= self.advance_margin:
            return "advanced", f"coverage advanced (edges {before.edges_found}->{after.edges_found})"
        return ("stalled",
                f"no coverage change (edges {before.edges_found}->{after.edges_found}); "
                f"the annotation likely did not execute on the normal path, or does "
                f"not expose state useful for progress")

    def run(self) -> LoopResult:
        orig = self._src(self.spec.src).read_text()
        working = make_clean_source(orig)
        result = LoopResult(solved=False, iterations=0, final_source=working)

        # initial run of the clean target to a plateau (baseline)
        self.log("[init] fuzzing clean target to plateau")
        baseline, base_queue = self._build_and_fuzz(
            working, "clean", ijon=False, until_solved_only=False,
            timeout=self.plateau_timeout)
        if baseline is None:
            self.log("[init] clean build/run failed"); return result
        if baseline.solved:
            self.log("[init] target solved without annotation?!")
            result.solved = True; return result
        baseline_cov = self._measure_cov(base_queue, "base")
        cov_note = (f"; real coverage {baseline_cov.n_functions} fns"
                    if baseline_cov else "")
        self.log(f"[init] baseline: {self.detector.explain(baseline)}{cov_note}")

        history = []  # reverted attempts to feed back
        for it in range(1, self.max_iters + 1):
            result.iterations = it
            self.log(f"\n[iter {it}] asking analyst "
                     f"({len(history)} prior failed attempt(s) in context)")
            try:
                prop = propose_annotation(self.model, working, baseline,
                                          source_name=self.spec.name,
                                          history=history)
            except Exception as e:
                self.log(f"[iter {it}] proposal failed: {e}"); break
            self.log(f"    propose [{prop.failure_class}] {prop.macro}: "
                     f"{prop.annotation.code}  after "
                     f"{prop.annotation.after_substring!r}")

            if self._already_present(working, prop.annotation.code):
                note = ("this annotation is already present and active; it is a "
                        "prior working step. Find the NEXT roadblock still "
                        "blocking progress, not this one")
                self.log(f"    [reject:duplicate] {note}")
                history.append((prop, note))
                result.attempts.append(Attempt(it, prop, baseline.edges_found,
                                               baseline.edges_found, "reverted",
                                               "duplicate of an existing annotation"))
                continue

            try:
                patched = apply_annotation(working, prop.annotation)
            except ValueError as e:
                note = f"placement anchor not found ({e})"
                self.log(f"    [reverted] {note}")
                history.append((prop, note))
                result.attempts.append(Attempt(it, prop, baseline.edges_found,
                                               baseline.edges_found, "reverted", note))
                continue

            after, after_queue = self._build_and_fuzz(
                patched, f"iter{it}", ijon=True, until_solved_only=False,
                timeout=self.eval_timeout)
            after_cov = self._measure_cov(after_queue, f"iter{it}")
            be, ae = baseline.edges_found, (after.edges_found if after else baseline.edges_found)
            verdict, note = self._classify(baseline, after, baseline_cov, after_cov)

            if verdict == "solved":
                self.log(f"    [SOLVED] {note}")
                result.solved = True
                result.kept.append(prop.annotation)
                result.attempts.append(Attempt(it, prop, be, ae, "solved", note))
                working = patched; result.final_source = working
                return result

            if verdict == "advanced":
                self.log(f"    [KEEP] {note}")
                working = patched; baseline = after
                if after_cov is not None:
                    baseline_cov = after_cov     # new coverage ceiling
                result.kept.append(prop.annotation)
                result.attempts.append(Attempt(it, prop, be, ae, "kept", note))
            else:  # saturated | stalled -> revert and feed the reason back
                self.log(f"    [REVERT:{verdict}] {note}")
                history.append((prop, note))
                result.attempts.append(Attempt(it, prop, be, ae, "reverted", note))

        self.log(f"\n[done] budget exhausted after {result.iterations} iters; "
                 f"solved={result.solved}")
        result.final_source = working
        return result
