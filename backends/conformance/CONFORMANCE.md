# Conformance results

**Run:** 2026-11-06, Linux x86_64, `python backends/conformance/run_conformance.py` (all three
backends in parallel, every call through the `recad` 0.1.0 dispatcher, 65 s wall time).
**Result: PASS.** build123d 10/10, freecad 10/10, solidcraft 10/10. Counts and types are
identical across backends for every case.

| Backend | Version under test | Interpreter |
|---|---|---|
| build123d | build123d 0.13.0 (OCP 8, cadquery-ocp-novtk 8.0.1.1), pin build123d-mcp 0.3.91 | CPython 3.13 venv |
| freecad | FreeCAD 26.3.0 (conda-forge), pin freecad-robust-mcp 0.6.1 | FreeCAD env CPython 3.11 |
| solidcraft | solidcraft-cli 0.30.1 (source build, rustc 1.97.0) | CPython 3.13 (stdlib) + CLI |

The volume error is `(replay STEP volume − expected) / expected`. `set-param` is the expected
`set_param` edit from `expected.json`, rebuilt and re-exported; its error is measured against
`set_param.volume_mm3`. Tolerance is 0.1 % for both (`tolerance_rel` 0.001). `n` is the feature
count from `extract`.

| Case | Expected n | build123d vol err / n / set-param | freecad vol err / n / set-param | solidcraft vol err / n / set-param | Cross-backend |
|---|---|---|---|---|---|
| C01_block | 1 | 0.0000 % / 1 / ok (0.0000 %) | 0.0000 % / 1 / ok (0.0000 %) | 0.0000 % / 1 / ok (0.0000 %) | identical |
| C02_through_hole | 2 | 0.0000 % / 2 / ok (0.0000 %) | 0.0000 % / 2 / ok (0.0000 %) | +0.0007 % / 2 / ok (+0.0011 %) | identical |
| C03_counterbore | 2 | 0.0000 % / 2 / ok (0.0000 %) | 0.0000 % / 2 / ok (0.0000 %) | +0.0003 % / 2 / ok (+0.0004 %) | identical |
| C04_revolve_groove | 2 | 0.0000 % / 2 / ok (0.0000 %) | 0.0000 % / 2 / ok (0.0000 %) | −0.0102 % / 2 / ok (−0.0102 %) | identical |
| C05_rect_pattern | 3 | 0.0000 % / 3 / ok (0.0000 %) | 0.0000 % / 3 / ok (0.0000 %) | +0.0005 % / 3 / ok (+0.0008 %) | identical |
| C06_circ_pattern | 3 | 0.0000 % / 3 / ok (0.0000 %) | 0.0000 % / 3 / ok (0.0000 %) | −0.0019 % / 3 / ok (−0.0015 %) | identical |
| C07_fillet_chamfer | 3 | 0.0000 % / 3 / ok (0.0000 %) | 0.0000 % / 3 / ok (0.0000 %) | −0.0007 % / 3 / ok (−0.0011 %) | identical |
| C08_shell | 2 | 0.0000 % / 2 / ok (0.0000 %) | 0.0000 % / 2 / ok (0.0000 %) | 0.0000 % / 2 / ok (0.0000 %) | identical |
| C09_mirror_bracket | 3 | 0.0000 % / 3 / ok (0.0000 %) | 0.0000 % / 3 / ok (0.0000 %) | +0.0002 % / 3 / ok (+0.0002 %) | identical |
| C10_draft_pocket | 3 | 0.0000 % / 3 / ok (0.0000 %) | 0.0000 % / 3 / ok (0.0000 %) | 0.0000 % / 3 / ok (0.0000 %) | identical |
| **Passed** | | **10/10** | **10/10** | **10/10** | **10/10** |

All 30 rows: exactly one solid, `features.json` valid against `features.schema.json`
(recadbench-spec 0.2.0, checked by the dispatcher), `unmapped` empty, and taxonomy types equal
to `expected.features` in IR order. Face counts match `expected.faces` everywhere. They are
informational and not gated.

Notes:

- The solidcraft volume errors come from the CLI's numeric `eval` of the re-imported STEP. The
  same STEP measured with OCP is within 1.3e-8 of expected (`../solidcraft/adapter/README.md`).
  The largest error is C04 at −0.0102 %, 10 times inside tolerance.
- The build123d and freecad errors are below 1e-8 relative (shown as 0.0000 %).

## Reproduce

```bash
pip install -e ./spec -e ./backends            # dispatcher + recadbench-spec (+ build123d for the reference backend)
export RECAD_BUILD123D_PYTHON=<b3d-env>/bin/python
export RECAD_FREECAD_PYTHON=<freecad-env>/bin/python
export RECAD_SOLIDCRAFT_CLI=<path>/solidcraft-cli
recad backends                                  # which environments are usable here
python backends/conformance/run_conformance.py --out /tmp/conformance.json
```

Rerun on every pin bump of any backend and on every change to `ir_cases/` or a replay, then
update this file and the `conformance` block of each `backend.yaml`.
