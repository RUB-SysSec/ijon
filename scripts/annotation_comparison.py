#!/usr/bin/env python3
"""Idea-1 evaluation: human-vs-LLM IJON annotation, on the IJON paper's OWN
benchmarks (RUB-SysSec/ijon-data).

For each ground-truth case we:
  1. take the paper author's annotated source,
  2. remove BOTH the IJON call AND the human's scaffolding (the helper vars that
     exist only to compute the fed value) -- every removed line is recorded, so
     the blind test is auditable,
  3. hand the model the clean whole-file source + a synthetic plateau telemetry,
  4. capture the single annotation it proposes,
  5. emit a record pairing the human ground truth against the LLM proposal.

Scoring (same primitive? same state variable? same placement?) is done by a
human/judge reading the emitted report -- this script does not self-grade, to
avoid the model judging itself.

The model never sees the answer: make_clean_source() hard-asserts no 'ijon'
token survives, and the per-case strip_lines remove the non-IJON scaffolding
(e.g. the maze's `//transition(hashint(x,y))` hint, TPM's `command_state` hash)
that would otherwise leak the intended state variable.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness.build import make_clean_source
from harness.fuzzer import Snapshot
from harness.model import AnalystModel
from harness.agent import propose_annotation

# read-only clone of github.com/RUB-SysSec/ijon-data (the paper's annotations)
REF = REPO.parent / "ijon-data-ref"
OUT = REPO / "experiments" / "human_vs_llm"


@dataclass
class GroundTruth:
    primitive: str       # IJON macro / helper the authors used
    state: str           # the program state they fed in
    placement: str       # where (function + control-flow point)
    verbatim: str        # the exact annotation line(s) from ijon-data


@dataclass
class Case:
    name: str
    source_file: Path
    source_name: str          # filename shown to the model
    strip_substrings: list    # non-IJON scaffolding lines to remove (leak guard)
    snapshot_stats: dict      # synthetic plateau telemetry
    situation: str            # one-line description of what's stuck (for the record)
    ground_truth: GroundTruth
    localization: str = None  # neutral call-structure context (fair-scope variant)


def plateau_snapshot(stats: dict) -> Snapshot:
    base = {
        "execs_done": 50_000_000, "edges_found": 0, "total_edges": 65536,
        "corpus_count": 12, "time_wo_finds": 7200, "pending_favs": 0,
        "saved_crashes": 0, "run_time": 7200,
    }
    base.update(stats)
    return Snapshot(base, OUT)  # crashes_dir unused here


def clean_source(case: Case) -> tuple[str, list]:
    """Return (clean_source_shown_to_model, removed_scaffolding_lines)."""
    text = case.source_file.read_text(errors="replace")
    removed = []
    kept = []
    for line in text.splitlines(keepends=True):
        if any(s in line for s in case.strip_substrings):
            removed.append(line.rstrip("\n"))
        else:
            kept.append(line)
    clean = make_clean_source("".join(kept))  # also strips any 'ijon' line + asserts
    return clean, removed


CASES = [
    Case(
        name="maze",
        source_file=REF / "maze" / "small.c",
        source_name="maze.c",
        # the ONLY human IJON line is commented (`//transition(...)`) and does
        # not contain 'ijon', so the generic redactor misses it -> strip here.
        strip_substrings=["transition(hashint"],
        snapshot_stats={"corpus_count": 9, "time_wo_finds": 3600},
        situation=("AFL plateaued on a maze game: input bytes are U/D/L/R moves; "
                   "edge coverage flat because every move hits the same handful "
                   "of edges regardless of WHERE in the maze the player is."),
        ground_truth=GroundTruth(
            primitive="ijon_push_state / IJON_SET (transition)",
            state="player coordinates (x, y)",
            placement="inside the per-move loop, right after the position update",
            verbatim="transition(hashint(x,y));   // maze/small.c:53",
        ),
    ),
    Case(
        name="tpm",
        source_file=REF / "tpm_fuzzing" / "src" / "CommandDispatcher.c",
        source_name="CommandDispatcher.c",
        # command_state is a global the authors added solely to build the
        # state hash they feed to ijon_push_state -> pure scaffolding, strip it.
        strip_substrings=[
            "uint32_t command_state=0;",
            "command_state=(command_state<<8)",
            'printf("\\tcommand_state',
        ],
        snapshot_stats={"corpus_count": 40, "time_wo_finds": 10800,
                        "edges_found": 0},
        situation=("AFL plateaued fuzzing the IBM TPM 2.0 command server. Inputs "
                   "are sequences of TPM commands. Coverage stalls: single "
                   "commands are reached, but deep states need specific ORDERED "
                   "command sequences (the command-to-command transitions) that "
                   "blind byte mutation cannot stumble into."),
        ground_truth=GroundTruth(
            primitive="ijon_push_state",
            state="the running command-index sequence "
                  "(command_state = (command_state<<8) | (command->index & 0xff))",
            placement="in CommandDispatcher, success branch after a command "
                      "executes (Exit: / TPM_RC_SUCCESS)",
            verbatim="command_state=(command_state<<8)|(command->index&0xff); "
                     "ijon_push_state(command_state);   "
                     "// tpm_fuzzing/src/CommandDispatcher.c:393-396",
        ),
    ),
]

# Fair-scope variant of the TPM case: same stripped source, but supply the
# neutral call-structure context a localizer would recover. Every fact below is
# verbatim/structural from the real source (ExecuteCommand's own pipeline
# doc-comment + the server's per-command loop) and contains NO IJON hint and no
# suggestion to capture a sequence -- it only makes visible that one fuzz input
# drives a SEQUENCE of commands and that command->index is the command type,
# which is invisible from CommandDispatcher.c alone.
_TPM_FAIR_LOCALIZATION = """\
CALL STRUCTURE (recovered by the localizer; structural facts only, no annotation hints):

