"""Unit tests for the frontier localizer (harness/localize.py).

Synthetic call graph + coverage — no FI/llvm-cov files needed.
Run:  .venv/bin/python -m unittest tests.test_localize -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.localize import FuncInfo, compute_frontier, hot_functions


def fn(name, complexity=1, reached=None, calls=None):
    # calls: list of callee names -> callsites at a dummy file:line
    callsites = [(c, "src.c", "10") for c in (calls or [])]
    return FuncInfo(name=name, source_file="src.c", line=1,
                    complexity=complexity, reached=reached or [],
                    callsites=callsites)


class TestFrontier(unittest.TestCase):
    def setUp(self):
        # harness -> A(covered) -> B(covered) -> C(UNCOVERED) -> {D,E uncovered}
        #                       -> F(covered, no uncovered callees)
        self.fi = {
            "A": fn("A", calls=["B", "F"]),
            "B": fn("B", calls=["C"]),
            "C": fn("C", complexity=4, reached=["D", "E"], calls=["D", "E"]),
            "D": fn("D", complexity=3),
            "E": fn("E", complexity=2),
            "F": fn("F"),
        }
        self.cov = {"A": 5, "B": 5, "F": 5}  # C,D,E uncovered

    def test_frontier_finds_the_uncovered_edge(self):
        fr = compute_frontier(self.fi, self.cov)
        edges = {(e.caller, e.callee) for e in fr}
        self.assertIn(("B", "C"), edges)          # covered caller -> uncovered reachable callee
        self.assertNotIn(("A", "F"), edges)       # F is covered -> not a frontier

    def test_gated_complexity_includes_downstream(self):
        fr = compute_frontier(self.fi, self.cov)
        edge = next(e for e in fr if e.callee == "C")
        # gated = C(4) + uncovered reachable D(3) + E(2) = 9
        self.assertEqual(edge.gated, 9)

    def test_ranking_is_by_gated_desc(self):
        fr = compute_frontier(self.fi, self.cov)
        gated = [e.gated for e in fr]
        self.assertEqual(gated, sorted(gated, reverse=True))

    def test_hot_functions_flags_the_gate(self):
        # B is covered and has an uncovered callee C -> a candidate gate
        hot = {h[0] for h in hot_functions(self.fi, self.cov)}
        self.assertIn("B", hot)
        self.assertNotIn("F", hot)   # F has no uncovered callee

    def test_uncovered_caller_is_not_a_frontier(self):
        # if B is not covered, B->C is not a frontier (fuzzer never reaches B)
        cov = {"A": 5, "F": 5}
        fr = compute_frontier(self.fi, cov)
        self.assertNotIn(("B", "C"), {(e.caller, e.callee) for e in fr})


if __name__ == "__main__":
    unittest.main()
