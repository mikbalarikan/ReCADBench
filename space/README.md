---
title: ReCADBench Open
emoji: 📐
colorFrom: indigo
colorTo: green
sdk: gradio
sdk_version: 6.32.0
app_file: app.py
pinned: false
license: apache-2.0
hf_oauth: true
hf_oauth_expiration_minutes: 480
short_description: Scan to parametric CAD benchmark, open submissions
---

# ReCADBench Open (Space)

Gradio app for public ReCADBench submissions: leaderboard (Open /95), per-task heatmap, the 10
dev task cards, scoring rubric, upload with Hugging Face sign-in, and an admin tab.
Design: `docs/adr/0006-open-track.md`; submission format: `docs/benchmark/submission.md`.

| File | Role |
|---|---|
| `app.py` | Gradio Blocks UI + FastAPI route `/reports/<id>/report.html` (public report, strict CSP) |
| `submit.py` | upload path: sign-in, size, `inspect_zip`, sha256 dedupe, 3 per user per UTC day, dispatch; startup sweep; admin actions |
| `store.py` | `LocalStore` / `HubStore`: `results.jsonl` (file-locked, row allowlist), zips, public reports |
| `jobs.py` | `LocalDispatcher` (subprocess) / `HFJobsDispatcher` (HF Jobs on the eval image), `refresh_jobs` |
| `leaderboard.py` | table, heatmap and task-card rendering (pure functions) |
| `Dockerfile.eval` | evaluator image for HF Jobs (python 3.11, `./spec ./evaluator`, OCP wheels) |
| `tests/test_space_logic.py` | pure logic tests (no network) |

## Data flow

1. Upload: `submit.handle_submission` checks the zip **without extracting or executing anything**
   (`recadbench_eval.submission.inspect_zip`, stdlib only), writes a `pending` row and stores the
   zip as `submissions/<sha256>.zip`.
2. Scoring: the dispatcher starts `recadbench-eval score-submission --layer open` (locally, or as
   an HF Job that downloads the private GT dataset). Only `public/` and `summary.json` leave the
   job; `private/` (full result.json) never does.
3. The Space polls the job (`jobs.refresh_jobs`), merges the summary into the row
   (allowlist: `store.clean_row`) and serves the report. Rows stuck in `pending`/`scoring` for more
   than 2 h are marked `failed` at startup (`submit.sweep_pending`).

Rows hold only the public row of the evaluator (`submit_score.public_row`) plus `status`,
`submitted_at`, `queued_at`, `hf_user` (username only), `validated`, `job_id`, `reference`,
`status_message`. No GT value, path or private result field is ever stored.

## Environment

| Variable | Mode | Meaning |
|---|---|---|
| `RCB_MODE` | all | `local` (default) or `hub` |
| `RCB_ADMINS` | all | comma-separated HF usernames that see the Admin tab |
| `RCB_TASKS_FILE` | all | task ids, one per line (default: the 10 dev ids) |
| `RCB_REFERENCE_FILE` | all | JSON list of reference rows (live-broadcast stations), shown with `reference` |
| `RCB_LOCAL_ROOT` | local | store + work folder (default `./space_data`) |
| `RECADBENCH_GT_DIR` | local | GT parts folder for the local evaluator subprocess |
| `RCB_INPUTS_DIR` | local | local copy of the inputs dataset (`tasks.json`, `parts/<id>/preview.png`) |
| `HF_TOKEN` | hub | Space **secret**: a fine-grained token with write on the submissions repo and read on the GT repo only (not a personal full-access token). The scoring job drops it from the scorer's environment before any STEP is parsed |
| `RCB_SUBMISSIONS_REPO` | hub | dataset repo: zips, `results.jsonl`, `reports/`, `jobs/` |
| `RCB_GT_REPO` | hub | PRIVATE GT dataset, read only inside the scoring job |
| `RCB_INPUTS_REPO` | hub | public inputs dataset (task cards, previews, downloads) |
| `RCB_EVAL_IMAGE` | hub | image built from `Dockerfile.eval` |
| `RCB_JOB_FLAVOR`, `RCB_JOB_NAMESPACE` | hub | HF Jobs hardware (default `cpu-upgrade`) and namespace |

## Local smoke

```
pip install gradio huggingface_hub filelock
RCB_MODE=local RCB_LOCAL_ROOT=/tmp/rcb-space RECADBENCH_GT_DIR=<gt parts> python space/app.py
python -m pytest space/tests
```

## Deploy (owner only)

The Space repo gets the contents of `space/` plus `recadbench_eval/__init__.py` and
`recadbench_eval/submission.py` copied from `evaluator/` (both stdlib only), so the upload check is
the same code the evaluator uses. Publishing the Space, the datasets or the eval image is done by
the owner (ADR 0006, decision D2).
