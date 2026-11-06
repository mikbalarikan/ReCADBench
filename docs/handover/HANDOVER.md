# ReCADBench — handover to the main desktop session

Written 2026-11-06 by the cloud session that built P1–P6. It hands everything over to a **main session on the
ASUS evaluator host**, which manages the remaining work with subagents until the **first benchmark test** can start.

- Private companion, with desktop paths, data provenance and client notes:
  `ReCADBench-gt/HANDOVER_PRIVATE.md`. It is only on the evaluator host.
- Remaining work as self-contained prompts: [`PROMPTS_v2.md`](PROMPTS_v2.md).
- Text to start the main session: [`MAIN_SESSION_BRIEF.md`](MAIN_SESSION_BRIEF.md).

## 1. What ReCADBench is (current design)

A **real 3D scan** goes in (`input.stl`: raw, noisy, holed, in the scanner frame). A clean, single-solid,
parametric CAD model capturing **design intent** has to come out. Three layers are evaluated separately:

| Layer | What | Repo | Track |
|---|---|---|---|
| L1 backend | pinned off-the-shelf CAD interface (build123d-mcp, `solidcraft-cli mcp`, a FreeCAD MCP, NX via `run_journal.exe`) | `ReCADBench/backends/` | — (measurement instruments) |
| L2 skillset | our know-how for operating each CAD program | `ReCADBench-cad-skills` | T |
| L3 strategy | CAD-agnostic RE method A–D (+ oracle), plus the `retk` toolkit | `ReCADBench-strategies` | S, E |

Read `docs/architecture/` in this order: README, 00_DECISIONS (D1–**D10**), 01, 02, 03, 05. Then
`docs/adr/0001`–`0003`. **ADR 0002 overrides the original plan wherever they disagree:** inputs are real scans, v1 is
dev-only, there's a registration step, a noise floor and observability.

## 2. State at handover

