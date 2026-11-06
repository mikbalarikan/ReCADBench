# runner/tests

Synthetic, with known ground truth; run in CI. Helpers: `runner_helpers.py` (on the pytest
`pythonpath`): `mini_run` (gate config + the repo's real `backend.yaml`), `content_repos` (git
repos standing in for strategies / cad-skills / data, one generated scan), the evaluator's
synthetic GT builder.

| File | Covers |
|---|---|
| `test_gate.py` | H8 cases (CAD blocked without a plan, shell blocked, `recadbench nx-run cad/x.py` only for nx, N+1 after a CAD change) + denied_tools read from backend.yaml, journal source matching like nx-run, execute_file bodies and importable siblings, scan-code patterns, paths, protected files, plan immutability, tool surface, hook protocol, SDK hook adapter |
| `test_frames.py` | rigid mapping with hand-computed answers, selector axes, reflection refused, plan_score round trip |
| `test_compose.py` | D6 layout, pins at a git ref, tracks T / E / S / none baseline, refusals, GT leak check (clean + 5 leak kinds), agent env |
| `test_agents.py` | dispatcher, LiteLLM loop (scripted fake model), replay expectations, budgets, SDK wiring with a fake client |
| `test_finish_cli.py` | finish run-dir contract (extract / export / hashes / native_sha256, agent copies dropped), CLI compose / gate / nx-run forwarding |
| `test_pipeline.py` | end to end: seed-plan -> compose -> replay (real build123d-mcp) -> finish -> recadbench-eval on a synthetic scan part (PASS, plan score 100) |
| `test_host_pilots.py` | evaluator host only (skip without `RECADBENCH_GT_DIR` + private repos): pilot seed plans score like the GT plan; the oracle x build123d@v1 x hs01 dry run |
