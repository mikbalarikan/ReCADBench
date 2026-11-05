# 07 — Roadmap

| Milestone | Prompts | Exit criteria |
|---|---|---|
| **M1 Skeleton** | P1, P2 | 5 repos scaffolded, public-repo data guard in CI, CI green, Termux spike done (build123d-mcp exports STEP on a phone), `recadbench-spec` v0.1.0 |
| **M2 Backends + measurement** | P3, P4, P5, P6, P7 | build123d, Solidcraft, FreeCAD, NX pass conformance with identical counts; NX harness ported (nx-v1); retk works on Termux; 3 pilot GT parts; evaluator golden tests green |
| **M3 Pilot + calibration** | P8, P9, P10, P11 | Skillsets v1 for all 4 backends; strategies A–D; compose + gate_hook working; pilot memo + threshold ADR; spec v1.0.0 |
| **M4 Dev campaigns** | P12 (repeat) | Track T on 10 dev parts: all backends × {none, latest} × all models × 3 replicates; ≥1 skillset improvement cycle per backend; Track S plan-only on dev |
| **M5 Frozen test + S5** | — | 50 GT parts done; S5 on; Track E on 40 test parts; leaderboard with CIs per layer |

## Staged experiment budget

1. Track T (dev only): 4 backends × {none, v1} × 3 models × 10 parts × 1 replicate = 240 runs (NX 60 at parallelism 1).
2. Track S plan-only: A, B, C × 7 models × 10 dev × 3 replicates = 630 cheap runs (no CAD).
3. Track E with build123d: A–D × top 3 models × 10 dev × 3 replicates = 360 runs.
4. Track E final: top 2 strategies × 4 skillsets × top 2 models × 40 test × 3 replicates ≈ 1.9k runs, ~480 NX — NX is the wall-clock bottleneck.

## Risks

- **Key-dim face matching** — most fragile evaluator module; hand-verify on pilot.
- **N_ref ambiguity** — define as a skilled modeller's minimal reasonable count; r ≤ 1.2 plateau absorbs the rest.
- **NX journal loop latency** — every agent step is a full `run_journal.exe` open/execute/save cycle; watch wall-time budgets.
- **Upstream churn** — Solidcraft and build123d-mcp move fast; pin, rerun conformance on bump, record versions in results.
- **FreeCAD MCP maturity** — selection in P3 may conclude none is headless-ready; fallback is a FreeCADCmd CLI backend like NX.
- **Open models' tool-call reliability** — report tool-call error rate per model; it can dominate agentic results.
- **Skillset overfitting** — improvement loop sees dev failures only; keep test frozen.
