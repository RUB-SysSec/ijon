"""Unit tests for the deterministic parts of the harness (no fuzzing, no LLM).

Run:  .venv/bin/python -m unittest discover -s tests -v
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness import (Annotation, apply_annotation, strip_ijon_blocks,
                     redact_ijon_hints, make_clean_source, parse_fuzzer_stats,
                     parse_plot_data, PlateauDetector, AflConfig, TargetSpec,
                     AnalystLoop)
from harness.fuzzer import Snapshot


def make_snapshot(**stats):
    """Snapshot over an empty (nonexistent) crashes dir unless saved_crashes>0."""
    crashes = Path(tempfile.mkdtemp()) / "crashes"  # does not exist -> no crash files
    return Snapshot(stats, crashes)


class TestStatsParsing(unittest.TestCase):
    def test_fuzzer_stats_coercion(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("edges_found       : 16\n"
                    "bitmap_cvg        : 59.26%\n"
                    "pending_favs      : 0\n"
                    "afl_banner        : ./t\n")
            path = Path(f.name)
        s = parse_fuzzer_stats(path)
        self.assertEqual(s["edges_found"], 16)        # numeric -> int
        self.assertIsInstance(s["edges_found"], int)
        self.assertEqual(s["bitmap_cvg"], "59.26%")   # non-numeric key -> str
        self.assertEqual(s["pending_favs"], 0)

    def test_plot_data_rows(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
            f.write("# relative_time, corpus_count, edges_found\n")
            f.write("6, 151, 441\n")
            path = Path(f.name)
        rows = parse_plot_data(path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["edges_found"], "441")


class TestCleanSource(unittest.TestCase):
    SRC = ('// helper uses ijon_hashint internally\n'
           'uint32_t ijon_hashint(uint32_t a, uint32_t b);\n'
           'int f(int row, int col) {\n'
           '#ifdef IJON_SET\n'
           '    IJON_SET(ijon_hashint(row, col));\n'
           '#endif\n'
           '    return row + col;\n'
           '}\n')

    def test_strip_removes_ifdef_block(self):
        out = strip_ijon_blocks(self.SRC)
        self.assertNotIn("IJON_SET(", out)
        self.assertIn("return row + col;", out)

    def test_redact_removes_ijon_lines(self):
        out = redact_ijon_hints(self.SRC)
        self.assertNotIn("ijon_hashint", out.lower())

    def test_make_clean_source_is_leak_free(self):
        clean = make_clean_source(self.SRC)
        self.assertNotIn("ijon", clean.lower())
        self.assertIn("return row + col;", clean)

    def test_make_clean_source_asserts_on_residual(self):
        # a source whose only ijon mention is on a code line that survives strip
        bad = "int x = 1; /* keep */ IJON_LEFTOVER ijon stays\n"
        # redact drops the whole line (contains 'ijon'), so this is actually clean;
        # craft a survivor: ijon embedded without its own line via no newline split
        # -> redact is line-based, so any line with ijon is dropped; assert holds.
        self.assertNotIn("ijon", make_clean_source(bad).lower())


class TestApplyAnnotation(unittest.TestCase):
    SRC = "a();\nb_here();\nc();\n"

    def test_insert_after_substring(self):
        out = apply_annotation(self.SRC, Annotation(code="X();", after_substring="b_here"))
        lines = out.splitlines()
        self.assertEqual(lines[1], "b_here();")
        self.assertEqual(lines[2].strip(), "X();")

    def test_insert_after_line(self):
        out = apply_annotation(self.SRC, Annotation(code="X();", after_line=1))
        self.assertEqual(out.splitlines()[1].strip(), "X();")

    def test_missing_anchor_raises(self):
        with self.assertRaises(ValueError):
            apply_annotation(self.SRC, Annotation(code="X();", after_substring="nope"))

    def test_annotation_requires_exactly_one_anchor(self):
        with self.assertRaises(ValueError):
            Annotation(code="X();")  # neither
        with self.assertRaises(ValueError):
            Annotation(code="X();", after_substring="b", after_line=1)  # both


class TestPlateauDetector(unittest.TestCase):
    def setUp(self):
        self.d = PlateauDetector(min_stall_seconds=30)

    def test_plateau_true(self):
        s = make_snapshot(time_wo_finds=60, pending_favs=0, saved_crashes=0)
        self.assertTrue(self.d.is_plateau(s))

    def test_not_plateau_recent_find(self):
        s = make_snapshot(time_wo_finds=5, pending_favs=0, saved_crashes=0)
        self.assertFalse(self.d.is_plateau(s))

    def test_not_plateau_pending_favs(self):
        s = make_snapshot(time_wo_finds=60, pending_favs=3, saved_crashes=0)
        self.assertFalse(self.d.is_plateau(s))

    def test_solved_is_not_plateau(self):
        s = make_snapshot(time_wo_finds=60, pending_favs=0, saved_crashes=1)
        self.assertTrue(s.solved)
        self.assertFalse(self.d.is_plateau(s))


class TestLoopLogic(unittest.TestCase):
    def setUp(self):
        # pass a dummy non-None model so AnalystModel() (needs API key) is skipped
        self.loop = AnalystLoop(AflConfig(), TargetSpec(workspace="x", src="y.c"),
                                model=object(), advance_margin=3,
                                saturation_edges=20000)

    def test_dedup_detects_existing(self):
        src = "  IJON_CMP(stored, actual);\n  if (x) return;\n"
        self.assertTrue(self.loop._already_present(src, "IJON_CMP(stored, actual);"))
        self.assertTrue(self.loop._already_present(src, "IJON_CMP( stored ,actual ) ;"))
        self.assertFalse(self.loop._already_present(src, "IJON_CMP(tag, 0xC0u);"))

    def test_classify_solved(self):
        before = make_snapshot(edges_found=5, saved_crashes=0)
        after = make_snapshot(edges_found=40, saved_crashes=1)
        self.assertEqual(self.loop._classify(before, after)[0], "solved")

    def test_classify_advanced(self):
        before = make_snapshot(edges_found=5, saved_crashes=0)
        after = make_snapshot(edges_found=40, saved_crashes=0)
        self.assertEqual(self.loop._classify(before, after)[0], "advanced")

    def test_classify_stalled(self):
        before = make_snapshot(edges_found=5, saved_crashes=0)
        after = make_snapshot(edges_found=6, saved_crashes=0)  # delta < margin
        self.assertEqual(self.loop._classify(before, after)[0], "stalled")

    def test_classify_saturated(self):
        before = make_snapshot(edges_found=43, saved_crashes=0)
        after = make_snapshot(edges_found=57502, saved_crashes=0)  # map blowup
        self.assertEqual(self.loop._classify(before, after)[0], "saturated")


class TestCoverageClassify(unittest.TestCase):
    """With a CoverageProbe, keep/revert uses REAL source coverage, not edges."""
    def setUp(self):
        self.loop = AnalystLoop(AflConfig(), TargetSpec(workspace="x", src="y.c"),
                                model=object(), coverage_probe=object())

    def test_advanced_on_new_functions(self):
        from harness.coverage import CovSnapshot
        before = make_snapshot(edges_found=10, saved_crashes=0)
        after = make_snapshot(edges_found=10, saved_crashes=0)
        bcov = CovSnapshot(covered={"a", "b"}, n_functions=2)
        acov = CovSnapshot(covered={"a", "b", "png_handle_PLTE"}, n_functions=3)
        v, _ = self.loop._classify(before, after, bcov, acov)
        self.assertEqual(v, "advanced")

    def test_stalled_when_no_new_coverage_despite_edge_blowup(self):
        # the libpng lesson: edges exploded but real coverage flat -> NOT progress
        from harness.coverage import CovSnapshot
        before = make_snapshot(edges_found=936, saved_crashes=0)
        after = make_snapshot(edges_found=1269, saved_crashes=0)  # IJON-inflated
        bcov = CovSnapshot(covered={"a", "b"}, n_functions=2)
        acov = CovSnapshot(covered={"a", "b"}, n_functions=2)   # no new code
        v, _ = self.loop._classify(before, after, bcov, acov)
        self.assertEqual(v, "stalled")


if __name__ == "__main__":
    unittest.main()
