# ADR 0004 — Spec 0.3.0: sketch refs, typed params, circular patterns, scan-tool deny list

- Status: accepted (pending merge)
- Date: 2026-11-06 (handover prompt H3)
- Spec version: **0.3.0** (schema change: `re_plan_ir`, `task`, `features`, `backend_manifest`,
  `feature_taxonomy.yaml`; `$id` → `…/0.3.0` everywhere)
- Builds on: ADR 0001 (IR params `anyOf`), ADR 0002 (real scans, 0.2.0)

## Context

The P2 / P3 / P5 / P6 sessions left a batch of contract gaps (STATUS.md open questions) that
P8 (skillsets) and P9 (strategies) would otherwise build on:

1. **Sketch dimensions cannot be parameters.** Sketch entity numbers were plain numbers; only
   feature `params` took `{"ref": name}`. A plate's width/height could not be named parameters,
   so S5 (`set_param`) could not edit them and GT authors got "unused parameter" warnings
   (75 on the three R385 pilots).
2. **Param values are untyped.** The taxonomy listed param *names* only; `radius: [1, 2, 3, 4]`
   or `count: "two"` validated.
3. **`pattern_circular.angle_deg` had no meaning.** Three readings were in use: the R385 pilot GT
   plans used it as the **pitch** (angle between instances), the 0.2.0 build123d `execute-ir`
   read it as the **span** first → last (with 360 special-cased as a full circle), build123d's
   `PolarLocations(angular_range, endpoint=False)` treats it as the **total sweep**.
4. **`features.json` keys** the adapters emit (`not_in_final_part`, `excluded`, `final_part`,
   `bodies`) were only accepted because the schema allows extra keys (P3 review).
5. **CAD MCPs can analyse the scan** (P3): build123d-mcp has `import_cad_file`, `mesh_*`,
   `find_holes`, `recognise_features`, `compare`; FreeCAD has `import_stl`; SolidCraft has
   `pointcloud.*` / `file.insert_mesh`. With them an agent can do RE analysis inside one backend
   that the others lack — strategies must use `retk`, identical across backends. `backend.yaml`
   had no field the gate hook could read.

## Decision

### 1. `{"ref"}` in sketches
Every sketch number — coordinates in `p1/p2/center/c1/c2/points`, `radius`, `width`, `height`,
`angle_deg`, `start_deg`, `end_deg` — may be a number **or** `{"ref": parameter}` (`$defs/num`;
`vec2` items are `num`). A ref must name a declared parameter and counts as a use (no
"unused parameter" warning). There are still no expressions: a ref binds the whole value, and a
rect keeps its `center` when its `width` changes (state the intent in `sketch.constraints`).

### 2. Typed taxonomy params
`feature_taxonomy.yaml` `types.<t>.params` is now a mapping `{name: type}`. Types are a small
closed vocabulary listed under `param_types` and defined once in the IR schema as
`$defs/ptype_<type>`:

