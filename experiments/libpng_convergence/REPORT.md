# libpng convergence — AFL vs AFL+IJON over time (real target, class-2 metric)

**Claim under test.** The autonomous-IJON thesis: a state annotation lets the
fuzzer *keep exploring deeper state over time* in a way plain AFL cannot. We test
it the way the IJON paper does — a metric-over-time A/B — on a real target
(libpng), using the paper's class-2 metric: **distinct chunk-type SEQUENCES**
explored (cf. paper Table IV).

**Setup.** Both arms fuzz the same CRC-disabled libpng read harness from one
minimal seed for 360 s (CRC off so the *only* barrier is chunk sequencing, not
the checksum). Control = plain AFL (`targets/libpng_crc_off`). Treatment = AFL +
IJON with the one-line chunk-sequence annotation
(`chunk_seq_log=(chunk_seq_log<<8)|(chunk_name&0xFF); IJON_SET(ijon_hashint(0,chunk_seq_log))`,
`patches/chunk_seq_log.patch`). We sample the live queue every 20 s.
Reproduce: `python scripts/libpng_convergence.py --budget 360`.

## Result — distinct chunk-sequences over wall-clock

```
 t(s)   plain    ijon
   20      43    2441
   40      43    2639
   80      48    2671
  160      52    2673
  240      52    2674
  320      60    2678
  360      65    2679
```

| metric (final, 360 s) | plain AFL | AFL+IJON | ratio |
|---|---|---|---|
| distinct chunk-**sequences** | 65 | **2679** | **41×** |
| distinct 3-chunk windows | 69 | 1760 | 26× |
| source **functions** covered | 124 | 125 | ~1.0× |

Two things stand out:
1. **IJON pulls ahead almost immediately** (2441 vs 43 by t=20 s) and sustains a
   ~40× lead the whole run. Plain AFL saturates near ~52 sequences for most of the
   campaign — it re-finds the same handful of chunk orderings and cannot escape.
2. **Function coverage is equal (124 vs 125).** This is the *honest, expected*
   outcome per `docs/architecture-design.md` §7b: libpng's deeper functions are
   format-gated (need valid IHDR/ancillary structure), not IJON-unlockable. The
   class-2 win shows on the **state** metric, not the function count — which is
   exactly what IJON's class-2 mechanism targets.

(Consistent with the earlier static-corpus measurement of 53× on 1.6.44; here it
is 41× as a controlled *time-series* on 1.6.58.)

## The autonomous-proposal gap (honest)

Part A asks the model, blind (localized read-path source + plateau telemetry, no
answer), for one annotation. Across runs it proposed **local** signals, never the
sequence:
- `IJON_CMP(chunk_name, png_iCCP)` — class-3, match one specific chunk tag;
- `IJON_SET((png_ptr->transformations & PNG_COMPOSE) != 0)` — class-1, one bitflag.

Both are defensible, but neither is the chunk-*sequence* annotation that produced
the 41×. This is the **same pattern as the TPM comparison** (`../human_vs_llm`):
one shot anchors on the nearest concrete barrier, not the cross-event sequence.

## The design insight (the path to closing autonomy)

The chunk-sequence annotation yields **41× sequences but +0 functions**. So a loop
whose keep/revert is driven by *function coverage* (our current
`scripts/libpng_loop.py`, correct for class 3) would **wrongly revert** the best
class-2 annotation. The reward signal must match the failure class:

| failure class | what "better" means | keep/revert reward |
|---|---|---|
| 3 — missing intermediate state | reach new code | new **functions** covered (`CoverageProbe`) |
| 2 — known state changes | explore more states | **state diversity** (distinct sequences / IJON-map richness) |

This is why class-2 autonomy has not closed on libpng yet, and it is the concrete
fix: drive the loop's keep/revert with a **state-diversity** reward (the distinct
chunk-sequence count here; generically, the fill/entropy of the IJON state map).
With that reward, the loop should KEEP the sequence annotation (41× ⇒ keep) and
REVERT local annotations that don't grow state diversity — closing the
"agent autonomously emits *and validates* the class-2 annotation" loop.

## Takeaways
1. **Mechanism, on a real target, over time: IJON's class-2 annotation gives a
   41× sustained gain in state exploration that plain AFL cannot reach** — the
   thesis, demonstrated in the paper's own evaluation shape.
2. **Function coverage equal** — honestly scoped; the class-2 win is a *state*
   win, not a code-reachability win (§7b).
3. **Autonomy is gated by the reward, not the reasoning**: one-shot proposes local
   annotations; closing the loop needs a class-matched (state-diversity)
   keep/revert signal. Next experiment.
