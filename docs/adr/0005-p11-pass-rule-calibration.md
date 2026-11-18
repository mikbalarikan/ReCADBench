# ADR 0005 — P11 calibration: k_floor, PASS rule, presence and deviation vetoes

- Status: **proposed** (awaiting owner acceptance; nothing implemented)
- Date: 2026-11-07 (P11)
- Evidence: [PILOT_MEMO.md](../../orchestrator/experiments/PILOT_MEMO.md) §3–4 (dev split only).
  The private per-candidate tables are in the evaluator host's `p11/calib/`.
- **Spec version on acceptance: 1.0.0.**
  - This is the M3 tag planned in 00_DECISIONS / 07_ROADMAP.
  - **03_SCORING sections that change:**
    - §3 S1: k_floor; the max deviation moves from report-only to a PASS veto.
    - §3 S3: `near_point_limit` gets documented.
    - §4: the PASS rule.
  - **ADR 0002 item 5** (k = 1.5 provisional) is amended to k = 1.0.
  - **result.schema.json is unchanged:** `details` is not schema-validated.
- Builds on: ADR 0002 (k provisional, "P11 calibrates"), 03_SCORING §4 (PASS).

## Context

The P11 pilot (58 scored runs) and a calibration set of 144 candidates on the 3 dev pilots show
that the v1 PASS rule does not separate good from bad:

1. **One wrong key dim passes.** PASS needs only ≥ 80 % of key dims within tolerance. 15 of 17
   single-dim edits PASS at both 1.5× and 3× tolerance, and S3 is blind to the size of the error.
2. **Plugged holes and missing features pass.** S1 allows 1 % of samples out of band. One small
   feature costs 0.2–1.1 %, and the observability mask hides part of it. 9 of 17 such edits PASS,
   and so did 2 real Track T builds: one with a missing feature a few mm tall, one with extra
   geometry.
3. **k = 1.5 is too loose.** The tight band is 2.9–5.2× the class band. It equals the wide band
   on dr02 and hs04 and nearly equals it on hs01 (0.293 vs 0.300).
4. **The S2 lower bound is loose.** At r = 0.5, a plan with fewer than half the GT features passes
   the r clause. Real Track T r is 0.83–1.33.

## Decision (proposed)

| # | Change | v1 | Proposed |
|---|---|---|---|
| 1 | `k_floor` (band_eff = max(class band, k · scan floor p95)) | 1.5 | **1.0** |
| 2 | PASS r window | 0.5 ≤ r ≤ 1.5 | **0.7** ≤ r ≤ 1.5 |
| 3 | PASS key-dim clause | ≥ 80 % within class tolerance | **every key dim** within class tolerance (`pass_key_dims = 1.0`); an unmeasured dim counts as not within |
| 4 | PASS topology clause (new) | — | **b1_pred = b1_gt** (genus, as S4 computes it) |
| 5 | PASS presence clause (new) | — | see "Rule 5 as calibrated" |
| 6 | PASS deviation veto (new) | — | **`metrics.max` ≤ 1.0 mm**, NaN-free (prerequisite a) |

**Rule 5 as calibrated.**
- **Which faces get a probe:** every GT cylinder face, partial or full turn and of either
  convexity, with r ≤ `presence_r_max_mm` (4.0) and per-face area ≥ `presence_area_min_mm2`
  (5.0).
- **Deduplication:** by radius plus axis position.
- **Where the probe sits:** one probe point per face, on the face.
- **What passes:** a candidate cylinder face, axis-filtered and within `near_point_limit`, with
  diameter error ≤ `presence_tol_band_mult` (2.0) × band_wide_eff.
- **Where the probes come from:** they are generated from gt.step at GT load time, so there is no
  new GT file and no schema change for GT authors.
- **`near_point_limit`** = max(`near_point_max_mm` 1.0, `near_point_band_k` 3.0 ×
  band_wide_eff). This is the existing S3 face-match limit, now written into 03_SCORING §3 S3.

**Rule 6 metric.** `metrics.max` is observable GT→pred plus all pred→GT samples, so unobserved GT
samples are excluded, as ADR 0002 item 6 requires. Two calibration numbers:
- **Defects that need it:** 1.32–2.95 mm.
- **Good Track T runs and golden good:** ≤ 0.44 mm.

