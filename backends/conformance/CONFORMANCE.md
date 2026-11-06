# Conformance results

Two hosts, same day, same pins, `python backends/conformance/run_conformance.py` (all three
backends in parallel, every call through the `recad` 0.1.0 dispatcher):

| Host | Run | Wall time | Result |
|---|---|---|---|
| **windows** (Windows 11 x64, evaluator host) | 2026-11-06, PR H1 | 96 s | **PASS**: build123d 10/10, freecad 10/10, solidcraft 10/10 |
| linux (Linux x86_64, cloud build host) | 2026-11-06, P3 | 65 s | **PASS**: build123d 10/10, freecad 10/10, solidcraft 10/10 |

Counts and types are identical across backends for every case, and the per-row volume and
set-param errors are **identical on both hosts** to the 4 decimals shown, so the table below holds
for both.

| Backend | Version under test (both hosts) | windows install | linux install |
|---|---|---|---|
| build123d | build123d 0.13.0 (OCP 8, cadquery-ocp-novtk 8.0.1.1), pin build123d-mcp 0.3.91 | pip venv, CPython 3.13 | pip venv, CPython 3.13 |
| freecad | FreeCAD weekly-2026.09.16 development build (git a4ce44d33, reports 26.3.0 dev; predates 26.3rc1, not a release), occt 8.0.1, pin freecad-robust-mcp 0.6.1 | conda-forge `freecad=2026.09.16` win-64 `py311h48521fb_0`, CPython 3.11 | conda-forge linux-64, CPython 3.11 |
| solidcraft | solidcraft-cli 0.30.1 | release `solidcraft-0.30.1-windows-x64-portable.zip` (sha256 matches the release `SHA256SUMS.txt`), adapter on CPython 3.13 | source build (rustc 1.97.0), CPython 3.13 |

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
(recadbench-spec 0.3.0 since ADR 0004; the runs above used 0.2.0 -- the features schema only gained optional keys, checked by the dispatcher), `unmapped` empty, and taxonomy types equal
to `expected.features` in IR order. Face counts match `expected.faces` everywhere. They are
informational and not gated.

Notes:

- The solidcraft volume errors come from the CLI's numeric `eval` of the re-imported STEP. The
  same STEP measured with OCP is within 1.3e-8 of expected (`../solidcraft/adapter/README.md`).
  The largest error is C04 at −0.0102 %, 10 times inside tolerance.
- The build123d and freecad errors are below 1e-8 relative (shown as 0.0000 %).
- windows, not the pin: the official stable FreeCAD 1.1.1 Windows bundle (1.1.4 not tested) (`bin\python.exe`) also
  passes 10/10 with the same counts (`--backend freecad`).
- Adapter test suites on windows: `backends/tests` + `backends/build123d/tests` +
  `backends/solidcraft/tests` 142 passed; `backends/freecad/tests` 43 passed (FreeCAD weekly-2026.09.16 env).

## MCP tool lists

`backends/<b>/mcp_tools.json` is the live `tools/list` of each pinned MCP server (names and
`readOnlyHint`), taken on windows 2026-11-06 with `mcp_tools.py refresh`. `mcp_tools.py check`
and the CI test `backends/tests/test_mcp_tools.py` check it against `backend.yaml`
`cad_prefixes.mutating`: no stale entry, every tool not annotated read-only is either mutating or
listed with a reason under the snapshot's `not_mutating`. The host-only test in the same file
re-lists the installed servers and fails on any drift.

| Backend | Server (serverInfo) | Tools | mutating | not_mutating (reason in snapshot) |
|---|---|---|---|---|
| build123d | build123d-mcp 0.3.91 | 48 | 8 (`load_part` only with `--library`) | 6 |
| freecad | freecad-robust-mcp 0.6.1 (reports `freecad-mcp 1.30.0`, the mcp library version) | 83 | 35 entries (prefixes `create_`, `add_sketch_`) | 31 (0.6.1 sets no annotations) |
| solidcraft | solidcraft 0.30.1 (`mcp --headless`) | 21 | 7 | 5 |

The windows check found one gap: freecad `recompute_document` runs the same `doc.recompute()`
as the mutating `recompute` and is now in `mutating`.

## Reproduce

```bash
pip install -e ./spec -e ./backends            # dispatcher + recadbench-spec (+ build123d for the reference backend)
export RECAD_BUILD123D_PYTHON=<b3d-env>/bin/python
export RECAD_FREECAD_PYTHON=<freecad-env>/bin/python
export RECAD_SOLIDCRAFT_CLI=<path>/solidcraft-cli
recad backends                                  # which environments are usable here
python backends/conformance/run_conformance.py --out /tmp/conformance.json
```

Windows (PowerShell): the same with `$env:RECAD_FREECAD_PYTHON = "<freecad-env>\python.exe"`
(conda-forge env root, no activation needed) and
`$env:RECAD_SOLIDCRAFT_CLI = "<dir>\solidcraft-cli.exe"`.

MCP snapshots (host-only; RECAD_FREECAD_CMD = the env's `Library\bin\freecadcmd.exe` and
RECAD_FREECAD_BRIDGE = `blocking_bridge.py` at the pinned tag start a private bridge):

```bash
export RECAD_BUILD123D_MCP=<b3d-env>/bin/build123d-mcp RECAD_FREECAD_MCP=<freecad-mcp-venv>/bin/freecad-mcp
python backends/conformance/mcp_tools.py refresh     # rewrites backends/<b>/mcp_tools.json
python backends/conformance/mcp_tools.py check
pytest backends/tests/test_mcp_tools.py              # live servers vs snapshots where installed
```

Rerun on every pin bump of any backend and on every change to `ir_cases/` or a replay, then
update this file and the `conformance` block of each `backend.yaml`.
