# P11 pilot memo — first benchmark test and scoring calibration

- Date: 2026-11-07 (P11). Dev split only: 3 R385 pilots (hs01 motor mount, dr02 wobble plate,
  hs04 pump head), 1 replicate, claude-opus-5-5 and claude-sonnet-5-5, harness level H3.
- Experiment: `orchestrator/experiments/pilot.yaml` (60 runs) plus `pilotgtfix` (the 4 hs04
  build123d Track T cells re-run on the fixed GT plan). Figures are CLI list-price estimates
  (logged-in subscription account, not an invoice).
- Status: **58 of 64 runs scored.** The 6 Track E strategy-D × nx@v2 runs are still queued. The
  addendum will update the D-on-NX row, the B vs D comparison (§3.3), the D-cap finding (§3.3,
  §4.1) and the Track E counts.
- Proposed rule changes: [ADR 0005](../../docs/adr/0005-p11-pass-rule-calibration.md)
  (status *proposed*). **No threshold has been changed in code.**
- **What this memo includes:** aggregates plus a few GT-derived summary values (genus counts,
  one face-area class, deviation sizes).
- **What stays private:** GT geometry, GT plans, key-dim definitions, candidate STEPs and the
  per-candidate tables. They are on the evaluator host under `p11/calib/`.

## 1. What ran, what it cost

| Stage | Runs | Cost (list price) | Expected (PILOT_ESTIMATE §4 per-run rates) | Time |
|---|---|---|---|---|
| Canary (separate run, not in the totals below) | 1 | $0.45 | $2.08 | — |
| Stage 1: Track T build123d (oracle × {build123d@v1, none@build123d}) | 12 | $14.75 | $32.6 | 1.5 h |
| Stage 2 + hs04 reruns (scored so far) | 46 | $108.70 | about $241 for these 46 (about $297 for all 52) | 5.6 h elapsed, 8.8 h of run time summed over both lanes, 2 interruptions |
| **Total scored** | **58** | **$123.45** | about $273 | — |

- **Cost came in at 45 % of the expected estimate overall, but it varies widely by cell:**

  | Cell | Actual vs expected |
  |---|---|
  | Opus Track E | 31–43 % (per run $2.59–6.85 vs about $12.3 expected) |
  | Sonnet Track E | 8–17 % (per run $0.44–1.50 vs about $6.1) |
  | NX Track T | 69–132 %; nx@v2 is above the estimate for both models |

  The estimate overstates build123d and Track E runs and understates NX Track T.
- **The cost guard never bound.** The owner's stop rule for P11 is the running total at
  expected + 50 % ($449 for this queue), and the spend stayed far below it. One in-flight run
  (E_D × nx@v2 × Opus × hs01) was killed by a host restart. Its partial cost is not recorded,
  and its retry is among the 6 queued runs.
- **Infrastructure faults:**
  - Session and host restarts stopped the owner-side queue server twice. One finished NX run was
    recovered from its run dir; one in-flight run was lost.
  - The services now run as detached processes. A queue server that survives the desktop
    session is a P12 item.

## 2. Results

`mean` is over parts × models; S1–S4 maxima are 40 / 25 / 20 / 10; plan = plan_score (0–100).

### Track T (oracle executes the GT plan)

| Skillset | Runs | Gate ok | PASS (v1) | Mean total | Mean S1 | Mean S3 | Mean plan | Cost |
|---|---|---|---|---|---|---|---|---|
| build123d@v1 | 6 | 4 | 4¹ | 62.4 | 26.7 | 12.5 | 98.7 | $9.22 |
| none@build123d | 6 | 4 | 4 | 62.0 | 26.7 | 12.4 | 98.7 | $5.53 |
| nx@v1 | 6 | 6 | 6 | 92.6 | 40.0 | 17.7 | 99.0 | $15.06 |
| nx@v2 | 6 | 6 | 6 | 92.6 | 40.0 | 17.7 | 99.0 | $21.99 |
| none@nx | 6 | 6 | 6 | 92.7 | 40.0 | 17.9 | 99.0 | $13.30 |
| build123d@v1, hs04 on fixed GT (`pilotgtfix`) | 2 | 2 | 2¹ | 91.0 | 40.0 | 18.0 | 97.0 | $2.44 |
| none@build123d, hs04 on fixed GT (`pilotgtfix`) | 2 | 1 | 1 | 46.3 | 20.0 | 9.0 | 97.0 | $2.19 |

¹ Each of these rows contains one build that PASSes under v1 but is defective (§4.1):
- the Opus hs04 build that omitted the suction throat and collar, total 92.4;
- the pilotgtfix Sonnet hs04 build with extra geometry in the throat region, total 88.9, b1 9 vs 6.

