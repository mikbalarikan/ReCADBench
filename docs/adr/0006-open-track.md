# ADR 0006 — ReCADBench Open: public submissions, Open vs Verified score, GT hosting

- Status: **proposed** (owner decisions D1–D3 of 2026-11-18 recorded below; nothing implemented yet)
- Date: 2026-11-18
- Builds on: ADR 0002 (real-scan inputs, dev-only v1), 03_SCORING (G0, S1–S4, PASS), 00_DECISIONS D3 (repo topology).
- Changes when accepted: 00_DECISIONS D3 (where GT lives, which inputs are public), 03_SCORING (new §9 "Open track").

## Context

The benchmark is announced publicly on 2026-11-18 with 10 dev tasks (5 R385 diaphragm-pump parts, 5 Open Dedica
espresso-machine parts). Outside participants should be able to run their own agent on the 10 scans and upload the
result, similar to Hugging Face's CADGenBench (a Space, public input dataset, private GT dataset, submissions dataset,
evaluation in HF Jobs, *Unvalidated* / *Validated* tables).

ReCADBench's score does not come from the STEP alone. G0 rejects non-parametric bodies via `features.json.unmapped`,
and S2 (parsimony, 25 points) is `N_pred / N_ref`, where the feature list is **extracted by our adapter from the
native model**, never self-reported. An outside submission arrives as a STEP plus, at best, a native model in an
arbitrary CAD system. Running submitted code or CAD files in a public Space is not safe, and a self-reported
`features.json` cannot be trusted.

## Decision (proposed)

### 1. Two score layers

| Layer | When | What is scored | Table |
|---|---|---|---|
| **Open** (automatic) | every upload, HF Job | G0-geo (STEP loads, one valid closed solid, faceting, freeform cover-up, bbox, units), **S1** (40), **S3** (20), **S4** (10), **S2-face** (25, see 2) | *Unvalidated*, "Open /95" |
| **Verified** (admin) | the submission includes a native model; an admin rebuilds it in a network-less sandbox | the adapter extracts `features.json` → official G0 (incl. `unmapped`) and S2 = `N_pred / N_ref`, PASS | *Validated*, "Official /95" |

- S5 (editability) stays disabled, so both layers are out of 95.
- A submitted `features.json` or `plan/re_plan.v1.json` is accepted and shown as **declared**; it never changes the
  Open score. A plan, if present, gets the Track S plan score in a separate column.
- Submitted code is **never executed in the Space or in the Open job**.

### 2. S2-face: the parsimony proxy for STEP-only submissions

`q = faces_pred / faces_gt`, scored as a **step** function (a continuous curve would let anyone invert the public S2,
or the total, back to the GT face count; the public report shows the same buckets):

| q | [0.35, 1.5] | (1.5, 2] | (2, 3] | [0.25, 0.35) | < 0.25 | > 3 |
|---|---|---|---|---|---|---|
| S2-face | 25 | 15 | 5 | 10 | 0 | 0 and total capped at 40 |

Calibration (2026-11-18): the 46 gate-ok P11 pilot runs, where both `r` (official) and `q` are known. On Track E the
agents usually leave out the GT's small fillets and chamfers, so `q` is about `0.55 r`; a plateau at 0.7-1.2 on `q`
would have scored good Track E runs 0. With the steps above, mean |S2-face - S2| is 1.9 points over all runs and 5.1
on Track E (was 5.4 / 13.5 with the S2 plateau). `q` correlates with `r` at 0.85. The Open table still labels
S2-face "provisional"; recalibrate with the broadcast runs.

### 3. GT hosting (owner decision D1 = a)

GT moves from "evaluator host only" to a **private Hugging Face dataset** read only by the evaluation job (HF token
as a Space/Job secret). Uploaded: `gt.step`, `gt_plan.json`, `key_dims.yaml`, `meta.yaml`,
`observability_mask.npz`, `registration_report.json`. **Never uploaded:** `gt_native.*`. The private GitHub repo
`ReCADBench-gt` stays the source of truth.

### 4. Public inputs (owner decision D3)

The 10 dev scans (`input.stl` + `task.yaml`) are published in a public HF dataset, including the 5 R385 scans that
were private until now. GT CAD is never published. Consequence: these 10 tasks are a **public dev set**; they are
contamination-exposed from the day of publication and are marked as such. A hidden test set (new parts, never
published) is future work; only it can carry contamination-free claims.

### 5. Hosting (owner decision D2)

Space and datasets live under the Hugging Face account `robotoguz`. Every publication step (Space, datasets, making
anything public) is done by the owner or with the owner's explicit approval.

### 6. Leak rules

Reports and the API never expose GT values: no GT render, no key-dim nominal (only within / not within tolerance),
no `gt_plan`. A test asserts that the report HTML contains no key-dim nominal. `check_public.py` stays green.

## Consequences

- The evaluator gains `--track O` (STEP-only scoring) and `score-submission` (zip → per-task results + summary).
- Open and Official scores are not comparable to each other at the row level; the leaderboard shows both columns.
- The 4 live-broadcast stations (SR-MCP, NX-B, NX-D, B3D-B) are added as reference rows with both scores.
