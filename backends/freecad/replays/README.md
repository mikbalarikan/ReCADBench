# backends/freecad/replays

Hand-written FreeCAD PartDesign replays of the conformance IR cases
(`../../conformance/ir_cases/<case>/re_plan.v1.json`), one `<case_id>.py` each, plus the helper
`_fc.py` (FreeCAD import bootstrap, `Params` spreadsheet, origin-plane sketches, rectangles).

```bash
<freecad-env>/bin/python C04_revolve_groove.py /tmp/native.FCStd   # or: adapter.py replay C04_revolve_groove …
```

Each replay builds one `PartDesign::Body` with native features only, in IR order. IR parameters
go in the `Params` spreadsheet and are bound by expressions; plain IR numbers stay literal.

| Case | Native features |
|---|---|
| C01 | Pad |
| C02 | Pad, Hole (ThroughAll) |
| C03 | Pad, Hole (Counterbore; one feature) |
| C04 | Revolution, Groove (`shaft_len` drives the named sketch constraint `shaft_len`) |
| C05 | Pad, Hole, LinearPattern (2 directions in one feature) |
| C06 | Pad, Hole, PolarPattern |
| C07 | Pad, Fillet (4 top edges), Chamfer (4 bottom edges) |
| C08 | Pad, Thickness (inward: `Reversed = True`, `Join = Intersection`) |
| C09 | Pad (sketch on XZ at y = +10, along −Y), Hole, Mirrored [Pad, Hole] about YZ |
| C10 | Pad, Pocket, Draft (neutral plane = top face, pocket narrows downwards) |

FreeCAD 26.3 quirk: a two-direction LinearPattern created from Python needs
`Spacings2 = [-1.0] * (Occurrences2 - 1)`, or the second-direction copies coincide (C05).
