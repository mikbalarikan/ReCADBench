# recadbench — CLAUDE.md

STL→parametric-CAD reverse-engineering benchmark. Architecture: `docs/architecture/` (read README.md + 00_DECISIONS.md before any change).

## Hard rules
- `spec/` is the contract source of truth. Never change a schema without an ADR in `docs/adr/` and a spec version bump. Downstream repos pin `spec-vX.Y.Z`.
- Ground truth never enters this repo. Evaluator reads it via `RECADBENCH_GT_DIR`. If you see a `gt.step` staged for commit, stop.
- `backends/` = L1 pins + adapters (measurement instruments). Adapters run on the worker host via `recad <b> …` CLI; the evaluator never imports backend code.
- Do not modify upstream backends (build123d-mcp, solidcraft-cli, FreeCAD MCP); pin versions in backend.yaml. NX is CLI-only via `recadbench nx-run`.
- Evaluator must be deterministic: fixed seeds for sampling/ICP; golden tests must pass before merge.
- Do not tune thresholds against the frozen `test` split. Iterate on `dev` only.
- Update root `STATUS.md` at the end of every session.
- Run pairing is produced only by `recadbench compose` (00_DECISIONS D6); never hand-assemble agent context.

## Layout
spec/ dataset/ evaluator/ runner/ orchestrator/ results/ (gitignored) leaderboard/ — see 02_REPO_LAYOUTS.md.
