# libtpms (TPM 2.0) — IJON-LLM bug hunt, end-to-end on a real target

First full deployment of the system on a fresh real target: the agent localizes
+ annotates a stateful library, we build + fuzz. libtpms is the **modern,
maintained vTPM** behind swtpm (real cloud/VM attack surface) — and unlike the
2018 IBM TPM, it builds cleanly on OpenSSL 3.

## Correctness-first harness (the maintainers' own)
A harness bug that crashes can waste the run *and* get rejected by maintainers.
So `workspace/libtpms/src/tpm_seq_fuzzer.c` is **derived from libtpms's own
OSS-Fuzz harness** (`tests/fuzz.cc`): identical callbacks, `TPMLIB_Process`
buffer handling, state suspend/resume round-trip, and `TPM_Free` cleanup —
correct-by-construction. The only change: it feeds the input as a **stream of TPM
commands** (split by the command-size field) so the command-to-command state
machine is exercised. It does NOT assert on functional TPM return codes (those
are expected for arbitrary sequences); real memory bugs are caught by **ASAN**.

## Two annotations — both proposed by the agent, from two localizations
The annotation is the agent's job, never hand-written. We ran it twice:

1. **Class-2 (depth) — harness command-loop localization.**
   `IJON_STATE(ijon_hashmem(0, data, off))` — feed the running command sequence
   into IJON state. Validated in a short A/B: **10.7× more distinct command-code
   sequences** than plain (580 vs 54), not map-saturation.
2. **Class-3 (breadth) — the real FI+coverage frontier.** Full pipeline:
   fuzz-introspector static call graph (1339 fns) + a 3rd llvm-cov libtpms build
   + seed replay → the coverage frontier (`ExecuteCommand` → the session/auth-gated
   uncovered handlers). The agent proposed `IJON_SET(command.index)` in
   `ExecCommand.c` — expose the dispatched command type so the fuzzer reaches all
   handlers (*"edge coverage is blind to which command is executed"*).

**Architectural finding:** our automated FI+cov localizer is **class-3-oriented**
(it surfaces the coverage frontier — reach new code). The **class-2** sequence
site lives in the input-processing *loop* (covered entry code, not a frontier),
so it needs *loop*-localization, not the frontier. Both annotations are valid and
**complementary** (reach all commands × explore their orderings); the hunt uses
both.

## The run
- IJON target: class-2 (`#ifdef _USE_IJON` in the harness) + class-3 (one-line
  patch in `ExecCommand.c`, declared minimally — the libtool build doesn't trigger
  afl-cc's force-include, and `afl-ijon-min.h` clashes with libtpms `config.h`;
  the IJON runtime resolves the symbols at the harness link). ASAN throughout.
- Plain workers: the unpatched ASAN lib, no IJON (truly plain — `AFL_LLVM_IJON`
  must be *unset*, not `=0`).
- Parallel: 1 IJON instance (`-S ijon`) + 1 master + 1 worker (plain) in one sync
  dir `out/hunt`, ASAN, `-x tpm.dict`, 6 seed command sequences, 8 hours.
  IJON cracks state barriers; the plain workers exploit its queue entries.

## Build / reproduce
```
workspace/libtpms/build.sh        # libtpms (ASAN) + plain & ijon harnesses
# FI localizer: workspace/libtpms/fi_proj + fi_out (data.yaml + coverage.json)
# annotations: scripts agent calls (class-2 harness loop, class-3 FI frontier)
```
Deps installed for this (see `docs/installed-system-deps.md`): `libtool`, `gawk`.

## Result — 12h campaign: no bug, but the annotations measurably (and increasingly) worked

A single 12-hour campaign, 3 instances (1 IJON + 2 plain), ~62M executions.
**0 crashes, 0 hangs** — an honest negative on the bug, the expected outcome on a
target that OSS-Fuzz fuzzes continuously.

But both agent-proposed annotations expanded TPM state exploration far beyond
plain AFL — and the gap **widened over time** (snapshots at 8h and 12h):

| IJON vs plain | at 8h | at 12h |
|---|---|---|
| distinct command **sequences** | 4,950 vs 818 (6.0×) | **9,461 vs 820 (11.5×)** |
| distinct command **types** | 1,852 vs 325 (5.7×) | **2,277 vs 325 (7.0×)** |
| IJON corpus size | 13,800 | 20,876 |

The trajectory is the real finding: **plain AFL saturated** — 818→820 distinct
sequences from 8h to 12h, dead flat (it is genuinely stuck, not merely slow) —
while **IJON nearly doubled** (4,950 → 9,461). So the two autonomous annotations
(class-2 sequence *depth* + class-3 command-type *breadth*) don't give a one-shot
boost; their advantage **compounds with time**, exactly what state-guided
exploration should do.

No new memory bug surfaced (libtpms is OSS-Fuzz-hardened; the paper's 18–32× was a
hand-tuned annotation vs weaker 2019 AFL — our ratio is honest, held back partly
by the param-noise tail in the agent's full-prefix hash and a stronger AFL++ plain
baseline). **The reliable outcomes — end-to-end deployment on a real vTPM, two
autonomous complementary annotations, and a state-exploration advantage that grows
to 11.5× — stand; a bug would have been the bonus.**

### Notes
- The 12h was run as 8h + a `-i -` resume of the same sync dir (+4h). AFL++
  *fast-resume* choked on the IJON instance's mild instability (persistent-mode +
  TPM state isn't fully idempotent across loop iterations); resume it with
  `AFL_NO_FASTRESUME=1` (normal re-calibration). Plain instances resume either way.
- Autonomous follow-up that could raise the ratio: feed back the param-noise
  observation so the agent refines the class-2 annotation to command-codes-only
  (less corpus inflation, more focused sequence search) — the libpng-closure pattern.
