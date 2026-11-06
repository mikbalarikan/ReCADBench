# evaluator/golden

The golden candidates (good, wrong_dim, snap_everything, lego, faceted, dumb_solid) are **generated at
test time** by `recadbench_eval.golden` into pytest temp dirs. No candidate file is stored here:

- **CI / public:** `evaluator/tests/test_golden_synthetic.py` builds synthetic GT parts inside the
  test (`tests/conftest.py`): a clean synthetic variant and a scan-rule variant with a known
  `T_scan_to_gt`, a floor and a mask. It then derives the six candidates.
- **Evaluator host only:** `evaluator/tests/test_golden_pilots.py` reads the three R385 pilots via
  `RECADBENCH_GT_DIR` and skips without it. Candidates derived from pilot GT never leave the
  temp dir and must never be committed (D3, `check_public.py`).

`recadbench-eval golden --part <id> --out-dir <tmp>` writes the same candidates for inspection.
