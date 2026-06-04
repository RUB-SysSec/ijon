# IJON-LLM — an LLM as the IJON fuzzing analyst

[IJON](docs/IJON-ExploringDeepStateSpacesviaFuzzing.pdf) (Aschermann et al.,
S&P 2020) lets a **human analyst** break fuzzing roadblocks by adding a tiny
source annotation that reshapes AFL's feedback function — exposing program
**state** that edge coverage is blind to. It solves mazes, games, checksums, and
CGC challenges that defeat every automated tool. The catch: it needs the human.

**This project replaces the human analyst with an LLM agent.** An autonomous
loop runs an AFL++ campaign, detects when it plateaus, asks DeepSeek *why* it is
stuck and *what* annotation to add, applies it, rebuilds, re-runs, and keeps or
reverts based on the result — accumulating annotations until the goal is reached.

The LLM is used **only at the two judgment points** (diagnose why-stuck,
synthesize the annotation). Everything mechanical — running the fuzzer, plateau
detection, patching, rebuilding, evaluation — is deterministic Python.

## Results

| Target | Roadblock class (agent's diagnosis) | Primitive chosen | Plain AFL | Agent |
|---|---|---|---|---|
| maze | known relevant state values | `IJON_SET` | stuck @16 edges | solved 8 s |
| checksum guard | missing intermediate state | `IJON_CMP` | 16.3M execs, 0 solves | solved 1 s |
| two-gate (loop) | missing intermediate state ×2 | `IJON_CMP` ×2 | — | solved, 2 iters |
| maxclimb | known relevant state values | `IJON_MAX` | 0 crashes / 16.8M execs | solved, 2 iters |
| libpng (real lib) | missing intermediate state | `IJON_CMP` | soft CRC roadblock | reached a new frontier handler |

In every case the agent saw only the **answer-stripped** source (a fairness gate
removes the ground-truth annotation and any `ijon` mention) plus the plateau
telemetry, and re-derived a working annotation by reasoning. The two-gate target
is solved by the loop *accumulating two annotations*, self-discovering the
second roadblock behind the first.

See [docs/architecture-design.md](docs/architecture-design.md) for the design
rationale and the failure modes we hit (placement, map saturation, duplicates)
and how they were fixed.

## Layout

```
harness/        deterministic harness + the LLM analyst
  config.py     AflConfig: AFL++ paths and the AFL_PATH=include build quirk
  fuzzer.py     FuzzerController, Snapshot, stats/plot parsing, run_until()
  plateau.py    PlateauDetector (time_wo_finds >= N and pending_favs == 0)
  build.py      Builder, Annotation/apply_annotation, the fairness gate
  model.py      AnalystModel: LiteLLM -> DeepSeek (JSON mode)
  agent.py      propose_annotation: classify + synthesize (structured output)
  loop.py       AnalystLoop: the autonomous keep/revert/retry loop
scripts/
  reproduce_m1.py     deterministic A/B (no LLM): clean plateaus, patched solves
  solve_target_llm.py one autonomous turn on a target
  autonomous.py       the full iterative loop
workspace/<t>/  per-benchmark: src/ (canonical source), in/ (seed)
tests/          unittest suite for the deterministic logic
docs/           the IJON paper + the living architecture/design notes
```

## Setup

- AFL++ with IJON built (`AFL_LLVM_IJON=1` support). Path set in
  `harness/config.py` (`DEFAULT_AFL_ROOT`).
- A DeepSeek API key in `DEEPSEEK_API_KEY` (env or `.env`; see `.env.example`).
- Python venv with LiteLLM:
  ```bash
  python3 -m venv .venv && .venv/bin/pip install litellm
  ```

## Run

```bash
# deterministic A/B sanity (no LLM)
.venv/bin/python scripts/reproduce_m1.py

# one autonomous analyst turn
.venv/bin/python scripts/solve_target_llm.py --workspace workspace/checksum --src checksum-guard.c

# full iterative loop (accumulates annotations across plateaus)
.venv/bin/python scripts/autonomous.py --workspace workspace/twogate --src twogate.c --max-iters 5

# tests
.venv/bin/python -m unittest discover -s tests -v
```

Model defaults to `deepseek/deepseek-v4-pro`; override with `--model` or
`IJON_LLM_MODEL`.

## Status

The agent works end to end and autonomously derives the right annotation on
several targets, covering **two of IJON's three roadblock classes** with three
primitives:

- *Known relevant state values* — `IJON_SET` (maze) and `IJON_MAX` (maxclimb).
- *Missing intermediate state* — `IJON_CMP` (checksum, two-gate, and a real
  libpng frontier).

Also working: the iterative keep/revert/retry loop, source-coverage evaluation
(immune to IJON map inflation), and frontier localization (fuzz-introspector +
llvm-cov) on libpng.

Not yet covered: IJON's third class, *known state changes* (`IJON_STATE`, e.g. a
protocol/message dispatcher), and the `IJON_STRDIST` primitive. A long libpng
campaign to break that target's soft CRC roadblock empirically is also deferred.
See `docs/architecture-design.md`.

## Acknowledgments & provenance

This repository is a fork of
[RUB-SysSec/ijon](https://github.com/RUB-SysSec/ijon), the reference
implementation of **IJON** (Cornelius Aschermann, Sergej Schumilo, Ali Abbasi,
Thorsten Holz — *IJON: Exploring Deep State Spaces via Fuzzing*, IEEE S&P 2020).
This project automates IJON's human-analyst role with an LLM; it **builds
directly on the IJON technique and codebase** and would not exist without it.

- **Inherited from upstream IJON / AFL, unmodified by this project:** the
  original AFL/IJON sources at the repo root (`afl-fuzz.c`, `llvm_mode/`,
  `qemu_mode/`, `test/ijon-maze.c`, etc.) — Copyright Google Inc. and the IJON
  authors, licensed under the Apache License 2.0 (see the per-file headers). The
  agent does not modify these; at runtime it builds targets against AFL++'s IJON
  support, not this in-repo AFL.
- **Original to this project (the LLM-analyst agent):** `harness/`, `scripts/`,
  `workspace/<target>/{src,in}`, `tests/`, `docs/architecture-design.md`, and
  this README.

If you use this work, please also cite the original IJON paper.
