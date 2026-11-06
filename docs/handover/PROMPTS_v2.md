# Remaining work as prompts (v2, after ADR 0002)

These prompts supersede P3–P11 in `docs/architecture/06_SESSION_PROMPTS.md` where they differ. The main session
runs each one as a **subagent or workflow** and reviews the result before merging.

Each prompt assumes:
- all 5 repos are cloned side by side on the ASUS (SSH remotes);
- `docs/architecture/`, `docs/adr/` and `docs/handover/` have been read;
- the session ends with PRs plus STATUS.md updates in every repo it touched.

Order and parallelism:

```
H0 bootstrap ─┬─ H1 P3-desktop ─┐
              ├─ H2 P5-phone    │
              ├─ H3 spec 0.3.0 ─┼─ H5 P7 evaluator ─┐
              └─ H4 P4 NX ──────┤                   │
                                ├─ H6 P8 skillsets ─┼─ H8 P10 runner ─ H9 pilot plan ─ STOP (user starts P11)
                                └─ H7 P9 strategies ┘
```

H1, H2, H3 and H4 can run in parallel. H6 and H7 can run in parallel once H3 is merged. H5 needs H3 for any spec
change it relies on.

---

### H0 — Bootstrap the desktop main session
```
1. Clone (SSH) ReCADBench, ReCADBench-cad-skills, ReCADBench-strategies, ReCADBench-data, ReCADBench-gt and
   mikbalarikan/agentic_STL-to-CAD side by side. Read ReCADBench/docs/handover/HANDOVER.md and
   ReCADBench-gt/HANDOVER_PRIVATE.md.
2. Create a dev venv (Python >= 3.11): pip install -e ReCADBench/spec -e ReCADBench/evaluator -e ReCADBench/runner
   -e ReCADBench/backends -r ReCADBench/dataset/requirements.txt -e ReCADBench-strategies/toolkit[server,render,test]
   build123d==0.13.0 build123d-mcp==0.3.91 ruff pytest. Record exact versions in HANDOVER_PRIVATE.md.
3. Review the open handover PRs (ReCADBench #8: P3; ReCADBench-strategies #2: P5; ReCADBench-gt #4) with an independent review subagent.
   Run their suites locally. Fix or comment, then merge when green.
4. Re-sync every working branch with main after the squash merges.
```

### H1 — P3 on the desktop (backends: build123d, FreeCAD, Solidcraft)
```
Install FreeCAD (conda-forge or the official 1.x/26.x installer) and Solidcraft v0.30.1 (Windows installer or cargo
build). Set RECAD_BUILD123D_PYTHON / RECAD_FREECAD_PYTHON / RECAD_SOLIDCRAFT_CLI and run
python backends/conformance/run_conformance.py on all three. Update backends/conformance/CONFORMANCE.md and each
backend.yaml conformance block (host = windows). Fix Windows-only issues (paths, LF, encodings). Check that the
build123d-mcp and solidcraft-cli MCP tool lists still match backend.yaml cad_prefixes/mutating.
```

### H2 — P5 and Solidcraft on the phone (Termux)
```
On one phone (proot Ubuntu env "recad" from backends/build123d/install.md): pip install the retk toolkit
(ReCADBench-strategies/toolkit) and run its test suite plus one MCP smoke call through proot from the Termux side.
Then follow backends/solidcraft/install.md "Termux test" steps. Record both in the respective install.md/README
spike tables. Phones are the planned build123d + retk workers.
```

### H3 — Spec 0.3.0 (batch of known gaps)
```
ADR 0004 + spec 0.3.0. Decide and implement, with tests and examples:
- {"ref"} allowed for sketch entity numbers (so sketch dims can be named parameters; S5);
- typed taxonomy params (feature_taxonomy.yaml params: {name: type}), with the compiled IR schema type-checking them;
- define pattern_circular.angle_deg (total sweep vs pitch). The R385 pilot gt_plan.json files used pitch: migrate them;
- anything P3/P5 found (see their STATUS.md open questions).
Re-validate every contract file in all repos (pilots, conformance cases, backend.yaml) against 0.3.0, then tag
spec-v0.3.0 (push the tag from the desktop) and bump the pins.
```

### H4 — P4: NX backend and the existing NX harness (desktop only)
```
Read 05_TOOLS.md (NX rows, the nx-run contract, the NX skillset seed table) and both repos' CLAUDE.md.
The NX install path, run_journal.exe and licence details are in HANDOVER_PRIVATE.md.
1) Backend: backends/nx/runner (`recadbench nx-run <journal.py>` over run_journal.exe: timeout, log capture,
   cad/last_run.json). Adapter (NX Open journals: export, extract feature tree → taxonomy, set-param via
   expressions). Replays for the 10 conformance cases, which must pass with the same counts as the other backends.
   backend.yaml: interface cli, allowed_commands, max_parallel 1.
2) Port: locate the existing NX harness (skill/agent .md files, NX Open code, example projects — e.g. the
   locations listed in HANDOVER_PRIVATE.md).
   List what you find before copying. Restructure it into ReCADBench-cad-skills/nx/:
   - skills/, agents/, lib/nxhelpers/, examples/ (redact client data), LESSONS.md;
   - measurement code goes to backends/nx/adapter;
   - RE methodology goes to MIGRATION_NOTES.md (for H7).
   Never modify originals. Tag nx-v1. Report original → new location / dropped.
```

