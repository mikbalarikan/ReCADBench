# backends/build123d — L1 backend pin + adapter (reference backend)

Pins build123d **0.13.0** (OCP 8) and [pzfreo/build123d-mcp](https://github.com/pzfreo/build123d-mcp)
**0.3.91** (MCP, persistent build123d session). Upstream is never modified. The adapter is a
measurement instrument (00_DECISIONS D3): it reads native build123d scripts in clean processes,
and the evaluator never imports it.

| Path | Content |
|---|---|
| `backend.yaml` | L1 manifest: MCP interface, `cad_prefixes` (all / mutating tools of 0.3.91), hosts, `max_parallel: 2`, conformance |
| `install.md` | desktop/CI pip route (this pin) and the Termux route (PoCAD method) |
| `adapter/adapter.py` | `recad build123d export \| extract \| set-param \| replay \| execute-ir` (stdlib + build123d only). See [adapter/README.md](adapter/README.md) |
| `adapter/b3d_trace.py` | `extract`: runtime trace of build123d operations → taxonomy features / `unmapped` |
| `adapter/b3d_ir.py` | `execute-ir`: deterministic IR → build123d script |
| `replays/C01…C10.py` | reference replays of the conformance cases; native format and operation → taxonomy table in [replays/README.md](replays/README.md) |
| `tests/test_adapter.py` | conformance + contract tests (in the repo CI; skipped if build123d is not importable) |

Native model: a Python script using the builder API. Driving dimensions are module-level literal
assignments named after the IR `parameters[].name` (`width = 40.0`), and the final part is the
module variable `part`.

## Conformance (2026-11-06, build123d 0.13.0, Python 3.13): 10/10

Volumes match expected to < 1e-8 relative, counts match and are identical to freecad and
solidcraft, and every set-param round trip passes. See
[`../conformance/CONFORMANCE.md`](../conformance/CONFORMANCE.md).

```bash
pip install -e ./spec -e ./backends "build123d==0.13.0"
recad build123d replay C05_rect_pattern --out-dir /tmp/c05      # RECAD_BUILD123D_PYTHON defaults to this interpreter
recad build123d execute-ir re_plan.v1.json --out /tmp/out.step --native-out /tmp/part.py
pytest backends/build123d/tests
```
