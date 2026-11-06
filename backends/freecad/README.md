# backends/freecad — L1 backend pin + adapter

FreeCAD 26.3 + [freecad-robust-mcp](https://github.com/spkane/freecad-robust-mcp-and-more) 0.6.1,
headless bridge in `freecadcmd` ([ADR 0003](../../docs/adr/0003-freecad-mcp-selection.md)). Upstream
is never modified. The adapter is a measurement instrument (00_DECISIONS D3).

| Path | Content |
|---|---|
| `backend.yaml` | pins (server, bridge tag, FreeCAD), MCP interface, `cad_prefixes`, hosts, `max_parallel: 2` |
| `install.md` | Linux (conda-forge) + Windows setup, per-run bridge start |
| `adapter/adapter.py` | `recad freecad export / extract / set-param / replay` (FreeCAD's Python, stdlib + FreeCAD only; `recad` finds it via `RECAD_FREECAD_PYTHON`) |
| `replays/` | hand-written PartDesign replays of the 10 conformance cases (`C*.py`, helper `_fc.py`) |
| `tests/test_adapter.py` | conformance + contract tests (`<freecad-python> -m pytest backends/freecad/tests`; skipped when FreeCAD is not importable, e.g. in CI) |

## Native model contract (what `extract` / `set-param` expect)

- `.FCStd` with **one** `PartDesign::Body` holding the solid; export = that Body's shape, exactly
  one solid, mm.
- Driving dimensions in a `Spreadsheet::Sheet` named **`Params`**. Its cell aliases are the IR
  `parameters[].name`, bound to feature properties by expressions (`Params.hole_d`). `App::VarSet`
  properties are read too.

## Feature mapping (`extract`, feature_taxonomy.yaml)

| Native `TypeId` | taxonomy | counted |
|---|---|---|
| `PartDesign::Pad`, `Pocket` | extrude | yes |
| `PartDesign::Revolution`, `Groove` | revolve | yes |
| `PartDesign::Hole` (simple / counterbore / countersink / threaded) | hole | yes |
| `PartDesign::Fillet`, `Chamfer`, `Draft` | fillet, chamfer, draft | yes |
| `PartDesign::Thickness` | shell | yes |
| `PartDesign::LinearPattern` (1 or 2 directions), `PolarPattern`, `Mirrored` | pattern_linear, pattern_circular, mirror | yes (seed counted separately) |
| `PartDesign::MultiTransform` | its transformations, one each | yes per transformation |
| `PartDesign::Boolean` (tool bodies in its `Group`) | boolean | yes, unless it only merges a single-feature tool body (Cut: tool has 1 counted feature; Fuse/Common: body or tool has ≤ 1), which folds it into that feature (`counted: false`, `params.merged_into`), the same rule as the build123d tracer |
| `PartDesign::Additive*/Subtractive*` Box, Cylinder, Sphere, Cone, Ellipsoid, Torus, Prism, Wedge | primitive | yes |
| `PartDesign::Additive/SubtractivePipe`, `…Helix` | sweep | yes |
| `PartDesign::Additive/SubtractiveLoft` | loft | yes |
| `Sketcher::SketchObject` | sketch | no |
| `PartDesign::Plane`, `Line`, `Point`, `CoordinateSystem` | datum_plane, datum_axis, datum_point, datum_plane | no |
| `Spreadsheet::Sheet`, `App::VarSet` | parameter | no |
| Body origin, `App::Part`, groups | ignored | — |
| `Part::*` (Box, Feature, imported STEP …), `Mesh::*`, `Points::*`, body `BaseFeature`, `PartDesign::FeatureBase`, `ShapeBinder`, `SubShapeBinder`, `Scaled`, any other geometry | **unmapped** ⇒ gate fail | — |

Bodies consumed as tools by a `PartDesign::Boolean` are not result bodies: export, set-param and
replay take the one remaining Body with a solid; their features are counted.

Suppressed features and features after the Body `Tip` are not part of the result. They are
skipped and noted on stderr.

## Conformance (2026-11-06, FreeCAD 26.3): 10/10

All cases match the expected volume (rel. error ≤ 2e-9), count and set-param volume, with
counts identical to build123d and solidcraft. See [`../conformance/CONFORMANCE.md`](../conformance/CONFORMANCE.md)
and `tests/`.

Through the dispatcher (validates features.json with recadbench-spec):

```bash
export RECAD_FREECAD_PYTHON=<freecad-env>/bin/python
recad freecad replay C05_rect_pattern --out-dir /tmp/c05
```
