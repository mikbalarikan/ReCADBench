# backends/conformance/ir_cases

Ten agent-free RE Plan IR cases (spec 0.2.0) with expected results. All backends must reproduce
them with hand-written native replays (`backends/<b>/replays/<case_id>.<ext>`). Synthetic
geometry only.

Each folder `<Cnn_name>/` holds:

| File | Content |
|---|---|
| `re_plan.v1.json` | Tool-neutral RE Plan IR: datum `D1` (primary plane at the origin, normal +Z, x_dir +X), named parameters, features with taxonomy types, selectors |
| `expected.json` | `volume_mm3`, `volume_source`, `faces` (informational, from the build123d reference), `feature_count`, `features` (taxonomy types in IR order), `set_param` {`name`, `value`, `volume_mm3`}, `tolerance_rel` (0.001) |

`feature_count` = taxonomy counting rule applied to the IR = number of IR features (every IR
feature in these cases is of a counted type; sketches, datums, reference planes and parameters
are not features). It must equal the `count` that `recad <b> extract` derives from the native
model, in every backend.

## Cases

| Case | Features (IR order) | Count | Volume mm³ | Source | Faces | set-param | Volume after mm³ |
|---|---|---|---|---|---|---|---|
| C01_block | extrude | 1 | 24000.0000 | analytic | 6 | height 20 → 25 | 30000.0000 |
| C02_through_hole | extrude, hole | 2 | 22429.2037 | analytic | 7 | hole_d 10 → 12 | 21738.0533 |
| C03_counterbore | extrude, hole (counterbore) | 2 | 31010.3983 | analytic | 9 | cb_depth 5 → 8 | 30755.9293 |
| C04_revolve_groove | revolve, revolve (cut) | 2 | 18397.1666 | analytic | 7 | shaft_len 60 → 70 | 21538.7592 |
| C05_rect_pattern | extrude, hole, pattern_linear | 3 | 18257.5222 | analytic | 12 | hole_d 5 → 6 | 17842.8320 |
| C06_circ_pattern | extrude, hole, pattern_circular | 3 | 26577.8738 | analytic | 9 | hole_d 6 → 8 | 25258.4049 |
| C07_fillet_chamfer | extrude, fillet, chamfer | 3 | 23274.5014 | reference build123d (analytic cross-check exact) | 14 | fillet_r 4 → 5 | 23027.4954 |
| C08_shell | extrude, shell | 2 | 7152.0000 | analytic | 11 | wall_t 2 → 3 | 10128.0000 |
| C09_mirror_bracket | extrude, hole, mirror | 3 | 13820.7080 | analytic | 12 | bracket_depth 20 → 25 | 17360.7080 |
| C10_draft_pocket | extrude, extrude (cut), draft | 3 | 34427.2376 | analytic | 11 | pocket_depth 10 → 12 | 33412.2829 |

The build123d reference replays reproduce every value (base and set-param) to 1e-4 mm³.

## Geometry, formulas and counting rationale

- **C01_block**: rect 40×30 on D1, extrude `height` = 20 along +Z. V = 40·30·20. One extrude.
  Exercises: extrude, `{"ref"}` on a feature param.
- **C02_through_hole**: block 40×30×20; through hole Ø`hole_d` = 10 at (0, 0) from the top face
  (`F1:plane:+Z:max`). V = 24000 − π·5²·20. The hole's cut is part of the hole feature (counts 1),
  not an extra boolean.
- **C03_counterbore**: block 40×40×20; hole `kind: counterbore` Ø6 through, counterbore Ø12 ×
  `cb_depth` = 5. V = 32000 − π·3²·15 − π·6²·5 = 32000 − 315π. One hole feature: the counterbore is
  a parameter of the hole, not a second feature (a backend that models it as two cuts must still
  report one hole, i.e. use its native counterbore hole command).
- **C04_revolve_groove**: rect R1 (x 0..10, y 0..`shaft_len`) on `P_xz` (origin 0, normal −Y,
  x_dir +X, so sketch y = world +Z) revolved 360° about `D2` (Z axis) → Ø20×60 shaft; groove = rect
  R2 (x 8..12, y 28..32) revolved 360° with `operation: cut` (radial depth 2, width 4, centre z =
  30). V = π·10²·60 − π·(10² − 8²)·4 = 5856π. Two revolve features (the cut revolve is a feature of
  its own; its subtract is not a separate boolean). Faces: 2 end disks, outer cylinder split in 2
  by the groove, groove floor, 2 groove walls = 7.
- **C05_rect_pattern**: plate 60×40×`plate_t` (8); seed through hole Ø`hole_d` = 5 at (−20, −10);
  `pattern_linear` of [F2]: 3 along +X at 20, 2 along +Y at 20 → holes at x ∈ {−20, 0, 20}, y ∈ {−10,
  10}. V = 19200 − 6·π·2.5²·8. Count 3: pattern = 1, its seed = 1 (instances are not features).
