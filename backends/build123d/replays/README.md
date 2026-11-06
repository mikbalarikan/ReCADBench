# backends/build123d/replays

Reference native replays of the 10 conformance IR cases
(`backends/conformance/ir_cases/<case_id>/re_plan.v1.json`), one build123d script per case:
`<case_id>.py`. build123d is the reference backend: these scripts produced the `faces` values and
confirmed every volume in `expected.json` (base and set-param, to 1e-4 mm³).

## Native format (what the adapter relies on)

- Plain Python using the build123d **builder API** (`BuildPart` / `BuildSketch`), no direct
  OCP/OCC calls (those would be `unmapped`).
- Driving dimensions are **module-level assignments** with numeric literals (`height = 20.0`).
  Names that are IR `parameters[].name` are marked `# IR parameter`; `set-param <name> <value>`
  rewrites exactly that one assignment (it must be assigned once, to a literal) and re-executes
  the script. Other assignments are sketch literals of the IR, named for readability. `extract`
  reports every module-level numeric assignment in `parameters[]`.
- **Final part** (export / extract / set-param): the module variable **`part`** (`part =
  bp.part`); if absent, `result`; then the last object passed to `show()` (build123d-mcp
  convention; the adapter provides a `show` stub); then the last top-level `BuildPart`. A
  `BuildPart` given as `part`/`result` is replaced by its `.part`. Export requires exactly one
  solid (mm) and re-imports the written STEP to check it. The replays always set `part`.
  Sub-shapes of a traced part (`bp.part.solids()[0]`, `.solid()`) keep its provenance.
