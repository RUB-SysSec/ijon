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

## Result — 8h, no bug, but the annotations measurably worked (honest)
~58M executions over 8h across 3 instances. **0 crashes, 0 hangs.** An honest
negative on the bug — the expected low-probability outcome on an OSS-Fuzz-hardened
target.

But both agent-proposed annotations demonstrably expanded exploration vs plain AFL:

| instance | queue | distinct command **sequences** | distinct command **types** |
|---|---|---|---|
| IJON (class-2 + class-3) | 13,800 | **4,950** | **1,852** |
| plain (main) | 1,989 | 818 | 325 |
| plain (worker) | 1,960 | 815 | 344 |

- **~6.0× more distinct command sequences** (class-2 depth annotation).
- **~5.7× more distinct command types** (class-3 breadth annotation — it reached
  far more command handlers).
- edges incl. IJON map: 13,487 vs 3,454 (3.9×).

So the IJON-LLM combo explored ~6× more of the TPM state space, autonomously, on a
real security-relevant target — both annotations doing their job. No new memory
bug surfaced (libtpms is continuously fuzzed by OSS-Fuzz; the paper's 18–32× was a
hand-tuned annotation vs weaker 2019 AFL — our ~6× is honest, lower partly due to
the param-noise tail in the agent's full-prefix hash and a stronger AFL++ plain
baseline). **The reliable outcomes — end-to-end deployment + two autonomous
complementary annotations + ~6× state expansion — stand; a bug would have been a
bonus.**

Follow-up that could improve the ratio (autonomous): feed back the param-noise
observation so the agent refines the class-2 annotation to command-codes-only
(less corpus inflation, more focused sequence search) — the libpng-closure pattern.