The fuzzed program is a TPM 2.0 command server. Its main loop reads and executes
commands one after another from the input stream
(TcpServerPosix.c: `do { read one command; RunCommand() -> ExecuteCommand(); } while(continueServing);`),
so a SINGLE fuzz input drives a sequence of commands, executed in order.

ExecuteCommand() (ExecCommand.c) is the per-command entry point. Per its own
source comment, for EACH command it:
  a) parses the command header from the input buffer;
  b) ParseHandleBuffer() parses the handle area;
  c) validates that each handle references a loaded entity;
  d) ParseSessionBuffer() unmarshals the sessions and checks authorizations;
  e) CommandDispatcher() (the function shown below) unmarshals the parameters
     and calls the routine that performs the command action;
  f) on any error in the steps above, builds an error response and returns.

`command->index` is the dispatched command's type identifier.
"""

_tpm = [c for c in CASES if c.name == "tpm"][0]
CASES.append(Case(
    name="tpm_scoped",
    source_file=_tpm.source_file,
    source_name=_tpm.source_name,
    strip_substrings=_tpm.strip_substrings,
    snapshot_stats=_tpm.snapshot_stats,
    situation=_tpm.situation + " [FAIR-SCOPE: localizer supplies call structure]",
    ground_truth=_tpm.ground_truth,
    localization=_TPM_FAIR_LOCALIZATION,
))


def run_case(model: AnalystModel, case: Case) -> dict:
    clean, removed = clean_source(case)
    snap = plateau_snapshot(case.snapshot_stats)
    prop = propose_annotation(model, clean, snap, source_name=case.source_name,
                              localization=case.localization)
    return {
        "case": case.name,
        "situation": case.situation,
        "fair_scope_localization": case.localization,
        "ground_truth": asdict(case.ground_truth),
        "scaffolding_removed_for_blind_test": removed,
        "clean_source_lines": len(clean.splitlines()),
        "llm_proposal": {
            "failure_class": prop.failure_class,
            "why_stuck": prop.why_stuck,
            "relevant_state": prop.relevant_state,
            "macro": prop.macro,
            "annotation_code": prop.annotation.code,
            "after_substring": prop.annotation.after_substring,
            "placement_reason": prop.placement_reason,
        },
        "model": prop.llm.model,
        "tokens": {"prompt": prop.llm.prompt_tokens,
                   "completion": prop.llm.completion_tokens},
    }


def main():
    only = sys.argv[1:] or [c.name for c in CASES]
    OUT.mkdir(parents=True, exist_ok=True)
    model = AnalystModel()
    results = []
    for case in CASES:
        if case.name not in only:
            continue
        print(f"\n{'='*70}\nCASE: {case.name}  ({case.source_name})\n{'='*70}")
        rec = run_case(model, case)
        results.append(rec)
        gt = rec["ground_truth"]
        lp = rec["llm_proposal"]
        print(f"  stripped {len(rec['scaffolding_removed_for_blind_test'])} "
              f"scaffolding line(s); model saw {rec['clean_source_lines']} clean lines")
        print(f"  HUMAN : {gt['primitive']}  | state={gt['state']}")
        print(f"          placement: {gt['placement']}")
        print(f"  LLM   : [{lp['failure_class']}] {lp['macro']}  "
              f"| state={lp['relevant_state']}")
        print(f"          code: {lp['annotation_code']}")
        print(f"          after: {lp['after_substring']!r}")
        out_path = OUT / f"{case.name}.json"
        out_path.write_text(json.dumps(rec, indent=2))
        print(f"  -> {out_path.relative_to(REPO)}")
    (OUT / "all.json").write_text(json.dumps(results, indent=2))
    print(f"\nWrote {len(results)} record(s) to {OUT.relative_to(REPO)}/")


if __name__ == "__main__":
    main()
