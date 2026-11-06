# STATUS — recadbench (public)

Checklist of the session prompts (`ReCADBench/docs/architecture/06_SESSION_PROMPTS.md`) that touch this repo.
Update at the end of every session.

| Prompt | Scope in this repo | State |
|---|---|---|
| P1 | Scaffold all five repos, contracts copied to spec/, CI (ruff + pytest + check_public), token docs, Termux spike | done |
| P2 | recadbench-spec: models, taxonomy, validate CLI, tag spec-v0.1.0 | done — merged (#2); spec 0.2.0 via ADR 0002. Tags: create `spec-v0.2.0` after this PR merges (`spec-v0.1.0` optional, superseded) |
| P3 | Backends build123d / solidcraft / freecad + conformance suite | done (uncommitted, awaiting review/PR): 3 adapters, `recad` dispatcher, conformance 10/10 on all three, counts identical; FreeCAD MCP = freecad-robust-mcp 0.6.1 (ADR 0003) |
| P4 | NX backend (runner, adapter, replays) | todo |
| P6 | register_gt.py + make_task.py (real scans, ADR 0002), AUTHORING.md, 3 R385 pilots | tooling done (synthetic-tested); pilots pending desktop data |
| P7 | Evaluator + golden tests | todo |
| P10 | Runner, compose, gate_hook, orchestrator | todo |
| P11 | Pilot + calibration, threshold ADR, spec-v1.0.0 | todo |
| P12 | Skillset improvement loop (reads results/) | recurring |

## Log
- 2026-11-06 — PR #8 adversarial review on the ASUS (Windows 11, build123d 0.13.0, FreeCAD 1.1.1): build123d extract fooled 4 ways (kernel handle swapped via setattr/`__dict__`, `insert()` of an untraced solid, `bounding_box(mode=ADD)`) -> now fingerprints shape handles + BuildPart parts and reports `unmapped`; freecad extract counted features of a shapeless scratch body (count 3 for a 1-pad part) -> counts the result body (+ Boolean tool bodies) only; adapters/run_conformance wrote CRLF -> LF; `recad --help` crashed on a cp437 console -> ASCII; last two brand names in ADR 0002 removed. Conformance on this host: build123d 10/10, freecad 10/10 (FreeCAD 1.1.1, not the pinned 26.3); solidcraft not installed. build123d-mcp 0.3.91 `tools/list` matches backend.yaml `mutating`.
- 2026-11-06 — Handover to a desktop main session: `docs/handover/` (HANDOVER.md state/decisions/rules/definition of done, PROMPTS_v2.md H0–H9, MAIN_SESSION_BRIEF.md); private companion `ReCADBench-gt/HANDOVER_PRIVATE.md`. Client names in current docs replaced by neutral ids GE-01…GE-05 (mapping private); they remain in git history (rewrite = owner decision).
- 2026-11-06 — P3: L1 backends build123d (0.13.0 + build123d-mcp 0.3.91, reference, `execute-ir`), freecad (FreeCAD 26.3 + freecad-robust-mcp 0.6.1, ADR 0003), solidcraft (solidcraft-cli 0.30.1): `backend.yaml`, install.md, adapters (export / extract / set-param / replay), 10 replays each, review fixes (dumb-solid fillet/mirror → unmapped, dead ops not counted, Boolean tool bodies, C09/C10 solidcraft replays, usage errors → one JSON). `recad` dispatcher (`backends/recad_cli`, package recadbench-backends 0.1.0): subprocess per call with the backend interpreter, features.json validation, needs_measure STEP measuring, `recad backends`. `backends/conformance/run_conformance.py`: 30/30 case×backend pass, cross-backend counts identical (CONFORMANCE.md). CI now installs ./backends + build123d 0.13.0 and runs backends/tests (28) + backends/build123d/tests (64); full suite 223 passed. FreeCAD (41) and SolidCraft (40) adapter tests pass locally, not in CI.
- 2026-11-06 — P3 completeness review: conformance rerun 30/30 (counts identical); count is derived (one extra fillet in a C10 copy → n 3→4 on all three backends, same volume 34349.98 mm³); `cad_prefixes.mutating` checked against live `tools/list` of build123d-mcp 0.3.91, solidcraft-cli 0.30.1 `mcp --headless` and freecad-robust-mcp 0.6.1. Fixed: solidcraft adapter usage errors now one JSON + exit 1 (was argparse exit 2), +4 tests (usage, added-fillet count); ADR 0003 test count 35→41; build123d backend.yaml note on mesh-analysis tools.
- 2026-11-06 — dataset tools on Windows: `make_task.py` / `register_gt.py` write LF (`newline="\n"`) so MANIFEST.json hashes of task.yaml survive git autocrlf; runtime messages are ASCII (the final summary `print` crashed on a cp1252 console). Tests pin both.
- 2026-11-06 — scanreg: robust shrinking ICP trim + coarse ICP after PCA (raw scans that include the scanner table / fixtures), KD-tree prefilter for far points (was pathologically slow), floor excludes extraneous scan geometry (> 1.5 mm, reported separately). Desktop survey: R385 scans and CAD are in different frames (no saved transform) → `--init pca`; pilots HS01, DR02, HS04.
- 2026-11-05 — P6 tooling: `dataset/tools/scanreg.py` (PCA coarse + trimmed point-to-plane ICP, floor, observability with open-boundary/opposite-normal masks), `register_gt.py`, `make_task.py` (byte-identical scan copy, agent-visible metadata only, MANIFEST + splits); 13 synthetic tests (ICP 4 µm in 6 iterations). `recadbench-gt/AUTHORING.md` written.
- 2026-11-05 — ADR 0002: inputs are REAL SCANS (scanner frame); spec 0.2.0 (task `input_kind` + scan metadata; gt_meta provenance/contamination, registration, scan floor, observability; result inferred-region metrics; test-split eligibility rules); architecture docs, P6/P7 prompts and roadmap updated. v1 = scan-rebuild parts, dev only.
- 2026-11-05 — P1 Termux spike PASSED on a phone (Xiaomi 23117RA68G, Android 13, aarch64): STEP export via build123d-mcp, also via `proot-distro` stdio from the Termux side. Details: `backends/build123d/install.md`.
- 2026-11-05 — P2: recadbench-spec 0.1.0 — pydantic models for all 10 schemas, taxonomy loader + counting rule, taxonomy-compiled IR schema (type enum, per-type params, needs_sketch), semantic rules, `recadbench-spec validate|schemas|taxonomy|compile-schema`, 101 tests (1 valid + 2 invalid examples per schema, round-trip, model↔schema drift). ADR 0001 fixes the IR `params` oneOf bug.
- 2026-11-05 — P1 Termux spike: PoCAD-method recipe + build123d-mcp 0.3.91 verified on x86_64 Ubuntu 24.04 (STEP export via MCP OK). Phone run still pending.
- 2026-11-05 — P1: scaffold created per 02_REPO_LAYOUTS.md. No logic.

## Open questions

### From P3 (backends)
- **CAD MCPs expose RE analysis** (P10): build123d-mcp 0.3.91 has read-only `mesh_section`,
  `mesh_holes`, `find_holes`, `recognise_features`, `compare` …; with `import_cad_file` an agent can
  analyse the scan inside the backend (strategies must use retk only, identical across backends).
  Importing is mutating and leaves `unmapped`, but import → analyse → reset leaves no trace. The
  same holds for freecad `import_stl` / solidcraft `pointcloud.*`. `backend_manifest` has no deny
  list (schema change ⇒ ADR); runner/gate_hook should deny import + mesh tools by name.
- **docs/adr/README.md index** lists only 0001; add 0002 and 0003.
- **SolidCraft on Termux ARM64 is untested.** `termux` is not in solidcraft `hosts`; the test steps
  are in `backends/solidcraft/install.md` ("Termux test"). Run them on the phone before listing it.
- **build123d on Termux stays on 0.10.0** (PoCAD route, install.md B) while the desktop/CI pin is
  0.13.0. The Termux worker can run the agent, but adapter calls need a route-A host. Conformance
  ran only on 0.13.0. Decided for P3; revisit if aarch64 OCP 8 wheels work on the phone.
- **FreeCAD MCP timeout:** with the 0.6.1 pin, `FREECAD_TIMEOUT_MS` has no effect, and
  `execute_python` allows 30 s per call unless the call passes `timeout_ms` (wired upstream from
  0.7.0-rc.3). The recadbench-cad-skills freecad skillset must tell agents to pass `timeout_ms` on long calls.
- **CI Python 3.11 vs local 3.13:** the build123d adapter suite ran on 3.13.18 here. CI uses 3.11 with
  `build123d==0.13.0`; the first CI run is the first 3.11 check.
- **FreeCAD / SolidCraft adapter tests are not in CI** (no FreeCAD / CLI on the runner). They skip
  without their environment. Conformance for those two must be rerun by hand on each pin bump
  (`run_conformance.py`). Add a CI job with conda-forge FreeCAD + a cached solidcraft build?
- **`needs_measure` path is unused** by the current adapters (all three report volumes). It is
  tested with a fake adapter only.
- **Non-editable `pip install ./backends`** finds the adapters only from a checkout's `./backends`
  (cwd search) or `RECAD_BACKENDS_DIR`. CI and docs use `-e`.
- **NX** has no adapter yet (P4). `recad nx …` reports "adapter not implemented", and `recad backends` lists it as unavailable.
- Unsupported / unmapped by design (gate fail): direct OCP calls and custom `BasePartObject`s
  (build123d), `Part::*` / imported shapes / binders (freecad); see each backend's mapping table.

### From ADR 0002 (real scans)
- **Golden-example scans over 100 MB** (GE-02 209 MB, GE-04 250 MB; GE-03 92 MB) cannot go in plain git. Options: Git LFS for those files only, Drive, or a lossless compressed copy. Decide before adding them (R385 pilots are all < 40 MB).
- R385 folder also holds **downloaded public RS-385 CAD** → R385 meta: `public_cad: true`.
- Dropped from the v1 pool: **M02** (its CAD was not fitted to the scan; scan is a segment of a client scan), **R385 SL02** (no scan), **R385 HS03** as a pilot (90° merge error between its two scan passes). **OD-H12** stays calibration-only (fused assembly, guessed interior, gate failed).
- Decided: golden examples **cleared** (v1 dev pool, ADR 0002 table); **open-dedica kept aside** (later: calibrated reference STEPs once agent rebuilds deviate less).
- M02-02-03 gear: is `M02-02-03.STL` a real scan? (provenance of the mesh)
- Full-res originals of the golden-example scans are on Drive (`H:\My Drive\…`, see each MANIFEST.json `source_path`) — P6 must use those, not the decimated copies.
- Decided: **scan storage = plain git** (no LFS) for v1 — scans < 100 MB/file, dev pool < 1 GB; LFS free bandwidth (1 GB/month) would not survive several workers. Revisit at M5.
- **gt_plan.json frame:** authored in the gt.step frame (AUTHORING.md §6); P10 compose must map it into the scanner frame with inv(T_scan_to_gt) before Track T hands it to an agent.
- **Tier definitions** used in AUTHORING.md are still provisional (threads alone ≠ tier 3) — confirm in P11.
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
- **Pin choice for P3** (decided in P3: desktop/CI build123d 0.13.0, Termux stays 0.10.0): build123d-mcp works with build123d 0.10 only if `bd_warehouse==0.2.0` is pinned
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
