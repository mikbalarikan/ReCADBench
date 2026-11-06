# recadbench_eval

The ReCADBench evaluator (pip `recadbench-eval`, P7 / PROMPTS_v2 H5). It implements
`docs/architecture/03_SCORING.md` with the ADR 0002 scan rules. Ground truth is read **only** from
`RECADBENCH_GT_DIR` (`gtstore.py`), and the package never imports backend or adapter code. It
uses the pinned kernel (build123d 0.13.0 / OCP) for B-rep work.

```
recadbench-eval score --run-dir runs/<id> --part <part_id> [--s5] [--config eval.yaml]
recadbench-eval plan-score --plan plan/re_plan.v1.json --part <part_id>
recadbench-eval report runs/ --md table.md --csv table.csv
recadbench-eval golden --part <part_id> --out-dir <tmp>     # test candidates, never commit
```

## Run dir contract (input)

| Path | Producer | Used for |
|---|---|---|
| `run_manifest.json` | orchestrator | ids and track (optional; `--part`/`--track` fill the gaps) |
| `out/output.step` | worker export, **input frame** (= scanner frame for scans), mm | gate, S1, S3, S4 |
| `out/features.json` | adapter `extract` on the worker (never self-report); `./features.json` also accepted | gate (unmapped), S2 |
| `plan/re_plan.v<N>.json` | strategy | plan score (highest N), `plan_drift` (v1 vs final) |
| `out/s5/edits.json` + edited STEPs | queued `set-param` job | S5 (only with `--s5`) |
| `efficiency.json` | runner | copied into `result.efficiency` |

## Pipeline

| Step | Module | Notes |
|---|---|---|
| Gate (static) | `gate.py` | loads; 1 solid; `BRepCheck` valid; closed shell; exactly one shell (b2 = 0, no internal voids); faceted (`faces > 3 x faces_gt` or > 50% of the planar faces are triangles); scale / volume sanity: `cbrt(V_pred / V_gt)` within 20% of 1; `features.json` readable (BOM accepted), valid against the spec `features` schema (incl. the `count` rule), present and `unmapped` empty |
| Align | `align.py` | `T_scan_to_gt`, then trimmed point-to-plane ICP (fixed seed). When the two-way F1 at the tight band is < 0.8, it tries 24 PCA orientations (coarse 20/8/3 mm, then fine) and keeps the best by F1, then RMSE. Reports `datum_offset` |
| Gate (aligned) | `gate.py` | bbox: per-axis extents of the aligned candidate within 20% of the GT's; freeform cover-up (tier 1-2): > 30% of the GT plane/cylinder area is B-spline in the candidate |
| S1 (40) | `deviation.py` | two-way, 50k samples per side, exact distances (`geom.Surface`). Observable region from `observability_mask.npz` (nearest mask sample): only unobserved GT samples are excluded, every candidate sample is scored (material added over an unobserved patch counts). `band_eff = max(class band, k * scan_floor p95)` with `k = 1.5` from config. `p_*_inferred` is reported only |
| S2 (25) | `parsimony.py` | `r = N_pred / n_ref`, `N_pred` = features.json rows with `counted: true` (recomputed, not the `count` field); the face-ratio and parametric-presence halvings; `r > 3` caps the total at 40 |
| S3 (20) | `intent.py` | 12 pts: intrinsic key dims, nearest same-type face (axis within 2 deg) within `max(1 mm, 3 x wide band_eff)` of the GT near_point (else not measured), class tolerance (limits included). 4 pts: standard values (only dims with `standard: true`). 4 pts: GT-coaxial groups stay coaxial (0.01 mm) and GT symmetry planes are kept |
| S4 (10) | `topology.py` | b1 from B-rep topology (`chi = V - E + sum(2 - wires)`), plus `1 - TVD` of the face-type area histogram |
| S5 (5) | `editability.py` | behind `--s5`: rebuild ok, volume moves in the expected direction, and the part outside the edited region stays within the tight band |
| Plan score (100) | `plan_score.py` | Hungarian F1 on (type, key params), key dims in the plan, primary datum (rotated by `T_scan_to_gt`), count ratio, order plausibility |
| result.json | `score.py` | validated against `spec` `result` schema; floats rounded to 1e-6, sorted keys, ASCII, LF, so it is byte-identical for identical inputs. `details` holds the diagnostics |

PASS = G0, S1 targets met, `0.5 <= r <= 1.5`, and at least 80% of the key dims within tolerance.
Without S5 the total is out of 95, as the spec example shows.

## Interpretations (v1, provisional, recorded here, calibrated in P11)

- When the GT has no `standard: true` dims, the 4 standard-value points follow the key-dim
  fraction. When it has no coaxial group or symmetry plane, the 4 relation points do the same.
  The points are neither free nor lost. None of the R385 pilots flag a standard dim today.
- The scale gate compares against the GT rather than the raw scan, whose extents include stray
  patches and table remnants. The static check is the rotation-invariant volume scale
  (vertex-PCA extents tilted with uneven tessellation density and zeroed a part with one small
  hole missing); the bbox check runs after alignment, in the GT frame.
- PASS does not include topology: a part with two of four holes plugged where the scan does not
  see them can PASS (b1 only costs S4 points). Known rubric gap, for P11.
- Key-dim tolerance includes its limits (|err| <= tol). The golden `wrong_dim` therefore uses
  1.5 x tol (0.03 mm for class B), so the error is detected robustly, where an error of exactly
  0.02 would sit on the limit.
