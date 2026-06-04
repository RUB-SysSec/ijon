"""The 'analyst' agent: given a stuck target's source + plateau telemetry,
classify why the fuzzer is stuck and synthesize one IJON annotation.

The prompt gives the model what a human IJON analyst has — the source, the
fuzzer's situation, the IJON primitive API, and the paper's taxonomy of
roadblocks — but never the specific answer. The model must reason out which
program state is invisible to edge coverage and expose it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .build import Annotation
from .fuzzer import Snapshot
from .model import AnalystModel, LLMResult

FAILURE_CLASSES = {
    "known_relevant_state_values",   # interesting behavior is a data VALUE, not an edge
    "known_state_changes",           # log which operations fired as a proxy state
    "missing_intermediate_state",    # no gradient at all (magic bytes/checksum/hash)
}

# A faithful, answer-free cheatsheet of the IJON primitives (from afl-ijon-min.h).
IJON_REFERENCE = """\
IJON annotations reshape AFL's feedback function. Insert ONE C statement into
the target. Available primitives (all are macros; helpers combine values):

  IJON_SET(x)        Treat each distinct value of integer x as a new coverage
                     event (new value -> input saved). Use to expose a state
                     VALUE so the fuzzer explores its distinct values.
  IJON_INC(x)        Like IJON_SET but rewards how OFTEN a value occurs.
  IJON_MAX(x)        Maximize x via a separate hill-climbing map (e.g. push a
                     counter/coordinate as far as possible). IJON_MIN(x) too.
  IJON_STATE(n)      Add a 'virtual state' n to edge encoding: the SAME code
                     path counts as different coverage in different states.
  IJON_CMP(x,y)      Reward closeness of two ints (differing-bit count).
  IJON_STRDIST(a,b)  Reward growing the common prefix of two strings.

Helpers usable inside the above:
  ijon_hashint(old, val)  combine two integers into one hash (e.g. fold two
                          coordinates into a single value for IJON_SET).
  ijon_hashstr(old, s) / ijon_hashmem(old, p, len)  hash a string / buffer.

The annotation must reference only variables in scope at the chosen line."""

_SYSTEM = """\
You are a fuzzing analyst automating the IJON technique (Aschermann et al.,
"Exploring Deep State Spaces via Fuzzing"). An AFL++ campaign has plateaued:
mutation produces no new edge coverage, so the fuzzer is stuck. Edge coverage
is blind to interesting program state that lives in DATA rather than in which
branches execute. Your job: figure out what state matters, then add ONE tiny
IJON annotation that exposes it to the feedback function.

The paper's taxonomy of why coverage gets stuck:
- known_relevant_state_values: the interesting behavior is a specific data
  value (a coordinate, an index, an internal register), reachable by code that
  doesn't add new edges. Expose the value (IJON_SET/IJON_INC), or push it
  (IJON_MAX/IJON_MIN).
- known_state_changes: state is spread across operations; log which ones fired
  (e.g. message types) and expose that as a proxy (IJON_STATE / IJON_SET).
- missing_intermediate_state: a hard equality with no gradient (magic bytes,
  checksum, hash). Manufacture a gradient (IJON_CMP / IJON_STRDIST).

PRIMITIVE SELECTION (critical):
- To pass a hard equality `x == TARGET` or `x == y` (magic value, checksum,
  hash, tag), use IJON_CMP(x, TARGET) / IJON_CMP(x, y) — it rewards matching
  more bits, giving a gradient to equality. NEVER use IJON_SET/IJON_INC for
  this: exposing a wide value creates one map entry per distinct value, which
  floods (saturates) the coverage map and overwhelms the fuzzer.
- Use IJON_SET/IJON_INC only to explore a SMALL, bounded set of states (a
  position in a small grid, a message-type id) — never a full 32/64-bit word.
- Use IJON_MAX/IJON_MIN to push a value as far as possible (counters, depth,
  coordinates) when there is no single target value.
- Use IJON_STRDIST for string / byte-buffer prefix comparisons.

PLACEMENT RULES (critical): the annotation must run on EVERY relevant
execution and BEFORE/OUTSIDE the branch it is meant to help the fuzzer reach.
Insert it on the normal path right after the relevant state is computed/updated
-- never inside the success branch of the very condition you are trying to
satisfy (code there runs only once the goal is already reached, giving no
gradient). For a hard `if (a == b)` gate, the annotation belongs immediately
BEFORE that `if`, not inside its body. In a loop, place it inside the loop body
so it fires each iteration. `after_substring` selects the line to insert AFTER,
so pick the statement that precedes the gate.

EXISTING ANNOTATIONS: the target source may already contain IJON_* calls added
in earlier iterations. Treat them as working prior steps — never propose one
identical to what is already there. If the fuzzer is still stuck, the barrier
those annotations addressed has been passed; identify the NEXT roadblock
(usually a deeper/later condition the fuzzer can now reach) and annotate THAT.

