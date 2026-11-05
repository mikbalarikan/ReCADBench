# STATUS — recadbench (public)

Checklist of the session prompts (`ReCADBench/docs/architecture/06_SESSION_PROMPTS.md`) that touch this repo.
Update at the end of every session.

| Prompt | Scope in this repo | State |
|---|---|---|
| P1 | Scaffold all five repos, contracts copied to spec/, CI (ruff + pytest + check_public), token docs, Termux spike | done |
| P2 | recadbench-spec: models, taxonomy, validate CLI, tag spec-v0.1.0 | done — merged (#2); spec 0.2.0 via ADR 0002. Tags: create `spec-v0.2.0` after this PR merges (`spec-v0.1.0` optional, superseded) |
| P3 | Backends build123d / solidcraft / freecad + conformance suite | todo |
| P4 | NX backend (runner, adapter, replays) | todo |
| P6 | register_gt.py + make_task.py (real scans, ADR 0002), AUTHORING.md, 3 R385 pilots | todo |
| P7 | Evaluator + golden tests | todo |
| P10 | Runner, compose, gate_hook, orchestrator | todo |
| P11 | Pilot + calibration, threshold ADR, spec-v1.0.0 | todo |
| P12 | Skillset improvement loop (reads results/) | recurring |

## Log
- 2026-11-05 — ADR 0002: inputs are REAL SCANS (scanner frame); spec 0.2.0 (task `input_kind` + scan metadata; gt_meta provenance/contamination, registration, scan floor, observability; result inferred-region metrics; test-split eligibility rules); architecture docs, P6/P7 prompts and roadmap updated. v1 = scan-rebuild parts, dev only.
- 2026-11-05 — P1 Termux spike PASSED on a phone (Xiaomi 23117RA68G, Android 13, aarch64): STEP export via build123d-mcp, also via `proot-distro` stdio from the Termux side. Details: `backends/build123d/install.md`.
- 2026-11-05 — P2: recadbench-spec 0.1.0 — pydantic models for all 10 schemas, taxonomy loader + counting rule, taxonomy-compiled IR schema (type enum, per-type params, needs_sketch), semantic rules, `recadbench-spec validate|schemas|taxonomy|compile-schema`, 101 tests (1 valid + 2 invalid examples per schema, round-trip, model↔schema drift). ADR 0001 fixes the IR `params` oneOf bug.
- 2026-11-05 — P1 Termux spike: PoCAD-method recipe + build123d-mcp 0.3.91 verified on x86_64 Ubuntu 24.04 (STEP export via MCP OK). Phone run still pending.
- 2026-11-05 — P1: scaffold created per 02_REPO_LAYOUTS.md. No logic.

## Open questions

### From ADR 0002 (real scans)
- Decided: golden examples **cleared** (v1 dev pool, ADR 0002 table); **open-dedica kept aside** (later: calibrated reference STEPs once agent rebuilds deviate less).
- M02-02-03 gear: is `M02-02-03.STL` a real scan? (provenance of the mesh)
- Full-res originals of the golden-example scans are on Drive (`H:\My Drive\…`, see each MANIFEST.json `source_path`) — P6 must use those, not the decimated copies.
- **Scan storage** for recadbench-data (18–50 MB each): Git LFS vs Drive — decide in P6.
- **`k` for the scan-floor band widening** (1.5 provisional) and the observability threshold — calibrate in P11.
- **recadbench-data / recadbench-gt READMEs** still describe tessellated inputs — update in P6.
- **No frozen test split** until `print_scan` (OGUZ print + scan) parts exist.

### From P2 (spec)
- **Sketch dimensions can't be parameters.** IR sketch entities (`rect.width`, `circle.radius`, …) take plain
  numbers; only feature `params` accept `{"ref": name}`. So a plate's width/height can't be named
  parameters, which limits S5 (`set_param`) to non-sketch dims, and the "unused parameter" warning fires
  for GT authors who declare them. Allow `{"ref"}` in sketch numbers? (schema change → ADR, spec 0.2.0)
- **Param values are untyped.** The taxonomy lists param *names* per type but not their types, so
  `radius: [1, 2, 3, 4]` or `count: "two"` pass. Add `params: {name: type}` to the taxonomy so the
  compiled schema can type-check? (taxonomy change → ADR)
- **Semantic rules are stricter than the schemas** in a few places (listed in `spec/README.md`), e.g.
  Track T requires `split: dev` (the schema only checks it when present), and refs must point to
  *earlier* features. Confirm these match intent.
- `docs/architecture/contracts/` mirrors `spec/` by hand; keep or drop the mirror?

### From P1
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