The 1.0 mm threshold is 2.3× the largest good value and 0.76× the smallest defect. It is a
class-independent absolute value, calibrated on class B dev parts. 03_SCORING's "never score on
max" becomes "S1 points never use max; PASS vetoes max > 1.0 mm".

**New PASS** = G0 ∧ S1 targets ∧ (2) ∧ (3) ∧ (4) ∧ (5) ∧ (6). The S1–S4 point formulas are
unchanged apart from the band change (1).

### Blocking prerequisites (same implementation PR)

**(a) NaN-free deviation.**
- **Root cause, verified:** zero-area triangles in a candidate tessellation make
  `geom._closest_on_triangles` divide 0/0 in its edge branch. `np.argmin` in `Surface.query`
  then picks the NaN, and that also skips the exact re-check.
- **Fix:** drop zero-area pieces after `_refine`, or make the argmin NaN-safe and guard the
  divisions. Do not apply `nanmax` downstream: a NaN sample would still count as out of band.
- **Tests:**
  - a degenerate triangle among the nearest pieces returns the brute-force distance;
  - no NaN reaches any result.json metric or the alignment inputs.

**(b) GT key-dim fix (private repo).**
- Replace the dr02 key dim defined on a sub-mm² sliver face with an axis distance between two
  real faces.
- Apply the two near-point placement fixes from the audit.
- Why it blocks: with the sliver dim, rule 3 fails 6 good NX runs.

**(c) Acceptance criterion for the implementation.** The evaluator code must reproduce the V3 and
V5 counts below on the private calibration set before merge, so the code matches what was
calibrated.

### Known limitations of rule 5 (to fix in the implementation where possible, re-test after)

- **Area filter per face.** A kernel that splits one cylinder into two half-faces can drop a small
  hole below 5 mm². Merging same-surface faces before filtering is recommended. It is not
  calibrated, so the V-counts must be re-checked if it is added.
- **Selection depends on the frame.** Deduplication by axis position depends on the GT frame. The
  probe point must be deterministic: use the face centroid projected onto the face, not a random
  GT sample.
- **Blends and slot ends count too.** Partial cylinders become probes. On the 3 pilots that caused
  no false negative, but it has to be checked on the expanded pool.

### Evidence (in-sample)

The rules were designed on these same 144 candidates and post-processed over the stored k = 1.0
rows. A verifier applied its own rule code to those rows for V0, V1, V3, V4 and V5 (not V2) and
fresh-rescored one example per class.

| Rule set | Defects that PASS (of 91) | Good that fail (of 30) |
|---|---|---|
| v1 | 46 | 0 |
| + (1) (2) | 46 | 0 |
| + (4) | 37 | 0 |
| + (5) | 35 | 0 |
| + (6) | 35 | 0 |
| + (3), after (b) | **0** | **0** |

- **In-sample fit.** 0 / 0 shows that the rules fit the defect classes they were designed for. No
  part or defect class was held out. 9 of 26 key dims were never edited, including the
  axis-distance dims and the replacement dr02 dim. Positional errors (a moved hole) were not
  tested.
- **(3), (4) and (5) are all needed.** (3) alone leaves 6 plugged / missing defects. (4) + (5)
  alone leave 35 wrong-dim defects.
- **(4) and (5) overlap but neither replaces the other.** (4) misses the omitted feature, whose
  b1 is unchanged. (5) misses the extra-geometry build.
- **(6) adds nothing on this set.** It catches no extra candidate once (4) and (5) apply
  (35 → 35). It is kept as a check that does not depend on the probe filter, not because this
  evidence requires it.
- **(1) and (2) flip no PASS verdict.** They change S1 and totals only: Track E mean −2.3
  (build123d) / −2.6 (nx), a +0.1 mm skin on hs01 −9.4. Both are adopted provisionally:
  - **k lower bound:** k = 1.0 is the smallest tested k at which the Opus Track E rebuilds on hs01
    and dr02 (4 runs) keep full S1. Those runs still fail key dims.
  - **k upper bound:** the bound k < 1.25 rests on one +0.1 mm skin on hs01, because the skins
    could not be built on dr02 or hs04.
  - **hs04 bands:** the tight and wide bands stay merged at k = 1.0. A separate wide-band rule is
    deferred.