- **C06_circ_pattern**: disk R30 × `plate_t` (10); seed through hole Ø`hole_d` = 6 at (20, 0);
  `pattern_circular` of [F2], 6 over 360° about `D2` (first instance = seed at 0°). V = π·30²·10 −
  6·π·3²·10 = 8460π. Count 3 as in C05.
- **C07_fillet_chamfer**: block 40×30×20; `fillet` R`fillet_r` = 4 on the 4 edges of the top face
  (`F1:plane:+Z:max`); `chamfer` `chamfer_d` = 2 (equal distance) on the 4 edges of the bottom face
  (`F1:plane:-Z:min`). The 4 vertical edges stay sharp. One fillet + one chamfer command each count
  1 regardless of how many edges they touch. Analytic (cross-sections are rectangles inset by
  e(z)): V = LWH − [2(L+W)r²(1−π/4) − 4r³(5/3 − π/2)] − [d²(L+W) − 4d³/3] = 23274.5014, equal to
  the reference. Source is "reference" because the closed form assumes the corner shape (two
  fillet cylinders meeting at a mitre over a sharp vertical edge); a kernel that blends those
  4 corners differently deviates only inside 4·r³ cells.
- **C08_shell**: block 40×30×20 shelled with `thickness` = `wall_t` = 2, **inward** (outer
  dimensions kept), `open_faces` = top face. V = 40·30·20 − 36·26·18 = 7152. Faces: 5 outer, 5
  inner, 1 rim = 11.
- **C09_mirror_bracket**: half L-profile (polygon (0,0) (35,0) (35,30) (29,30) (29,6) (0,6)) on
  `P_xz` (origin (0, 10, 0), normal −Y, x_dir +X) extruded `bracket_depth` = 20 along −Y (y +10 →
  −10); through hole Ø`hole_d` = 6 at (17, 0) from the base top face (`F1:plane:+Z:min`, i.e. the
  lowest +Z-facing plane, z = 6); `mirror` of [F1, F2] about `P_yz` (x = 0). Result: one U-bracket
  70 wide, 30 high, two holes. V = 2·(354·20 − π·3²·6). Count 3: the mirror counts 1, its seeds
  F1 and F2 count separately; the merge of the mirrored copy into the body is part of the mirror.
- **C10_draft_pocket**: block 50×40×20; pocket = rect 30×20 sketched on `P_top` (z = 20),
  `extrude` `operation: cut`, `pocket_depth` = 10 along −Z; `draft` `draft_deg` = 5 on the 4 pocket
  walls (`F2:plane:±X:any`, `F2:plane:±Y:any`), neutral = top face. V = 40000 − [ab·h − (a+b)·t·h²
  + 4t²h³/3] with a = 30, b = 20, h = 10, t = tan 5°. Count 3: a cut extrude is an extrude.

## Conventions decided for these cases (taxonomy/IR leaves them open)

1. **Edge sets given as a face selector** (`edges: "F1:plane:+Z:max"`) mean the boundary edges of
   that face. The selector grammar's `:edges:<filter>` forms (`outer`, `convex`, …) cannot select
   "the top edges only".
2. **Hole**: `positions` are points on the entry face; the hole axis is the inward normal of the
   face in `refs`. `through: true` cuts through all material along that axis.
3. **Shell**: positive `thickness` is inward (outer skin kept).
4. **Draft**: the pull direction is the outward normal of the `neutral` face (+Z here); a
   positive angle tapers the walls so that the pocket narrows away from the neutral plane
   (mould-release sense). The draft params allow only `angle_deg`, `faces`, `neutral`, so the pull
   direction cannot be stated explicitly.
5. **Selector extremum** is taken along the selector's direction axis: `F1:plane:+Z:min` is the
   lowest +Z-facing plane of F1 (the bracket base top at z = 6, not the wall tops at z = 30).
6. **Extrude** `distance` is one-sided along `direction` (`symmetric` is never used: its meaning,
   total vs. per side, differs between CAD programs).
7. **Feature selectors after modification** (`F2:plane:...` in C10, `F1:plane:+Z:max` after F2 in
   C10) refer to the faces generated by that feature as they exist in the current body.
8. **Sketch-only parameters**: IR 0.2.0 sketch entities are numbers only (no `{"ref"}`). C04's
   `shaft_len` drives the height of sketch rect `R1`; the binding is stated in
   `sketch.constraints` (`"R1.height = shaft_len (bottom edge on y = 0)"`). The validator
   therefore warns `parameter 'shaft_len' is not referenced by any feature` — expected. All other
   cases bind their parameters via `{"ref"}` and validate without warnings.

## Validation

```bash
python -m recadbench_spec.cli validate backends/conformance/ir_cases/*/re_plan.v1.json
```

10/10 OK, 0 errors; 1 warning (C04, convention 8).