- **Track T separates only at the gate, and that is a scoring gap.** Every gate-passing Track T
  run scored 88.9–94.9 and PASSed under v1, including the 2 defective builds above.
- **The totals of the other passing runs differ by small amounts:** S3 key-dim artefacts (§4.3),
  S4 and, on two runs with r > 1, S2 (23.1 and 24.7).
- **The other build123d Track T failures had three causes:**
  - **GT plan bug (fixed).** The hs04 GT plan, followed literally, gives a part in two pieces: its
    suction throat and collar ended below the plate. It also had a missing pad trim, a C-ring
    opening / delivery-bore start that was too high, and sketch line ends rounded to 4 dp that
    left wires open on dr02 and hs04 (ReCADBench-gt #7, each item checked against gt.step).
    - This caused 3 of the 4 Stage 1 failures.
    - The pilotgtfix reruns pass 3 of 4 under v1, and 2 of 4 once the extra-geometry defect is
      counted.
  - **Real model failures.** Sonnet × build123d@v1 × dr02 built an inconsistent solid (its volume in
    memory was a fraction of its re-imported volume), and finish correctly refused it. One
    pilotgtfix Sonnet run used a construct that cannot be traced to a feature, and the gate
    failed it.
  - **NX harness bug (fixed, ReCADBench #18).** All 6 NX dr02 Track T runs first failed finish with
    "STEP read-back mismatch".
    - Parasolid and OCCT volumes of the spherical dome differ by 3e-5..1e-4, and the adapter's
      tolerance was 1e-5. It is now 1e-3, the same as the Solidcraft adapter.
    - The runs were re-finished without new model calls, and all 6 pass.

### Track E (full reverse engineering from the scan)

| Strategy × skillset | Model | Runs | Gate ok | PASS | Mean total | S1 | S2 | S3 | S4 | Plan | Cost |
|---|---|---|---|---|---|---|---|---|---|---|---|
| B × build123d@v1 | Opus | 3 | 3 | 0 | 78.3 | 38.9 | 25.0 | 6.5 | 7.95 | 62.9 | $11.29 |
| B × build123d@v1 | Sonnet | 3 | 3 | 0 | 19.6 | 5.6 | 8.3 | 3.1 | 2.60 | 34.1 | $1.48 |
| B × nx@v2 | Opus | 3 | 3 | 0 | 74.6 | 37.2 | 24.2 | 5.3 | 7.93 | 55.3 | $16.10 |
| B × nx@v2 | Sonnet | 3 | 3 | 0 | 23.1 | 9.4 | 7.5 | 3.5 | 2.66 | 38.8 | $3.14 |
| D × build123d@v1 | Opus | 3 | 3 | 0 | 74.2 | 38.4 | 21.4 | 6.5 | 7.93 | 62.5 | $14.13 |
| D × build123d@v1 | Sonnet | 3 | 2 | 0 | 33.9 | 14.5 | 13.3 | 2.4 | 3.64 | 44.6 | $1.72 |
| D × nx@v2 | both | 6 | — | — | pending | | | | | | |

- **No Track E run passes (0/18 scored).**
- **Opus Track E reaches 62–83 against about 93 for the oracle.** Most of the gap is S3: 6.1 over
  all Opus E runs (6.5 on build123d), against 18.2 for the Opus build123d@v1 oracle.

### Track S (plan only, strategy B)

| Model | Runs | Mean plan_score | Range | Cost |
|---|---|---|---|---|
| Opus | 3 | 52.0 | 34.8–70.8 | $5.38 |
| Sonnet | 3 | 27.9 | 19.4–32.5 | $0.47 |

- **Track E plans scored higher than Track S plans.**
  - Opus: 55.3–62.9 (cell means) against 52.0 for Track S, whose range is 34.8–70.8 (n = 3).
  - Sonnet: 34.1–44.6 against 27.9.
- **This is consistent with execution feedback improving the plan, but it is not evidence of an
  effect.** Each cell has n = 3, Track E grades the final plan after several revisions while
  Track S grades one plan, and Track S ran strategy B only.

## 3. Answers to the P11 questions

### 3.1 Do S1 bands, the S2 curve, the gate and PASS separate good from bad?

| Component | Separates? | Evidence |
|---|---|---|
| Gate (G0) | **Yes**, on gross failures | It catches 2-solid builds, internal voids (b2), non-parametric bodies and broken exports. Before the fixes it also caught a GT-data bug and an NX harness bug, both false alarms that are now fixed. |
| S1 deviation | **Only for whole-part errors** | See the list below the table. |
| S2 parsimony | **Weakly** | Track T r is 0.83–1.33. Gated Track E r is 0.25–1.00 (median 0.67, n = 17). The 1.5 upper bound never binds. The 0.5 lower bound let a D run with fewer than half the GT features pass the r clause. |
| S3 key dims | **Size-blind** | One wrong dim costs a flat 16 / n points (2.0 or 1.6), whether the error is 1.5× or 3× the tolerance. |
| PASS | **No** | Under v1, 46 of 91 defective candidates PASS (§4.2). |

**S1 in detail:**

- **What it catches:**
  - On hs01, a uniform +0.1 mm skin (S1 30.6 at k = 1.0) and a 1 % scale error (S1 17.1). The
    skin could not be built on dr02 or hs04.
  - Through the p_wide clause, one plugged dr02 arm hole and both plugged hs01 holes.
- **What it misses:**
  - On dr02 and hs04, a 1 % scale error keeps S1 40 at k = 1.0 and fails only on key dims.
  - A missing feature a few mm tall: an Opus hs04 Track T build got S1 40/40.
  - One plugged hs01 hole, and 1–2 plugged hs04 holes.
- **Why:** one small feature costs 0.2–1.1 % of samples against the 1 % budget, and the
  observability mask hides 17–85 % of a feature's samples.
- **k never changes PASS on any of the 144 candidates.** No k-sensitive candidate is decided by S1
  alone, so this does not show that k is irrelevant to PASS.

### 3.2 Is the skillset delta (v1 − none) visible per backend?

None of this follows the 03_SCORING §5 protocol (3 replicates, part-clustered bootstrap, Wilcoxon /
Holm). It is descriptive only.

- **NX, Track T: no delta is measurable.**
  - nx@v1, nx@v2 and none@nx all pass 6/6 with mean totals 92.6–92.7. The geometry is the same in
    every cell except none@nx × Sonnet × hs01 (94.6).
  - Track T on NX is at ceiling, so it cannot show a skillset effect on score.
  - **The skillset does change behaviour.** Skillset runs take 6–17 main-loop turns, against
    53–103 for none@nx, yet cost more: nx@v2 $21.99, nx@v1 $15.06, none@nx $13.30 for 6 runs
    each, with per-run spread about 5×.
  - **nx@v1 vs nx@v2** (the `_shared/` files): the only visible difference is cost (+46 %), which
    is within run-to-run spread.
- **build123d, Track T: no conclusion possible.** With the hs04 GT bug excluded:

  | Skillset | dr02 / hs01 | Fixed hs04 | Good builds only |
  |---|---|---|---|
  | build123d@v1 | 3 of 4 (Sonnet dr02 failed the gate) | 2 of 2, one of them the extra-geometry defect | 4 of 6 |
  | none@build123d | 4 of 4 | 1 of 2 | 5 of 6 |

  At n = 1 per cell neither direction means anything.
- **Track E:** no none@ cell exists in the pilot, so the delta cannot be measured there. It has to
  be measured on Track E with replicates (P12).

### 3.3 Track E strategies B and D vs oracle

| Opus, 3 parts, build123d@v1 | Mean total | Mean S3 | Mean plan | Cost | PASS |
|---|---|---|---|---|---|
| Oracle (Track T) | 93.1 | 18.2 | 98.7 | $6.88 | 3/3 (1 of them the throat-omission defect) |
| B | 78.3 | 6.5 | 62.9 | $11.29 | 0/3 |
| D | 74.2 | 6.5 | 62.5 | $14.13 | 0/3 |

- **B and D are close.** B is slightly ahead on build123d, and B on NX is 74.6. D costs more and
  does not score higher. This is n = 3 per cell, and D × nx@v2 is pending.
- **Every deviation-driven refinement helped.** The intermediate revisions were rebuilt from the
  transcripts and rescored; the final rebuilds reproduce the official totals exactly. The mean
  gain per geometric revision is +5.8, with steps of +1.7 to +15.7.
- **The D refine cap (3 here, 6 in 04_STRATEGIES) was not binding in the 6 build123d D runs.**
  - Opus D used 1, 2 and 2 of its 3 loops. Each run stopped on its own "accept with limitations"
    with 72–77 % of its turns and 64–68 % of its wall time left.
  - Sonnet D made no self_deviation call and ended without verifying.
  - This does not show that 3 is better than 6. The cap decision waits for the D × nx@v2 runs.
- **What actually stopped Opus D:**
  - **The accept rule.** On hs04, D was still improving (+1.7 on its last loop) when it accepted
    with its own tight-band share at 48 % against a 95 % target.
  - **The hard feature budget.** It declined to add a feature because of its own feature
    estimate, about half the GT count.
- **Recommendation** (after the NX D runs):
  - Keep `max_refine_iters: 3` for v1 and correct 04_STRATEGIES to match (docs PR).
  - Fix D's accept rule and make its feature budget soft (strategies repo, P12).
  - Count refine loops by verify rounds, not by plan files: the gate forces a new plan revision
    on every CAD change (22 plan files, 7 geometric revisions).

### 3.4 Failure modes (counts over scored runs)

| Failure mode | Runs | Where |
|---|---|---|
| Key dims below the PASS threshold | 17/17 gated E | See the note below the table. |
| S1 targets missed | 7/11 gated build123d E; all Sonnet E | hs04 p_tight 0.949–0.962 against band 0.52 mm |
| Sonnet stops early without verifying | 6/6 Sonnet E (build123d) | 0 self_deviation calls, 16–25 turns, $0.44–0.63. 5 of 6 never exported and relied on finish. |
| Under-featuring (r < 0.7) | 9/17 gated E, all 3 parts | — |
| Self-accepting below its own target | 3/3 Opus D | — |
| Gate: invalid or ambiguous solid | 4 (2-solid hs04 ×2 before the GT fix, b2 void, inconsistent solid) | — |
| Gate: untraceable construct (non-parametric) | 2 (Track T, no skillset) | — |
| Harness / data | GT plan (hs04, dr02), NX read-back tolerance, queue-server loss | All fixed, or recovered by re-finish / rescore |

**Key dims on Track E:**
- Across the 17 gated E runs, 26 of 140 key dims are within ±0.02 mm, 86 are within 0.2 mm and
  36 are unmeasured. The count excludes the sliver dim of §4.3.
- The errors cluster at round values (0.05–0.20 mm). That points to agent rounding more than scan
  noise.
- One key dim per part is unmeasured in almost every E run (§4.3).

## 4. Calibration (dev pilots only; evidence for ADR 0005)

### 4.1 The known issues

| Known issue | Confirmed? | Measured |
|---|---|---|
| wrong_dim PASSes with 1 of 8–10 key dims wrong | **Yes** | 15 of 17 single-dim edits PASS at 1.5× and at 3× tolerance (S1 40, total −1.6 to −2.0). 32 of 48 wrong candidates PASS. |
| 2 of 4 mounting holes plugged still PASSes | **Yes** | hs04 with 2 holes plugged: total 89.9, key dims 1.0, b1 mismatch. Overall 9 of 17 plugged / missing-feature candidates PASS, plus the 2 real defective Track T builds. |
| k needs calibrating | **Yes** | k = 1.5 makes the tight band 2.9–5.2× the 0.1 mm class band; it equals the wide band on dr02 and hs04 and nearly equals it on hs01 (0.293 vs 0.300). k = 1.0 is the smallest tested k at which the Opus Track E rebuilds on hs01 and dr02 keep full S1, and it still costs the hs01 +0.1 mm skin 9.4 S1 points. On hs04 the bands stay merged at k = 1.0. |
| D refines up to 3 vs 6 | **Not binding in the build123d runs** | §3.3; NX D runs pending |

### 4.2 Rule sets scored together

The same 144 candidates were scored under each rule set:
- the 6 golden kinds × 3 pilots (18);
- 48 wrong_dim edits;
- 17 plugged-hole / missing-feature edits;
- 6 scale errors and 3 offset skins;
- 52 real geometry runs (34 T including the 4 pilotgtfix, 18 E). The 6 plan-only S runs and the
  6 queued D × nx@v2 runs are excluded.

V2–V5 are post-processed from the k = 1.0 results and are not evaluator code yet.

| Set | Rules | Defects that PASS (of 91) | Good that fail (of 30) |
|---|---|---|---|
| V0 | v1 defaults | 46 | 0 |
| V1 | k_floor 1.0, pass_r_min 0.7 | 46 | 0 |
| V2 | V1 + PASS needs b1 match | 37 | 0 |
| V3 | V2 + per-face presence probes | 35 | 0 |
| V4 | V3 + max-deviation veto at 1.0 mm (NaN-free) | 35 | 0 |
| **V5** | V4 + all key dims within tolerance (sliver dim replaced) | **0** | **0** |

- **This is an in-sample fit.** The rules were designed after looking at these failures on these 3
  parts. No part or defect class was held out.
- **What each rule contributes:**
  - (b1, presence) and the key-dim rule are complementary. The key-dim rule alone leaves 6
    plugged / missing defects open. b1 + presence alone leave 35 wrong-dim defects open.
  - The max-deviation veto catches no extra candidate once b1 and presence apply (35 → 35). It is
    kept as a check independent of the probe filter.
- **The veto metric is `metrics.max`:** observable GT→pred plus all pred→GT samples, consistent
  with ADR 0002.
  - On this set the defects that need it measure 1.32–2.95 mm.
  - Good Track T runs and golden good measure ≤ 0.44 mm.
- **An independent verifier reproduced V0, V1, V3, V4 and V5** from the stored rows with its own
  rule code (V2 was not recomputed). It also fresh-rescored one example per class and re-measured
  the sliver finding.
- **Prerequisites before V5 can ship:**
  - **NaN-free deviation.** 2 stored dr02 T results have NaN max / hausdorff. The root cause is
    zero-area triangles in the candidate tessellation: the point-to-triangle code divides 0/0, and
    `argmin` picks the NaN. It must be fixed at the source, not by `nanmax`.
  - **The GT key-dim fix below.**

### 4.3 Key dims (GT audit)

- **One dr02 key dim is on a sub-mm² sliver face.** The 6 NX dr02 Track T runs match a blend face
  instead, at 0.015 mm, with the geometry within 0.044 mm. They lose that dim even though the
  shape is right.
  - Replacing it with an axis distance between two real faces gives Track T key dims 1.0 on 29 of
    29 PASS runs, against 23 of 29 today, and changes nothing on the wrong_dim candidates.
  - The wrong_dim builder cannot move the replacement dim, so its detection power is untested.
  - This is a private GT edit (key_dims.yaml), not a code change.
- **Per-instance pattern dims (e.g. all 4 hs04 lug holes) add no information as separate dims.**
  They found 0 disagreements on 46 runs and only dilute the key-dim fraction. Instance presence is
  covered by the probe rule instead.
- **On Track E, one key dim per part is unmeasured in almost every run:** 6/6 on hs01, 5/6 on
  dr02, 5/5 on hs04 (the measurement finds both ends on one face, or no face). This and agent
  rounding against the ±0.02 mm tolerance block Track E. The scan floor p95 is 0.20–0.35 mm.
  - **Loosening the Track E tolerance is not the fix.** At max(0.02, 0.5 × floor) all 48 wrong_dim
    edits and all 3 golden wrong_dim candidates would PASS, and still 0/18 E runs would.

### 4.4 Golden tests with the proposed values

- **The golden tests pass today:** evaluator suite with RECADBENCH_GT_DIR, 23 golden tests passed.
- **Under V5, 5 assertions in 4 tests change.** Each hard-codes a v1 value; none is a broken
  property:
  1. `test_pilot_band_eff_and_inferred`:
     - `band_tight == max(0.1, 1.5 × floor)` breaks on all 3 pilots.
     - `band_wide == max(0.3, 1.5 × floor)` breaks on dr02 and hs04.
  2. `test_scan_rules` expects a scan band_tight of 0.15; it becomes 0.10.
  3. `test_pilot_ordering_and_pass` expects `wrong_dim PASS == (kd ≥ 0.8)`. Under V5 wrong_dim must
     fail.
  4. `test_golden_synthetic::test_ordering_and_pass_flags` (public CI) asserts
     `ok["wrong_dim"] is True` at kd 6/7. Under V5 it is False.
- **Golden good stays PASS on all pilots:** total 95.0, key dims 1.0, max deviation ≤ 0.005 mm.
  lego, faceted and dumb_solid stay failed.

## 5. Caveats

- **Small sample, no inferential statistics.** 3 dev parts × 1 replicate. The 27 good Track T runs
  are about 6 distinct geometries, so "0 false negatives" carries little statistical weight.
- **In-sample calibration.** The presence filter (r ≤ 4 mm, area ≥ 5 mm²), the 1.0 mm veto and the
  replaced key dim were chosen on these same parts. They must be re-checked on the expanded dev
  pool before the test split is touched.
- **Some edits were not exercised.** 9 of 26 key dims were never edited, including the
  axis-distance dims and the replacement dim.
- **Track E evidence is thin.** No Track E run passes under any rule set tested, so every rule's
  false-negative cost on Track E is unmeasured.
- **The k bounds are narrow.** They rest on 4 Opus E runs (lower bound) and one hs01 skin
  (upper bound).
- **D × nx@v2 is pending** (6 runs).

## 6. Next

The P12 proposal (queue robustness, Track E key-dim measurement, D's accept rule, Track E skillset
delta with replicates, dev-pool expansion before any test-split run) is in the P11 hand-off.
