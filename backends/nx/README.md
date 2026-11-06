# backends/nx — L1 backend pin + runner + adapter

Siemens NX 2206 driven headless through `run_journal.exe` (CLI, no MCP; 05_TOOLS.md, 00_DECISIONS
D1). NX itself is never modified. The runner is the agent's **only** NX access path; the adapter is
a measurement instrument (00_DECISIONS D3).

| Path | Content |
|---|---|
| `backend.yaml` | pin (NX 2206, embedded Python 3.10.9), `interface: cli`, `allowed_commands: recadbench nx-run cad/*.py`, `max_parallel: 1` |
| `install.md` | host setup (NX, licence, Python), environment variables |
| `runner/nx_run.py` | `recadbench nx-run <cad/journal.py>`: host side (Python ≥ 3.11, stdlib): run_journal launch, timeout, host-wide NX lock, logs, syslog excerpt, `cad/last_run.json` |
| `runner/nx_wrapper.py` | NX side: open/create `cad/part.prt`, run the journal, update, save only on success, measure |
| `adapter/adapter.py` | `recad nx export / extract / set-param / replay / version` (host side, stdlib; STEP read-back with build123d when importable) |
| `adapter/nx_adapter_journal.py` | NX side of export / extract / set-param |
| `adapter/nx_measure.py` | NX-side measurement: feature tree rows, bodies, volume, user expressions, STEP export |
| `adapter/nx_taxonomy.py` | pure-Python NX feature → taxonomy mapping + counting rule (CI-tested) |
| `replays/` | hand-written NX Open replays of the 10 conformance cases (`C*.py`, helper `_nxr.py`); run through the same wrapper as an agent journal |
| `tests/` | `test_nx_taxonomy.py` (CI), `test_nx_runner.py` (CI part + NX host part, skips without NX) |

## `recadbench nx-run` (agent interface)

```bash
cd <run dir>                         # contains cad/ (and lib/ from the skillset)
recadbench nx-run cad/step_01.py     # [--timeout 120] [--part cad/part.prt]
```

- One journal path under `cad/`; it models in `cad/part.prt`, which the wrapper **creates** from the
  plain mm template when absent, else **opens**, and makes the work part. A journal reads it with
  `NXOpen.Session.GetSession().Parts.Work`. The journal's folder and the run dir's `lib/`
  (`RECADBENCH_NX_LIB` overrides) are on `sys.path`; `sys.argv == [journal]`.
- After the journal: update (failure action AcceptAll) and feature-error check. The part is
  **saved only when** the journal raised nothing, kept the same work part and no feature reports an
  error. A failed or timed-out run leaves `part.prt` as it was (transaction per run).
- `cad/last_run.json`: `ok, error, traceback, saved, created, features[] (name, type,
  taxonomy_type, counted), count, unmapped[], parameters[] (expressions with `IsUserSpecified`, or
  a name not of NX's own form), bodies, sheets, facet_bodies, volume, faces,
  feature_errors, timed_out, exit_code, elapsed_s, syslog_excerpt[], log`. Everything is measured
  by the wrapper from the NX session (the same counter as `recad nx extract`), never self-reported.
- stdout/stderr of run_journal go to `cad/logs/<journal>.log`. stdout of `nx-run` is one JSON
  summary. Exit 0 = ok, 1 = NX / journal error or timeout, 2 = usage error.
- **Denied journals (ADR 0004).** Before NX starts, the journal and every other `.py` under
  `cad/` (importable by the journal) are matched against `backend.yaml` `denied_tools`
  (`tool: recadbench nx-run`, `args_regex` searched in `json.dumps({"command", "journal"})`):
  journal code that imports or analyses the scan inside NX (NXOpen.Facet / faceted bodies,
  convergent bodies, Fit Surface, STL/OBJ/PLY/3MF import, mesh libraries, the workspace `input/`
  folder) is refused, exit 1, `denied: true`, the part untouched. Best-effort on free-form code;
  `extract` still reports facet / convergent / imported bodies as unmapped.
- Timeout default 120 s (`--timeout`, `RECADBENCH_NX_TIMEOUT_S`); only the run_journal process
  started here (and its child processes) is killed, never NX by name.
- The run_journal exit code is not trusted (NX 2206 returns 1 after correct journals); the verdict
  is the result file the wrapper wrote.
- `run_journal.exe` gets the environment of the caller minus `UGII_CUSTOM_DIRECTORY_FILE` /
  `UGII_STARTUP_MACRO` (session-capture plugins), never the `-nx` switch (rejected by 2206).
- A host-wide lock file (`RECADBENCH_NX_LOCK`, default `<temp>/recadbench-nx.lock`) serialises NX
  processes (`max_parallel: 1`), so the conformance suite and an agent run queue instead of
  colliding. Measured cost per call on the ASUS: 25–30 s (≈ 23 s NX start + licence, the rest the
  journal).

## Native model contract (what `extract` / `set-param` expect)

- One `.prt` with **exactly one solid body**; export = that body, AP242, mm.
- Driving dimensions are **user-named NX expressions** (`height`, `hole_d`: the IR
  `parameters[].name`) referenced by feature or sketch dimension formulas. NX's own names (`p12`,
  `p12_x`, `Pattern_*`, `ASSO_BLEND_*`, `Sheet_Metal_*`) are not parameters.
