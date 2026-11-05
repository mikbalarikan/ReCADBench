# 03 — Scoring

All thresholds are v1 proposals, derived from manufacturing standards (ISO 2768, ISO 20457/DIN 16742, 3D-print process tolerances) and scanner accuracy figures; no published RE-specific acceptance rubric exists. Calibrate after the pilot (M3).

## 1. Gate G0 (fail ⇒ all scores 0)

- STEP loads, exactly 1 solid, `BRepCheck_Analyzer` valid, closed shell.
- **Faceted / mesh-to-BREP:** `faces_pred > 3 × faces_gt` OR >50% of planar faces are triangles.
- **Freeform cover-up** (Tier 1–2 only): >30% of the GT area that is plane/cylinder is B-spline in the prediction.
- Units mm; bbox within 20% of STL bbox (catches inch/mm errors early).
- **Non-parametric body:** `features.json.unmapped` non-empty (dumb solid, raw OCP shape, FreeCAD `Part::Feature`, NX unparameterized body, imported geometry) ⇒ FAIL. This closes the "history-free solid gets r≈0.1" loophole.

## 2. Alignment

- Rigid only (no scale). Point-to-plane ICP; inits = identity + 24 PCA orientations; pick best by F1 then RMSE.
- Report `datum_offset` = how far the alignment transform is from identity (did the agent model in the part's design CS or in scan CS). Reported, not scored in v1.

## 3. Rubric (100)

### S1 Deviation — 40
Two-way sampling **GT B-rep ↔ prediction B-rep** (`gt.step`, not the STL; 50k pts each, fixed seed), unsigned distance. The agent's `self_deviation` uses the STL (the raw scan), so it sees a proxy, not its score.

**Scan inputs (ADR 0002):**
- The prediction is in the scanner frame; it is mapped into the GT frame with `meta.yaml: registration.T_scan_to_gt`, then the usual alignment runs.
- **Observable region only:** GT sample points the scan does not cover (`observability`) are excluded from `p_tight`/`p_wide` and reported as `p_tight_inferred`/`p_wide_inferred` (not scored in v1).
- **Noise floor:** effective bands `band_eff = max(class band, k · scan_floor.scan_to_gt_p95_mm)`, `k = 1.5` provisional (P11 calibrates). Reported as `band_tight_eff_mm` / `band_wide_eff_mm`.
- Synthetic inputs (`input_kind: synthetic_*`) skip registration and floor; `make_task` tessellates them with chord ≤ ⅓ of the tight band.

Tolerance class from `meta.yaml`:

| Class | Tight band | Target | Wide band | Target |
|---|---|---|---|---|
| A machined / clean synthetic | ±0.05 mm | ≥95% | ±0.10 mm | ≥99% |
| B molded (**default v1**) | ±0.10 mm | ≥95% | ±0.30 mm | ≥99% |
| C printed / scanned | ±0.30 mm | ≥95% | ±0.50 mm | ≥99% |

`S1 = 24·lin(p_tight; 60%→0, target→1) + 16·lin(p_wide; 85%→0, target→1)`, clipped to [0,1].
Reported only: mean, RMS, P95, max, Hausdorff. Never score on max (outliers dominate).

### S2 Parsimony — 25 (anti-LEGO)
`r = N_pred / N_ref` (counting rule: `feature_taxonomy.yaml`, extracted by adapter, never self-reported).
- `0.7 ≤ r ≤ 1.2` → 25; `1.2 < r ≤ 3` → linear to 0; `r > 3` → 0 **and total capped at 40**.
- `0.5 ≤ r < 0.7` → linear 25→10; `r < 0.5` → 0 (implausibly few features ⇒ geometry probably not built as features).
- `face_ratio = faces_pred / faces_gt > 2` → S2 halved (catches one giant sketch with 300 segments).
- Parametric presence (cheap S5 proxy, active from v1): each GT key dim must map to ≥1 feature parameter or named IR parameter in the native model; coverage < 50% ⇒ S2 halved.

### S3 Design intent — 20
- Key dims (12): **intrinsic** dims preferred (diameters, radii, face-to-face distances, axis-to-axis distances) so ICP error does not count against the agent; face matching after alignment = nearest same-type face, axis within 2°. Tolerance by class: A ±0.01, B ±0.02, C ±0.05 mm.
- Standard values (4): credited **only on dims whose GT value is itself standard** (`key_dims.standard: true`). Snapping a non-standard GT dim to a standard value costs the key-dim credit — prevents "snap everything" gaming.
- Relations (4): GT-coaxial holes coaxial (axis offset < 0.01 mm); GT symmetry planes preserved.

### S4 Topology — 10
- Through-hole/handle count b1 match (4). (b0 = 1 and b2 = 0 are already enforced by the gate.)
- Face-type histogram similarity `1 − TVD(pred, gt)` over {plane, cylinder, cone, sphere, torus, bspline} (6).

### S5 Editability — 5 (enabled from M5)
For 1–2 designated key dims: adapter `set_param` → rebuild succeeds, volume changes in the expected direction, and deviation outside the edited region stays within the tight band.

## 4. PASS (binary — "the problem has one answer")

PASS ⇔ G0 ∧ S1 targets met ∧ `0.5 ≤ r ≤ 1.5` ∧ ≥80% of key dims within class tolerance.
Report pass@1 (mean over replicates) and pass^3 (all 3 replicates pass).

## 5. Plan score (Track S, IR vs `gt_plan.json`) — 100, reported separately

| Component | Pts | Method |
|---|---|---|
| Feature multiset match | 30 | Hungarian matching on (type, key params); F1 over matched features |
| Key-dim values in plan | 30 | Same class-scaled tolerance as S3, read from IR parameters |
| Datums | 15 | Primary datum type + normal/axis matches GT within 2° |
| Feature count ratio | 15 | Same curve as S2 |
| Order plausibility | 10 | Dependencies respected (e.g. fillet after the faces it references exist) — validity, not exact order |

## 6. Track-specific notes

- **Track T:** agent receives GT plan ⇒ S3 key dims should be ~perfect; failures isolate the L2 skillset (with L1 fixed). Headline: `Δ = score(skillset vN) − score(none)` per backend, paired per part. Also report `ir_adherence` = feature multiset match between `gt_plan` and extracted `features.json`.
- **Track S (backend=none):** only plan score. Strategy D not ranked here (needs execution feedback).
- **Track E:** full rubric + plan score of the final IR + `plan_drift` (diff v1→final) + `ir_adherence` (final IR vs extracted features). Strategy comparison with geometry = Track E with tool = build123d.

## 7. Efficiency (never in score)
Input/output tokens, $ cost, wall time, turns, tool-call errors, MCP calls by type. Leaderboard shows cost–score Pareto per track.

## 8. Statistics
- 3 replicates per (config, part). Unit of analysis = **part** (replicates averaged into a per-part pass rate / mean score); replicates are not independent items.
- Item-clustered paired bootstrap (10k) → 95% CI. Pairwise: Wilcoxon signed-rank on per-part scores (or paired cluster bootstrap) + Holm correction. McNemar only on single-replicate PASS.
- With 40 test parts only ~15–20 pt differences are resolvable; the leaderboard must show CIs and flag overlaps.
- Frozen test set evaluated only at milestones; all iteration on the 10 dev parts.
