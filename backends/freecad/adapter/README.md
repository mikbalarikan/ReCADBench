# backends/freecad/adapter

`adapter.py` = `recad freecad export|extract|set-param|replay`. Run it with FreeCAD's Python
(stdlib + FreeCAD only; it never imports `recadbench_spec`). It runs on the worker host, post-run,
in a clean process; the evaluator never imports it. Contract, mapping table and native model rules:
[`../README.md`](../README.md).

```bash
FCPY=<freecad-env>/bin/python
$FCPY adapter.py export    part.FCStd --out output.step            # {"ok","solids","volume_mm3","faces","unit"}
$FCPY adapter.py extract   part.FCStd --out features.json          # features.schema.json
$FCPY adapter.py set-param part.FCStd hole_d 12 --out sp.step [--native-out sp.FCStd]
$FCPY adapter.py replay    C05_rect_pattern --out-dir out/C05      # native.FCStd, output.step, features.json
```

Every call prints one JSON object on stdout (OCCT chatter is redirected to stderr at the fd level)
and exits 0 on success, 1 on failure.
