# ADR 0003 — FreeCAD backend: freecad-robust-mcp over a headless FreeCADCmd bridge

- Status: accepted
- Date: 2026-11-06 (P3)
- Spec version: 0.2.0 (no schema change; `backends/freecad/backend.yaml` validates as is)

## Context

05_TOOLS lists FreeCAD as an MCP backend "chosen in P3", candidates neka-nat/freecad-mcp and
freecad-robust-mcp, and states a preference for headless `FreeCADCmd`. 07_ROADMAP names the risk
that no candidate is headless-ready (fallback: a FreeCADCmd CLI backend like NX). Workers are
headless Linux boxes plus the Windows hosts; the benchmark runs several agents in parallel and
needs PartDesign (parametric feature tree) and STEP export.

Both candidates were cloned and their code and docs read. Then each was installed in a throwaway
venv and started against FreeCAD 26.3 (conda-forge `freecad 2026.09.16`, Python 3.11, OCCT 8.0.1)
with no display. An MCP 2.x client listed the tools and ran a PartDesign Body → Sketch → Pad →
STEP smoke test.

| Criterion | neka-nat/freecad-mcp | freecad-robust-mcp (spkane/freecad-robust-mcp-and-more) |
|---|---|---|
| Pinned version tested | PyPI `freecad-mcp==0.1.28` (2026-10-25), git `v0.1.28` | PyPI `freecad-robust-mcp==0.6.1` (2026-01-13) + bridge git tag `robust-mcp-workbench-v0.6.1` |
| Architecture | MCP server ↔ XML-RPC ↔ **GUI addon** (`addon/FreeCADMCP`, started from `InitGui.py`; imports `FreeCADGui`, PySide; dispatches every call on the Qt GUI thread) | MCP server ↔ XML-RPC/JSON-RPC ↔ **bridge** that runs in GUI *or* `freecadcmd` (`blocking_bridge.py`); an embedded in-process mode exists too (Linux only) |
| Headless | **No** for the toolset: with no GUI running, 16 of 17 tools fail (`ConnectionRefused`). Only `execute_code_headless` works, which starts a fresh `freecadcmd -c` per call (stateless; the script must open and save the document itself) | **Yes**: `freecadcmd blocking_bridge.py` started in 2 s, "headless mode"; 83 tools listed; all modelling, document and export tools are marked "All" modes (only view/screenshot tools need the GUI) |
| Arbitrary code | `execute_code` (GUI), `execute_code_headless` (one-shot) | `execute_python` (persistent session state; returns `_result_`, stdout, traceback) |
| PartDesign | via code only (generic `create_object`) | dedicated tools: body, sketch (rect/circle/line/arc/point), pad, pocket, revolution, groove, hole, loft, sweep, linear/polar pattern, mirrored, fillet, chamfer + `execute_python` |
| STEP export | via code | `export_step` tool (+ via code) |
| Smoke test (headless) | `execute_code_headless`: volume 24000 mm³, STEP written | `execute_python`: volume 24000 mm³, 1 solid; `export_step` wrote STEP; two bridges on separate ports served two sessions independently |
| Licence | MIT | Apache-2.0 AND MIT (bridge files MIT) |
| Activity | very active: 29 PyPI releases, tags to v0.1.28, commits 2026-10-25 | active repo: commits to 2026-11-05, server 0.6.1/0.6.2 and 0.7.0-rc.1…4, workbench rc.5; **PyPI lags** (last 0.6.1, Jan 2026) |
| Install | `uvx freecad-mcp` + copy addon into FreeCAD Mod + start FreeCAD GUI and click "Start RPC Server" | `pip install freecad-robust-mcp` (own venv) + bridge folder from the pinned tag, started with `freecadcmd` |
| Defects found | — | 0.6.1 declares `mcp>=1.25` with no upper bound but imports `mcp.server.fastmcp`, so it **crashes under mcp 2.x**. Install with `mcp<2` (1.30.0 tested) |

## Decision

1. **FreeCAD backend = freecad-robust-mcp, interface `mcp`, XML-RPC mode against a headless
   bridge.** On every worker, a run gets its **own** `freecadcmd …/freecad_mcp_bridge/blocking_bridge.py`
   process on per-slot ports (`FREECAD_XMLRPC_PORT` = 9875 + 10·slot,
   `FREECAD_SOCKET_PORT` = 9876 + 10·slot). The agent's MCP client launches `freecad-mcp` (the
   robust server's console script) with `FREECAD_MODE=xmlrpc` and the same ports. Pins are in
   `backends/freecad/backend.yaml`: server `freecad-robust-mcp==0.6.1` + `mcp<2`, bridge
   `robust-mcp-workbench-v0.6.1`, FreeCAD 26.3. `max_parallel: 2`, hosts linux and windows.
