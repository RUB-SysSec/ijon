# IJON-LLM — Architecture & Design Notes

> Living design document. Captures *what* we built and *why*, including dead
> ends and the lessons they taught. Intended to seed a later technical
> report / blog post. Keep entries dated and append-only where practical.

Last updated: 2026-06-04

---

## 1. Problem & motivation

[IJON](IJON-ExploringDeepStateSpacesviaFuzzing.pdf) (Aschermann et al., S&P
2020) shows that coverage-guided fuzzers (AFL family) plateau whenever
interesting program behavior lives in **data values** rather than in **which
edges execute**. Edge coverage is then blind, no mutation yields new feedback,
and the campaign stalls. IJON's fix: a human analyst inserts a tiny (1–2 line)
source annotation that reshapes the fuzzer's feedback function to expose the
hidden state. With those hints, AFL solves mazes, Super Mario levels, checksums,
and CGC challenges that defeat every automated tool.

**The bottleneck is the human.** The analyst must read the code, read the
fuzzer's progress, diagnose *why* it is stuck, and choose the right annotation
and placement. The IJON authors themselves flag automating this as future work.

**Our thesis:** an LLM agent can do the analyst's job. The task is mostly *code
comprehension + reading fuzzer telemetry* — an LLM's strength — and demands
little security expertise (the IJON authors note they "typically had very
limited understanding of the target application").

## 2. Core idea

An autonomous loop that:

1. runs an AFL++ campaign until it **plateaus**,
2. asks an LLM ("the analyst") *why* it is stuck and *what* annotation to add,
3. applies the annotation, rebuilds, and re-runs,
4. **evaluates** the result (kept if it helped, reverted+retried if not),
5. repeats, accumulating annotations, until the goal is reached or budget runs out.

### Design principle — model only for judgment

(Following the project's coding rules.) The LLM is invoked **only at the two
genuine judgment points**: *classify why-stuck* and *synthesize the
annotation*. Everything mechanical — running the fuzzer, detecting the plateau,
patching source, rebuilding, measuring coverage deltas, keep/revert decisions —
is deterministic Python. Code does what code can; the model does analyst
reasoning. This keeps the system debuggable, cheap (~1–7k tokens / decision),
and the model's contribution auditable.

## 3. Key decisions & rationale

| Decision | Choice | Why |
|---|---|---|
| Fuzzer substrate | **AFL++** (customized IJON fork already on the machine) | IJON built in (`AFL_LLVM_IJON=1`), actively maintained; binary-only (Ghidra) is a later goal |
| Agent framework | **Raw LiteLLM + own loop** (not Google ADK) | Our control flow is a deterministic loop with 2 LLM calls; ADK's orchestration would fight that. LiteLLM gives DeepSeek + JSON mode with us owning the loop |
| Model | **DeepSeek V4 Pro** (`deepseek/deepseek-v4-pro`) | A *reasoning* model (separate `reasoning_content`; supports JSON mode; needs large `max_tokens`). Decisively better than `v4-flash` at placement/primitive judgment — see §8.5. ~24 s/call. Swappable via `--model` / `IJON_LLM_MODEL` |
| Autonomy | **Fully autonomous loop** | The goal is to remove the human entirely |
| Telemetry to the model | source + coverage/plateau stats (+ Binary Ninja decompile later) | What a human analyst sees; source for "why", bitmap for "stuck", CFG for "where" (future) |
| Validation strategy | **Ground-truth A/B first, then agent** | Each target ships a `#ifdef`-gated correct annotation; we confirm plain-plateaus / IJON-solves, then make the agent re-derive it. Falsifiable |

## 4. System components (`harness/`, stdlib + LiteLLM only)

- **`config.py`** — `AflConfig`: paths and build/run env. Encodes the one
  non-obvious build fact: `AFL_PATH` must point at `$AFL_ROOT/include` so the
  afl-cc wrapper can force-include `afl-ijon-min.h`; otherwise `IJON_*` macros
  silently compile out and the binary is identical to a plain build.
- **`fuzzer.py`** — `FuzzerController` (launch/stop `afl-fuzz`, parse
  `fuzzer_stats`/`plot_data`), `Snapshot` (typed view + `solved`/`edges_found`/
  `time_wo_finds`/`pending_favs`), `run_until(predicate, timeout)`.
- **`plateau.py`** — `PlateauDetector`: plateau ⇔ `time_wo_finds ≥ N`
  ∧ `pending_favs == 0` (AFL's own "nothing new, nothing favored left" signal).
- **`build.py`** — `Builder.compile` (the `AFL_PATH`+`AFL_LLVM_IJON` invocation);
  `Annotation` + `apply_annotation` (insert one C statement after an anchor
  line); `strip_ijon_blocks` / `redact_ijon_hints` / `make_clean_source` (the
  fairness gate — see §6).
- **`model.py`** — `AnalystModel`: LiteLLM → DeepSeek in JSON mode, key loaded
  from env or a sibling project's `.env` (no secret duplication), with a
  parse-retry.
- **`agent.py`** — `propose_annotation`: builds the prompt (IJON primitive
  reference + the paper's 3-class roadblock taxonomy + clean source + plateau
  telemetry + feedback from failed attempts), calls the model, validates the
  structured output into an `Annotation` + diagnosis.
- **`loop.py`** — `AnalystLoop`: the autonomous outer loop (§5).

Scripts: `reproduce_m1.py` (deterministic A/B), `solve_target_llm.py` (one
autonomous turn on any target), `autonomous.py` (the full iterative loop).

## 5. The autonomous loop (`AnalystLoop`)

```
clean = make_clean_source(original)            # answer stripped + leak-gated
baseline = fuzz(clean) until plateau
repeat up to max_iters:
    proposal = analyst(working_source, baseline, history_of_failures)
    if proposal already present in source -> reject (dedup), feed back, continue
    patched = apply(working_source, proposal)
    after = fuzz(patched) until solved-or-plateau
    verdict = classify(baseline, after):
        solved     -> success (return)
        advanced   -> KEEP (working=patched, baseline=after)
        saturated  -> REVERT + feed back "map flooded, use IJON_CMP not IJON_SET"
        stalled    -> REVERT + feed back "did not execute / not useful"
```

Annotations **accumulate**: a kept annotation stays in the working source, so
the next plateau is diagnosed against partially-annotated code. Reverted
attempts are fed back to the model so it does not repeat them.

## 6. Fairness gate (scientific integrity)

Each benchmark source ships the ground-truth annotation behind `#ifdef`. To
test the agent *fairly* it must never see that answer. `make_clean_source` =
`strip_ijon_blocks` (remove `#ifdef IJON_* … #endif`) + `redact_ijon_hints`
(drop any residual line mentioning `ijon`, e.g. leaky comments or helper
prototypes) + a **hard assertion** that no `ijon` token survives.

*Why this exists:* our first checksum run "passed", but the source's top comment
literally named `IJON_CMP` as the fix — the model could read the answer off a
comment. We caught it, redacted, and re-ran. The model still knows the IJON
**API and taxonomy** (legitimately, from its own prompt); it must not be handed
the **specific answer** by the target.

## 7. Empirical results

| Milestone | Target | Failure class (agent) | Primitive | Plain AFL | Agent result |
|---|---|---|---|---|---|
| M1 | maze (ground truth) | — | `IJON_SET` | stuck @16 edges | solved 6 s |
| M3 | maze (agent, leak-free) | known_relevant_state_values | `IJON_SET` | stuck | **solved 8 s** |
| M5 | checksum (agent, leak-free) | missing_intermediate_state | `IJON_CMP` | 16.3M execs, 0 solves | **solved 1 s** |
| M6 | two-gate (autonomous loop) | missing_intermediate_state ×2 | `IJON_CMP` ×2 | — | **solved, 2 iters** (v4-pro) |
| M7 | maxclimb (synthetic IJON_MAX) | known_relevant_state_values | `IJON_MAX` | 0 crashes/16.8M execs | **solved, 2 iters** |
| M7 | dmg2img (real, IJON_MAX) | — | `IJON_MAX` (ground truth) | finds crash in 154s | honest negative — see §below |

M6 detail: the loop autonomously kept `IJON_CMP(stored, actual)` (gate 1), then
diagnosed the second gate behind it and added `IJON_CMP(tag, 0xC0FFEE42)`
**staged after gate 1's return** — two accumulated annotations, no human input.
Ground-truth (both annotations) solves in 4 s.

Plateau signal that drives the loop: flat `plot_data` + `pending_favs == 0`
(AFL writes telemetry ~60 s into a quiet run, so detection granularity ≈ that).

### First real target — libpng (M4 step 1)

Built libpng 1.6.44 + zlib 1.3.1 instrumented with AFL++ (`workspace/libpng/`,
`build.sh`), using the OSS-Fuzz read harness with the CRC action flipped from
`PNG_CRC_QUIET_USE` to `PNG_CRC_ERROR_QUIT` so the per-chunk CRC-32 is enforced
as a roadblock. Baseline A/B (single 1-bit seed, ~200 s, plain AFL coverage):

| | CRC enforced (roadblock) | CRC disabled (contrast) |
|---|---|---|
| edges | 936 | 1125 |
| corpus | 395 | 839 (2×) |

The CRC is a **real but *soft* roadblock**: CRC-on still reaches ~83 % of
CRC-off's edges because libpng **parses each chunk before verifying its CRC**
(`png_handle_*` then `png_crc_finish`), so AFL exercises mutated chunks'
parse-branches before the CRC aborts them. What CRC blocks is the deeper,
multi-chunk, valid-input decode (the 2× corpus / ~189-edge gap, which should
widen over a long campaign). Implication: libpng's value to the project is
primarily as a **localization testbed** — the agent must find the CRC compare
among hundreds of functions — not as a high-drama roadblock. The ~189-edge / 2×
gap is still well above the loop's keep threshold, so an `IJON_CMP`-on-CRC
annotation would register as clear progress.

### Localizer — fuzz-introspector (M4 step 2)

We stand up fuzz-introspector as a *separate* helper to localize roadblocks on
multi-function targets (reachability + reachable-but-uncovered + blocker
ranking). The full C/C++ frontend (`frontends/llvm`) needs a patched clang + LTO
pass (flagged as painful in a 2022 internal eval, and impractical here: no
docker, LLVM-from-source). Instead we use FI's **tree-sitter frontend** (`main.py
full --language c++`, no compilation) from the cloned repo in a dedicated
`.venv-fi` (the PyPI package fails — pulls `atheris`; the repo `requirements.txt`
does not). Gotcha: FI's `EXCLUDE_DIRECTORIES` includes the substring `build`, so a
target path under any `build/` dir is silently skipped — stage sources at a clean
path (`workspace/libpng/fi_proj/`). See memory: fuzz-introspector-setup.

Status: FI's static pass runs and correctly maps the call graph from our CRC
harness into libpng — it sees `png_crc_finish`/`png_crc_read` and the
`png_handle_*`/`png_read_*` chain. **But** `branch-blockers.json` is empty
without coverage: the blocker / reachable-but-uncovered analysis — the actual
*frontier* signal — needs a runtime-coverage overlay. **Step 3 (done):** rather than feed coverage back through FI's finicky overlay,
we use each tool for its strength and join them ourselves (`harness/localize.py`).

Coverage is unavoidable and source-anonymous in AFL (the bitmap is hashed edge
IDs), so source coverage must come from replaying the corpus through a coverage
build. We build an `llvm-cov` variant (`cov-build.sh`: clean clang, no AFL,
`-fprofile-instr-generate -fcoverage-mapping`, libFuzzer driver), replay the
`crc_on` AFL corpus (463 inputs) through it, and `llvm-cov export` to JSON.
(`AFLplusplus/cov-analysis` automates this same replay; we did it directly.)

`localize.py` then joins FI's static `data.yaml` (callsites with source
locations, reachable-set, cyclomatic complexity) with the llvm-cov counts to
compute the **coverage frontier**: a COVERED caller whose call to a
statically-REACHABLE callee is never covered, ranked by the downstream
uncovered complexity it gates. On libpng this narrows 654 functions to the right
handful:

```
Frontier (reachable but UNCOVERED): png_handle_iCCP/iTXt/sCAL/zTXt/pCAL/tEXt/sPLT
  — the chunk handlers, all dispatched from png_read_info, none covered
Hot gate (executed a lot, callee uncovered): png_calculate_crc … 2713x
```

i.e. the chunk handlers are the unreached frontier and the CRC is the hot gate
the fuzzer can't pass — exactly the localization an analyst needs, computed
mechanically. This "static graph + replayed coverage → frontier" pattern is
tool-agnostic and ports to Ghidra/Binja for the binary-only future. ### Step 4 — agent-on-libpng using the localizer (done, with a caveat)

`localize.py` now also surfaces the **gate** (a covered function the uncovered
frontier handlers commonly call — `png_crc_finish`, found via `common_gates`),
expands one level to the actual check (`png_crc_error`), slices those functions'
source (`extract_function_source` / `build_localization_context`), and the agent
prompt grew a `localization` slot. Fed only those ~10 focused functions (not all
of libpng), v4-pro **correctly localized and annotated**: failure class
`missing_intermediate_state`, *"without a valid CRC, code like png_handle_iCCP
is never reached"* (it used the frontier hint), and `IJON_CMP(crc, png_ptr->crc)`
placed after `crc = png_get_uint_32(crc_bytes);` in `png_crc_error` — exactly the
right primitive, function, and placement. We patched `pngrutil.c`, rebuilt
AFL+IJON (verified the header was force-included and the annotation is live), and
fuzzed.

**Honest outcome:** in a 200 s run the annotation produced **no real coverage
gain** — source-coverage replay shows 132 vs 133 functions covered, frontier
handlers still uncovered. Two takeaways:
1. **Raw AFL edges lied:** they rose to 1269 (above the 1125 CRC-off ceiling),
   inflated by IJON map entries; only the source-coverage replay revealed the
   handlers are still dark. This is concrete proof that keep/revert on real
   targets must use *source* coverage, not `edges_found` (cf. §8.4).
2. **It's a throughput, not an agent, problem:** the frontier handlers sit 2+
   CRC-32s deep from the minimal seed (pass IHDR's CRC, then synthesize a new
   chunk with a valid CRC); 200 s of slow libpng execs can't grind that, and
   libpng-CRC is a soft long-timescale roadblock anyway. The agent's job — the
   novel part: autonomous localization + correct annotation in a 30k-LOC library
   — succeeded. That IJON_CMP cracks a 32-bit checksum was already proven
   definitively on the toy (M5/M6).

Open follow-ups: a long (hours) libpng campaign to show the break empirically;
and the cleaner `IJON_MAX` demo on dmg2img.

### Source-coverage keep/revert, validated in-loop on libpng

`harness/coverage.py` `CoverageProbe` replays a corpus through the fixed
llvm-cov build (no IJON/AFL instrumentation) and returns the REAL covered
function set; `AnalystLoop` takes an optional `coverage_probe` and, when set,
`_classify` decides keep/revert on real-coverage delta instead of `edges_found`.
`scripts/libpng_loop.py` runs this coverage-driven loop on libpng (multi-file:
localize → agent → patch the right .c → rebuild AFL+IJON → fuzz → measure real
coverage → keep/revert with feedback).

Result (2 iters): the agent did NOT grind the CRC — from the localization
(frontier = `png_handle_iCCP`) it reasoned the cheaper route is to match the
chunk *name*, emitting `IJON_CMP(chunk_name, png_iCCP)`. The handler is entered
once the name matches (before the CRC check), so this **covered 2 new real
functions** (`png_handle_iCCP`, `png_inflate_read`) → coverage-driven KEEP. The
near-duplicate retry added no real coverage (only IJON edges) → correctly
REVERTED. So the metric works both ways in-loop, and the agent broke a real
frontier on libpng. Caveat: short-window fuzzing has ±2-function coverage noise,
so the set-difference metric is jittery; a production version should smooth it
(repeat/threshold) — the named deep functions make this instance a real win.

### The third class — IJON_MAX (M7)

We closed the value-maximization class two ways:

- **dmg2img (real target), honest negative.** Built dmg2img with AFL+ASAN
  (`workspace/dmg2img`; needed a fetched `bzlib.h` + linking `libbz2.so.1.0`),
  crafted a valid minimal DMG seed (512-byte big-endian koly trailer at EOF with
  `koly` sig + nonzero XMLOffset/XMLLength; the plist must contain
  `<plist version="1.0">`/`<key>blkx</key>`/`</array>`/`</plist>` to avoid a
  spurious NULL-deref at `dmg2img.c:245`). The overflow fires exactly at the
  paper's `dmg2img.c:240` (`plist[XMLLength]='\0'` with `XMLLength=UINT64_MAX`).
  **But plain AFL finds the crash in 154 s** — it just sets the 8 `XMLLength`
  bytes to `0xFF` (a standard AFL interesting-value) while the trivial koly stays
  intact. So this stripped-down setup is *not* a roadblock and doesn't
  demonstrate `IJON_MAX` necessity (the paper's difficulty was harder real DMG
  inputs / the CGC env). We did not fake a plateau.

- **maxclimb (synthetic), the clean demo.** A target whose score = how many input
  positions match a per-position pseudo-random expected byte; goal = score ≥ 32.
  The score has no coverage gradient (same compare every position) and the bytes
  can't be guessed in bulk, so **plain AFL plateaus (0 crashes in 16.8M execs)**;
  `IJON_MAX(score)` solves in <1 s. First design used a *contiguous* prefix and
  even the IJON ground truth stalled — multi-byte havoc corrupts a fragile
  prefix; the fix is a **non-contiguous match count** (adding a match doesn't
  require preserving a prefix), which climbs robustly. The autonomous loop solved
  it in 2 iters: the agent first tried `IJON_CMP(score, TARGET)` (kept as partial
  progress), then landed on the correct `IJON_MAX(score)`.

All three IJON roadblock classes are now demonstrated on real roadblocks with the
agent deriving the annotation autonomously: `IJON_SET` (maze), `IJON_CMP`
(checksum + libpng frontier), `IJON_MAX` (maxclimb).

## 8. Findings & lessons (the interesting part)

These are failures the agent/loop hit, and the general fixes they motivated —
each is a faithful instance of a difficulty the IJON paper itself notes.

1. **Diagnosis is easy; placement is hard.** (M5) The model correctly diagnosed
   the checksum and chose `IJON_CMP`, but placed it *inside* the `if (stored ==
   actual)` success branch — code that runs only once the goal is already
   reached → zero gradient. *Fix:* explicit placement rules in the prompt (the
   annotation must run on every execution, before/outside the gate it helps
   pass). *Deeper point:* this is why a verifying loop is needed — a single shot
   cannot catch a non-executing annotation.

2. **Map saturation masquerades as progress.** (M6) For a 32-bit `tag == const`
   gate the model chose `IJON_SET(tag)`, which creates one map entry per
   distinct value → bitmap flooded (edges 43 → 57,502). The naive "more edges =
   better" metric *kept* it, and the noise then poisoned evaluation of the
   correct later annotation. *Fixes:* (a) prompt — `IJON_SET`/`INC` only for
   small bounded state; use `IJON_CMP` to match a wide value; (b) metric — a
   huge edge jump is flagged as **saturation (harmful) and reverted**, not
   progress. This is the paper's own "virtual state too large overwhelms the
   fuzzer" caveat, surfaced autonomously.

3. **The model re-proposes annotations already applied.** (M6) *Fix:* dedup —
   reject an annotation already present in the working source and feed back
   "find the NEXT roadblock"; plus prompt note that existing `IJON_*` calls are
   prior working steps.

4. **`edges_found` conflates real coverage with IJON map entries.** Open
   problem: our progress metric is a heuristic (margin + saturation cap). A
   cleaner signal would read only the real-coverage region of the shared bitmap,
   separate from the IJON set/max regions. Tracked for later.

5. **Model capability gates *placement*, not diagnosis.** On the two-gate
   target, `v4-flash` chose the right primitives but placed the second
   `IJON_CMP` *before* gate 1 — a joint (checksum × tag) search that didn't
   converge in the eval window; the loop then dead-ended on duplicates.
   `v4-pro` (a reasoning model) instead reasoned, unprompted, that the tag
   annotation should run "only when the checksum is already correct (avoiding
   noise from invalid payloads)" and placed it *after* gate 1's return —
   staged, matching ground truth — solving in 2 iterations. The upgrade was the
   real fix; we did **not** add a placement-staging prompt hack. Lesson: for the
   judgment points, model strength buys correct *placement* reasoning that
   prompt rules struggle to encode generally. (v4-pro needs large `max_tokens`
   because hidden reasoning consumes output tokens.)

## 9. Open problems / next

- **Robust progress metric** separating real edges from IJON entries (§8.4).
- **Frontier localization (M4):** on a multi-function target, mechanically
  identify *where* the fuzzer is stuck (covered edges whose CFG successors are
  never taken) to point the model at the right code — via Binary Ninja now,
  Ghidra for binary-only later.
- **Third failure class:** a protocol/message dispatcher needing `IJON_STATE`
  (known_state_changes) — not yet exercised.
- **Reverting a previously-kept harmful annotation** when later evidence shows
  it hurt (currently we only avoid keeping it).
- **Feeding solving inputs back** into a long-running campaign rather than fresh
  runs per iteration.

## 10. Reproduction

```bash
# one autonomous turn on a target (strip→plateau→annotate→rebuild→re-fuzz)
.venv/bin/python scripts/solve_target_llm.py --workspace workspace/checksum --src checksum-guard.c
# full iterative loop
.venv/bin/python scripts/autonomous.py --workspace workspace/twogate --src twogate.c --max-iters 5
```
Prereqks: AFL++ with IJON at `$AFL_ROOT` (see `harness/config.py`),
`DEEPSEEK_API_KEY` in env or `.env`, `.venv` with `litellm`.
```
```
