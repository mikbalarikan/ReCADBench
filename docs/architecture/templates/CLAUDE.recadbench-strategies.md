# recadbench-strategies — CLAUDE.md

Tool-agnostic reverse-engineering strategies (`strategies/oracle|A|B|C|D`) + the shared RE toolkit MCP (`toolkit/retk`). Architecture lives in the `recadbench` repo: `docs/architecture/` (04_STRATEGIES.md, 01_ARCHITECTURE.md §3).

## Hard rules
- Playbooks must not name any CAD program or command (CI grep enforces). They decide WHAT to build and in what reasoning order; CAD skillsets (recadbench-cad-skills) decide HOW.
- Every strategy writes a schema-valid `re_plan.v1.json` (recadbench-spec RE Plan IR) before any CAD call; refinements write v2, v3… with change_log entries tied to a region/feature.
- Strategy analysis uses `retk` tools only (not backend-specific measure tools), so behavior is identical across backends.
- `self_deviation` compares against the input STL only. No code path may read ground truth.
- Toolkit must run on Termux (numpy/scipy/trimesh; heavy deps optional).
- Folder name == strategy id (`strategies/A`); each has `skills/SKILL.md` that `recadbench compose` copies into run dirs.
- Strategies use only their own feature estimate, never GT n_ref.
- Update root `STATUS.md` at the end of every session.
