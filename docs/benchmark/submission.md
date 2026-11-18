# Submitting to ReCADBench Open (draft)

> Draft. The submission Space is not open yet; the format below may still change before it opens.
> Design and scoring rationale: [ADR 0006](../adr/0006-open-track.md).

## What you get

10 tasks. Each task is one real 3D scan of a mechanical part:

```
<task_id>/
  input.stl     raw scan, scanner frame, mm (noisy, with holes, never repaired)
  task.yaml     scanner, number of sessions, known scan defects, tolerance class, tier
```

Your agent must turn each scan into **one clean, parametric solid** that captures the design intent.

## What you upload

One zip:

```
submission.zip
  meta.json                        required
  <task_id>/                       one folder per task you solved (missing task = 0)
    output.step                    required: ONE solid, mm, in the SCANNER frame (same frame as input.stl)
    features.json                  optional, declared feature list (spec/ schema)
    plan/re_plan.v1.json           optional, RE Plan IR (scored separately as plan score)
    native/                        optional: model.py | *.prt | *.FCStd | *.SLDPRT | ... (needed for Verified)
    run.json                       optional, declared: wall time, cost $, tokens, turns, model
```

`meta.json`:

```json
{
  "submitter_name": "...",
  "submission_name": "...",
  "agent_url": "https://...",
  "model": "claude-opus-5-5",
  "cad_backend": "build123d | NX | FreeCAD | SolidWorks | Onshape | other",
  "harness": "...",
  "human_in_loop": false,
  "notes": "<= 500 characters",
  "agree_to_publish": true
}
```

Folder names must be exactly the task ids. No other top-level entries. No symlinks, absolute paths or `..`.
Limits (planned): zip ≤ 300 MB, each STEP ≤ 50 MB, 3 submissions per Hugging Face user per day.

## How it is scored

| Layer | Needs | Scores |
|---|---|---|
| **Open** (automatic, every upload) | `output.step` | G0 geometry gate, S1 deviation (40), S2-face parsimony proxy (25), S3 design intent (20), S4 topology (10) → **Open /95** |
| **Verified** (admin) | `native/` model that an admin can rebuild offline | official G0 and S2 from the feature tree extracted by our adapter → **Official /95** and PASS |

Full rubric: [03_SCORING.md](../architecture/03_SCORING.md). Your report shows per-task scores, a deviation map on
**your** model and, for key dimensions, only within / not within tolerance.

## Rules

- The agent sees only `input.stl` and `task.yaml`. No ground truth exists in any public place.
- Do not use public CAD of the same commercial products (contamination). Declare anything you used.
- `human_in_loop: true` if a person edited geometry or steered the agent beyond the initial prompt; those rows get a
  separate badge.
- Partial submissions are fine; unsolved tasks score 0.
- These 10 tasks are a **public dev set**. A hidden test set comes later.

## Check before you upload

`tools/sanity_check_submission.py submission.zip` (coming with the Space) checks the folder set, that each STEP loads
as one solid in mm, and that its bounding box overlaps the scan.
