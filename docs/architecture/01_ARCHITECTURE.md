# 01 — Architecture

## 1. Data flow (Track E)

```
 recadbench compose  ──► runs/<run_id>/  (CLAUDE.md, skills, agents, settings, .mcp.json, lib/, manifest)
                                │
   ┌──────────────────────── Agent session ────────────────────────┐
   │ L3 STRATEGY (recadbench-strategies/strategies/<S>)             │
   │    stages: intake → analyze → datum → plan → execute → verify  │
   │    retk MCP: mesh_stats, slice, fit_primitives, detect_holes,  │
   │              snap_nominal, self_deviation, render              │
   │        │ writes plan/re_plan.v1.json (RE Plan IR)              │
   │        ▼                         [gate_hook: CAD locked until] │
   │ L2 SKILLSET (recadbench-cad-skills/<b>)  skills + agents + lib │
   │        │ translates IR → native operations                     │
   │        ▼                                                       │
   │ L1 BACKEND (pinned external)                                   │
   │    MCP: build123d-mcp | solidcraft-cli mcp | freecad MCP       │
   │    CLI: recadbench nx-run cad/step_NN.py  → run_journal.exe    │
   │        ▼                                                       │
   │   cad/ native file  →  out/output.step                         │
   └────────────────────────────────────────────────────────────────┘
                                │
   post-run on WORKER: recadbench/backends/<b>/adapter → features.json, output.step, hashes
                                │
                                ▼
   evaluator (evaluator host, GT mounted read-only)
     gate → align → S1 (vs gt.step) → S2 → S3 → S4 → S5 (queued to backend host) + plan score
                                │
                                ▼
   results/runs.sqlite → leaderboard (per track; per layer)
```

Track T: strategy = `oracle` (hook pre-seeds GT plan). Track S: backend = `none`, stop after plan.

## 2. Contracts (`recadbench/spec/`, mirrored in `contracts/`)

| Contract | Producer → Consumer | Purpose |
|---|---|---|
| `task.schema.json` | dataset → agent | Per-part task card |
| `re_plan_ir.schema.json` | L3 → L2, evaluator | Tool-neutral feature plan (the seam) |
| `feature_taxonomy.yaml` | spec → all | Canonical feature types + counting rule |
| `backend_manifest.schema.json` | `backends/<b>/backend.yaml` → runner, gate_hook | L1 pin: install, interface (mcp/cli), CAD prefixes / allowed commands, adapter entry points, hosts, parallelism |
| `skillset_manifest.schema.json` | `recadbench-cad-skills/<b>/skillset.yaml` → compose | L2 bundle: target backend + version range, skills, agents, lib, version |
| `strategy_manifest.schema.json` | `strategies/<id>/strategy.yaml` → compose | L3 bundle: stages, playbook, retk tools, knobs |
| `run_manifest.schema.json` | orchestrator → runner | Full pin of one run |
| `features.schema.json` | adapter → evaluator | Normalized features extracted from the native model |
| `result.schema.json` | evaluator → results DB | Metrics, subscores, pass, versions |
| `key_dims.schema.json`, `gt_meta.schema.json` | GT author → evaluator, make_task | Critical dims, class, tier, n_ref, face stats |

### Backend adapter interface (in `recadbench/backends/<b>/adapter/`, measurement instrument)

| Entry point | Runs on | Contract |
|---|---|---|
| `recad <b> export <native> → output.step` | worker | Canonical export (mm, single solid) |
| `recad <b> extract <native> → features.json` | worker, post-run, clean process | Native tree → taxonomy; never agent self-report. Non-parametric bodies ⇒ `unmapped` ⇒ gate fail |
| `recad <b> set-param <native> <name> <value> → output.step` | backend host (queue job) | S5 edit + rebuild |
| `recad <b> replay <case>` | worker | Conformance: replays a hand-written native script per IR case |
| `execute_ir` | build123d only | Deterministic IR → STEP (validates the IR itself) |

NX adapter = NX Open journals run through `run_journal.exe` (same mechanism as the agent, separate process).

### Skillset content (in `recadbench-cad-skills/<b>/`, agent-facing)

| Item | Purpose |
|---|---|
| `skillset.yaml` | conforms to `skillset_manifest.schema.json` |
| `skills/<name>/SKILL.md` | How to model in this software: IR feature type → native ops, single-solid discipline, naming, export, measuring, recovery |
| `agents/*.md` | Subagent definitions (e.g. `nx-journal-writer`, `nx-debugger`) |
| `lib/` | Helper code the agent imports from its own scripts (e.g. NX Open Python helpers). Read/import only; compose copies it into the run dir |
| `examples/` | IR snippet → native code, one per taxonomy type; real past-project excerpts |
| `LESSONS.md` | Error → fix table distilled from Track T failures |

### Strategy content (in `recadbench-strategies/strategies/<id>/`)

| Item | Contract |
|---|---|
| `strategy.yaml` | conforms to `strategy_manifest.schema.json`; folder name == id |
| `playbook.md`, `stages/*.md` | Tool-agnostic; CI grep forbids CAD program/command names |
| `skills/SKILL.md` | Copied into run dir by compose |
| Output | `plan/re_plan.v1.json` before first CAD call — enforced by gate_hook |

## 3. RE toolkit (`recadbench-strategies/toolkit/retk`)

| Tool | Function |
|---|---|
| `mesh_stats` | bbox, volume, area, watertightness, PCA axes, genus estimate |
| `slice` | Planar cross-sections → polylines + fitted lines/arcs |
| `fit_primitives` | RANSAC/region growing → planes/cylinders/cones/spheres/tori + inlier % |
| `detect_holes` | Holes: axis, Ø, depth, nearest standard size |
| `snap_nominal` | Value → nearest preferred number / drill / clearance, with residual |
| `self_deviation` | Candidate STEP vs **input STL**: two-way % within bands, P95, per-region summary |
| `render` | Multi-view PNG (optional, H2) |

`self_deviation` uses the same algorithm as S1 but against the STL; the evaluator samples `gt.step`. With `make_task` chord ≤ ⅓ tight band the self-check is a close proxy, not the score.

Overlap note: build123d-mcp has its own measure/compare/hole tools. They are L1 and stay available, but strategies must use `retk` for their analysis so strategy behavior is identical across backends.

## 4. Isolation and reproducibility

1. GT never on workers (except GT plan in Track T, dev only).
2. `gate_hook.py` enforces plan-before-CAD and shell allowlist (D6).
3. Fresh run dir per run; no cross-run memory in v1.
4. Artifacts hashed; spec, evaluator, skillset, strategy, backend versions in `result.json`.
5. Rubric change ⇒ re-score stored outputs, never re-run agents.

## 5. Compute placement

| Node | Role |
|---|---|
| ASUS TUF | Orchestrator, evaluator (single deterministic host), NX worker (parallelism 1) |
| MateBook | NX / FreeCAD / Solidcraft worker |
| 5 phones (Termux) | build123d-mcp workers (PoCAD install method), Solidcraft if ARM64 CLI runs, retk, post-run extraction. No evaluator |
| Drive / GitHub | Artifacts on Drive; `recadbench-data` pulled by workers; `recadbench-gt` only on evaluator host |
