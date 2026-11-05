# 06 — Session Prompts (copy-paste, numbered in dependency order)

Every prompt assumes the repo(s) it names are attached and this package is at `recadbench/docs/architecture/`. Each session ends by committing, opening a PR, and updating root `STATUS.md` of **every repo it touched**.

Dependencies: P1 → P2 → (P3, P5, P6 parallel) → P4 (needs P3 conformance) → P7 (needs P6) → (P8, P9 parallel) → P10 → P11 → P12 (recurring).

---

### P1 — Scaffold all five repos
Repos: `ReCADBench` (public; docs call it `recadbench`), `recadbench-cad-skills`, `recadbench-strategies`, `recadbench-data`, `recadbench-gt` (all private).
```
Read docs/architecture/README.md, 00_DECISIONS.md, 01_ARCHITECTURE.md, 02_REPO_LAYOUTS.md fully.
Scaffold all five repos exactly per 02_REPO_LAYOUTS.md: folders, pyproject.toml stubs, empty
packages, and a README.md in every leaf folder stating its purpose and which prompt fills it.
Copy templates/CLAUDE.<repo>.md to each repo root as CLAUDE.md. Create root STATUS.md in each repo.
Copy contracts/*.json → recadbench/spec/recadbench_spec/schemas/ and contracts/feature_taxonomy.yaml
→ recadbench/spec/recadbench_spec/. CI: ruff + pytest (empty test passes) + dataset/tools/check_public.py
guard in the public repo (rejects STL/STEP/PRT/gt_plan/key_dims files outside synthetic fixtures).
Document the token setup from 00_DECISIONS D3 in ReCADBench/README.md (no tokens committed).
Termux spike: on one phone, install build123d + pzfreo/build123d-mcp using the method the PoCAD
repo (mikbalarikan/PoCAD) already uses; confirm a STEP export works; record exact steps in
recadbench/backends/build123d/install.md. No other logic. Missing pieces → STATUS.md "Open questions".
```

### P2 — Spec package
```
Implement recadbench-spec: pydantic v2 models for every schema, taxonomy loader with counting
rule, IR `type` enum + per-type params check generated from the taxonomy, `recadbench-spec
validate <file>` CLI, round-trip tests (1 valid + 2 invalid examples per schema). Tag spec-v0.1.0.
Schema changes you think are needed → ADR in docs/adr/, not silent edits.
```

### P3 — L1 backends: build123d, Solidcraft, FreeCAD + conformance suite (`recadbench/backends`)
```
Read 05_TOOLS.md. For build123d (pin pzfreo/build123d-mcp), solidcraft (pin a tangency-labs/
solidcraft release, interface `solidcraft-cli mcp`) and freecad (evaluate existing FreeCAD MCP
servers — at least neka-nat/freecad-mcp and freecad-robust-mcp — on headless support, code
execution, STEP export, activity; pick one and justify in an ADR): write backend.yaml, install.md,
and the adapter CLI `recad <b> export|extract|set-param|replay` (+ execute-ir for build123d).
Author the 10 conformance IR cases with expected volume/faces/feature count and the replays for
these three backends; all must pass with identical feature counts. Do NOT write agent-facing
skills here (that is recadbench-cad-skills). Test whether Solidcraft ARM64 CLI runs on Termux.
```

### P4 — NX backend + port existing NX harness (needs desktop access; repos `recadbench`, `recadbench-cad-skills`)
```
Read 05_TOOLS.md (NX rows, nx-run contract, NX skillset seed table) and both repos' CLAUDE.md.
1) Backend: implement recadbench/backends/nx/runner (`recadbench nx-run <journal.py>` over
   run_journal.exe per the contract), adapter (NX Open journals: export, extract feature tree →
   taxonomy, set-param via expressions), replays for the 10 conformance cases; pass conformance.
   Fill backend.yaml (interface: cli, allowed_commands, max_parallel: 1).
2) Port: on the user's computer, locate the existing NX harness (skill/agent .md files, NX Open
   code/journals, example projects). Ask the user for folders if not obvious; list what you find
   before copying. Restructure into recadbench-cad-skills/nx/ per 02_REPO_LAYOUTS: skills/,
   agents/, lib/nxhelpers/ (reusable modeling helpers), examples/ (redact client-identifying data),
   LESSONS.md. Measurement-type code (feature dump/export/expression edit) goes to
   recadbench/backends/nx/adapter instead. Any RE methodology → MIGRATION_NOTES.md (for P9).
   Tag nx-v1. Never modify or delete originals. Report a table: original → new location / dropped (why).
```

### P5 — RE toolkit (`recadbench-strategies/toolkit`)
```
Implement retk as an MCP server per 01_ARCHITECTURE §3: mesh_stats, slice, fit_primitives (RANSAC
+ region growing, params + inlier %), detect_holes, snap_nominal (R10/R20, ISO metric drill and
clearance tables), self_deviation (two-way sampling vs input STL, % within bands, P95, per-region
summary), render (optional, skip without GL). Must run on Termux (numpy/scipy/trimesh; open3d
optional). Tests on synthetic meshes of known primitives.
```

