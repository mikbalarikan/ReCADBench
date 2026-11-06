# backends/solidcraft — L1 backend pin + adapter

Pins and wraps tangency-labs/solidcraft **v0.30.1** (`solidcraft-cli`, built from source with
cargo). Upstream is never modified. The agent talks to `solidcraft-cli mcp --headless` over MCP.
The adapter is a measurement instrument (00_DECISIONS D3) that drives the same CLI in clean
processes.

| Path | Content |
|---|---|
| `backend.yaml` | L1 manifest (validated against `backend_manifest.schema.json`): MCP interface, tool prefixes, hosts, pin, conformance |
| `install.md` | Source build, release installers, MCP registration, Termux test steps |
| `adapter/adapter.py` | `recad solidcraft export \| extract \| set-param \| replay` (stdlib Python ≥ 3.11 + the CLI). See [adapter/README.md](adapter/README.md) |
| `replays/C01…C10.json` | Hand-written SolidCraft command scripts for the conformance cases. See [replays/README.md](replays/README.md) |
| `tests/test_adapter.py` | Conformance + contract tests. Skipped without the CLI |

Conformance (2026-11-06, Linux x86_64): **10/10**. Every case gives one solid. The volume of the
STEP measured with OCP is within 1.3e-8 of expected. The CLI's own volume is within 2e-4. Every
count matches expected (identical to build123d and freecad), and the set-param round trip
passes. Full table: [`../conformance/CONFORMANCE.md`](../conformance/CONFORMANCE.md).

```bash
export RECAD_SOLIDCRAFT_CLI=/path/to/solidcraft-cli
pytest backends/solidcraft/tests
recad solidcraft replay C09_mirror_bracket --out-dir /tmp/c09   # via the dispatcher
```

The dispatcher resolves `RECAD_SOLIDCRAFT_CLI` (default `solidcraft-cli` on PATH) and runs the
adapter with `RECAD_SOLIDCRAFT_PYTHON` (default: its own interpreter). The tests are not in the
repo CI (no CLI there) and skip without it.
