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
    line_end: int = 0


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
            line_end=int(e.get("functionLinenumberEnd", 0) or 0),
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


def common_gates(fi: dict, cov: dict, top_n: int = 6, frontier_n: int = 12) -> list:
    """Covered functions that the uncovered frontier functions commonly CALL.
    A function every blocked handler invokes (e.g. a CRC/length/format check) is
    the shared gate the fuzzer keeps hitting but can't pass — even when raw
    execution count would rank an inner loop higher. Returns [(name, hits)]."""
    from collections import Counter
    def covered(n): return cov.get(n, 0) > 0
    frontier_callees = []
    for e in compute_frontier(fi, cov)[:frontier_n]:
        if e.callee not in frontier_callees:
            frontier_callees.append(e.callee)
    hits: Counter = Counter()
    for callee in frontier_callees:
        info = fi.get(callee)
        if not info:
            continue
        seen = set()
        for dst, _, _ in info.callsites:
            if dst in fi and covered(dst) and dst not in seen:
                hits[dst] += 1
                seen.add(dst)
    return hits.most_common(top_n)


def candidate_functions(fi: dict, cov: dict, top_n: int = 6) -> list:
    """Functions worth showing the LLM: the hot gates (covered, with an
    uncovered reachable callee) plus the callers of the top frontier edges.
    These are where an annotation likely belongs or near it."""
    names: list = []
    for name, _, _, _ in hot_functions(fi, cov, top=top_n):
        if name not in names:
            names.append(name)
    for e in compute_frontier(fi, cov)[:top_n]:
        if e.caller not in names:
            names.append(e.caller)
    return names


def extract_function_source(fi: dict, names: list,
                            source_root: Optional[Path] = None) -> dict:
    """Return {name: source_text} for the named functions, sliced from their
    source files by FI's line range. If source_root is given, file basenames are
    resolved there (use when FI's recorded paths differ from where you read)."""
    out: dict = {}
    for name in names:
        info = fi.get(name)
        if not info or not info.source_file:
            continue
        path = Path(info.source_file)
        if source_root is not None:
            path = Path(source_root) / path.name
        if not path.exists():
            continue
        lines = path.read_text(errors="replace").splitlines()
        start = max(0, int(info.line) - 1)
        end = int(info.line_end) if info.line_end and info.line_end > info.line \
            else start + 80
        text = "\n".join(lines[start:end])
        out[name] = f"// {path.name}:{info.line}  ({name})\n{text}"
    return out


def build_localization_context(fi: dict, cov: dict,
                               source_root: Optional[Path] = None) -> tuple:
    """Assemble what the analyst LLM sees for a multi-function target: the
    frontier/gate hint, plus the source of the most likely annotation sites —
    the shared gates the frontier calls, those gates' covered callees (where the
    actual check usually lives), and the top frontier dispatch sites."""
    hint = localization_hint(fi, cov)
    names: list = []

    def add(n):
        if n in fi and n not in names:
            names.append(n)

    for g, _ in common_gates(fi, cov, top_n=4):
        add(g)
        for dst, _, _ in fi[g].callsites:           # one level down: the check
            if dst in fi and cov.get(dst, 0) > 0:
                add(dst)
    for e in compute_frontier(fi, cov)[:3]:
        add(e.caller)
    src = extract_function_source(fi, names, source_root)
    return hint, src


if __name__ == "__main__":
    import sys
    fi = load_fi(Path(sys.argv[1]))
    cov = load_cov(Path(sys.argv[2]))
    print(localization_hint(fi, cov))