### Golden tests

The golden tests pass today. These assertions are updated with the implementation, each because
it hard-codes a v1 value:

| Test | Assertion | Change |
|---|---|---|
| `test_pilot_band_eff_and_inferred` | `band_tight == max(0.1, 1.5·floor)` and `band_wide == max(0.3, 1.5·floor)` | Use `cfg.k_floor` in both. band_wide changes on dr02 and hs04. |
| `test_scan_rules` | band 0.15 | Becomes 0.10 |
| `test_pilot_ordering_and_pass` | `wrong_dim PASS == (kd ≥ 0.8)` | Becomes `not PASS` |
| `test_golden_synthetic::test_ordering_and_pass_flags` (public CI) | `ok["wrong_dim"] is True` (6/7 ≥ 80 %) | Becomes False; update its comment |

- **New tests:** golden kinds for wrong_dim at 3×, plugged hole, missing feature and extra
  geometry, so that each new clause has a test that fails without it, plus the NaN tests of
  prerequisite (a).
- **Unchanged:** golden good stays PASS on every pilot (total 95.0, key dims 1.0,
  `metrics.max` ≤ 0.005 mm).

## Rejected

- **Looser Track E key-dim tolerance, max(0.02, c · floor).** At c ≥ 0.5 every wrong_dim
  candidate PASSes, and 0 of 18 Track E runs gain PASS.
- **A gt→pred p_tight floor of 0.99.** It catches 4 of 9 plugged / missing defects and fails one
  good run (an edge-round deviation of 0.44 mm).
- **S1 over inferred samples too.** It catches 1 of 9.
- **Grading S3 by error size (16·frac² + 4·rel).** It improves ranking but never changes PASS.
  It is optional and not part of this ADR.
- **Separate per-instance pattern key dims.** They dilute the key-dim fraction, and (5) covers
  instance presence.
- **Changing D's `max_refine_iters`.** It was not binding in the 6 build123d D runs. Decide after
  the D × nx@v2 runs (docs-only change).

## Consequences

- **Track T:** PASS still means "built the plan correctly". The 2 real Track T defects that passed
  under v1 now fail.
- **Track E: PASS is unreachable by construction on the 3 dev parts.**
  - 0 of 18 scored runs pass under v1 or under the new rule.
  - Every gated E run has a key dim that is either unmeasured on the rebuild or outside
    ±0.02 mm. The looser tolerance is rejected above.
  - The 1.0 mm veto (6) would also fail most Opus E rebuilds on dr02 and hs04, with `metrics.max`
    of about 1–2.9 mm.
  - The false-negative cost of (4)–(6) on Track E is unmeasured.
  - Until P12 fixes key-dim measurement on rebuilds and re-tests (5) and (6) on a passing E
    population, Track E is ranked by total only. Track E pass@1 / pass^3 are reported as N/A, not
    0. This is a known limitation, not a tuned outcome.
- **Statistical weight:** the thresholds of (5) and (6) and the replaced key dim were chosen on
  the same 3 dev parts. They must be re-checked on the expanded dev pool (remaining R385 parts,
  GE-01..GE-04) before any test-split run. This ADR does not cover the test split.
- **Version: spec 1.0.0.** In the same release:
  - update `recadbench_spec` `SPEC_VERSION`, `MIGRATABLE_VERSIONS` and `$id`s;
  - re-pin `requirements-spec.txt` in the downstream repos;
  - tag `spec-v1.0.0`.
- **result.json and config:**
  - `result.json` carries `details.pass_checks.{b1, presence, max_dev}`. `details` is not
    validated, so there is no schema change.
  - `EvalConfig` gets `k_floor 1.0`, `pass_r_min 0.7`, `pass_key_dims 1.0`,
    `presence_r_max_mm 4.0`, `presence_area_min_mm2 5.0`, `presence_tol_band_mult 2.0` and
    `max_dev_veto_mm 1.0`.
- **Already merged, recorded here:** the NX adapter's STEP read-back tolerance went from 1e-5 to
  1e-3 relative (ReCADBench #18). It is an export-validity check, not a scoring threshold.
