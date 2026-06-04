"""Frontier localization for multi-function targets (M4).

Joins fuzz-introspector's STATIC call graph (which function calls which, with
source locations + reachable-set + complexity) with llvm-cov RUNTIME coverage
(which functions actually executed) to find the coverage frontier:

    a COVERED caller whose call to a statically-REACHABLE callee is never
    covered -> the branch guarding that callee is where the fuzzer is stuck.

Edges are ranked by the downstream complexity they gate (how much still-unseen
code sits behind them). The top of that ranking, plus the "hot" covered
functions adjacent to it, is the localization hint handed to the analyst LLM —
so it reads the right few functions instead of the whole target.

Neither tool alone suffices: AFL's bitmap is source-anonymous (need llvm-cov for
source coverage); coverage alone doesn't say what's reachable-but-missed (need
the static graph). See docs/architecture-design.md.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class FuncInfo:
    name: str
    source_file: str
    line: int
    complexity: int
    reached: list                      # transitively reachable function names
    callsites: list = field(default_factory=list)  # (callee, file, line)


def load_fi(yaml_path: Path) -> dict:
    """Parse a fuzz-introspector data.yaml into {name: FuncInfo}."""
    d = yaml.safe_load(Path(yaml_path).read_text())
    out: dict = {}
    for e in d["All functions"]["Elements"]:
        name = e.get("functionName")
        if not name:
            continue
        callsites = []
        for cs in e.get("Callsites", []) or []:
            dst = cs.get("Dst")
            src = cs.get("Src", "")
            f, _, lc = src.partition(":")
            line = lc.split(",")[0] if lc else ""
            callsites.append((dst, f, line))
        out[name] = FuncInfo(
            name=name,
            source_file=e.get("functionSourceFile", ""),
            line=e.get("functionLinenumber", 0),
            complexity=int(e.get("CyclomaticComplexity", 0) or 0),
            reached=list(e.get("functionsReached", []) or []),
            callsites=callsites,
        )
    return out


def load_cov(cov_json_path: Path) -> dict:
    """Parse llvm-cov export JSON into {function_name: execution_count}."""
    cov = json.loads(Path(cov_json_path).read_text())
    counts: dict = {}
    for f in cov["data"][0].get("functions", []):
        # last covered wins; keep max count seen for a name
        counts[f["name"]] = max(counts.get(f["name"], 0), f.get("count", 0))
    return counts


def _gated_complexity(callee: str, fi: dict, covered) -> int:
    """Cyclomatic complexity of the callee plus its still-uncovered reachable
    functions = how much undiscovered code this edge gates."""
    info = fi.get(callee)
    if not info:
        return 0
    total = info.complexity
    for r in info.reached:
        if r in fi and not covered(r):
            total += fi[r].complexity
    return total


@dataclass
class FrontierEdge:
    caller: str
    callee: str
    callsite_file: str
    callsite_line: str
    gated: int            # downstream uncovered complexity behind this edge

    @property
    def loc(self) -> str:
        base = Path(self.callsite_file).name if self.callsite_file else "?"
        return f"{base}:{self.callsite_line}"


def compute_frontier(fi: dict, cov: dict, min_gated: int = 1) -> list:
    """Covered caller -> reachable uncovered callee, ranked by gated complexity."""
    def covered(n: str) -> bool:
        return cov.get(n, 0) > 0

    edges: dict = {}  # dedup by (caller, callee)
    for name, info in fi.items():
        if not covered(name):
            continue                      # fuzzer must reach the caller
        for callee, f, line in info.callsites:
            if callee not in fi:
                continue                  # external/macro
            if covered(callee):
                continue                  # already explored
            gated = _gated_complexity(callee, fi, covered)
            if gated < min_gated:
                continue
            key = (name, callee)
            if key not in edges or edges[key].gated < gated:
                edges[key] = FrontierEdge(name, callee, f, line, gated)
    return sorted(edges.values(), key=lambda e: -e.gated)


def hot_functions(fi: dict, cov: dict, top: int = 8) -> list:
    """Most-frequently-executed functions that have a reachable uncovered
    callee — candidate 'gates' the fuzzer keeps hitting but not passing."""
    def covered(n): return cov.get(n, 0) > 0
    hot = []
    for name, info in fi.items():
        c = cov.get(name, 0)
        if c <= 0:
            continue
        if any(cs[0] in fi and not covered(cs[0]) for cs in info.callsites):
            hot.append((name, c, info.source_file, info.line))
    return sorted(hot, key=lambda x: -x[1])[:top]


def localization_hint(fi: dict, cov: dict, top_n: int = 8) -> str:
    """Human/LLM-readable localization summary for the analyst prompt."""
    frontier = compute_frontier(fi, cov)
    hot = hot_functions(fi, cov)
    n_cov = sum(1 for n in fi if cov.get(n, 0) > 0)
    lines = [
        f"COVERAGE FRONTIER (joined static reachability + runtime coverage):",
        f"  {n_cov}/{len(fi)} reachable functions covered; "
        f"{len(frontier)} frontier edges (covered caller -> reachable but "
        f"UNCOVERED callee).",
        "",
        f"  Top reachable-but-uncovered code, ranked by gated complexity:",
    ]
    for e in frontier[:top_n]:
        callee = fi[e.callee]
        lines.append(f"    - {e.callee}  ({Path(callee.source_file).name}:"
                     f"{callee.line})  gated~{e.gated}; "
                     f"call from {e.caller} at {e.loc}")
    lines += ["", "  Hot functions the fuzzer executes a lot but whose callee "
              "stays uncovered (likely the gate):"]
    for name, c, sf, ln in hot:
        lines.append(f"    - {name}  ({Path(sf).name}:{ln})  executed {c}x")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    fi = load_fi(Path(sys.argv[1]))
    cov = load_cov(Path(sys.argv[2]))
    print(localization_hint(fi, cov))
