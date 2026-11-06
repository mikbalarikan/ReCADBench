# backends/solidcraft/adapter

`adapter.py` is the `recad solidcraft` instrument. It runs on the worker host after the run, in a
clean process, and the evaluator never imports it. It uses only stdlib Python 3.11+ and shells out
to the pinned CLI at `$RECAD_SOLIDCRAFT_CLI` (default `solidcraft-cli` on `PATH`). Each subcommand
prints one JSON object on stdout and sends logs to stderr. Exit code is 0 on success, 1 on failure.

```
adapter.py export    <native> --out <file.step>        -> {ok, solids, volume_mm3, faces, needs_measure, volume_method, model_volume_mm3, model_faces}
adapter.py extract   <native> --out <features.json>    -> {ok, out, count, unmapped, types}
adapter.py set-param <native> <name> <value> --out <file.step> [--native-out <file.json>]
                                                        -> {ok, name, value, volume_before_mm3, volume_after_mm3, solids, faces}
adapter.py replay    <case_id> --out-dir <dir>         -> writes native.json, output.step, features.json;
                                                           {ok, volume_mm3, faces, count, solids, unmapped, types}
adapter.py version                                      -> {ok, cli, version}
usage error (bad subcommand / argument / value)         -> {ok: false, error: "usage: ..."}, exit 1 (never argparse's exit 2)
```

## Native format

The native file is a SolidCraft **JSON command script**: `{"commands": [{"command", "params"},
…]}`. See upstream `docs/scripts.md`. A bare list is also accepted, and so are extra top-level or
per-command keys such as `description` or `ir`, which the CLI ignores. Scripts exported by
`timeline.export_script` or `solidcraft-cli script` (`timeline.add` steps) work as well. A saved
design `.sc` is accepted and converted with `solidcraft-cli script` first.

Driving dimensions are **SolidCraft user parameters**, named after the IR `parameters[].name`. They
are created with `parameters.add {name, expression: "20 mm"}`, or with a sketch dimension
`"value": "name = 40"`, and referenced by name in feature/sketch expressions (`"distance":
"height"`, `"p1": [10, "shaft_len"]`). This is SolidCraft's own parameter mechanism. Nothing is
rewritten textually inside feature parameters.

## How each instrument works

- **Rebuild.** Every instrument rebuilds the script in a fresh headless session with
  `solidcraft-cli run <script> --results`. Read-only commands are appended to the script:
  `document.inspect`, `parameters.list` and `timeline.inspect`. A feature that fails stops the
  run, and the instrument then fails.
- **export.** Requires exactly one body in the design, and that body must be closed. SolidCraft
  writes the STEP and the adapter checks that its length unit is `SI_UNIT(.MILLI.,.METRE.)`. The
  STEP is then re-imported in a second process (`solidcraft-cli eval output.step`). It must again
  hold one closed body, and its volume must agree with the model's to within 1e-3. That
  re-imported measurement is the reported `volume_mm3` / `faces`.
- **Volume precision.** SolidCraft's volume is a numeric integral, not an exact B-rep volume. On the
  conformance cases it is within 2e-4 of the exact value (C04 revolve: −1.0e-4). The OCP volume of
  the same STEP is within 1.3e-8. So `needs_measure` is `false`, because the CLI does report a
  volume, and `volume_method` says how it was measured. The dispatcher may re-measure
  `output.step` with OCP when it needs an exact number for a gate. If a future CLI stops reporting
  a volume, `volume_mm3` is `null` and `needs_measure` is `true`.
- **extract.** Features come from **SolidCraft's timeline after the rebuild**, not from the command
  list. Later `timeline.delete`, `timeline.suppress` and `timeline.edit` are therefore honoured,
  and it is never agent self-report. Each timeline item's `type` (FeatureKind type name) maps to
  the taxonomy (table below). `params.definition` is SolidCraft's own `timeline.inspect`
  definition. Suppressed and rolled-back items are not in the model. They go in `excluded`, which
  is neither counted nor unmapped. `parameters[]` lists user parameters, each with the features it
  drives, followed transitively through feature inputs (`parameters.list`).