| Prompt | Status | Where |
|---|---|---|
| P1 scaffold + Termux spike | ✅ done. build123d-mcp exports STEP on the phone | all repos; `backends/build123d/install.md` |
| P2 spec | ✅ `recadbench-spec` 0.1 → **0.2.0** (ADR 0002), tag **`spec-v0.2.0`** | `spec/` |
| ADR 0002 real scans | ✅ accepted | `docs/adr/0002-real-scan-input.md`, D10 |
| P6 tooling | ✅ `register_gt.py`, `make_task.py`, `scanreg.py`, AUTHORING.md, Windows fixes (#7) | `dataset/tools/`, `ReCADBench-gt/AUTHORING.md` |
| P6 pilots | ✅ 3 R385 scan-rebuild parts (human NX ground truth), registered, in `-gt` and `-data` | `ReCADBench-gt/parts/`, `ReCADBench-data/parts/` |
| **P3 backends** | 🟡 built in the cloud session (build123d, FreeCAD, Solidcraft adapters, 10 conformance cases, `recad` CLI). **Review and merge the PR, then re-run conformance on the ASUS** | `ReCADBench` #8 |
| **P5 retk** | 🟡 built in the cloud session (7 tools + MCP server). **Review and merge the PR, then check on Termux** | `ReCADBench-strategies` #2 |
| P4 NX backend + harness port | ⬜ needs the desktop (NX 2206, your existing NX harness files) | `backends/nx/`, `ReCADBench-cad-skills/nx/` |
| P7 evaluator | ⬜ | `evaluator/` |
| P8 skillsets v1 | ⬜ | `ReCADBench-cad-skills` |
| P9 strategies | ⬜ | `ReCADBench-strategies/strategies/` |
| P10 runner / compose / gate / orchestrator | ⬜ | `runner/`, `orchestrator/` |
| P11 first benchmark test (pilot) | ⬜ the goal | — |

Pilot ground-truth numbers (scan-to-GT, mm; all converged; tolerance class B):

| Part | Tier | n_ref | RMSE | Floor p50 / p95 | Observable |
|---|---|---|---|---|---|
| r385_dr02_wobble_plate | 3 | 12 | 0.055 | 0.039 / 0.201 | 87 % |
| r385_hs01_motor_mount | 2 | 10 | 0.058 | 0.041 / 0.195 | 77 % |
| r385_hs04_pump_head | 2 | 18 | 0.090 | 0.064 / 0.346 | 82 % |

## 3. Decisions already made (do not re-open without an ADR)

1. **The input is the raw scan,** byte-identical, in the scanner frame. Synthetic inputs are calibration only
   (ADR 0002).
2. **Sources and contamination:**
   - **Sources:** `scan_rebuild` (v1: R385 plus the golden examples), `print_scan` (later: OGUZ
     own-design prints, which become the clean **test** set), `synthetic` (calibration).
   - **The validator blocks test-split parts** that are contaminated, have agent-made ground truth, or are
     synthetic. So **v1 is dev-only** and has no public leaderboard.
3. **open-dedica is kept aside.** It can return later as reviewed calibration reference STEPs. The
   `agentic_STL-to-CAD` golden examples are cleared for the dev pool, minus M02 (CAD not fitted to the scan);
   OD-H12 is calibration-only.
4. **Scoring S1** is against `gt.step` (never the scan), mapped through `registration.T_scan_to_gt`:
   - only the observable region is scored;
   - bands are `max(class band, k·floor p95)` with **k = 1.5 provisional**;
   - the inferred region is reported but not scored in v1.
5. **`gt_plan.json` and `key_dims.yaml` are in the gt.step frame.** P10 must map the ground-truth plan into the
   scanner frame with `inv(T_scan_to_gt)` before Track T hands it to an agent.
6. **Scan storage: plain git, no LFS,** for v1 (all R385 scans are under 40 MB). Open: golden scans over 100 MB.
7. **Tiers are provisional:** 1 = prismatic/turned; 2 = adds fillets, patterns, shells and drafts;
   3 = genuine freeform. Threads alone don't make a part tier 3. Confirm in P11.
8. **Repos and access:**
   - one public repo plus four private ones;
   - worker tokens are read-only and never include `-gt`;
   - `check_public.py` blocks STL, STEP, PRT and ground-truth files in the public repo;
   - `make_task.py` refuses to write into the public repo.

## 4. Rules for every session (from the CLAUDE.md files plus lessons learned)

- **Contracts:**
  - `spec/` is the source of truth. A schema change needs an ADR plus a version bump plus a tag
    (`spec-vX.Y.Z`).
  - The cloud sessions **could not push tags**, so tags must be pushed from the desktop or phone.
  - Downstream repos pin `recadbench-spec @ git+https://github.com/mikbalarikan/ReCADBench@spec-v0.2.0#subdirectory=spec`.
- **Ground truth and data hygiene:**
  - Ground truth never enters the public repo or a worker's run directory.
  - Agent self-checks use the scan; the evaluator uses `gt.step`.
  - Never read test-split results while tuning (there is no test split yet anyway).
- **Every session:**
  - Work on a branch, open a PR, and merge only with green CI.
  - Update root `STATUS.md` in **every repo it touched**.
  - Run `ruff` + `pytest` + `python dataset/tools/check_public.py` before pushing in the public repo.
  - After a squash merge, re-sync the branch with `main` before new work, otherwise you'll hit add/add
    conflicts (seen three times).
- **Windows:** write text files with LF and keep console output ASCII (see #7). Use a venv with Python ≥ 3.11
  (the existing desktop venv is named in HANDOVER_PRIVATE.md). GitHub works over **SSH**.
- **Quality bar used so far:** synthetic tests with known ground truth for every tool, independent
  adversarial review of each module (a fresh agent that must reproduce defects), then fixes and a completeness critic.
  Keep using it, now with real pilot data available on the ASUS.

## 5. Open items at handover

- **PRs to review and merge first** (opened by the cloud session at handover):
  - `ReCADBench` #8: P3 backends + conformance + these handover docs;
  - `ReCADBench-strategies` #2: P5 retk;
  - `ReCADBench-gt` #4: HANDOVER_PRIVATE.md.

  Re-run their test suites on the ASUS before merging.
- **P3 on the desktop:**
  - re-run `backends/conformance/run_conformance.py` with the local build123d, FreeCAD and Solidcraft
    installs. Solidcraft ships Windows installers; FreeCAD comes from conda-forge or the official installer;
  - **Solidcraft on Termux (ARM64)** is untested. Steps are in `backends/solidcraft/install.md`, so run them on a phone.
- **P5 on the phone:** `retk` must run in the phone's proot Ubuntu `recad` env (numpy, scipy, trimesh, rtree).
- **Spec gaps** (from STATUS.md):
  - sketch dimensions can't be `{"ref"}` parameters;
  - param values are untyped;
  - `pattern_circular.angle_deg` meaning is undefined (the pilots used it as the pitch);
  - the scanner scale was never verified against a reference artifact.

  Batch these into one ADR and spec 0.3.0 before P8 and P9 rely on them.
- **Golden scans over 100 MB** (GE-02 209 MB, GE-04 250 MB): decide between LFS for those files,
  Drive, or a lossless compressed copy before adding them.
- **Dev pool after the pilots:** the other R385 parts (HS02, SL01 and SL03 include the scanner table; HS03 has a
  merge error, so dev-only or out) plus golden examples GE-01 to GE-04. Author them per AUTHORING.md
  once P7 works on the pilots.

## 6. Definition of done: "ready to initiate the first benchmark test"

All of the following, verified on the ASUS:

1. P3 and P5 merged; conformance passes on build123d (and FreeCAD and Solidcraft where installed);
   `retk` runs on the ASUS and on one phone.
2. **P7 evaluator** implements 03_SCORING with the ADR 0002 scan rules. Golden tests pass: synthetic parts in CI,
   plus the 3 R385 pilots on the ASUS, with the good / lego / faceted / dumb_solid / wrong_dim / snap_everything
   orderings and PASS flags.
3. **P8:** `_shared/` plus a **build123d skillset v1**, seeded from `agentic_STL-to-CAD/skills/stl-re-rebuild-build123d`
   and split so it contains no RE methodology, tagged `build123d-v1`. The NX skillset `nx-v1` comes from P4 if done
   (recommended, since NX is your main tool).
4. **P9:** strategies `oracle` plus at least **B** and **D**, with `retk` wired in, playbooks free of CAD names, and
   `strategy.yaml` validated. D is ported from the `stl-re-*` orchestrate, intake and measure skills.
5. **P10:**
   - `recadbench compose` (including the scanner-frame mapping of the ground-truth plan for Track T), `gate_hook.py`,
     the Claude Agent SDK runner, `recadbench finish`, the queue and worker;
   - the gate-hook tests pass;
   - a **dry run of oracle × build123d@v1 × r385_hs01** goes end to end and produces a `result.json`.
6. `orchestrator/experiments/pilot.yaml` exists for the first test: 3 pilots × {oracle, B, D} × build123d@v1
   (+ nx@v1) × 1–2 models × 1 replicate. Run `expand.py` and give the user a cost and wall-time estimate.
7. **All repos:** STATUS.md updated, CI green, and the `spec-v0.x` tag current.

The main session then **stops and asks the user to start P11**, the first benchmark test.
