# backends/solidcraft/replays

These are hand-written SolidCraft JSON command scripts, one per conformance case in
`backends/conformance/ir_cases/<case>/re_plan.v1.json`. They are run with
`adapter.py replay <case>`, or directly with `solidcraft-cli run <case>.json --out part.step`.
Each script carries a `description` and the IR path. Feature commands carry `"ir": "F<n>"`, which
SolidCraft ignores. All of them pass `--param-check strict`.

Every IR parameter is a SolidCraft user parameter with the IR name (`parameters.add`), and the
feature that uses it references it by name. Other dimensions are plain numbers, as in the IR.
**No case is unsupported:** SolidCraft 0.30.1 has a native command for every IR feature,
including counterbore hole, shell, draft, feature patterns and mirror.

| Case | SolidCraft commands (counted ones in bold) | Notes |
|---|---|---|
| C01_block | sketch rect · **solid.extrude** `height` | |
| C02_through_hole | … · **solid.hole** simple, `diameter: hole_d`, at [0,0,20] | No depth means through all. Drilled against the face normal |
| C03_counterbore | … · **solid.hole** `type: counterbore`, `cb_depth` | One native counterbore hole, so 1 feature |
| C04_revolve_groove | sketch on `XZ` + `flip` · **solid.revolve** about Z · sketch · **solid.revolve** `operation: cut` | `flip: true` makes the sketch normal −Y with sketch y = world +Z (IR `P_xz`). `shaft_len` drives the rectangle corner `[10, "shaft_len"]` |
| C05_rect_pattern | … · **solid.hole** at `[-20,-10,"plate_t"]` · **solid.pattern.rectangular** `features: ["Hole1"]` 3×20 / 2×20 | A feature pattern: the seed and the pattern count 1 each |
| C06_circ_pattern | circle · **extrude** · **solid.hole** · **solid.pattern.circular** about Z, 6 over 360° | |
| C07_fillet_chamfer | … · **solid.fillet** 4 top edges, picked at edge midpoints · **solid.chamfer** 4 bottom edges | One command each. The 4 vertical edges stay sharp. The rolling-ball corner gives the reference volume (OCP −1.0e-8) |
| C08_shell | … · **solid.shell** `faces: [[0,0,20]]`, `direction: inside` | |
| C09_mirror_bracket | sketch `XZ` + `flip` + `offset: bracket_depth / 2` · polyline · **extrude** `bracket_depth` · **solid.hole** at z = 6 · **solid.mirror** `features: ["Extrude1", "Hole1"]` about YZ · **solid.combine** join `Body2` into `Body1` | See below |
| C10_draft_pocket | … · sketch on XY offset 20 · **solid.extrude** `operation: cut`, `direction: negative` · **solid.draft** the 4 walls, neutral `{origin [0,0,20], normal +Z}`, `angle: draft_deg` | The default pull is the neutral normal (+Z). A positive angle narrows the pocket toward its floor, matching IR convention 4 |

**C09 mirror.** The replay follows the IR: `solid.mirror` with `features: ["Extrude1", "Hole1"]`
mirrors the seed features. Because the extrude is `new`, the mirror makes a second body, and
`combine` is not applied in feature mode (v0.30.1). A `solid.combine` (`join`, one tool `Body2`
into `Body1`) merges it. The adapter folds a single-tool join into the feature that made its tool
body (`merged_into: Mirror1`, not counted), so the count is 3 (extrude, hole, mirror) and the
export is one solid. The sketch plane is offset by `bracket_depth / 2`, so the bracket stays
centred on y = 0 when `bracket_depth` changes.

**C10 draft faces.** SolidCraft does not take expressions in face picks, so the four walls are
picked at z = 19.5, just below the top edge. They stay on the walls for any `pocket_depth` > 0.5
(e.g. `pocket_depth` 4 → 37669.3378 mm³, the same as the other backends).

## Results (2026-11-06, solidcraft-cli 0.30.1, Linux x86_64)

The OCP volume is the exact B-rep volume of `output.step` (build123d `import_step`). The CLI
volume is what the adapter reports (`solidcraft-cli eval` of the re-imported STEP).

| Case | expected mm³ | OCP volume (rel) | CLI volume (rel) | count | faces | set-param → OCP / CLI (expected) |
|---|---|---|---|---|---|---|
| C01_block | 24000 | 24000.0000 (0) | 24000.0000 (0) | 1/1 | 6/6 | height 25 → 30000.0000 / 30000.0000 (30000) |
| C02_through_hole | 22429.2037 | 22429.2037 (−1.2e-9) | 22429.3627 (+7.1e-6) | 2/2 | 7/7 | hole_d 12 → 21738.0533 / 21738.2822 (21738.0533) |
| C03_counterbore | 31010.3983 | 31010.3983 (+4.6e-10) | 31010.4965 (+3.2e-6) | 2/2 | 9/9 | cb_depth 8 → 30755.9293 / 30756.0529 (30755.9293) |
| C04_revolve_groove | 18397.1666 | 18397.1666 (−1.1e-9) | 18395.2892 (−1.0e-4) | 2/2 | 7/7 | shaft_len 70 → 21538.7592 / 21536.5619 (21538.7592) |
| C05_rect_pattern | 18257.5222 | 18257.5222 (+2.1e-10) | 18257.6176 (+5.2e-6) | 3/3 | 12/12 | hole_d 6 → 17842.8320 / 17842.9693 (17842.8320) |
| C06_circ_pattern | 26577.8738 | 26577.8738 (+1.9e-9) | 26577.3747 (−1.9e-5) | 3/3 | 9/9 | hole_d 8 → 25258.4049 / 25258.0255 (25258.4049) |
| C07_fillet_chamfer | 23274.5014 | 23274.5012 (−1.0e-8) | 23274.3366 (−7.1e-6) | 3/3 | 14/14 | fillet_r 5 → 23027.4951 / 23027.2452 (23027.4954) |
| C08_shell | 7152 | 7152.0000 (0) | 7152.0000 (0) | 2/2 | 11/11 | wall_t 3 → 10128.0000 / 10128.0000 (10128) |
| C09_mirror_bracket | 13820.7080 | 13820.7080 (−4.8e-10) | 13820.7424 (+2.5e-6) | 3/3 | 12/12 | bracket_depth 25 → 17360.7080 / 17360.7424 (17360.7080) |
| C10_draft_pocket | 34427.2376 | 34427.2376 (+8.5e-10) | 34427.2376 (+8.5e-10) | 3/3 | 11/11 | pocket_depth 12 → 33412.2829 / 33412.2829 (33412.2829) |

All 10 cases pass: one solid, volume within 0.1 % (CLI and OCP), count and types equal to
`expected.json`, `unmapped` empty, and the set-param round trip OK. Face counts match the build123d
reference in every case.
