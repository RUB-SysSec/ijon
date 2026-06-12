# libpng autonomy closure (class 2) — CLOSED

**Claim.** The autonomous loop, with a **class-matched reward** (state diversity,
not function coverage), can — *over iterations* — escalate from its local one-shot
picks to a state annotation that unlocks chunk-sequence exploration, **without
ever seeing the human annotation**. The history feedback is general class-2
guidance and never names the chunk-sequence answer.

Reward = distinct chunk-type sequences. KEEP threshold = 5× the plain baseline.
Each iteration is independent (pristine base + one candidate) so we measure
whether the agent *finds* the unlocking annotation, not annotation stacking.
Reproduce: `python scripts/libpng_autonomy.py --iters 5 --window 90`.

## Result — closed by iteration 4 (baseline 51, threshold 255)

| iter | proposal | class | sequences | outcome |
|---|---|---|---|---|
| 1 | `IJON_CMP(chunk_name, png_iCCP)` | 3 missing-intermediate | 196 | revert |
| 2 | `IJON_STATE(png_ptr->mode)` | 2 known-state-changes | 102 | revert |
| 3 | `IJON_CMP(crc, png_ptr->crc)` | 3 | 63 | revert |
| 4 | **`IJON_STATE(ijon_hashint(png_ptr->mode, chunk_name))`** | 2 | **1575** | **KEEP** |

**1575 distinct chunk-sequences = 30.9× the plain baseline, kept by the
state-diversity reward, fully autonomous.**

### The agent climbed the intended ladder
1. **Local class-3 guess** (`IJON_CMP(chunk_name, …)`) — match one chunk tag. 196
   sequences: a little better, but reverted (below threshold).
2. **Right class, too-coarse state** (`IJON_STATE(png_ptr->mode)`) — libpng's own
   phase flag, only ~102 distinct values.
3. Fed *only* the granularity feedback — *"right idea, but the state is too coarse;
   pick a state that changes at every step and combine it with its running history
   so distinct processing ORDERS map to distinct values"* (never names chunk_name)
   — it **constructed the richer state itself**:
   `IJON_STATE(ijon_hashint(png_ptr->mode, chunk_name))`, combining the phase flag
   *with the current chunk type*. Its own words for the state:
   *"png_ptr->mode (cumulative chunk flags) and chunk_name (current chunk type)
   combined to represent the processing state at each step of the chunk loop."*

This is essentially the human annotation's idiom (hash the running state with the
per-chunk name) — **reached by iteration, never shown.** One-shot
(experiments/libpng_convergence Part A, experiments/human_vs_llm) never left
class 1/3; the loop's reward feedback moved it to the right class *and* the right
state granularity.

## How we got here (design iterations that matter for the write-up)
The first attempt did NOT close (`IJON_STATE(png_ptr->mode)`, 58 sequences,
below threshold), and the diagnosis was decisive:
- **Reward must match the class.** Function coverage is flat for this annotation
  (+0 functions), so a coverage-driven loop would wrongly revert it; the
  state-diversity reward keeps it. (See experiments/libpng_convergence.)
- **The gap was state-GRANULARITY, not scope.** The chunk loop (`png_read_info`,
  `chunk_name`, the `for(;;)`) was already in the agent's localized view — it just
  defaulted to the obvious labeled-as-state field (`mode`) over the subtler
  "order of chunk types is the state" insight.
- **Granularity feedback closes it.** Telling the agent its kept-class state was
  too coarse pushed it to combine `mode` with `chunk_name` — the unlocking move.

(Two early iterations were also lost to mechanics — an unresolved `after_substring`
and a build race — fixed with whitespace-normalized anchor resolution feeding the
exact file line to `apply_annotation`.)

## Takeaways
1. **Autonomy closed on a real target**: from local guesses to a 30.9× state
   annotation, no human annotation seen — the iteration-beats-one-shot thesis,
   demonstrated.
2. **Two levers, both class-matched**: the *reward* (state diversity for class 2)
   and the *feedback granularity* (coarse-state → finer-state). Neither names the
   answer.
3. **Next (agreed follow-up)**: generalize the reward from PNG-specific sequence
   count to IJON-map richness (handling the saturation confound), so the same loop
   drives class-2 autonomy on any target.
