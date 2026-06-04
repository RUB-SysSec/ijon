"""Source-level coverage measurement for keep/revert decisions on real targets.

AFL's edges_found conflates real coverage with IJON map entries, so an annotation
that merely inflates the IJON map (e.g. IJON_CMP buckets) looks like progress
when no new program code was reached. We saw this on libpng: edges rose to 1269
(above the no-roadblock ceiling) while real coverage was flat (132 vs 133
functions). The fix: measure REAL source coverage by replaying the corpus through
a fixed llvm-cov build that has NO IJON/AFL instrumentation.

The coverage build is fixed (original source); annotations don't change which
functions an input exercises in the original program, so one build is reused
across loop iterations — we just replay each iteration's corpus through it.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class CovSnapshot:
    covered: set = field(default_factory=set)   # function names with count>0
    n_functions: int = 0
    n_regions: int = 0

    def new_vs(self, other: "CovSnapshot") -> set:
        return self.covered - other.covered


class CoverageProbe:
    """Replays a corpus through an llvm-cov-instrumented build to get real
    source coverage. `cov_binary` is a libFuzzer-driver binary built with
    -fprofile-instr-generate -fcoverage-mapping (see workspace/libpng/cov-build.sh)."""

    def __init__(self, cov_binary: Path, llvm_bin: Path, work_dir: Path):
        self.cov_binary = Path(cov_binary)
        self.llvm_bin = Path(llvm_bin)
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)

    def _tool(self, name: str) -> str:
        p = self.llvm_bin / name
        return str(p) if p.exists() else name

    def measure(self, corpus_dir: Path, tag: str = "cov") -> CovSnapshot:
        corpus_dir = Path(corpus_dir)
        files = sorted(str(p) for p in corpus_dir.glob("id:*"))
        if not files:
            files = sorted(str(p) for p in corpus_dir.iterdir() if p.is_file())
        if not files:
            return CovSnapshot()
        profraw = self.work_dir / f"{tag}.profraw"
        profdata = self.work_dir / f"{tag}.profdata"
        env = {"LLVM_PROFILE_FILE": str(profraw)}
        import os
        full_env = dict(os.environ); full_env.update(env)
        # libFuzzer replays each file once and exits when given file args
        subprocess.run([str(self.cov_binary), *files], env=full_env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not profraw.exists():
            return CovSnapshot()
        subprocess.run([self._tool("llvm-profdata"), "merge", "-sparse",
                        str(profraw), "-o", str(profdata)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        out = subprocess.run([self._tool("llvm-cov"), "export",
                              f"-instr-profile={profdata}", str(self.cov_binary)],
                             capture_output=True, text=True)
        try:
            data = json.loads(out.stdout)
        except json.JSONDecodeError:
            return CovSnapshot()
        d0 = data["data"][0]
        covered = set()
        regions = 0
        for f in d0.get("functions", []):
            if f.get("count", 0) > 0:
                covered.add(f["name"])
        regions = int(d0.get("totals", {}).get("regions", {}).get("covered", 0))
        return CovSnapshot(covered=covered, n_functions=len(covered), n_regions=regions)
