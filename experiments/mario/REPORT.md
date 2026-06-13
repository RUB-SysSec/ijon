# Mario (SuperMarioBros-C) — the paper's flagship benchmark, two honest results

The IJON paper's most iconic demo: AFL "plays" Super Mario Bros. via one
annotation, `ijon_max(pos_y/16, world_pos)`. We use the authors' own C++ port +
harness (RUB-SysSec/ijon-data), built headless on Ubuntu 22.04 (no ROM, no
display; build at `-O0` — the codegen'd 6502 emulation miscompiles at `-O2`).

## Part A — autonomous annotation (a clean positive)
Blind (annotation stripped, plateau telemetry, no answer), the model proposed:

    IJON_MAX(world_pos);   // after: uint64_t world_pos = screen*255 + pos;

`known_relevant_state_values` — the **right primitive on the right state**,
reproducing the core of the paper's iconic annotation on its own benchmark. A
class-1 `IJON_MAX` human-vs-LLM datapoint (complements the maze's class-1 SET).
Honest nuance: the human buckets by height (`pos_y/16`) to also preserve vertical
exploration; the LLM used a single bucket. (`scripts/mario_annotation.py`)

## Part B — effectiveness A/B (an honest negative)
Plain AFL vs AFL+IJON, headless, level 1-1, max `world_pos` over time.

| arm | annotation | 15 min | 1 hour |
|---|---|---|---|
| plain AFL | none | 1517 | 1784 |
| IJON-1D (LLM) | `IJON_MAX(world_pos)` | 1514 | 1787 |
| IJON-2D (human) | `IJON_MAX(pos_y/16, world_pos)` | 1517 | 1791 |

**All three tie** — at 15 min *and* at 1 hour. IJON provides no measurable
benefit on Mario 1-1 with a modern fuzzer, even the human's "correct" 2-D
annotation. (`scripts/mario_convergence.py`)

### Why (the principled reason)
IJON beats edge coverage **only when the chosen state is invisible to edge
coverage.** Mario's `world_pos` is *visible*: each new screen executes new level
code → new edges. The plain-arm data shows it directly — edges climb (1652→1722)
*as Mario advances*. So plain AFL already has the forward gradient; `IJON_MAX`
re-encodes a signal it already has. Two factors compound it:
1. **`world_pos` ⊂ coverage** — position leaks into edges (above).
2. **AFL++ (2024) is a far stronger baseline than the paper's 2019 AFL** — the
   paper's IJON gain was measured against a weaker fuzzer that got stuck early.

### A diagnosis we corrected mid-flight (worth recording)
All arms first capped at exactly `world_pos=1517`. We initially read this as an
input-encoding wall. **Wrong** — the byte→controller mapping is the authors' own
and works; an append-only crossing test was the artifact. Proper havoc mutation
(re-timing the run-up) crossed 1517 → 1787, and the 1-hour runs all crossed it.
The cap was **runtime**, not encoding. Lesson: test crossability with real
mutation, not just appends.

## The contribution
This is a *critical* result, not a failure: it sharpens the central thesis.
**IJON helps exactly where state ⊄ coverage** — demonstrated cleanly on libpng
(coverage equal, 41× chunk-sequence diversity) and maxclimb (score invisible to
coverage) — **and adds nothing where state leaks into coverage** (Mario
position). The analyst's real skill is choosing a coverage-*invisible* state; the
LLM picks the salient one (`world_pos`), which is correct-looking but, here,
redundant. Mario's role: Part A (annotation reproduction) + this honest negative +
the placeholder-CHR video (the agent's annotation driving a real playthrough).
For effectiveness, lean on libpng and maxclimb.

## The video (ROM-free)
`mario_playthrough.gif` — the agent-annotated playthrough, headless-rendered with
a synthesized CHR (each tile a solid block in the game's *real* palette; the
dominant sky tile 292 + tile 0 left blank so the sky reads correctly), with a
live `world_pos` overlay climbing 40 → 1789. **No Nintendo ROM data is used or
produced** — the game logic + palette are compiled C++; only the tile graphics
are synthetic. Reproduce: `python scripts/mario_video.py` (capture path:
`workspace/mario/patches/capture_mode.patch`, seed: `workspace/mario/playthrough.input`).
