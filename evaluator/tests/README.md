# evaluator/tests

| File | Runs | What |
|---|---|---|
| `conftest.py` | everywhere | synthetic GT folders generated in-test (`syn_clean`, `syn_scan`) |
| `test_units.py` | CI | distance kernel, sampling, genus, curves, plan score, gate failures (units, freeform, missing features.json, 2 solids), S5 behind the flag, CLI/report, GT only via env, no backend imports |
| `test_golden_synthetic.py` | CI | six golden candidates x 2 synthetic variants: ordering, PASS flags, scan rules (band_eff, inferred region), result.json schema + ASCII/LF, byte-identical rerun, PCA fallback alignment |
| `test_golden_pilots.py` | evaluator host (`RECADBENCH_GT_DIR`) | six candidates x 3 R385 pilots: ordering, PASS, band_eff, observable share. `RECADBENCH_PILOT_TABLE=<file>` writes the result table |