Reply with ONLY a JSON object, no prose, no markdown, of this exact shape:
{
  "why_stuck": "<1-3 sentences: what behavior the fuzzer cannot reach and why edge coverage misses it>",
  "failure_class": "<one of: known_relevant_state_values | known_state_changes | missing_intermediate_state>",
  "relevant_state": "<the specific in-scope variable(s) that capture the interesting state>",
  "annotation": {
    "macro": "<IJON_SET | IJON_INC | IJON_MAX | IJON_MIN | IJON_STATE | IJON_CMP | IJON_STRDIST>",
    "code": "<one C statement to insert verbatim, e.g. SOMEMACRO(expr);>",
    "after_substring": "<an exact, unique substring of the source LINE to insert the statement AFTER>",
    "placement_reason": "<why this location: the variables must be in scope and updated each relevant iteration>"
  }
}"""


@dataclass
class AnnotationProposal:
    why_stuck: str
    failure_class: str
    relevant_state: str
    annotation: Annotation
    macro: str
    placement_reason: str
    llm: LLMResult

    @property
    def summary(self) -> str:
        return (f"[{self.failure_class}] state={self.relevant_state!r} "
                f"-> {self.annotation.code!r} after {self.annotation.after_substring!r}")


def _telemetry_block(snap: Snapshot) -> str:
    return (
        f"- executions run: {snap.stats.get('execs_done')}\n"
        f"- edges covered: {snap.edges_found} of {snap.stats.get('total_edges')} "
        f"(flat; no growth)\n"
        f"- corpus size: {snap.corpus_count} inputs\n"
        f"- seconds since last new find: {snap.time_wo_finds}\n"
        f"- favored queue entries left to fuzz: {snap.pending_favs} "
        f"(0 = nothing left to try)\n"
        f"- crashes/goals reached: {snap.saved_crashes}\n")


def _history_block(history: list) -> str:
    """Render previously-tried annotations that did NOT help, so the model
    avoids repeating them. Each item is an AnnotationProposal plus an outcome
    note string, as (proposal, note) tuples."""
    if not history:
        return ""
    lines = ["PREVIOUS ATTEMPTS THIS SESSION THAT DID NOT HELP "
             "(do something different — change the primitive AND/OR the "
             "placement; address the stated reason):"]
    for i, (prop, note) in enumerate(history, 1):
        lines.append(
            f"  {i}. {prop.macro}: code `{prop.annotation.code}` inserted "
            f"after line `{prop.annotation.after_substring}` "
            f"-> {note}")
    return "\n".join(lines) + "\n\n"


def build_user_prompt(source: str, snap: Snapshot,
                      source_name: str = "target.c",
                      history: list = None,
                      localization: str = None) -> str:
    loc = ""
    if localization:
        loc = (f"LOCALIZATION (for a large multi-function target, this is where "
               f"the fuzzer is stuck — only the relevant functions' source is "
               f"shown below, not the whole program):\n{localization}\n\n")
    return (
        f"IJON PRIMITIVE REFERENCE:\n{IJON_REFERENCE}\n\n"
        f"FUZZER SITUATION (plateaued):\n{_telemetry_block(snap)}\n"
        f"{_history_block(history)}"
        f"{loc}"
        f"TARGET SOURCE ({source_name}):\n```c\n{source}\n```\n\n"
        f"Analyze why the fuzzer is stuck and propose exactly one IJON "
        f"annotation to break the plateau. The annotation will be inserted into "
        f"the source file that contains your chosen `after_substring` line; pick "
        f"a line that is unique and in scope. Respond with only the JSON object.")


def parse_proposal(obj: dict, llm: LLMResult) -> AnnotationProposal:
    if not obj:
        raise ValueError("empty proposal object")
    ann = obj.get("annotation") or {}
    code = (ann.get("code") or "").strip()
    after = ann.get("after_substring")
    if not code:
        raise ValueError("proposal missing annotation.code")
    if not after:
        raise ValueError("proposal missing annotation.after_substring")
    fc = obj.get("failure_class", "")
    if fc not in FAILURE_CLASSES:
        raise ValueError(f"unknown failure_class: {fc!r}")
    return AnnotationProposal(
        why_stuck=obj.get("why_stuck", ""),
        failure_class=fc,
        relevant_state=obj.get("relevant_state", ""),
        annotation=Annotation(code=code, after_substring=after),
        macro=ann.get("macro", ""),
        placement_reason=ann.get("placement_reason", ""),
        llm=llm,
    )


def propose_annotation(model: AnalystModel, source: str, snap: Snapshot,
                       source_name: str = "target.c",
                       history: list = None,
                       localization: str = None) -> AnnotationProposal:
    user = build_user_prompt(source, snap, source_name, history, localization)
    res = model.complete_json(_SYSTEM, user)
    return parse_proposal(res.obj, res)
