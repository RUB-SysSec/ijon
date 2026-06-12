# Experiments — evidence for the autonomous-IJON thesis

Three experiments, run on the IJON paper's **own** benchmarks (`ijon-data`) and on
a real target (libpng), building one argument: an LLM can do the human analyst's
IJON job, and — over iterations — reach annotations a single shot (human or model)
does not.

## 1. Human vs. LLM, on the paper's own benchmarks — `human_vs_llm/`
Blind: strip the paper authors' annotation + scaffolding, hand the model the clean
source, compare.
- **maze (class 1): exact match.** LLM `IJON_SET(ijon_hashint(x,y))` ≡ human
  `transition(hashint(x,y))` — same primitive, state, hash, placement.
- **TPM (class 2): one-shot stays local.** Even given the call structure, the
  model annotates the nearest visible barrier, not the cross-command sequence.
  → the lever for class 2 is **iteration, not context**.

## 2. Convergence A/B on libpng (real target) — `libpng_convergence/`
The paper's evaluation shape (metric-over-time, AFL vs AFL+IJON), on the class-2
metric (distinct chunk-type sequences), CRC disabled so sequencing is the only
variable.
- **41× more distinct chunk-sequences**, opening by t=20 s and sustained.
- **Function coverage equal (124 vs 125)** — honestly scoped (§7b: deep libpng is
  format-gated); the class-2 win is a *state* win.
- Insight: IJON edge-inflation is **noise for class 3** but the **signal for
  class 2** → keep/revert reward must match the failure class.

## 3. Autonomy closure on libpng — `libpng_autonomy/`
Close the loop with the class-matched reward (state diversity). Fully autonomous,
never shown the human annotation.
- **CLOSED, iteration 4**: the agent climbed `IJON_CMP(chunk_name,…)` [class 3] →
  `IJON_STATE(mode)` [class 2, too coarse] → **`IJON_STATE(ijon_hashint(mode,
  chunk_name))`** = **30.9× baseline**, kept by the diversity reward.
- It **reinvented the human idiom** (hash running-state with the per-event name)
  via iteration + general (answer-free) feedback — what one shot never reached.

## Through-line
- **Class 1**: LLM matches the expert one-shot (maze).
- **Class 2**: one-shot (human-style or model) lands local; the **autonomous loop,
  with a class-matched reward and granularity feedback, climbs to the rich state
  annotation** — the regime a human-in-the-loop cannot practically sustain.

## Reproduce
```
python scripts/annotation_comparison.py            # 1: maze + TPM (+ tpm_scoped)
python scripts/libpng_convergence.py --budget 360  # 2: time-series A/B (41x)
python scripts/libpng_autonomy.py --iters 5        # 3: autonomy closure (30.9x)
```
Ground truth: read-only clone of `github.com/RUB-SysSec/ijon-data` at
`../ijon-data-ref` (outside this repo).

## Open follow-ups
- Generalize the class-2 reward from PNG-specific sequence count to **IJON-map
  richness** (any target; handle the saturation confound).
- **Mario demo** (`ijon-data/SuperMarioBros-C`, `ijon_max(pos_y/16, world_pos)`):
  LLM does the annotation; doubles as a class-1 IJON_MAX human-vs-LLM comparison.
