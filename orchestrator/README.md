# orchestrator

pip `recadbench-orch` (package `recadbench_orch`, P10 / H8): experiment expansion, the SQLite job
queue with an HTTP front, and the pull-based worker. A package rather than loose scripts so
`queue.py` never shadows the standard-library `queue` in a worker process.

```
recadbench-orch expand experiments/pilot.yaml --count              # run count summary
recadbench-orch expand experiments/pilot.yaml --jobs jobs.json
recadbench-orch expand experiments/pilot.yaml --enqueue orchestrator.db   # evaluator host
recadbench-orch serve  --db orchestrator.db --host 0.0.0.0 --port 8765 --artifacts results/in
recadbench-orch worker --queue http://asus:8765 --caps build123d --runs-dir ~/runs   # phone
recadbench-orch worker --queue orchestrator.db --caps build123d,nx,gt --runs-dir runs  # ASUS
recadbench-orch status --queue orchestrator.db
```

| Module | What |
|---|---|
| `expand.py` | experiment YAML -> run jobs: parts x tracks x strategies x skillsets x models x replicates, with the D4 rules enforced (T = oracle on a backend, dev only; S = plan only; E = no oracle), `exclude` filters, deterministic run ids, `summary` counts; `--enqueue` adds the Track T seed plans (made from `RECADBENCH_GT_DIR` on the evaluator host, D7) |
| `queue.py` | `JobQueue` (stdlib sqlite3): `enqueue` (idempotent on run_id), `claim` (capabilities, priority, per-host backend limit: nx = 1), leases + `heartbeat`, `complete`, `fail` (retry until `max_attempts`); `make_server` (stdlib HTTP, bearer token `RECADBENCH_QUEUE_TOKEN`, artifact upload); `RemoteQueue` client |
| `worker.py` | pull loop: claim -> run (`recadbench_run.run.run_job`; NX jobs also under a host lock directory) -> complete / fail; a run enqueues a `score` job (requires `gt`), a remote worker uploads the run dir first (without `input/input.stl`, `.claude/`, `lib/`, `_shared/`); `score` jobs run `recadbench-eval score` |
| `cli.py` | `recadbench-orch` |

Termux: `queue.py` and `worker.py` import only the standard library at module level (a test pins
it); the runner is imported when a job runs. Workers declare what they can run (`--caps`):
`build123d`, `nx`, `freecad`, `solidcraft`, `gt` (evaluator host). Plan-only (Track S) jobs need
no capability.

Experiment format: `orchestrator/experiments/README.md`; a tiny example with a known run count:
`tests/tiny.yaml` (32 runs).