- The script runs in a fresh namespace (`__name__ == "__recad_native__"`) in a clean process,
  with its own directory on `sys.path` (so agent `lib/` helpers can be imported; ops they call
  are traced like the script's own).

## Operation → taxonomy mapping (adapter `extract`, runtime trace)

`extract` executes the script with build123d's operation classes/functions wrapped for the
duration of the run and records each **top-level** call (calls made inside another traced
operation, i.e. build123d internals, are ignored). A call inside a Python loop counts once per
execution. Ops whose result is 1D/2D (inside `BuildSketch`/`BuildLine`, or a sketch in algebra
mode) are sketch geometry and are not counted.

| build123d call | Taxonomy | Cases |
|---|---|---|
| `BuildSketch`, `BuildLine`, `Rectangle`, `Circle`, `Polygon`, …, `Plane`, `Axis`, `Location`, single-location `Locations` | not counted (sketch / datum / placement) | all |
| `Box`, `Cylinder`, `Cone`, `Sphere`, `Torus`, `Wedge` | primitive | — |
| `extrude(...)` (any mode, incl. `Mode.SUBTRACT`) | extrude | C01–C03, C05–C10 |
| `revolve(...)` (any mode) | revolve | C04 (×2) |
| `sweep(...)` / `loft(...)` (3D) | sweep / loft | — |
| `Hole`, `CounterBoreHole`, `CounterSinkHole` | hole (`kind` simple / counterbore / countersink) | C02, C03, C05, C06, C09 |
| part object executed under a multi-location `GridLocations` / `HexLocations` / `Locations` | seed feature **+** pattern_linear | C05 |
| part object executed under a multi-location `PolarLocations` | seed feature **+** pattern_circular | C06 |
| algebra `GridLocations(...) * shape` / `[locs] * shape` (>1) | pattern (seed counted where it was made) | — |
| nested `BuildPart` published under a multi-location context | pattern of the nested part | — |
| `mirror(...)` (3D) | mirror | C09 |
| `fillet(...)` / `chamfer(...)` (3D) | fillet / chamfer (1 per call) | C07 |
| `offset(...)` on a part (`amount<0, openings=...` = inward shell) | shell | C08 |
| `draft(...)` | draft | C10 |
| `Shape.mirror(plane)` on a 3D body (user code) | mirror (same as `mirror(...)`) | — |
| placement copies (`Shape.moved/located/translate/rotate`, `Pos(...) *`) of one shape merged back into a body that already holds an instance of it (`h + h.moved(L)`, `plate - h - h.moved(L)`) | **one** pattern (only translations → pattern_linear, any rotation → pattern_circular; `count` = instances); no boolean — same as `[locs] * h` | — |
| `scale(...)`, a single `Shape.moved/located/rotate/translate`, `Pos(...) *` (repositioning) | not counted (transform) | — |
| algebra `+ - &`, `fuse/cut/intersect`, builder `add(...)`, nested `BuildPart` transfer | boolean **only** if it combines two bodies that each comprise > 1 feature (subtract: the tool comprises > 1); merging a single feature into a body is part of that feature | — |
| `thicken`, `split`, `make_brake_formed`, `ConvexPolyhedron`, any other `BasePartObject` subclass (custom, bd_warehouse) | **unmapped** (no taxonomy type) | — |
| `import OCP/OCC/cadquery`, `.wrapped`, `exec/eval/compile/__import__`, `importlib` (static scan of the script and of user modules it imports) | **unmapped** | — |
| `Solid.make_*`, `Solid.extrude/revolve/sweep/thicken/fillet/chamfer/draft/…`, `Shape.cast` called by user code | **unmapped** (raw topology) | — |
| `import_step/stl/brep/threemf`, `Mesher.read` | **unmapped** (imported geometry) | — |
| `fillet/chamfer/offset/draft/mirror` applied to a body with no traced provenance (hand-sewn `Solid(Shell(...))`, unpickled/imported/raw-kernel solids); boolean/`add` operand with no traced provenance; final part not built by traced ops; no feature at all | **unmapped** | — |
| `import builtins` / `builtins.exec` / `__builtins__` (static scan) | **unmapped** (dynamic code) | — |

**Only features in the final part's provenance are counted.** The tracer follows every traced
result through booleans, `add()`, builder transfer, placement copies and sub-shape selection;
traced ops whose result never reaches the final part (scratch/exploratory geometry, a
`Mode.PRIVATE` object or builder that is never `add`ed, padding) are listed by name in
features.json `not_in_final_part` (informational) and are not in `features`/`count`.

build123d has no command that patterns an existing feature, so a pattern is written as the seed
operation run once under a multi-location context. Counting it as 1 seed + 1 pattern (not N
features, not 1) keeps the count identical to backends that have native pattern commands
(taxonomy: "a pattern/mirror counts 1, its seed separately"). One multi-location context = one
pattern, however many ops run inside it; nested multi-location contexts = one pattern each. A
`Locations` context with a single location is placement only. Consequence: a multi-position hole
written as `with Locations(p1, p2): Hole(...)` counts hole + pattern_linear (an IR `hole` with two
`positions` counts 1) — write such holes as seed + `pattern_linear` in the IR.

## Run

```bash
python -c "import runpy; p = runpy.run_path('C05_rect_pattern.py')['part']; print(p.volume, len(p.faces()))"
python ../adapter/adapter.py replay C05_rect_pattern --out-dir /tmp/c05   # = recad build123d replay
```

| Case | Volume mm³ | Faces | Solids |
|---|---|---|---|
| C01_block | 24000.0000 | 6 | 1 |
| C02_through_hole | 22429.2037 | 7 | 1 |
| C03_counterbore | 31010.3983 | 9 | 1 |
| C04_revolve_groove | 18397.1666 | 7 | 1 |
| C05_rect_pattern | 18257.5222 | 12 | 1 |
| C06_circ_pattern | 26577.8738 | 9 | 1 |
| C07_fillet_chamfer | 23274.5014 | 14 | 1 |
| C08_shell | 7152.0000 | 11 | 1 |
| C09_mirror_bracket | 13820.7080 | 12 | 1 |
| C10_draft_pocket | 34427.2376 | 11 | 1 |

(build123d 0.13.0, OCP 8.)
