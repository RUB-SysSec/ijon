# Human vs. LLM IJON annotation — on the paper's own benchmarks

**Question.** When the IJON paper's authors hand-annotated their benchmarks, how
close does our LLM analyst get to the *same* annotation, given only the clean
source (their annotation removed) and generic plateau telemetry — no hints?

**Ground truth.** `github.com/RUB-SysSec/ijon-data` (cloned read-only to
`../ijon-data-ref`), the authors' own annotated benchmarks.

**Protocol (blind).** For each case we remove the IJON call *and* the human's
scaffolding (helper variables that exist only to compute the fed value — every
removed line is recorded in the JSON), assert no `ijon`/leak token survives, then
hand the model the clean **whole file** + a synthetic plateau snapshot. The model
localizes itself; the `situation` text in the script is for the human reader and
is **not** shown to the model. One shot, no iteration, no coverage feedback.
Reproduce: `python scripts/annotation_comparison.py`.

## Results

| Case | Class | Human annotation | LLM (blind, 1 shot) | Verdict |
|---|---|---|---|---|
| maze | 1 — known relevant state value | `transition(hashint(x,y))` on player `(x,y)`, in the move loop | `IJON_SET(ijon_hashint(x,y))`, after the per-move draw | **Exact match** — same primitive, same state, same hash idiom, same placement region |
| TPM | 2 — known state *change* (sequence) | `command_state=(command_state<<8)\|(command->index&0xff); ijon_push_state(command_state)` — the command **sequence** | `IJON_MAX(command->handleNum)` in the handle-parse loop | **Valid but different** — a locally-correct progress metric; misses the cross-command sequencing |
| TPM (fair scope) | 2 | same as above | `IJON_MAX(pNum)` in the parameter-unmarshal loop | **Still local** — given the call structure (inputs are command *sequences*; `command->index` is the type), the one-shot model *still* anchors on the innermost visible barrier, not the sequence |

### maze — exact reproduction
The model's stated reasoning matches the paper's rationale almost verbatim:
*"All valid move sequences execute the same basic blocks regardless of how close
they get to the goal, so the fuzzer receives no feedback."* It then fed the
player coordinates into the state map — the canonical IJON maze annotation —
from a stripped source it had never seen annotated.

### TPM (fair scope) — context alone does not flip it; iteration is the lever
We re-ran TPM after supplying the neutral call structure a localizer recovers —
verbatim from the real source, with no IJON hint and no suggestion to capture a
sequence: *the server executes a stream of commands in a loop; `ExecuteCommand`
runs once per command via the documented parse→dispatch pipeline; `command->index`
is the command type.* The one-shot model **still stayed local** — it moved from
`IJON_MAX(handleNum)` to `IJON_MAX(pNum)` (parameter-unmarshal counter), again
with locally-correct reasoning, and again did **not** reach the cross-command
sequence. Conclusion: for class 2, **more context is not the lever — iteration
is.** This is the empirical motivation for the autonomous loop (below).

### Why the local choices are rungs, not wrong answers
`IJON_MAX(handleNum)` and `IJON_MAX(pNum)` are *real* gradients: they get the
fuzzer past handle/parameter parsing. The human's `ijon_push_state(sequence)`
targets a *deeper* goal — exploring the command state machine. These are not
competitors; they are **successive plateaus**. A human annotator typically lands
one insight and stops; the autonomous loop, fed real coverage feedback, can solve
parsing first and — when that saturates and a *new* plateau appears deeper —
escalate to the command sequence. The stack of annotations emitted over time *is*
the contribution: continuous, adaptive, multi-step annotation a human-in-the-loop
cannot practically sustain.

### TPM (single function) — a genuine, instructive divergence
The LLM's reasoning is **locally correct**: within `CommandDispatcher.c` it found
the handle-parsing loop, saw that malformed inputs all take the same error return
(flat coverage), and rewarded inputs that parse more handles before failing
(`IJON_MAX(handleNum)`). That is a real, defensible annotation for *that* barrier.

But it is not the paper's annotation. The authors fed the **command-index
sequence** into IJON state, targeting the TPM's cross-command state machine
(deep states reachable only by specific *ordered* command sequences). Why the LLM
missed it — and why this is a scope finding, not a reasoning failure:

- `CommandDispatcher.c` dispatches **one** command. Its only loops are *intra*-
  command (unmarshal handle/parameter types).
- The sequencing is implicit: `command_state` is a **persistent global** that
  accumulates `command->index` across the *repeated per-command calls*, and that
  repetition lives in the outer `ExecuteCommand()` — a different file the model
  never saw.
- From a single function, "inputs are command *sequences*" is not visible. The
  model optimized the barrier fully in view instead of one requiring whole-
  program knowledge of the call structure.

This mirrors our own class-2 experience (libpng): the sequence move
(`chunk_seq_log=(chunk_seq_log<<8)|(chunk_name&0xFF)` fed into IJON state) is the
*direct analog* of the human TPM annotation — and our agent produced it when the
localizer pointed it at the chunk-processing loop with the right scope. So the
TPM gap predicts exactly what the agent needs: whole-program scope / localization
that surfaces the per-command repetition.

## Takeaways
1. **Class 1 (relevant state value): the LLM reproduces the expert annotation
   blind, first try.** Strong, clean credibility result — the model matched the
   paper's maze annotation *and* its rationale from stripped source.
2. **Class 2 (state change / sequence): one shot anchors local, even with correct
   scope.** Both the single-function and the fair-scope runs produced
   locally-optimal `IJON_MAX` annotations on inner parse counters, never the
   cross-command sequence. Context is not the lever.
3. **The lever is iteration — which is the central thesis.** Human-in-the-loop
   IJON is effectively one expert insight per plateau. The autonomous LLM makes
   annotation a tight online loop: try → measure real coverage delta → keep/revert
   → and, as the fuzzer digs to a *new* plateau, annotate again. The class-2
   plateaus (handle parse → param parse → command sequence) are exactly the ladder
   such a loop is built to climb — a continuous, adaptive, multi-step regime a
   human cannot practically sustain. The next experiment must show this **as a
   convergence curve** (iteration vs real source-coverage), with keep/revert driven
   by `CoverageProbe` (never IJON-inflated edge counts).

## Reproduce
```
python scripts/annotation_comparison.py            # maze + tpm (single function)
python scripts/annotation_comparison.py tpm_scoped # fair-scope TPM re-run
```
Records: `maze.json`, `tpm.json`, `tpm_scoped.json` (each lists exactly which
scaffolding lines were stripped for the blind test).
