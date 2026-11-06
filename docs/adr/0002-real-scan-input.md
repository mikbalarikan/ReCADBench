# ADR 0002 — Real 3D scans are the benchmark input; v1 starts with scan-rebuild parts

- Status: accepted
- Date: 2026-11-05
- Spec version: **0.2.0** (schema change: `task`, `gt_meta`, `result`; version consts bumped everywhere)
- Supersedes: the plan-v4 assumption that `input.stl` is a tessellation of `gt.step`
  (00_DECISIONS D7, 02_REPO_LAYOUTS "recadbench-data", 06 P6)

## Context

Plan v4 built every input by tessellating the GT B-rep (`make_task`: chord ≤ ⅓ tight band). That
tests *clean mesh → parametric CAD*. The owner's intent is the industrial reverse-engineering task:
**a real scan** (noisy, holed, partly unobserved, in an arbitrary scanner frame) → clean parametric
CAD/STEP that recovers design intent.

Available data (2026-11-05):

| Set | Parts | Scan | GT | Provenance |
|---|---|---|---|---|
| R385 diaphragm pump (owner's desktop) | 8 shortlisted | owner's own scans, private | owner's NX rebuild (`.prt` with feature tree + `.stp`), **human** | replica of a commercial part; public RS-385 CAD exists |
| `mikbalarikan/open-dedica` (public, CC BY 4.0) | 14 (≈11 usable; PCBs out) | public raw STLs (Creality CR-Scan Raptor) | build123d STEPs made **by agents** with the stl-re skills; most verdicts `BAND_NOT_MET`, accepted by owner | public scan + public CAD; commercial (commercial product) parts |
| `agentic_STL-to-CAD/golden-examples` (private) | 5 | mixed | successful RE projects | not yet cleared (some may be client work) |
| OGUZ own designs (inventory, 32) | — | **none yet**; owner can print + scan | original NX design | own design, private |

The owner has a 3D scanner and can print the OGUZ parts later, but decided to **start with
scan-rebuild parts (source B) only**.

## Decision

1. **Input = the raw scan.** `input.stl` is the scan, byte-identical, in the **scanner frame**
   (`task.yaml: input_kind: scan`, `scan.frame: scanner`). Finding the datum frame is part of the
   task. Scan metadata a customer would provide (scanner, mode, sessions, known defects) is in
   `task.yaml`; optional calipers/photos/notes go in `aux_inputs` (empty in v1).
2. **Three part sources** (`meta.yaml: source_kind`):
   - `scan_rebuild` — scan of a physical part, GT = a reconstruction. **v1 starts here.**
   - `print_scan` — scan of a print of an original design, GT = the design CAD. Later (OGUZ);
     this is the clean, uncontaminated source and the future test set.
   - `synthetic` — input generated from the GT B-rep (clean or degraded). Calibration, evaluator
     golden tests and Track T sanity only; never ranked, never `test`.
3. **GT provenance is recorded and gates the test split.** `gt_author: human | agent |
   agent_human_reviewed` and `contamination: {public_scan, public_cad, commercial_replica}` are
   required. `split: test` is rejected by the validator if any contamination flag is true, if
   `gt_author: agent`, or if the source is synthetic.
   - Agent-made GT (open-dedica) is **circular** for this benchmark (it was produced by the same
     stl-re pipeline that will be evaluated as a strategy/skillset) → dev/calibration only until the
     owner reviews it (`agent_human_reviewed`).
   - **Consequence for v1: every source-B part is dev-only. v1 has no frozen test split and no
     public leaderboard**; results are development results until `print_scan` parts exist.
4. **Registration is a GT-authoring step.** `meta.yaml: registration.T_scan_to_gt` (rigid 4×4,
   `x_gt = T·x_scan`, method, RMSE, converged, seed). The agent works in the scanner frame; the
   evaluator maps its output into the GT frame with T, then runs its own fine alignment.
5. **Noise floor.** `meta.yaml: scan_floor` = deviation of `gt.step` itself from the registered
   scan (scan→GT p95, GT→scan observable p95). No agent can be expected to beat it. S1 bands are
   widened to `band_eff = max(class band, k · scan_floor p95)`, `k = 1.5` provisional, calibrated
   in P11.
6. **Observability.** `meta.yaml: observability` = share of the GT surface the scan covers
   (point within `threshold_mm`, same-facing). S1 is scored on the **observable** GT region; the
   unobserved region is reported separately as **inferred** (`result.metrics.p_*_inferred`), not
   scored in v1. Masks follow the `stl-re-verify` approach (open boundary, opposite-normal).
7. **Unchanged:** S1 is still measured against `gt.step`, never against the scan; the agent's
   `self_deviation` is against the scan (a proxy), so it still cannot see its score. Key dims come
   from the GT nominals, so recovering design intent from noise is rewarded.

## Spec 0.2.0 changes

| Schema | Change |
|---|---|
| `task` | `spec_version` 0.2.0; **required** `input_kind` (`scan`, `synthetic_clean`, `synthetic_degraded`); `scan` {scanner, frame, mode, sessions, triangles, watertight, known_defects} required when `input_kind: scan`; optional `aux_inputs` |
| `gt_meta` | **required** `source_kind`, `gt_author`, `contamination`; `scan` {file, sha256, …}, `registration`, `scan_floor`, `observability` required unless `synthetic`; `tessellation_chord_mm` required only for `synthetic` |
| `result` | `metrics.p_tight_inferred`, `p_wide_inferred`, `band_tight_eff_mm`, `band_wide_eff_mm`; `p_tight`/`p_wide` are now defined on the observable region |
| `re_plan_ir` | `spec_version` const 0.2.0 only; coordinates = input frame = scanner frame for scans |
| all | `$id` → `…/0.2.0` |

Validator semantic rules added: rigid-transform check (orthonormal, det > 0, last row), converged
registration, non-negative floors, test-split eligibility (above), no `scan` block on synthetic
tasks.

## Consequences

- **P6 changes** (06_SESSION_PROMPTS): `make_task.py` packages the raw scan + `task.yaml` instead of
  tessellating; a GT-authoring tool registers scan↔GT and computes `scan_floor` + `observability`.
  `AUTHORING.md` covers scan-rebuild GT. Pilots come from R385 (human GT).
- **P7 changes**: S1 maps the prediction by `T_scan_to_gt`, uses `band_eff`, scores the observable
  region, reports the inferred region. Golden tests use synthetic parts plus R385 dev parts.
- **Prior art to port**: `mikbalarikan/agentic_STL-to-CAD` (`stl-re-*` skills and scripts) seeds
  P5 (`retk`: intake/measure scripts), P7 (ICP, masks, zones from `stl-re-verify`), P8 (build123d
  skillset from `stl-re-rebuild-build123d`) and P9 (a strategy from `stl-re-orchestrate` +
  intake + measure), split along the WHAT/HOW rule (D6).
- **Data size**: raw scans are 18–50 MB each; `recadbench-data` may need Git LFS or Drive (open
  question, P6).
- **Docs updated**: 00_DECISIONS (D7, D9, new D10), 01_ARCHITECTURE (data flow), 02_REPO_LAYOUTS
  (data + GT folders), 03_SCORING (S1), 06_SESSION_PROMPTS (P6, P7), 07_ROADMAP.

## Owner decisions (2026-11-05)

- **`agentic_STL-to-CAD` golden examples: cleared for use** (owner). They join the v1 dev pool.
- **open-dedica: kept aside for now.** Not in the v1 dataset. Later, once benchmark agents produce
  lower-deviation rebuilds, the best rebuild per part can be reviewed and kept as a calibrated
  reference STEP (`gt_author: agent_human_reviewed`, still dev/calibration because scan and CAD are
  public).
- R385: dev only (commercial replica, public RS-385 CAD exists).

## v1 dev pool (scan_rebuild, all dev)

| Part(s) | Scan | GT | `gt_author` | Contamination | Notes |
|---|---|---|---|---|---|
| R385 ×8 (HS01–HS04, SL01–SL03, DR02) | owner's, private | owner's NX `.prt` + `.stp` | `human` | `commercial_replica` | pilots for P6; frames/scans being checked on the desktop |
| GE-01 (golden example, client part) | original full-res on Drive (repo has a cleaned mesh) | build123d + NX, verdict APPROVED | `agent_human_reviewed` | `commercial_replica` | |
| GE-02 (golden example, client part) | full-res on Drive (repo: decimated) | build123d + NX, APPROVED | `agent_human_reviewed` | `commercial_replica` | |
| GE-03, GE-04 (golden examples, client parts) | full-res on Drive (repo: decimated) | build123d + NX, gates PASS | `agent_human_reviewed` | `commercial_replica` | 2 parts |
| OD-H12 pump (calibration) | same scan as open-dedica OD-H01 (**public**) | build123d + NX, band not met (p95 0.448) | `agent_human_reviewed` | `public_scan`, `commercial_replica` | calibration-grade only |
| M02-02-03 compound gear | `M02-02-03.STL` (179k faces, not watertight) | build123d `model.py` | `agent_human_reviewed` | to confirm | provenance of the mesh to confirm |
| GE-05 (golden example stub) | — | — | — | — | stub, excluded |

Caveat: the `stl-re-*` skills were distilled from these golden projects, so a strategy/skillset ported
from them (P8/P9) has seen these parts. That is acceptable for dev, and it is another reason none of
them may ever enter `test`.

Official inputs must be the **original full-resolution scans** from Drive (`MANIFEST.json
source_path`), never the `*_scan_decimated.stl` inspection copies.
