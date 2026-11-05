# recadbench-spec

The ReCADBench contracts: JSON Schemas, the feature taxonomy, pydantic v2 models and a validator.
**Source of truth** for every repo. Schema changes need an ADR in `docs/adr/` (see
[0001](../docs/adr/0001-ir-params-anyof.md), [0002](../docs/adr/0002-real-scan-input.md)) and a spec version bump. Downstream repos pin a tag.

## Install (pinned)

```bash
pip install "recadbench-spec @ git+https://github.com/mikbalarikan/ReCADBench@spec-v0.2.0#subdirectory=spec"
```

## Use

```bash
recadbench-spec validate plan/re_plan.v1.json task.yaml      # schema detected from file names
recadbench-spec validate --schema re_plan_ir candidate.json  # explicit schema
recadbench-spec validate --json --strict plan/*.json         # machine output; warnings fail
recadbench-spec schemas | taxonomy | compile-schema re_plan_ir
```

Exit codes: `0` valid · `1` invalid (or warnings with `--strict`) · `2` usage error.

```python
from recadbench_spec import validate_file, load_taxonomy, RePlan

report = validate_file("plan/re_plan.v2.json")
assert report.ok, [str(e) for e in report.errors]
plan: RePlan = report.model  # typed access
n = load_taxonomy().count(["sketch", "extrude", "hole", "pattern_linear"])  # → 3
```

## Contents

| Schema (`schemas/<name>.schema.json`) | Detected file names | Model |
|---|---|---|
| `task` | `task.yaml` | `Task` |
| `re_plan_ir` | `re_plan.vN.json`, `gt_plan.json` | `RePlan` |
| `key_dims` | `key_dims.yaml` | `KeyDims` |
| `gt_meta` | `meta.yaml` | `GtMeta` |
| `features` | `features.json` | `Features` |
| `backend_manifest` | `backend.yaml` | `BackendManifest` |
| `skillset_manifest` | `skillset.yaml` | `SkillsetManifest` |
| `strategy_manifest` | `strategy.yaml` | `StrategyManifest` |
| `run_manifest` | `run_manifest.json` | `RunManifest` |
| `result` | `result.json` | `Result` (`pass` ↔ `pass_`) |

## Validation layers

1. **schema** — JSON Schema Draft 2020-12. For `re_plan_ir` the *compiled* schema is used: the on-disk
   schema plus rules generated from `feature_taxonomy.yaml` — per type, `params` keys must be in the
   taxonomy's list, and `sketch` is required when `needs_sketch: true`. The `type` enum comes from
   the taxonomy (a test keeps the readable enum on disk in sync). `recadbench-spec compile-schema re_plan_ir`
   prints it, e.g. for tool definitions.
2. **model** — parse into the pydantic model. A failure here means models drifted from schemas (tests guard
   field names, required sets and `additionalProperties` for all top-level and IR nested objects).
3. **semantic** — rules JSON Schema cannot express. Errors make a document invalid, warnings never do:

| Schema | Errors | Warnings |
|---|---|---|
| `re_plan_ir` | `revision` == N in `re_plan.vN.json`; revision > 1 needs `change_log`; one primary datum, unique roles, no zero vectors; ids unique across datums, reference planes, features; `{"ref": p}` → declared parameter; parameter names unique; `sketch.plane`, `refs` and selector-valued params (`edges`, `faces`, `open_faces`, `neutral`, `plane`, `axis`, `direction`, `path` when strings) are valid selectors whose id is a datum, reference plane or **earlier** feature; `features` (pattern/mirror) = non-empty list of earlier feature ids; `tool_feature` = earlier feature id | unused parameter; sketch on a type with `needs_sketch: false` |
| `features` | `taxonomy_type` known; `counted` agrees with the counting rule (exception: a `boolean` may be folded into the feature it merges); `count` == number of `counted: true` | non-empty `unmapped` (⇒ gate fail) |
| `run_manifest` | skillset id `none` or `<backend>@vN` targeting `backend.id`; backend `none` ⇒ skillset `none`; strategy id `oracle`/`[A-Z]`; Track T ⇒ `split: dev`; budgets > 0 | `spec_version` ≠ this package |
| `backend_manifest` | MCP: non-empty `cad_prefixes.all`, `mutating` ⊆ `all`; CLI: ≥1 allowed command; build123d has `execute_ir`; conformance passed ≤ total; `backend.yaml` sits in folder `<id>/` | `allowed_commands` on MCP; NX `max_parallel` ≠ 1 |
| `skillset_manifest` | `id` == `backend.id`; `skillset.yaml` in folder `<id>/`; no duplicate skills/agents/lib | |
| `strategy_manifest` | only `oracle` may set `inject_gt_plan`; `strategy.yaml` in folder `<id>/`; ≥1 stage; no duplicate tools | oracle without `inject_gt_plan` |
| `task` | unique deliverables; `input_stl` ends in `.stl`; budgets > 0; no `scan` block unless `input_kind: scan` (schema: `scan` required for scans) | |
| `key_dims` | unique names; diameter/radius > 0; no zero `axis_dir` | |
| `gt_meta` | `face_types` keys ⊆ taxonomy `face_types`, fractions ≥ 0 and sum to 1 (±0.01); `n_ref`, `faces_gt` ≥ 1; chord > 0; `T_scan_to_gt` is rigid (orthonormal, det > 0, last row 0 0 0 1) and converged; floor values ≥ 0; **`split: test` requires no contamination flag, `gt_author` ≠ `agent`, and a non-synthetic source** (schema: scan/registration/scan_floor/observability required unless synthetic; chord required if synthetic) | chord on a scan part; registration on a synthetic part |
| `result` | `pass` ⇒ `gate.ok`; fractions in [0, 1] | Track S without `plan_score` |

## Tests

`tests/examples/<schema>/` holds one `valid` and ≥2 `invalid_*` documents per schema (synthetic). Valid
examples must round-trip: document → model → `to_document()` → identical document, schema-valid again.
