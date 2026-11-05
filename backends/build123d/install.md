# build123d backend — install

L1 backend = [pzfreo/build123d-mcp](https://github.com/pzfreo/build123d-mcp) (Apache-2.0), pinned, not modified.
Exact pins live in `backend.yaml` (P3); the P1 spike used:

| Component | Pin | Source |
|---|---|---|
| Python | 3.12 | conda-forge (micromamba) |
| OCP (OpenCascade bindings) | `ocp=7.8.1.1` | conda-forge (brings `vtk 9.3.1`, registered for pip) |
| build123d | `0.10.0` | PyPI |
| ocp_gordon | `<0.3` | PyPI |
| bd_warehouse | `0.2.0` | PyPI — **0.3.0+ needs build123d ≥ 0.11.1 / 0.4.0 needs ≥ 0.12**, so it must be pinned with build123d 0.10 |
| build123d-mcp | `0.3.91` | PyPI (requires build123d `>=0.10,<0.14`, mcp `>=2,<3`, vtk `>=9.3`) |
| mcp | 2.3.0 | PyPI (resolved) |

## Termux (Android phone) — PoCAD method

Same mechanism as [mikbalarikan/PoCAD](https://github.com/mikbalarikan/PoCAD) `install.sh` +
`scripts/setup-ubuntu.sh`: Termux → proot-distro Ubuntu → micromamba Python 3.12 + conda-forge OCP
(PoCAD's reason: no `cadquery-ocp` 7.8 aarch64 pip wheel) → pip for the rest, with two metadata-only
shims so pip accepts the conda OCP (`cadquery-ocp` dist-info) and the missing `lib3mf` aarch64 wheel
(placeholder module, 3MF export disabled). The benchmark uses its **own env `recad`**, so an existing
PoCAD env (`cad`, which pins agentcad) is not touched.

### 0. Phone prep (manual)
1. Install **Termux from F-Droid** (Play Store build is outdated). Wi-Fi + charger; ~1–1.5 GB download.
2. In Termux:
   ```bash
   pkg update -y && pkg install -y proot-distro python git curl termux-tools
   proot-distro install ubuntu        # skip if PoCAD already installed it
   git clone https://github.com/mikbalarikan/PoCAD ~/PoCAD   # only for shims/lib3mf/__init__.py
   ```

### 1. CAD env inside Ubuntu (proot)
```bash
proot-distro login ubuntu --bind "$HOME/PoCAD:/pocad" -- bash -s <<'EOF'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive MAMBA_ROOT_PREFIX=/opt/mamba
apt-get update -qq
apt-get install -y -qq curl bzip2 ca-certificates git libgl1 libglu1-mesa libxrender1 libegl1
case "$(uname -m)" in aarch64|arm64) A=linux-aarch64 ;; x86_64) A=linux-64 ;; esac
[ -x /usr/local/bin/micromamba ] || (cd /usr/local && curl -fsSL "https://micro.mamba.pm/api/micromamba/$A/latest" | tar -xj bin/micromamba)
micromamba create -y -n recad -c conda-forge python=3.12 ocp=7.8.1.1 pip
P=/opt/mamba/envs/recad/bin
SP=$($P/python -c 'import site; print(site.getsitepackages()[0])')
# shim 1: tell pip the conda OCP satisfies cadquery-ocp 7.8.x
D=$SP/cadquery_ocp-7.8.1.1.dist-info; mkdir -p $D
printf 'Metadata-Version: 2.1\nName: cadquery-ocp\nVersion: 7.8.1.1\n' > $D/METADATA; echo pocad-shim > $D/INSTALLER; : > $D/RECORD
# shim 2: lib3mf placeholder (no aarch64 wheel)
D=$SP/lib3mf-2.4.1.dist-info; mkdir -p $D
printf 'Metadata-Version: 2.1\nName: lib3mf\nVersion: 2.4.1\n' > $D/METADATA; echo pocad-shim > $D/INSTALLER; : > $D/RECORD
mkdir -p $SP/lib3mf && cp /pocad/shims/lib3mf/__init__.py $SP/lib3mf/__init__.py
$P/pip install --root-user-action=ignore "build123d==0.10.0" "ocp_gordon<0.3" "bd_warehouse==0.2.0" "build123d-mcp==0.3.91"
$P/pip check
$P/build123d-mcp --version
EOF
```

### 2. STEP export check (through the MCP server)
Save as `~/step_check.py` in Termux, copy into the proot rootfs or bind it, then run with the env's python:
```python
import asyncio, sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
CODE = "from build123d import *\nwith BuildPart() as p:\n    Box(40, 30, 10)\n    Hole(radius=4)\nshow(p.part)\n"
async def main():
    async with stdio_client(StdioServerParameters(command=sys.argv[1], args=[])) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            for name, args in [("version", {}), ("execute", {"code": CODE}),
                               ("export", {"format": "step", "filename": sys.argv[2]})]:
                res = await s.call_tool(name, args)
                print(name, "error" if res.is_error else "ok", [c.text for c in res.content])
asyncio.run(main())
```
```bash
proot-distro login ubuntu --bind "$HOME:/termux-home" -- /opt/mamba/envs/recad/bin/python -I /termux-home/step_check.py /opt/mamba/envs/recad/bin/build123d-mcp /tmp/spike.step
proot-distro login ubuntu --bind "$HOME:/termux-home" -- /opt/mamba/envs/recad/bin/python -I -c "from build123d import import_step; s=import_step('/tmp/spike.step'); print(len(s.solids()), round(s.volume,2))"
```
Expected: `export ok … Exported to /tmp/spike.step … volume 1.15e+04 mm³, bbox 40×30×10 mm, 7 faces`
and re-import `1 11497.35` (= 40·30·10 − π·4²·10).

### 3. Register the MCP server for a Claude Code session in Termux
```json
{ "mcpServers": { "build123d": {
    "command": "proot-distro",
    "args": ["login", "ubuntu", "--", "/opt/mamba/envs/recad/bin/build123d-mcp"] } } }
```
(`recadbench compose` writes this into the run dir's `.mcp.json` in P10.)

## Spike log (P1, 2026-11-05)

| Where | Result |
|---|---|
| **x86_64 Ubuntu 24.04 (cloud container), steps 1–2 above with `linux-64` micromamba** | ✅ `pip check`: no broken requirements. MCP `version` → build123d-mcp 0.3.91 / build123d 0.10.0 / bd_warehouse 0.2.0. `execute` + `export format=step` → 20 kB STEP; re-import: 1 solid, volume 11497.35 mm³ (analytic 11497.35) |
| **Android phone (Termux, aarch64)** | ⏳ **Not yet run** — the P1 session had no phone access. Run steps 0–2 on one phone and fill this row (phone model, Android, Termux version, wall time, outputs). |

Notes for the phone run:
- All aarch64 pip wheels needed exist on PyPI (checked: quiddity, resvg-py, vtk); vtk itself comes from conda-forge with OCP.
- Newer PyPI `cadquery-ocp(-novtk)` 7.9/8.0 now ship aarch64 wheels; a plain-pip route (build123d 0.13 + OCP 8.0, no shims) may work too. Not used: P1 follows the PoCAD method. Revisit in P3 when pinning.
- micromamba: `micro.mamba.pm` was blocked in the cloud container; `https://github.com/mamba-org/micromamba-releases/releases/latest/download/micromamba-linux-aarch64` is an equivalent source (plain binary, `chmod +x`).
- build123d-mcp sandboxes `execute` by default (import allowlist); leave defaults unless P3 decides otherwise.