### H5 — P7: evaluator (scan rules)
```
Implement recadbench-eval per 03_SCORING.md and ADR 0002:
- gate (incl. unmapped ⇒ fail);
- align: map the prediction from the scanner frame by T_scan_to_gt, then a fixed-seed fine ICP;
- S1 vs gt.step on the OBSERVABLE region (use observability_mask.npz: nearest-neighbour lookup of evaluator
  samples), band_eff = max(class band, k·scan_floor p95) with k from config (1.5); report the inferred region;
- S2 (counts from worker-produced features.json, never self-report), S3 key dims (class tolerance, intrinsic dims,
  standard credit), S4, plan_score (IR vs gt_plan.json), score.py → result.json (validate), report (markdown/CSV);
- S5 behind a flag.
The evaluator never imports backend code and reads GT only via RECADBENCH_GT_DIR.
Golden tests:
(a) public/CI: synthetic parts generated in-test (no files from -gt);
(b) evaluator-host only (skip without RECADBENCH_GT_DIR): the 3 R385 pilots with hand-made candidates (good =
    gt.step itself, lego, faceted, dumb_solid, wrong_dim 9.98 vs 10, snap_everything) — assert ordering and PASS.
Reuse ideas from agentic_STL-to-CAD/skills/stl-re-verify (port, don't import) and dataset/tools/scanreg.py.
```

### H6 — P8: skillsets v1 (build123d first; Solidcraft and FreeCAD after)
```
Read 05_TOOLS.md L2 rules, ReCADBench-cad-skills/CLAUDE.md and nx/ (from H4, if done) as the reference structure.
Write _shared/ir_execution.md and _shared/single_solid_rules.md. Then build123d/: skillset.yaml, skills/, agents/,
lib/, examples/ (one per taxonomy type, verified against the pinned backend via `recad build123d execute-ir`/replay),
LESSONS.md (empty table). Seed: agentic_STL-to-CAD/skills/stl-re-rebuild-build123d. Keep only HOW-to-operate-build123d
content. Measurement/intent content goes to strategies (H7). Name parameters after IR parameters.
CI: validate skillset.yaml with recadbench-spec; grep for RE-methodology keywords (define the list in CLAUDE.md).
Tag build123d-v1. Then solidcraft-v1 and freecad-v1 if time allows (not required for the first test).
```

### H7 — P9: strategies
```
Read 04_STRATEGIES.md, the cad-skills nx/MIGRATION_NOTES.md (if H4 done) and the agentic_STL-to-CAD stl-re-*
skills. Write strategies/oracle, A, B, C, D (folder == id): strategy.yaml (validated), playbook.md, stages/*.md,
skills/SKILL.md. Also shared/ir_authoring.md and shared/design_intent_rules.md.
- Strategies analyse the RAW SCAN in the scanner frame with retk only. They choose datums, write
  plan/re_plan.v1.json before any CAD call, and refine via self_deviation (scan proxy).
- D = the stl-re pipeline (intake → measure → rebuild → verify loop, max 3), made CAD-agnostic.
- CI grep: no CAD program/command names in playbooks.
eval_local/: plan-only scoring on dev pilots (imports recadbench-eval plan_score; runs only where RECADBENCH_GT_DIR
exists).
```

### H8 — P10: runner, compose, gate, orchestrator
```
Implement `recadbench compose` per 00_DECISIONS D6:
- both --skillset <b>@vX and --skillset none --backend <b>;
- copies input/ (scan + task.yaml), strategy skills, skillset skills/agents/lib, .mcp.json (retk with the
  strategy's tool subset + L1 MCP), .claude/settings.json with gate_hook;
- **Track T: pre-seeds plan/re_plan.v1.json from gt_plan.json mapped into the scanner frame with
  inv(T_scan_to_gt)**, shipped in the job payload, never from repo access.
gate_hook.py (PreToolUse + LiteLLM dispatcher check; shell denied except backend allowed_commands; plan-revision
rule). The gate must also deny the L1 tools that import or analyse the scan inside the CAD program
(build123d-mcp import_cad_file / mesh_* / find_holes / recognise_features / compare, FreeCAD import_stl,
SolidCraft pointcloud.*; see STATUS.md open questions from the P3 review), so analysis goes through retk only. render_prompt.py. Agent backends (Claude Agent SDK; LiteLLM loop). `recadbench finish` (post-run extract
via `recad <b> extract` + export, hashes, enqueue). SQLite queue, pull-based worker.py (Termux-capable), expand.py.
Tests: the hook blocks CAD without a plan, blocks shell, allows `recadbench nx-run cad/x.py` only for nx, and
requires N+1 after a CAD change. Dry run: oracle × build123d@v1 × r385_hs01_motor_mount end to end → result.json.
```

### H9 — First-test plan (then STOP)
```
Write orchestrator/experiments/pilot.yaml: 3 R385 pilots × strategies {oracle (Track T), B, D (Track E)} ×
skillsets {build123d@v1 (+ nx@v1 if H4 done), none for Track T baseline} × models {claude-opus-5-5,
claude-sonnet-5-5} × 1 replicate; Track S plan-only for B. Run expand.py, report the run count, and estimate
cost/wall-time per host (NX parallelism 1). Check the HANDOVER.md §6 definition of done item by item with
evidence. Then STOP and ask the user to start P11.
```
