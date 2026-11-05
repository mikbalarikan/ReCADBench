# 05 — Backends (L1) and Skillsets (L2)

## L1 backends — pinned, not modified

| Backend | Interface | Host | Feature extraction (adapter) | set_param (S5) | Pin / notes |
|---|---|---|---|---|---|
| **build123d** | MCP: pzfreo/build123d-mcp (`execute` in persistent session, measure, find holes/patterns, compare, export) | Linux, Termux (PoCAD method) | Runtime trace of build123d operations in the executed code; direct OCP/OCC calls ⇒ `unmapped` ⇒ gate fail | Script-level parameters | Pin PyPI version. `execute_ir` required. Reference backend. |
| **solidcraft** | MCP: `solidcraft-cli mcp` (headless or `--connect`) | Win/Mac/Linux; test Termux ARM64 | Parse JSON command script / timeline | Unit-aware parameter expressions | Pin release; rerun conformance on every bump. |
| **freecad** | MCP: chosen in P4 (candidates: neka-nat/freecad-mcp, freecad-robust-mcp) | Linux/Win | `Document.Objects` PartDesign types; `Part::Feature` dumb shapes ⇒ `unmapped` | Spreadsheet / expressions | Prefer headless `FreeCADCmd` support. |
| **nx** | **CLI**: agent writes `cad/step_NN.py` (NX Open Python journal), runs `recadbench nx-run cad/step_NN.py` | Windows (ASUS, MateBook) | NX Open journal: traverse feature tree, map types | Expressions via NX Open | `nx-run` = thin wrapper over `run_journal.exe`: timeout, captures stdout/err + NX syslog excerpt, returns feature list + body count after each run. NX_MCP not used in v1. |

`backend.yaml` must declare: `interface` (mcp|cli), `cad_prefixes.all` / `cad_prefixes.mutating` (MCP tool names) or `allowed_commands` (CLI patterns), `hosts`, `max_parallel`, software version.

### `recadbench nx-run` contract
- Input: one journal path under `cad/`. Journals operate on `cad/part.prt` (create if absent, else open).
- Each run: open part → execute journal → save → dump `cad/last_run.json` {ok, error, traceback, features[], bodies, volume}.
- Timeout per call (default 120 s). Non-zero exit if NX error. No other NX access path for the agent.

## L2 skillsets — ours, evaluated in Track T

Per backend: `skills/` (SKILL.md files), `agents/` (subagent .md), `lib/` (agent-importable helpers), `examples/`, `LESSONS.md`. See 02_REPO_LAYOUTS.

Rules:
- Teach **how to operate the software** to execute an RE Plan IR faithfully: each taxonomy type → native ops, one-solid discipline, parameters/expressions named after IR parameters (makes S5 work), export, measuring, error recovery.
- No RE methodology (no "segment the mesh first…"). Found during porting → `MIGRATION_NOTES.md` → forwarded to strategies.
- Never instruct importing/converting the STL into a body or surfaces.
- `lib/` helpers must emit native parametric features (no dumb bodies), otherwise the gate fails the run.

Baseline `none`: only `_shared/ir_execution.md` is injected. `Δ = score(skillset vX) − score(none)` per backend is the headline L2 metric.

### NX skillset seed (P3 ports existing desktop material)
| Existing material | Goes to |
|---|---|
| Skill / agent .md | `nx/skills/`, `nx/agents/` (strip RE methodology → MIGRATION_NOTES) |
| NX Open code / journals | Reusable modeling helpers → `nx/lib/nxhelpers/`; measurement-type code (feature dump, export, expression edit) → copy to `recadbench/backends/nx/adapter/` |
| Example projects | `nx/examples/` (redact client-identifying data) + error→fix lessons into `LESSONS.md` |

## Conformance (`recadbench/backends/conformance/`) — agent-free

10 IR cases with expected volume, face count, feature count: block, block+through-hole, counterbore, revolve shaft with groove, rectangular pattern, circular pattern, fillet+chamfer, shell, mirror-symmetric bracket, draft pocket.

Each backend ships hand-written native replays (`backends/<b>/replays/`). Pass = STEP valid, volume within 0.1%, `extract` count == expected (identical across backends — the cross-tool counting normalization test), `set-param` round-trip OK. A backend must pass before entering Track T.