### P6 — Dataset + GT tooling (`ReCADBench/dataset/tools`, `recadbench-data`, `recadbench-gt`)
```
Read docs/adr/0002-real-scan-input.md first: inputs are REAL SCANS (spec 0.2.0).
Implement dataset/tools/register_gt.py (GT-authoring, evaluator host): scan_raw.stl + gt.step
(+ optional initial alignment, e.g. an open-dedica alignment.json or 3 picked point pairs) →
masked ICP (fixed seed) → meta.yaml registration, scan_floor, observability. Reuse the ICP/mask
approach of mikbalarikan/agentic_STL-to-CAD skills/stl-re-verify (port, don't import).
Implement dataset/tools/make_task.py: GT folder → recadbench-data/parts/<id>/{input.stl
(byte-identical copy of the scan), task.yaml (input_kind, scan metadata)} + MANIFEST.json and
splits; synthetic mode (tessellate gt.step, chord ≤ 1/3 tight band) for calibration parts only.
Decide Git LFS vs Drive for 18–50 MB scans. Write recadbench-gt/AUTHORING.md (gt_plan.json,
key_dims.yaml, n_ref, registration, provenance/contamination fields). Create 3 pilot GT parts from
the R385 scan-rebuild set (human NX GT, dev split) in recadbench-gt; validate every file with
recadbench-spec. No part data, scan or GT ever enters the public ReCADBench repo.
```

### P7 — Evaluator (`recadbench/evaluator`)
```
Implement recadbench-eval per 03_SCORING.md (incl. the ADR 0002 scan rules: map by T_scan_to_gt,
observable region, band_eff from the scan floor, inferred region reported): gate (incl. unmapped ⇒ fail), align, S1 vs gt.step,
S2 (bounds + parametric presence), S3, S4, plan_score, score.py → result.json, markdown/CSV report.
S5 behind a flag. Golden tests on synthetic parts (public) and the 3 R385 pilot parts (evaluator host only) with hand-made candidates: good, lego,
faceted, dumb_solid, wrong_dim (9.98 vs 10), snap_everything — assert ordering and PASS flags.
Evaluator reads worker-produced features.json; never imports backend code.
```

### P8 — Skillsets v1 for build123d, Solidcraft, FreeCAD (`recadbench-cad-skills`)
```
Read 05_TOOLS.md L2 rules and nx/ (from P4) as the reference structure. Write _shared/ and
build123d/, solidcraft/, freecad/ skillsets: skills/, agents/, lib/, examples/ (one per taxonomy
type, verified by running against the pinned backend), LESSONS.md (empty table). Parameters must
be named after IR parameters. CI: validate skillset.yaml with recadbench-spec; grep that no
skill contains RE methodology keywords listed in CLAUDE.md. Tag build123d-v1, solidcraft-v1,
freecad-v1.
```

### P9 — Strategies (`recadbench-strategies/strategies`)
```
Read 04_STRATEGIES.md and recadbench-cad-skills/nx/MIGRATION_NOTES.md. Write strategy.yaml,
playbook.md, stages/*.md, skills/SKILL.md for oracle, A, B, C, D (folder name == id), plus
shared/ir_authoring.md and shared/design_intent_rules.md. CI grep: no CAD program/command names
in playbooks. Validate strategy.yaml with recadbench-spec.
```

### P10 — Runner, compose, gate, orchestrator (`recadbench/runner`, `orchestrator`)
```
Implement `recadbench compose` exactly per 00_DECISIONS D6 (both --skillset <b>@vX and
--skillset none --backend <b>), gate_hook.py (PreToolUse for Claude Agent SDK + interactive;
same check in the LiteLLM dispatcher; shell denied except backend.yaml allowed_commands; plan
revision rule), render_prompt.py for non-Claude models, agent backends (Claude Agent SDK, LiteLLM
loop for DeepSeek/Qwen/GLM/Nemotron), `recadbench finish` (post-run extraction on worker +
enqueue), SQLite queue, pull-based worker.py (Termux-capable), expand.py. Tests: hook blocks CAD
without plan, blocks shell, allows `recadbench nx-run cad/x.py` only for nx, requires N+1 after a
CAD change. Dry-run: oracle × build123d@v1 × 1 dev pilot part end-to-end.
```

### P11 — Pilot + calibration (M3)
```
Run experiments/pilot.yaml on the 3 dev pilot parts × {opus-5.5, sonnet-5.5, one open model} ×
1 replicate: Track T (oracle × {none, v1} × {build123d, solidcraft, freecad, nx}), Track S
plan-only (A, B, C), Track E (A, B, D × build123d@v1; B × nx@v1). Write a calibration memo: do
S1 bands, S2 curve, gate rules and PASS separate good from bad? Is Δ(skillset − none) visible?
Propose threshold changes as an ADR. After acceptance, tag spec-v1.0.0.
```

### P12 — Skillset improvement loop (recurring, one backend per session)
```
For skillset <b>: read the latest Track T dev results and failure logs in results/. Cluster
recurring failures, add LESSONS.md entries, update skills/agents/lib, tag <b>-v(N+1), enqueue
Track T dev for the new version vs previous vs none. Never look at test-split results.
```

---

## Ad-hoc usage (after P10)

Interactive pairing:
```
In recadbench run `recadbench compose --strategy B --skillset nx@v1 --part 017 --out
runs/manual_B_nx_017`, then work only inside that run dir following its CLAUDE.md.
When done run `recadbench finish runs/manual_B_nx_017`.
```

Batch:
```
Create experiments/<name>.yaml: strategies [C, D] × skillsets [build123d@v2, solidcraft@v1] ×
models [sonnet-5.5, qwen-…] × dev split × 3 replicates; expand; report cost estimate before enqueueing.
```
