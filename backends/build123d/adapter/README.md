# backends/build123d/adapter

Measurement instrument for the build123d backend (00_DECISIONS D3, 01_ARCHITECTURE §2): the
`recad build123d …` dispatcher runs `adapter.py` in a subprocess with the **backend's**
interpreter (build123d 0.13.0 env, see `../install.md`). stdlib + build123d only; it never
imports `recadbench_spec` and the evaluator never imports it.

| File | Role |
|---|---|
| `adapter.py` | CLI entry point (argparse); runs native scripts, canonical STEP export, set-param, replay, execute-ir |
| `b3d_trace.py` | `extract`: runtime trace of build123d operations → taxonomy features, `unmapped`, `parameters` |
| `b3d_ir.py` | `execute-ir`: IR validation + deterministic IR → build123d script generator (stdlib only) |
| `b3d_sandbox.py` | `RECAD_SANDBOX=1` (set by `recadbench finish` for agent-written code): AST check, restricted builtins, import allow-list (build123d, math, geometric OCP modules; no `io` / `os` / `inspect`, nothing next to the script); off by default (conformance, replays) |

## Contract

Each subcommand prints **one** JSON object on stdout (logs/tracebacks on stderr); exit 0 if
`ok`, else 1.

| Command | Output |
|---|---|
| `export <native.py> --out <f.step>` | `{ok, solids, volume_mm3, faces}`; mm; exactly one solid else `ok=false` and no file; the STEP is re-imported and checked |
| `extract <native.py> --out <features.json>` | writes features.json (`features.schema.json`): `features`, `count`, `unmapped`, `parameters`, `face_count`, `native_sha256`, `final_part`, `not_in_final_part` (traced ops that do not reach the final part: not counted); prints `{ok, out, count, unmapped, types}`. A script that does not run → features.json with `count 0` + `unmapped` (gate fail), exit 1 |
| `set-param <native.py> <name> <value> --out <f.step> [--native-out <f.py>]` | rewrites the single module-level literal assignment `name = …`, rebuilds, exports; `{ok, name, value, volume_before_mm3, volume_after_mm3, solids, faces}` |
| usage error (bad subcommand/argument/value) | `{ok: false, error: "usage: ..."}`, exit 1 (never argparse's exit 2) |
| `replay <case_id> --out-dir <dir>` | copies `../replays/<case_id>.py` to `<dir>/native.py`, writes `output.step`, `features.json`; `{ok, volume_mm3, faces, count, solids, unmapped, types}` |
| `execute-ir <re_plan.vN.json> --out <f.step> [--native-out part.py]` | validates the IR, writes the generated build123d script, exports; `{ok, volume_mm3, faces, solids, count, ir_feature_count, unmapped, warnings, native}` |

Native format, final-part convention and the full operation → taxonomy table:
[`../replays/README.md`](../replays/README.md). Translation conventions of `execute-ir`: module
docstring of `b3d_ir.py`.

## Determinism

The STEP header timestamp is fixed (`2000-01-01T00:00:00`); `execute-ir` emits byte-identical
scripts for the same IR; the trace has no randomness.

## Tests

```bash
<b3d-env>/bin/pytest backends/build123d/tests     # skips if build123d is not importable
```
