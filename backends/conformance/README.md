# backends/conformance

Cross-backend conformance suite (agent-free). It checks that each L1 backend's measurement
instruments (`backends/<b>/adapter/adapter.py`) agree on the same parts, above all on the feature
**count** (the cross-tool counting normalisation that Track T's gate and S-scores depend on).
A backend must pass before entering Track T (05_TOOLS.md, "Conformance").

## Layout

| Path | Content |
|---|---|
| `ir_cases/<case_id>/re_plan.v1.json` | 10 tool-neutral IR cases (spec 0.2.0), see [ir_cases/README.md](ir_cases/README.md) |
| `ir_cases/<case_id>/expected.json` | expected volume, faces (informational), feature count + types, set-param round trip, `tolerance_rel` |
| `backends/<b>/replays/<case_id>.<ext>` | hand-written native replay per backend (build123d `.py` is the reference) |
| `run_conformance.py` | runs every case on every available backend through the `recad` dispatcher, compares, checks cross-backend counts |
| [`CONFORMANCE.md`](CONFORMANCE.md) | latest recorded results (date, versions, case × backend table) |

Cases: C01 block · C02 block + through hole · C03 counterbore · C04 revolved shaft + groove ·
C05 3×2 linear pattern · C06 6× circular pattern · C07 fillet + chamfer · C08 shell (open top) ·
C09 mirror-symmetric bracket · C10 drafted pocket.

## Pass criteria (per case, per backend)

1. `recad <b> replay <case_id> --out-dir <dir>` succeeds: writes `native.<ext>`, `output.step`,
   `features.json`.
2. STEP valid: exactly one solid, mm; `|volume − expected.volume_mm3| ≤ tolerance_rel · expected`
   (0.1 %).
3. `features.json` is schema-valid (checked by the dispatcher), `count == expected.feature_count`,
   the counted taxonomy types equal `expected.features` as a multiset (IR order is reported, not
   gated), and `unmapped` is empty.
4. `recad <b> set-param <native> <set_param.name> <set_param.value>` rebuilds and the new volume is within
   tolerance of `set_param.volume_mm3`.

5. **Cross-backend:** for each case, every backend that ran it reports the same count and the
   same types. This is the counting normalisation that the gate and S-scores rely on.

`faces` is reported but not gated (kernels may split periodic or merged faces differently).

## Running

```bash
export RECAD_BUILD123D_PYTHON=<b3d-env>/bin/python      # default: the running interpreter
export RECAD_FREECAD_PYTHON=<freecad-env>/bin/python
export RECAD_SOLIDCRAFT_CLI=<path>/solidcraft-cli
python backends/conformance/run_conformance.py [--backend b ...] [--case C01_block ...] \
       [--out report.json] [--work-dir DIR]
```

The script needs `recadbench-spec` in the running interpreter. It loads `recad_cli` from this
checkout, so `backends` does not have to be installed. Backends whose environment is unavailable
(`recad backends`) are skipped with a note on stderr. The script prints progress and a
case × backend table, and writes the report JSON (`rows`, `cross_backend`, per-backend
`passed/total`, `skipped`). Exit code: 0 if all pass, 1 on any failure, 2 if no backend is
available. Replay outputs (STEP, native models) go to `--work-dir`, or a temporary directory by
default; never point it into the repo outside this folder. Backends run in parallel, and cases
run in order within a backend.

## Rules

- Synthetic geometry only; STEP/STL are allowed under this folder by `check_public`.
- Expected values come from analytic formulas where possible; otherwise from the build123d
  reference replay with an independent analytic cross-check stated in `volume_source`.
- Changing a case changes every backend's replay: rerun the whole suite on all backends.
- After a run on all backends, update [CONFORMANCE.md](CONFORMANCE.md) and the `conformance`
  block (`passed`, `total`, `date`, `software_version`) of each `backend.yaml`. Rerun on every pin
  bump.
- Do not tune adapters to a case; fix the mapping so it follows `feature_taxonomy.yaml`.
