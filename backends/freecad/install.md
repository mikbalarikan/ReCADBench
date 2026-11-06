# freecad backend — install

L1 = FreeCAD weekly-2026.09.16 development build (unmodified; reports 26.3.0, see the note below) + the **freecad-robust-mcp** server and its **RobustMCPBridge**
running headless in `freecadcmd` (choice and evaluation: [ADR 0003](../../docs/adr/0003-freecad-mcp-selection.md)).
The pins are in [`backend.yaml`](backend.yaml).

| Component | Pin | Where it runs |
|---|---|---|
| FreeCAD | weekly-2026.09.16 dev build, git `a4ce44d33`, reports 26.3.0 (conda-forge `freecad=2026.09.16`, Python 3.11, OCCT 8.0.1) | worker |
| MCP server | PyPI `freecad-robust-mcp==0.6.1` + `mcp<2` (1.30.0 tested), dedicated venv, Python ≥ 3.11 | worker, launched by the agent's MCP client over stdio |
| Bridge | `addon/FreecadRobustMCPBridge/freecad_mcp_bridge/blocking_bridge.py` at git tag `robust-mcp-workbench-v0.6.1` | inside `freecadcmd`, one process per run |
| Adapter | `backends/freecad/adapter/adapter.py` (this repo) | FreeCAD's own Python, clean process |
| Tests | `pytest` installed into the FreeCAD env | FreeCAD's Python |

## Linux (conda-forge route, used for the P3 conformance run)

```bash
# 1. FreeCAD weekly-2026.09.16 dev build, headless (no display needed)
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

## Windows (verified 2026-11-06, Windows 11 x64)

What the pin is: conda-forge `freecad=2026.09.16` is built from the upstream tag
`weekly-2026.09.16` (commit `a4ce44d33b7e`), a development snapshot that predates 26.3rc1
(2026-10-08) and 26.3rc2 (2026-10-18). It is not a release or release candidate; "26.3.0" is the
development branch's version number. Latest stable upstream is 1.1.4 (2026-09-28). The pin is kept
because the identical build exists on linux-64 and win-64 and both conformance runs used it;
re-pinning is an open question in `STATUS.md`. Any conda / mamba / micromamba works; no activation
is needed for the adapter.

```powershell
# 1. FreeCAD weekly-2026.09.16 dev build (conda-forge win-64 py311h48521fb_0, occt 8.0.1)
conda create -y --prefix <tools>\freecad\fc263 -c conda-forge --override-channels `
      freecad=2026.09.16 python=3.11 pytest
$FC = "<tools>\freecad\fc263"
& "$FC\python.exe" -c "import sys,os; sys.path.insert(0, os.path.join(sys.prefix,'Library','bin')); import FreeCAD; print(FreeCAD.Version()[:4])"
#   ['26', '3', '0', '20260916 (Git shallow)']
$env:RECAD_FREECAD_PYTHON = "$FC\python.exe"   # FreeCAD.pyd is in $FC\Library\bin; the adapter finds it

# 2. MCP server in its own venv (never together with neka-nat/freecad-mcp)
py -3.13 -m venv <venvs>\freecad-mcp
<venvs>\freecad-mcp\Scripts\pip install "freecad-robust-mcp==0.6.1" "mcp<2"   # mcp 1.30.0

# 3. Bridge at the pinned tag (the tag object 109ebaf7 points at commit 3e07be28)
git clone --depth 1 --branch robust-mcp-workbench-v0.6.1 `
    https://github.com/spkane/freecad-robust-mcp-and-more <tools>\freecad\robust-bridge
```

Start the bridge with `$FC\Library\bin\freecadcmd.exe <clone>\addon\FreecadRobustMCPBridge\freecad_mcp_bridge\blocking_bridge.py`.
Called directly (without `conda activate`), the conda `freecadcmd.exe` exits with 0xC0000135
(DLL not found). Put the env's DLL folders first on `PATH` for that process: `$FC`,
`$FC\Library\mingw-w64\bin`, `$FC\Library\usr\bin`, `$FC\Library\bin`, `$FC\Scripts`.
`backends/conformance/mcp_tools.py` does this when it starts its private bridge. `python.exe`
itself does not need it.

freecad-robust-mcp 0.6.1 connects to the bridge during `initialize` and exits when none is
listening, so even a `tools/list` needs a running bridge.

Not the pin, but also checked: the official stable FreeCAD 1.1.1 Windows bundle (1.1.4 not tested)
(`...\FreeCAD 1.1\bin\python.exe`, FreeCAD.pyd next to it) passes conformance 10/10 with the
same counts. The bundle's `python.exe` has no pip, so its tests need `python.exe -m ensurepip`
first.

## Verify

```bash
cd recadbench
$FC/bin/python backends/freecad/adapter/adapter.py replay C07_fillet_chamfer --out-dir /tmp/c07
$FC/bin/python -m pytest backends/freecad/tests -q        # 43 passed (Linux and Windows)
```

## Upgrading a pin

Change `backend.yaml` and this file. Rerun the conformance tests and update
`backend.yaml: conformance`. For robust ≥ 0.7, re-check the headless start, because bridge
*pairing* is new in 0.7.
