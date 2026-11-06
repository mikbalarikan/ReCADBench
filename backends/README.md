# backends — L1 pins, adapters, `recad` dispatcher, conformance

L1 of ReCADBench (00_DECISIONS D1/D3, 01_ARCHITECTURE §2, 05_TOOLS). Each backend folder pins
an **unmodified** upstream CAD program and ships the measurement instruments that read its
native models: `export`, `extract`, `set-param`, `replay` (and `execute-ir` for build123d).
Agents never call the adapters; the worker and evaluator hosts call them through `recad`.

| Folder | Upstream | Agent interface | Adapter interpreter | State |
|---|---|---|---|---|
| [`build123d/`](build123d/README.md) | build123d 0.13.0 + pzfreo/build123d-mcp 0.3.91 | MCP | build123d env Python | conformance 10/10 (reference backend) |
| [`freecad/`](freecad/README.md) | FreeCAD 26.3 + freecad-robust-mcp 0.6.1 ([ADR 0003](../docs/adr/0003-freecad-mcp-selection.md)) | MCP | FreeCAD's Python | conformance 10/10 |
| [`solidcraft/`](solidcraft/README.md) | tangency-labs/solidcraft 0.30.1 (`solidcraft-cli mcp`) | MCP | any Python ≥ 3.11 + the CLI | conformance 10/10 |
| [`nx/`](nx/README.md) | Siemens NX 2206 (`run_journal.exe`) | CLI: `recadbench nx-run cad/*.py` | any Python ≥ 3.11 (stdlib) + NX on the host | conformance 10/10 (Windows) |
| [`conformance/`](conformance/README.md) | 10 synthetic IR cases + `run_conformance.py` | — | — | results: [CONFORMANCE.md](conformance/CONFORMANCE.md) |
| `recad_cli/`, `pyproject.toml` | the `recad` dispatcher (package `recadbench-backends`) | — | — | 0.1.0 |
| `tests/` | dispatcher tests (fake adapter) | — | — | in CI |

## The `recad` dispatcher

```bash
pip install -e ./spec -e ./backends          # + [measure] extra = build123d, for STEP measuring
recad backends                               # each backend: available / unavailable + why
recad <backend> export    <native> --out output.step
recad <backend> extract   <native> --out features.json
recad <backend> set-param <native> <name> <value> --out sp.step [--native-out sp.<ext>]
recad <backend> replay    <case_id> --out-dir <dir>
recad build123d execute-ir re_plan.v1.json --out output.step [--native-out part.py]
```

`recad <backend> …` runs `backends/<backend>/adapter/adapter.py` in a **fresh subprocess** with
that backend's interpreter. It passes the arguments through unchanged and never imports backend
code. The adapter's stderr is passed through. Its single JSON result (last stdout line) is then
completed and printed as one JSON object:

- **features.json validation** (`extract`, `replay`): the file is checked with recadbench-spec
  (`features` schema + semantic rules). The result gets `features_valid`; if it is invalid, the
  result gets `ok: false` and `validation_errors`, and the exit code is 1. Adapters cannot import
  recadbench-spec (backend environments do not have it), so this check lives here.
- **STEP measuring**: when the adapter reports `needs_measure: true`, the dispatcher measures the
  STEP it wrote (solids, volume, faces) with build123d. It uses this interpreter if build123d
  imports, else `RECAD_BUILD123D_PYTHON`. For `set-param` it fills `volume_after_mm3`. A STEP
  with more than one solid turns the result into `ok: false`. With no measurer, `measure_error`
  is set and `needs_measure` stays `true`. None of the three adapters needs this today, since all
  of them report volumes, but a backend without a volume query can rely on it.
- **Exit codes**: the adapter's exit code is propagated. The exit code is 1 when the dispatcher
  fails a result (invalid features.json, ≠ 1 solid, no JSON, timeout, environment missing), and
  2 for usage errors (unknown backend, missing subcommand).

| Environment | Meaning | Default |
|---|---|---|
| `RECAD_BUILD123D_PYTHON` | build123d interpreter | this interpreter |
| `RECAD_FREECAD_PYTHON` | Python that can import FreeCAD | **required** for freecad |
| `RECAD_SOLIDCRAFT_PYTHON` | Python ≥ 3.11 for the solidcraft adapter | this interpreter |
| `RECAD_SOLIDCRAFT_CLI` | `solidcraft-cli` binary (resolved and passed to the adapter) | `solidcraft-cli` on PATH |
| `RECAD_NX_PYTHON` | Python ≥ 3.11 for the nx adapter (NX itself: `backends/nx/install.md`) | this interpreter |
| `RECAD_BACKENDS_DIR` | folder holding `<b>/adapter/adapter.py` | this source tree (editable install), else `./backends` of the current checkout |
| `RECAD_TIMEOUT_S` | per adapter call | 1800 |

`recad backends [--json]` checks each backend: the adapter is present, the interpreter exists,
and a probe runs in it (`import build123d`, `import FreeCAD`, or `solidcraft-cli --version` / the run_journal.exe
lookup via the adapter's `version` subcommand). `run_conformance.py` uses this check to skip unavailable
backends.

## Tests

```bash
pytest backends/tests backends/build123d/tests                 # CI (repo pyproject testpaths)
<freecad-env>/bin/python -m pytest backends/freecad/tests       # needs FreeCAD; skips without
RECAD_SOLIDCRAFT_CLI=… pytest backends/solidcraft/tests          # needs the CLI; skips without
pytest backends/nx/tests                                         # CI part + NX host part (skips without NX)
python backends/conformance/run_conformance.py                  # all available backends
```