2. **Why not neka-nat**: its useful tools need a running FreeCAD GUI with the addon started by
   hand (or by its auto-start setting), i.e. a display per parallel slot. Its only headless tool is
   a stateless one-shot script runner, which is effectively the CLI fallback with extra
   layers. It is the more polished GUI integration, and remains the candidate to re-evaluate if it
   gains a headless RPC host.
3. **XML-RPC mode, not embedded**: embedded mode needs the MCP server's Python to match FreeCAD's
   bundled Python minor version and is documented to crash on macOS/Windows. XML-RPC with a
   separate bridge process works the same way on Linux and Windows, and a FreeCAD crash only kills
   the bridge.
4. **Measurement does not go through the MCP.** `backends/freecad/adapter/adapter.py` runs in a
   clean process with FreeCAD's own Python (stdlib + FreeCAD only) on the saved `cad/part.FCStd`.
   - `export`: STEP export of the single Body's shape, read back to check 1 solid and mm units.
   - `extract`: Body features by `TypeId` → taxonomy.
   - `set-param`: sets the alias in the `Params` spreadsheet and recomputes.
   - `replay`: runs the replay script, then export + extract.

   Native contract for agents and skillsets: one `PartDesign::Body`, and a `Spreadsheet::Sheet`
   named `Params` whose aliases equal the IR parameter names, bound by expressions.
5. **Fallback stays specified**: if a bump of the robust server or bridge breaks headless operation, the
   backend switches to `interface.kind: cli` with
   `allowed_commands: ["recadbench freecad-run cad/*.py"]`. That wrapper runs `freecadcmd` on a
   journal that opens or creates `cad/part.FCStd`, executes, saves and dumps `cad/last_run.json`,
   as `nx-run` does. The adapter is unchanged, because it already only needs FreeCAD's Python.

## Consequences

- Conformance: 10/10 cases pass with FreeCAD 26.3. Volumes and set-param volumes are within
  2e-9 relative, and counts are identical to the IR `feature_count` (`backends/freecad/tests`, 41 tests).
- **Package-name clash**: both candidates ship the console script `freecad-mcp` and the import
  package `freecad_mcp`. The robust server must live in its own venv, never next to neka-nat's.
- **Per-run bridge**: a bridge keeps its documents across MCP sessions (observed: a document
  from an earlier session was still listed). The runner must start a fresh bridge per run and kill
  it afterwards. Otherwise runs leak state into each other.
- **Security**: bridge 0.6.1 listens on localhost with no authentication and executes arbitrary
  Python. Bind to loopback only (the default). 0.7.0 introduces bridge *pairing*: re-evaluate the
  headless start-up at that bump (pairing credentials have to be provisioned non-interactively).
- **Gate interplay**: `import_stl`, `import_step` and `insert_part_from_library` are available
  to the agent. Whatever they bring in is a `Part::Feature`/`Mesh::Feature` (or a body BaseFeature)
  and `extract` reports it as `unmapped` ⇒ gate fail. They are listed as mutating CAD calls in
  `backend.yaml`. The skillset must forbid them (L2), and the runner may additionally deny them.
- **FreeCAD 26.3 quirks found while writing the replays** (forwarded to the P8 skillset):
  - After `openDocument`, changing a spreadsheet cell does not re-trigger the expressions bound to it
    until the dependants are touched. The adapter's `set-param` touches every expression-driven
    object and recomputes.
  - A two-direction `LinearPattern` created from Python keeps `Spacings2 = [0.0]`, so all copies
    in the second direction coincide. Set `Spacings2 = [-1.0] * (Occurrences2 - 1)`.
  - `import FreeCAD` deletes `Path` and `json` from `__main__`. The adapter runs from its module namespace.
  - `PartDesign::Thickness` grows outward by default, so set `Reversed = True` for an inward shell.
  - The `XZ_Plane` normal is −Y (= IR `P_xz`).
- Pin bumps (FreeCAD, server, bridge) require rerunning the conformance suite and updating
  `backend.yaml: conformance`.
