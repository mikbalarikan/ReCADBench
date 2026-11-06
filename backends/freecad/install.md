# freecad backend — install

L1 = FreeCAD 26.3 (unmodified) + the **freecad-robust-mcp** server and its **RobustMCPBridge**
running headless in `freecadcmd` (choice and evaluation: [ADR 0003](../../docs/adr/0003-freecad-mcp-selection.md)).
The pins are in [`backend.yaml`](backend.yaml).

| Component | Pin | Where it runs |
|---|---|---|
| FreeCAD | 26.3.0 (conda-forge `freecad=2026.09.16`, Python 3.11, OCCT 8.0.1) | worker |
| MCP server | PyPI `freecad-robust-mcp==0.6.1` + `mcp<2` (1.30.0 tested), dedicated venv, Python ≥ 3.11 | worker, launched by the agent's MCP client over stdio |
| Bridge | `addon/FreecadRobustMCPBridge/freecad_mcp_bridge/blocking_bridge.py` at git tag `robust-mcp-workbench-v0.6.1` | inside `freecadcmd`, one process per run |
| Adapter | `backends/freecad/adapter/adapter.py` (this repo) | FreeCAD's own Python, clean process |
| Tests | `pytest` installed into the FreeCAD env | FreeCAD's Python |

## Linux (conda-forge route, used for the P3 conformance run)

```bash
# 1. FreeCAD 26.3 headless (no display needed)
export MAMBA_ROOT_PREFIX=$HOME/.recad/mamba
curl -fsSL -o micromamba https://github.com/mamba-org/micromamba-releases/releases/latest/download/micromamba-linux-64
chmod +x micromamba
./micromamba create -y -n freecad -c conda-forge freecad=2026.09.16 python=3.11
FC=$MAMBA_ROOT_PREFIX/envs/freecad
$FC/bin/freecadcmd -c "import FreeCAD; print(FreeCAD.Version()[:3])"   # ['26', '3', '0']
$FC/bin/python -m pip install pytest          # only for backends/freecad/tests

# 2. MCP server in its own venv (its console script `freecad-mcp` and package `freecad_mcp`
#    clash with neka-nat/freecad-mcp: never install both into one env)
python3 -m venv $HOME/.recad/freecad-mcp
$HOME/.recad/freecad-mcp/bin/pip install "freecad-robust-mcp==0.6.1" "mcp<2"

# 3. Bridge at the pinned tag
git clone --depth 1 --branch robust-mcp-workbench-v0.6.1 \
    https://github.com/spkane/freecad-robust-mcp-and-more $HOME/.recad/robust-bridge
BRIDGE=$HOME/.recad/robust-bridge/addon/FreecadRobustMCPBridge/freecad_mcp_bridge/blocking_bridge.py
```

Note: the conda `python` cannot `import FreeCAD` until `$FC/lib` is on `sys.path`. The adapter
and the replays do that themselves (`sys.prefix/lib`, or `FREECAD_LIB` if set).

### Per run (runner, slot `k` = 0, 1)

```bash
export FREECAD_XMLRPC_PORT=$((9875 + 10*k)) FREECAD_SOCKET_PORT=$((9876 + 10*k))
$FC/bin/freecadcmd "$BRIDGE" > run/logs/freecad_bridge.log 2>&1 &   # "MCP Bridge started in headless mode!"
# agent MCP config (server name `freecad` ⇒ tool prefix mcp__freecad__):
#   command: $HOME/.recad/freecad-mcp/bin/freecad-mcp
#   env: FREECAD_MODE=xmlrpc FREECAD_XMLRPC_PORT=… FREECAD_SOCKET_PORT=… FREECAD_TIMEOUT_MS=120000
# … run …
kill %1      # one fresh bridge per run: a bridge keeps documents across MCP sessions
```

Timeout: with the pinned 0.6.1, `FREECAD_TIMEOUT_MS` has **no effect**. 0.6.1 parses it but
never reads it; upstream wires it in from 0.7.0-rc.3. The real limit is `execute_python`'s
default `timeout_ms=30000` (30 s) per call. A longer call must pass `timeout_ms` explicitly; the
per-call value is honoured. The variable stays in the config so that it takes effect once the pin is
bumped.

The bridge listens on localhost only and has no authentication in 0.6.1, so never rebind it to
0.0.0.0.

## Windows

1. Install the FreeCAD 26.3 release bundle, e.g. into `C:\Program Files\FreeCAD 26.3`.
   `bin\FreeCADCmd.exe` is the headless executable and `bin\python.exe` is FreeCAD's Python.
   That Python finds `FreeCAD.pyd` in `bin\`; the adapter also adds `sys.prefix\Library\bin` and
   the executable's directory, and honours `FREECAD_LIB`. Alternatively use conda-forge as on
   Linux (`micromamba create -n freecad -c conda-forge freecad=2026.09.16 python=3.11`). Then
   FreeCAD.pyd is in `%CONDA_PREFIX%\Library\bin`.
2. MCP server: `py -3.11 -m venv %USERPROFILE%\.recad\freecad-mcp` then
   `%USERPROFILE%\.recad\freecad-mcp\Scripts\pip install "freecad-robust-mcp==0.6.1" "mcp<2"`.
3. Bridge: clone the tag as above. Start it with
   `"C:\Program Files\FreeCAD 26.3\bin\FreeCADCmd.exe" <clone>\addon\FreecadRobustMCPBridge\freecad_mcp_bridge\blocking_bridge.py`.
4. Adapter: `"C:\Program Files\FreeCAD 26.3\bin\python.exe" backends\freecad\adapter\adapter.py …`.
   The bundle's `python.exe` has no pip, so run the tests from a conda-forge env, or with
   `python.exe -m ensurepip` first.

The Windows steps follow the FreeCAD bundle layout and the upstream docs. They have not been run
yet; the first Windows worker setup must run `pytest backends/freecad/tests` and record the result.

## Verify

```bash
cd recadbench
$FC/bin/python backends/freecad/adapter/adapter.py replay C07_fillet_chamfer --out-dir /tmp/c07
$FC/bin/python -m pytest backends/freecad/tests -q        # 35 passed
```

## Upgrading a pin

Change `backend.yaml` and this file. Rerun the conformance tests and update
`backend.yaml: conformance`. For robust ≥ 0.7, re-check the headless start, because bridge
*pairing* is new in 0.7.
