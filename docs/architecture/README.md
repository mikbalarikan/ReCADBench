# ReCADBench — Architecture & Build Plan (handoff package)

STL → parametric CAD reverse-engineering benchmark with **three layers**:

1. **L1 — CAD backend**: off-the-shelf interface, pinned, not modified (build123d-mcp, `solidcraft-cli mcp`, a FreeCAD MCP, NX via NX Open journals + `run_journal.exe`).
2. **L2 — CAD skillset**: our own know-how for operating each program (skills, subagent files, helper libs, examples, lessons). Evaluated in Track T, against a bare-L1 `none` baseline.
3. **L3 — RE strategy**: CAD-agnostic reverse-engineering method A–D. Evaluated in Track S / E.

A run is `strategy × skillset (→ backend) × model × part × replicate` — e.g. "strategy B with nx@v1", "strategy D with build123d@v2".

## Read in this order

| File | What it is |
|---|---|
| `00_DECISIONS.md` | Key decisions + rationale (repo topology, layer split, tracks). Read first. |
| `01_ARCHITECTURE.md` | Layers, data flow, contracts, evaluation tracks, isolation rules |
| `02_REPO_LAYOUTS.md` | Exact folder trees for the 5 repos, what goes where |
| `03_SCORING.md` | Rubric (gate, alignment, S1–S5), pass/fail, per-track scoring |
| `04_STRATEGIES.md` | Initial strategy definitions A–D (seed content for the strategy repo) |
| `05_TOOLS.md` | L1 backends (pins, interfaces, NX journal runner) and L2 skillset rules, NX port map |
| `06_SESSION_PROMPTS.md` | Copy-paste prompts for each follow-up Claude session, in order |
| `07_ROADMAP.md` | Milestones and exit criteria |
| `contracts/` | JSON Schemas + taxonomy — the frozen interface between the repos |
| `templates/` | CLAUDE.md for each repo (drop into repo root) |
| `REVIEW_NOTES.md` | Independent review of v1, and v3 restructure notes |

## How to use this package

1. GitHub repos: `ReCADBench` (public, exists) + private `recadbench-cad-skills`, `recadbench-strategies`, `recadbench-data`, `recadbench-gt` — see `00_DECISIONS.md` D3.
2. Unzip this package into `ReCADBench/docs/architecture/`.
3. Start a new session with all 5 repos attached and **Prompt P1** from `06_SESSION_PROMPTS.md` (scaffold all 5 repos).
4. Continue with P2… in order. Each prompt is self-contained.

Status: plan v4 (3-layer model, 1 public + 4 private repos), 2026-11-05.
