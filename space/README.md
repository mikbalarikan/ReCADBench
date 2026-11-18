---
title: ReCADBench Open
emoji: 📐
colorFrom: red
colorTo: gray
sdk: docker
app_port: 7860
pinned: true
license: mit
hf_oauth: true
hf_oauth_expiration_minutes: 480
short_description: Scan → parametric CAD benchmark for AI agents
datasets:
  - mikbalarikan/recadbench-inputs
tags:
  - leaderboard
  - benchmark
  - cad
  - 3d
  - reverse-engineering
  - agents
  - step
---

<p align="center">
  <img src="https://huggingface.co/spaces/mikbalarikan/recadbench-open/resolve/main/assets/banner-dark.webp" width="100%" alt="ReCADBench: a raw 3D scan goes in, a parametric STEP must come out.">
</p>

# ReCADBench Open

**Can your AI agent turn a raw 3D scan into clean, editable CAD?** ReCADBench gives an agent a
real scan (`input.stl`: noisy, holed, in the scanner frame) and asks for one parametric solid as a
STEP file. Not a surface wrap, not a mesh-to-B-rep conversion: a model a human engineer could open
and edit. This Space is the open submission site: download the 10 scans, run your own agent with
any CAD backend, upload one zip, get an automatic score on the leaderboard.