- `set-param` works on a copy: sets `<name> = value`, updates, refuses feature errors, saves the
  copy (`--native-out`), exports STEP, reads it back.

## Feature mapping (`extract`, feature_taxonomy.yaml)

Measured on NX 2206 (scratch probes, 2026-11-06). By Python class first, `FeatureType` second.
Rows NX marks `IsInternal` (pattern instances, hole child rows `SIMPLE HOLE` / `CBORE_HOLE`, the
sketch's own datum CSYS, `MASTER SNAPSHOT BODY`) are not history steps and are skipped.

| NX class (FeatureType) | taxonomy | counted |
|---|---|---|
| `Extrude` (EXTRUDE), incl. Subtract / Unite inside the extrude | extrude | yes |
| `Revolve` (SWP104), `Groove` | revolve | yes |
| `HolePackage` (HOLE PACKAGE): simple / counterbored / countersunk / threaded | hole | yes |
| `EdgeBlend` (BLEND), `FaceBlend` | fillet | yes |
| `Chamfer`, `Shell`, `Draft` / `DraftBody`, `Rib` | chamfer, shell, draft, rib | yes |
| `PatternFeature` (Pattern Feature) layout Linear / Circular (read from its edit builder) | pattern_linear / pattern_circular | yes (seed counted separately) |
| `Mirror` (Mirror Feature), `MirrorBody` (MIRROR: Extract Geometry > Mirror Body, one node) | mirror | yes |
| `Geomcopy` (Mirror / Pattern Geometry) layout Mirror / Translate / Rotate | mirror / pattern_linear / pattern_circular | yes |
| `Block`, `Cylinder`, `Cone`, `Sphere` | primitive | yes |
| `BooleanFeature` (UNITE / SUBTRACT / INTERSECT) | boolean | yes, unless it only merges a single-feature operand (subtract: tool has 1 counted feature; unite / intersect: body or tool has ≤ 1), then `counted: false` — same rule as build123d / FreeCAD. Operands from `Feature.GetParents()` (target first, measured); each operand = the counted features in that parent's closure (an unrelated earlier body is not part of the target), folded booleans in tree order no longer count |
| `SweepAlongGuide`, `Swept`, `Tube`, `VariationalSweep` / `ThroughCurves`, `ThroughCurveMesh` | sweep / loft | yes |
| `SketchFeature`, associative curve features | sketch | no |
| `DatumPlaneFeature`, `DatumCsys` / `DatumAxisFeature` / `PointFeature` | datum_plane / datum_axis / datum_point | no |
| user-named expressions | `parameters[]` | — |
| `Brep` (BREP / UNPARAMETERIZED_FEATURE: Remove Parameters, imported bodies), STEP-import rows (class `Feature` / `BodyFeature`, type = file name, `COMPOUND`), `ExtractFace` (EXTRACT_BODY), linked bodies, Move Object, synchronous edits, Trim / Split Body, surface features `BoundedPlane` (SKIN), `Sew` (SEW), `Thicken` (THICKEN_SHEET), `TrimSheet` (TRIMMED_SH), `ExtendSheet` (EXTEND_SHEET), `LawExtensionEx` (LAWEXTENSION), `TrimAndExtend` (TRIM_AND_EXTEND) - no spec 0.3.0 taxonomy type, reason "not countable in v1" (measured H4 fixer 2), other pattern layouts, assembly components, facet bodies without an associated body (STL imported as a JT facet body: no feature row), **any unknown class** | — | **unmapped ⇒ gate fail** |

- A counted feature that does not contribute to the single result solid (`Body.GetFeatures()`
  timestamps) is listed in `not_in_final_part` and not counted. Suppressed / rolled-back rows are
  listed in `excluded` (neither counted nor unmapped).
- features.json follows spec 0.3.0 (ADR 0004 section 4): `bodies` = journal identifiers of the
  solid bodies, `final_part` names the single solid, plus the adapter-specific `sheets` /
  `facet_bodies` counts.
- Replays read their spec 0.3.0 IR (`replays/_ir.py`, no NXOpen): every IR parameter becomes a
  named expression, `{"ref"}` numbers become expression names in formulas, and
  `pattern_circular` follows ADR 0004 (`angle_deg` = total sweep: 360 -> NX Span 360, partial
  sweep -> Offset pitch `angle_deg / count`; `pitch_deg` -> Offset pitch).
- Conformance C09: NX's Mirror Feature of a body-creating extrude yields a second body, so the
  replay unites it; that Unite merges one feature (the mirror) and is folded. Count 3 as on every
  backend.

## Provenance

The measurement code is ported from the measurement half of the owner's NX helper modules
(`nxh` feature table / user expressions / bodies report / mass properties / feature-error recheck,
`nxf` STEP export), split off so the L2 skillset (ReCADBench-cad-skills `nx/lib/nxhelpers`) has no
copy of the counter. The runner is seeded from the owner's `run_journal.ps1` launcher (child-only
kill, capture-plugin variables cleared, no `-nx`). The replay helper `_nxr.py` re-implements the
verified builder recipes (sketch, extrude, revolve, HolePackage, Pattern / Mirror Feature, Edge
Blend, Chamfer, Shell, Draft) so the conformance suite does not depend on the skillset.

## Tests

```bash
pytest backends/nx/tests                 # CI: taxonomy + usage errors; host: + NX runs (skip without NX)
python backends/conformance/run_conformance.py --backend nx [--backend build123d]
```