| type | value |
|---|---|
| `number` | number or `{"ref"}` |
| `count` | integer ≥ 1 or `{"ref"}` (the parameter's value must be an integer ≥ 1) |
| `boolean`, `string` | as named |
| `vec3` / `vec3_list` | `[x, y, z]` / non-empty list of them |
| `number_list` | non-empty list of numbers / refs |
| `selector` / `selectors` | one selector / one or a non-empty list |
| `direction` | `vec3` or a selector |
| `feature_id` / `feature_ids` | earlier feature id / non-empty list of them |
| `{enum: [...]}` | one of the listed strings (`hole.kind`, `primitive.shape`) |

The compiled IR schema adds `params.properties.<name>: {$ref: ptype_<type>}` (or the enum) per
taxonomy type, so wrong types are **schema** errors. The semantic layer resolves ids by the
declared type instead of hard-coded key lists. Names did not change, so adapters' taxonomy
mappings are unaffected.

### 3. `pattern_circular`
Instance *k* = 0 … count−1 (*k* = 0 is the seed, unmoved) is rotated by *k*·step about `axis`,
right-hand positive about the axis direction, with

- `angle_deg` = **total sweep**, step = `angle_deg / count`; 360 = full circle (default when
  neither key is given), or
- **new** `pitch_deg` = step (angle between neighbouring instances).

At most one of the two (error). Errors: zero step, |count·step| > 360 (overlap). Warnings:
`angle_deg × count = 360` with `angle_deg ≠ 360` ("looks like a pitch", the 0.2.0 habit);
`pitch_deg × count = 360` (prefer `angle_deg: 360`, which keeps a full circle closed when the
count is edited). Normative helper: `recadbench_spec.circular_offsets`. Full circles are written
`angle_deg: 360`; partial patterns with a known step `pitch_deg`.

Why total sweep and not span: it is uniform (no 360 special case), it is build123d's native
form, and it keeps a full circle full when S5 edits a `{"ref"}` count. Why add `pitch_deg`:
partial patterns (e.g. three pads at 90°) are naturally specified by their step.

### 4. `features.json`
Optional, informational, never counted: `not_in_final_part: [str]`, `excluded: [{native_name,
native_type, reason}]`, `final_part: str`, `bodies: [str]`. The schema stays open (extra keys
allowed) for adapter-specific detail.

### 5. `backend.yaml: denied_tools`
```yaml
denied_tools:
- tool: mcp__build123d__mesh_          # prefix of the MCP tool name (kind=cli: of the command)
  reason: mesh analysis of the scan
- tool: mcp__solidcraft__execute
  args_regex: 'pointcloud\.'           # optional: deny only when the JSON arguments match
  reason: point-cloud import
```
`gate_hook` (P10) must deny a call when its name starts with `tool` and, if given, `args_regex`
(Python `re.search`) matches `json.dumps(arguments)`. Validator: MCP entries must be covered by
`cad_prefixes.all`; the regex must compile; no duplicates; an MCP backend without the field
warns. Filled for build123d, freecad and solidcraft; `backends/tests/test_denied_tools.py` pins
representative allowed and denied calls (including ordinary modelling code that must stay
allowed: a variable `points`, the word "mesh" in a comment, `export_stl` to an output path).
FreeCAD `create_macro` gets the same `args_regex` as `execute_python` (its `code` argument is
visible); `run_macro` is denied outright (only the name is visible at run time, and
`execute_python` covers the same need). Regexes that use `\b` must account for the JSON text:
a line break is the two characters `\n`, so an identifier at the start of a line is preceded by
`n`; the shipped regexes use `(?:(?<!\w)|(?<=\\[nt]))` instead.
**`args_regex` on free-form code tools (`execute`, `execute_python`, `create_macro`,
`execute_file`) is best-effort**: it catches the documented loaders, import-shaped module use,
load calls on mesh-file literals and references to the workspace `input/` folder, but not code
that builds names or paths at runtime (e.g. `getattr(b, 'import_' + 'stl')` on a computed
path), nor the body of an `execute_file` script. The scan is therefore not guaranteed to be
loadable only through the listed patterns; residual detection is adapter `extract` (imported
geometry is `unmapped`) and the P10 gate (open question: hide `input/` from the CAD process).

### 6. Versioning and migration
`spec_version` const → `0.3.0` (task, IR); every `$id` → `…/0.3.0`; package 0.3.0.
0.2.0 documents stay loadable through `recadbench_spec.migrate.migrate_document` (in memory;
`validate_document(..., migrate=True)`; CLI `recadbench-spec migrate [--write | -o]` and
`validate --migrate`). It bumps `spec_version` and rewrites circular patterns; an `angle_deg`
other than 360 is ambiguous in 0.2.0 and **requires** `--circular-angle pitch|span|sweep`.
A `{"ref"}` `angle_deg` stays bound under `pitch` (`pitch_deg: {"ref"}`); full-circle and
`span` rewrites cannot keep it and say so in a migration note. Typed params (section 2)
*narrow* some values 0.2.0 accepted: a scalar where the type is `number_list` (e.g.
`primitive.size: 5`) is wrapped into a list; any other rejected value (e.g. `hole.kind: tapped`,
a `{"ref"}` in `hole.positions`) makes the migration fail with an error naming the param, to be
fixed by hand. No contract file in the repos was affected.
`run_manifest.spec_version` is a pin record and is never rewritten. Without `--migrate` a 0.2.0
document fails with a hint naming the command.

## Consequences

- **Pilot GT plans** (`ReCADBench-gt`) migrated with `--circular-angle pitch`: five full-circle
  patterns → `angle_deg: 360`, HS04 `PAD_PATTERN` → `pitch_deg: 90`. The new host-only check
  `dataset/tools/check_gt_patterns.py` (cylinders in `gt.step` vs seed + k·step) found that the
  0.2.0 HS04 plan seeded the pads at −45° (E4), which with pitch +90° puts a pad at +135° where
  the GT has none (NX deleted one instance of a 4-pattern). The seed moved to E3 (−135°); all six
  pilot patterns now match the GT STEP exactly.
- **Conformance IR cases**, `task.yaml` files (`ReCADBench-data`) and spec examples migrated;
  only `spec_version` changed (C06 uses `angle_deg: 360`). Conformance counts/volumes unchanged.
- **build123d `execute-ir`** accepts 0.3.0 only, emits sketch refs as parameter names (S5) and
  uses step = sweep/count or pitch (`endpoint=False`); the 0.2.0 span reading is gone.
- **Downstream pins** move from `spec-v0.2.0` to `spec-v0.3.0` after the tag (H3 PR checklist).
- Not decided here (recorded as open): **scanner scale** was never verified against a reference
  artifact (needs a physical measurement); IR expressions (e.g. `center = width / 2`); whether
  `hole.positions` coordinates may be refs.