[GitHub](https://github.com/mikbalarikan/ReCADBench) ·
[Inputs dataset](https://huggingface.co/datasets/mikbalarikan/recadbench-inputs) ·
[Live race](https://youtube.com/live/S4zE6OxCbLg) ·
[Submission format](https://github.com/mikbalarikan/ReCADBench/blob/main/docs/benchmark/submission.md) ·
MIT

## Take part in four steps

1. **Download** the 10 tasks (`input.stl` + `task.yaml` each):
   `hf download mikbalarikan/recadbench-inputs --repo-type dataset --local-dir recadbench`
2. **Run your agent** with any CAD kernel (build123d, FreeCAD, NX, SolidWorks, Onshape, ...).
   Deliver one solid per task, in millimetres, in the scanner frame.
3. **Zip** `meta.json` + one folder per task with `output.step`, then check it locally:
   `python tools/sanity_check_submission.py submission.zip`
4. **Sign in and upload** in the *Take part* tab. Scoring runs in a separate process inside this
   Space (one submission at a time, others wait in a queue); your report and the leaderboard
   update when it finishes.

## Scoring (Open, /95)

A gate comes first: the STEP must load as exactly one valid, closed, non-faceted solid. Fail the
gate and the task scores 0.

| | Component | Points | What it rewards |
|---|---|:-:|---|
| **S1** | Deviation | 40 | two-way surface distance to the hidden ground truth, inside tolerance bands |
| **S2** | Parsimony (face count) | 25 | a face count close to the reference; LEGO-style stacks score 0 |
| **S3** | Design intent | 20 | key dimensions within tolerance |
| **S4** | Topology | 10 | through-hole count and face-type mix |
| | **Total** | **/95** | mean over all 10 tasks, a missing task counts 0 |

Open scores are computed from the STEP alone. Submissions that include the native model can be
*Verified* by an admin, which adds the official feature-tree score and PASS.

## Rules

- Ground truth is never published: the Space downloads it from a private dataset outside the
  app folder, scores in a subprocess without any token, and only the public report is shown.
  Submitted code is never executed.
- 3 uploads per Hugging Face account per UTC day, 300 MB per zip.
- No hand-editing of the CAD unless `human_in_loop` is `true` in `meta.json` (shown as a badge).
- These 10 tasks are a public dev set; a hidden test set follows later. Questions and issues:
  GitHub issues.

---

## How the Space works

A Docker Space (free `cpu-basic`) running a Gradio app for public ReCADBench submissions:
leaderboard (Open /95), per-task heatmap, the 10 dev task cards, scoring rubric, upload with
Hugging Face sign-in, an admin tab, and the scorer itself (`RCB_MODE=space`).
Design: `docs/adr/0006-open-track.md`; submission format: `docs/benchmark/submission.md`.

| File | Role |
|---|---|
| `app.py` | Gradio Blocks UI + FastAPI route `/reports/<id>/report.html` (public report, strict CSP) |
| `submit.py` | upload path: sign-in, size, `inspect_zip`, sha256 dedupe, 3 per user per UTC day, one open (pending/scoring) upload per user and at most 20 waiting (in-app queue), dispatch; startup sweep; admin actions |
| `store.py` | `LocalStore` / `HubStore`: `results.jsonl` (file-locked, row allowlist), zips, public reports |
| `jobs.py` | `LocalDispatcher` (scorer subprocess, used in-Space) / `HFJobsDispatcher` (HF Jobs, later), `refresh_jobs` |
| `runtime.py` | `RCB_MODE=space` backends: HubStore, GT download to `$HOME/.rcb-gt`, in-Space scorer + queue |
| `leaderboard.py` | table, heatmap and task-card rendering (pure functions) |
| `theme.py` | Gradio theme + CSS (dark brand theme, readable in light mode) |
| `docker/Dockerfile` | the Space image: python 3.11 + OCP runtime libs + `./spec ./evaluator` + `requirements.txt`; app in a root-owned `/opt/rcb/app` (read-only for uid 1000), `SYSTEM=spaces` (real HF OAuth, never Gradio's mocked sign-in), `PYTHONNOUSERSITE=1`; exec-form `CMD ["python", "app.py"]` with no ENTRYPOINT or init wrapper, so the hardened (non-dumpable) app is PID 1 and reaps the scorer processes it inherits |
| `requirements.txt` | Space packages (gradio, huggingface_hub, filelock, fastapi, uvicorn, matplotlib); pinned at stage |
| `Dockerfile.eval` | evaluator-only image for a later HF Jobs mode; repo only |
| `deploy_hf.py` | stage + push to the Hub (owner only); repo only |
| `tests/` | pure logic tests (no network); repo only |

### Data flow

1. Upload: `submit.handle_submission` checks the zip **without extracting or executing anything**
   (`recadbench_eval.submission.inspect_zip`, stdlib only), writes a `pending` row, stores the zip
   as `submissions/<sha256>.zip` in the private submissions dataset and keeps a local copy.
2. Scoring: `LocalDispatcher` starts `python -I -m recadbench_eval.cli score-submission --layer open`
   on the local copy, in its own output folder and process group, with `HF_TOKEN`, `HUGGING_FACE_HUB_TOKEN`, `HF_HUB_TOKEN` and every
   `OAUTH_*` variable removed from its environment. At most `RCB_MAX_PARALLEL` scorers run at
   once; further uploads stay `pending` and a background poller (every 20 s) dispatches them FIFO.
   The GT comes from the private GT dataset, downloaded at startup to `$HOME/.rcb-gt` (outside the
   app folder, never a static or Gradio-allowed path); if that fails, the poller retries it every
   10 min and waiting uploads start once it succeeds. A scorer whose row was deleted or failed, or
   that runs past the watchdog, is killed on the next poll, so it never holds the queue.
3. The poller merges the public summary into the row (allowlist: `store.clean_row`) and stores
   the public report. After a restart, rows that were scoring for more than 2 h become `failed`
   (`submit.sweep_pending`); queued rows are dispatched again.

Rows hold only the public row of the evaluator (`submit_score.public_row`) plus `status`,
`submitted_at`, `queued_at`, `hf_user` (username only), `validated`, `job_id`, `reference`,
`status_message`. No GT value, path or private result field is ever stored.

### Environment

| Variable | Mode | Meaning |
|---|---|---|
| `RCB_MODE` | all | `local` (default), `space` (deployed: in-Space scoring) or `hub` (HF Jobs, later) |
| `RCB_ADMINS` | all | comma-separated HF usernames that see the Admin tab |
| `RCB_TASKS_FILE` | all | task ids, one per line (default: the 10 dev ids) |
| `RCB_REFERENCE_FILE` | all | JSON list of reference rows (live-broadcast stations), shown with `reference` |
| `RCB_LOCAL_ROOT` | local | store + work folder (default `./space_data`) |
| `RECADBENCH_GT_DIR` | local | GT parts folder for the local evaluator subprocess (space mode sets it itself) |
| `RCB_INPUTS_DIR` | local | local copy of the inputs dataset (`tasks.json`, `parts/<id>/preview.png`) |
| `HF_TOKEN` | space | Space **secret**: a fine-grained token with exactly these permissions and no others: write on `datasets/<owner>/recadbench-submissions`, read on `datasets/<owner>/recadbench-gt` (never a personal full-access token). Never passed to the scorer. Without it the Space starts and uploads stay `pending` ("scoring not configured") |
| `RCB_SUBMISSIONS_REPO` | space | PRIVATE dataset repo: zips, `results.jsonl`, `reports/` (persists restarts) |
| `RCB_GT_REPO` | space | PRIVATE GT dataset, downloaded at startup to `$HOME/.rcb-gt` |
| `RCB_INPUTS_REPO` | space | public inputs dataset (task cards, previews, downloads) |
| `RCB_MAX_PARALLEL` | space | scorer subprocesses at once (default and deploy: `1`; cpu-basic has 2 vCPU) |
| `RCB_TASK_TIMEOUT` | space, local | seconds per scorer child (default `900`, deploy: `420`); the run watchdog (10 tasks x 2 children + reserve) follows it |
| `RCB_MAX_RUN` | space | cap of that watchdog in seconds (default and deploy: `2700` = 45 min): one upload never holds the scorer slot longer |
| `RCB_EVAL_IMAGE`, `RCB_JOB_FLAVOR`, `RCB_JOB_NAMESPACE`, `RCB_JOB_TIMEOUT` | hub | HF Jobs mode (needs PRO or an org with credits; not deployed): image built from `Dockerfile.eval`, hardware, namespace, job timeout |

### Local smoke

```
pip install -r space/requirements.txt ./spec ./evaluator
RCB_MODE=local RCB_LOCAL_ROOT=/tmp/rcb-space RECADBENCH_GT_DIR=<gt parts> python space/app.py
python -m pytest space/tests
```

## Deploy (owner only)

Everything is staged locally first and audited; nothing is uploaded until the owner runs `push`
while logged in (`hf auth login`). Hub layout (owner `mikbalarikan`):

| Repo | Type | Visibility | Content |
|---|---|---|---|
| `mikbalarikan/recadbench-inputs` | dataset | public | scans + task cards (`dataset/tools/export_hf.py`) |
| `mikbalarikan/recadbench-gt` | dataset | **private** | ground truth (`dataset/tools/export_hf.py`) |
| `mikbalarikan/recadbench-submissions` | dataset | **private** | zips, `results.jsonl`, `reports/` |
| `mikbalarikan/recadbench-open` | Docker Space | public | this app + the in-Space scorer (free `cpu-basic`) |

```
# 1. build + audit (outside any git checkout)
python space/deploy_hf.py stage --out <STAGING> [--owner mikbalarikan]

# 2. push (each push re-runs the audit; --dry-run shows the plan only)
python space/deploy_hf.py push --staging <STAGING> --what all-but-secrets
#    = --what submissions (private dataset + card), then --what space (public app + variables)
```

`stage` writes `STAGING/space/`: `Dockerfile` (from `docker/Dockerfile`), this README,
`requirements.txt` with every package pinned to the version installed where stage runs (gradio:
`--gradio-version`, default the installed one), every `space/*.py` except `deploy_hf.py` and
tests, `tasks_public.json` (public task titles), `assets/` (banner, flow and scan renders from
`docs/assets`), and `spec/` + `evaluator/` without tests or caches (pip-installed in the image);
plus `STAGING/submissions_card/README.md`. It refuses a Dockerfile whose OCP base drifted from
`Dockerfile.eval`, that does not COPY a staged app file, that lacks `SYSTEM=spaces`,
`PYTHONNOUSERSITE=1`, that has an ENTRYPOINT or an init wrapper (tini, dumb-init, ...) instead of
the app as PID 1 (that PID 1 would keep `HF_TOKEN` in a `/proc/1/environ` the scorer can read),
or that makes the app folder writable for uid 1000. The audit refuses STL/STEP files
outside `evaluator/golden`, GT file names (`gt.step`, `gt_plan.json`, `key_dims.yaml`,
`meta.yaml`, `gt_native*`, ...), anything that looks like an HF token, absolute Windows user
paths, tests, caches and a top-level `recadbench_eval/` (it would shadow the installed evaluator).

`push --what submissions` creates the dataset private and refuses to upload unless the Hub
reports it private. `push --what space` refuses when the submissions or GT dataset exists and is
not private, creates the public Docker Space and sets these Space **variables** (never secrets):
`RCB_MODE=space`, `RCB_SUBMISSIONS_REPO`, `RCB_GT_REPO`, `RCB_INPUTS_REPO`,
`RCB_ADMINS=<owner>`, `RCB_MAX_PARALLEL=1`, `RCB_TASK_TIMEOUT=420`, `RCB_MAX_RUN=2700`. It only
deletes stale `*.py` files in the Space.
Every push re-audits its folder: if anything was run inside the staged folder (Python writes
`__pycache__/` with absolute paths), re-run `stage`; smoke-test a copy, or use `python -B`.

Then, by hand in the Space settings (*Variables and secrets*), add the secret **`HF_TOKEN`**: a
fine-grained token with write on `datasets/<owner>/recadbench-submissions` and read on
`datasets/<owner>/recadbench-gt`, nothing else. Restart the Space, sign in as the owner and check
that the Admin tab is visible.
