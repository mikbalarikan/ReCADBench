# STATUS — recadbench (public)

Checklist of the session prompts (`ReCADBench/docs/architecture/06_SESSION_PROMPTS.md`) that touch this repo.
Update at the end of every session.

| Prompt | Scope in this repo | State |
|---|---|---|
| P1 | Scaffold all five repos, contracts copied to spec/, CI (ruff + pytest + check_public), token docs, Termux spike | partial — phone spike pending |
| P2 | recadbench-spec: models, taxonomy, validate CLI, tag spec-v0.1.0 | todo |
| P3 | Backends build123d / solidcraft / freecad + conformance suite | todo |
| P4 | NX backend (runner, adapter, replays) | todo |
| P6 | dataset/tools/make_task.py | todo |
| P7 | Evaluator + golden tests | todo |
| P10 | Runner, compose, gate_hook, orchestrator | todo |
| P11 | Pilot + calibration, threshold ADR, spec-v1.0.0 | todo |
| P12 | Skillset improvement loop (reads results/) | recurring |

## Log
- 2026-11-05 — P1 Termux spike: PoCAD-method recipe + build123d-mcp 0.3.91 verified on x86_64 Ubuntu 24.04 (STEP export via MCP OK). Phone run still pending.
- 2026-11-05 — P1: scaffold created per 02_REPO_LAYOUTS.md. No logic.

## Open questions
- **Termux phone spike not executed.** The P1 session ran in a cloud container without access to any phone.
  The PoCAD-method recipe was verified end-to-end on x86_64 Ubuntu 24.04 (build123d-mcp 0.3.91 exports a valid
  STEP via MCP; see `backends/build123d/install.md`). Still needed for the M1 exit criterion: run
  install.md steps 0–2 on one phone and fill the spike-log row. Also unverified: MCP stdio through
  `proot-distro login` from a Termux-side Claude Code session.
- **Pin choice for P3:** build123d-mcp works with build123d 0.10 only if `bd_warehouse==0.2.0` is pinned
  (0.3+/0.4 need build123d ≥0.11.1/≥0.12). PyPI `cadquery-ocp-novtk` 7.9/8.0 now has aarch64 wheels, so a
  shim-free build123d 0.13 path may be possible on phones. Decide in P3 (`backend.yaml`).
- **evaluator/golden vs data hygiene:** P7 builds golden candidates from the 3 *pilot GT parts*, but D3 says no
  real-part STEP/GT in this repo and `check_public.py` allows STEP in `evaluator/golden/` as "synthetic only".
  Either golden parts must be synthetic stand-ins, or the golden tests read pilot GT via `RECADBENCH_GT_DIR`
  on the evaluator host. Decide before P7.
- **Write access for make_task.py:** D3 defines only read-only tokens, but `make_task.py` on the evaluator host
  writes to `recadbench-data`. Who pushes, with which credential (operator interactive vs a separate
  Contents: read-write token scoped to `recadbench-data` only)?
- **Unassigned folders:** `leaderboard/build.py` is in the layout but in no prompt of 06_SESSION_PROMPTS.
- **check_public coverage:** D3 lists STL/STEP/STP/PRT + gt_plan.json/key_dims.yaml. The guard additionally
  rejects `gt.step`/`gt_native.*`. Other CAD/mesh formats (`.3mf`, `.obj`, `.fcstd`, `.sldprt`, `.x_t`, `.igs`,
  `.iges`) are *not* rejected — extend?
- **Credential helper scope:** the README's helper applies to all of github.com on a host; fine for dedicated
  workers, but a shared dev machine may want a path-scoped helper (`credential.useHttpPath`).

- **Placeholder content files not created.** Files whose content is authored by later prompts and validated
  against a schema (`backend.yaml`, `skillset.yaml`, `strategy.yaml`, `SKILL.md`, agent `.md`, `LESSONS.md`,
  `MIGRATION_NOTES.md`, `AUTHORING.md`, `MANIFEST.json`, splits) were *not* stubbed — an empty stub would fail
  validation or be loaded by compose. Each folder's README.md lists the expected files and the filling prompt.
- **Prompt-number inconsistencies in the plan:** 00_DECISIONS D1 and 05_TOOLS say the FreeCAD MCP is selected
  in P4, but 06_SESSION_PROMPTS assigns it to P3; 05_TOOLS "NX skillset seed (P3 ports…)" and the
  02_REPO_LAYOUTS `MIGRATION_NOTES.md` comment say P3, but the NX port is P4. READMEs follow 06_SESSION_PROMPTS.