- **unmapped (gate fail)** covers these cases:
  - a command that imports external geometry: `file.insert_step`, `file.insert_mesh`, `doc.open`,
    `component.insert_design` / `paste_from_design` / `relink` / `replace_with_link`,
    `parts.insert`, `parts.fastener`, `library.apply`, `sketch.insert_dxf` / `insert_svg`,
    `sketch.fit_curves_to_section`, `canvas.to_sketch`, `frame.profile.insert`, `pointcloud.*`;
  - a timeline item of type `BaseFeature` (imported B-rep), `MeshFeature` or
    `MeshConvertFeature`;
  - a timeline item with no taxonomy type (see below);
  - a feature with a rebuild error;
  - a model with no counted feature at all.
- **set-param.** Rebuilds the original script and takes `volume_before` from it. The parameter must
  exist in `parameters.list`. If it has exactly one `parameters.add` or `parameters.change`
  definition, that expression is rewritten. Otherwise, for example for a sketch-dimension
  parameter, `parameters.change {name, expression}` is appended to the script. The value is passed
  as `"<v> mm"` for length parameters and `"<v> deg"` for angle parameters (IR units), whatever the
  parameter's display unit. The edited script is written to `--native-out` and rebuilt. The
  parameter's value is checked, then the script is exported (`volume_after`).

## Taxonomy mapping (feature_taxonomy.yaml, spec 0.3.0; type names unchanged since 0.1.0)

| SolidCraft timeline type | taxonomy | counted |
|---|---|---|
| ExtrudeFeature (new/join/cut/intersect, thin, from face) | extrude | 1 |
| RevolveFeature (incl. cut) | revolve | 1 |
| SweepFeature, PipeFeature, CoilFeature | sweep | 1 |
| LoftFeature | loft | 1 |
| HoleFeature (simple/counterbore/countersink/tapped; several points in one command = 1) | hole | 1 |
| FilletFeature / ChamferFeature (any number of edges) | fillet / chamfer | 1 |
| ShellFeature / DraftFeature | shell / draft | 1 |
| RibFeature, WebFeature | rib | 1 |
| RectangularPatternFeature / CircularPatternFeature | pattern_linear / pattern_circular | 1 (seed counted separately) |
| PathPatternFeature | pattern_linear (no taxonomy type for a path pattern; noted in params) | 1 |
| MirrorFeature (features or bodies; `combine: true` merge is part of the mirror) | mirror | 1 |
| ThreadFeature | thread_cosmetic | 1 |
| Box/Cylinder/Sphere/Torus/ConeFeature | primitive | 1 |
| CombineFeature | boolean | 1, or 0 when it only joins one tool body created by a single counted feature and changed by no other feature (counting_rule note 2; `params.merged_into`). A saved `.sc` design or its exported script replays through `timeline.add`, which reports no created/changed bodies; the adapter then rolls the history marker past each feature and diffs the bodies, so every native form folds the same join |
| Sketch | sketch | – |
| ConstructionPlane / Axis / Point | datum_plane / datum_axis / datum_point | – |
| MoveFeature, AlignFeature, BodyTransferFeature | transform | – |
| BaseFeature, MeshFeature, MeshConvertFeature | **unmapped** (dumb / imported) | – |
| everything else (sheet metal, plastic Boss/Lip/SnapFit/Rest, FrameMember, Emboss, Split/SplitFace, OffsetFaces, DeleteFace, ReplaceFace, Remove, Scale, BoundingSolid, BoundaryFill, all surface features, Thicken, Stitch, …) | **unmapped** (no taxonomy type) | – |

Open points for the spec owner: PathPattern, Thread (modelled vs cosmetic), Emboss, plastic
features, sheet metal, and surface features (a possible `freeform_region`) have no taxonomy type.
They map conservatively (see table) until an ADR decides.
