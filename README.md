# ReCADBench

Scan → parametric CAD reverse-engineering benchmark. An agent gets a **real 3D scan** (`input.stl`:
raw, noisy, holed, in the scanner frame) and must produce a clean, editable, single-solid parametric
model that recovers design intent ([ADR 0002](docs/adr/0002-real-scan-input.md)). Three layers are evaluated separately:

1. **L1 — CAD backend** (pinned, external): build123d-mcp, `solidcraft-cli mcp`, a FreeCAD MCP, NX via journals — `backends/`
2. **L2 — CAD skillset** (ours, private `recadbench-cad-skills`) — Track T
3. **L3 — RE strategy** (ours, private `recadbench-strategies`) — Track S / E

Architecture and build plan: [`docs/architecture/`](docs/architecture/README.md) (start with `00_DECISIONS.md`).
Progress: [`STATUS.md`](STATUS.md).

> Status: P1 scaffold + P2 spec (`recadbench-spec` 0.2.0). v1 data = scan-rebuild parts, **dev-only** (no frozen test split yet).

## Repositories

| Repo | Visibility | Contents |
|---|---|---|
| `ReCADBench` (this) | public | contracts (`spec/`), backend pins + adapters, dataset *tooling*, evaluator, runner, orchestrator, leaderboard |
| `recadbench-cad-skills` | private | L2 skillsets per backend |
| `recadbench-strategies` | private | L3 strategies A–D + oracle, RE toolkit `retk` |
| `recadbench-data` | private | agent-visible inputs: `parts/<id>/{input.stl, task.yaml}`, splits |
| `recadbench-gt` | private | ground truth — evaluator host only |

No part data or ground truth is ever committed here: CI runs `dataset/tools/check_public.py`, which
rejects `*.stl`, `*.step`, `*.stp`, `*.prt` outside `backends/conformance/` and `evaluator/golden/`
(synthetic fixtures only) and any `gt_plan.json` / `key_dims.yaml` / `gt.step` / `gt_native.*` file.

## Layout

```
spec/          recadbench-spec: JSON Schemas, feature taxonomy (contract source of truth)
backends/      L1 pins + adapters (build123d, solidcraft, freecad, nx) + conformance suite
dataset/tools/ make_task.py (GT → inputs), check_public.py (CI guard)
evaluator/     recadbench-eval
runner/        recadbench-run: compose, gate_hook, agent backends, finish
orchestrator/  experiments, queue, worker
results/       gitignored (synced to Drive)
leaderboard/   build.py
```

## Access tokens (00_DECISIONS D3)

The public repo's `compose` / `worker` / evaluator read the private repos with **fine-grained GitHub
personal access tokens**. Tokens are never committed — not in code, configs, `.mcp.json`, run dirs or
remote URLs. `.gitignore` excludes `.env`, `.env.*` and `*.token` as a safety net.

| Host role | Env var | Repository access (fine-grained PAT, owner `mikbalarikan`) | Permissions |
|---|---|---|---|
| **Worker** (phones, MateBook, ASUS as NX worker) | `RECADBENCH_GH_TOKEN` | `ReCADBench-cad-skills`, `ReCADBench-strategies`, `ReCADBench-data` — **only these three** | Contents: **Read-only** (Metadata: read-only is added automatically). Nothing else |
| **Evaluator host** (ASUS, the only one) | `RECADBENCH_GT_TOKEN` | the three above **+ `ReCADBench-gt`** | Contents: **Read-only** |

Rules:
- **No worker token ever includes `ReCADBench-gt`.** Workers receive GT only as a Track T dev-part
  `gt_plan.json` inside a job payload (D7), never repo access.
- `make_task.py` writes to `recadbench-data` from the evaluator host; that push uses the operator's own
  credentials interactively, not a host token (see STATUS.md open questions).
- One token per host; set an expiry (e.g. 90 days) and rotate; revoke immediately if a phone is lost.

### Create a token
GitHub → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → *Generate new token*:
Resource owner `mikbalarikan` · Repository access *Only select repositories* (the list above for the host role) ·
Repository permissions → *Contents: Read-only* · Expiration set.

### Use it on a host
Store it outside any repository, readable only by you:
```bash
mkdir -p ~/.config/recadbench && chmod 700 ~/.config/recadbench
read -rs RECADBENCH_GH_TOKEN && printf 'export RECADBENCH_GH_TOKEN=%s\n' "$RECADBENCH_GH_TOKEN" > ~/.config/recadbench/env && chmod 600 ~/.config/recadbench/env
```
```bash
echo '. ~/.config/recadbench/env' >> ~/.bashrc
```
Clone/pull through a credential helper that reads the env var, so the token never lands in `.git/config` (this applies to all github.com URLs on that host — intended for dedicated worker hosts):
```bash
git config --global credential.https://github.com.helper '!f() { echo username=x-access-token; echo "password=$RECADBENCH_GH_TOKEN"; }; f'
```
```bash
git clone https://github.com/mikbalarikan/ReCADBench-data "$HOME/recadbench-data"
```
Then point the tools at the clones:

| Env var | Host | Meaning |
|---|---|---|
| `RECADBENCH_DATA_DIR` | workers, evaluator | path to the `recadbench-data` clone |
| `RECADBENCH_GT_DIR` | evaluator only | path to the `recadbench-gt` clone (mount/treat read-only) |

On the evaluator host use `RECADBENCH_GT_TOKEN` in the helper instead (it covers all four repos).

## Development

Python ≥ 3.11. Per-package `pyproject.toml` (`spec/`, `evaluator/`, `runner/`); repo-level ruff/pytest config in `pyproject.toml`.
```bash
pip install ruff pytest -e ./spec -e ./evaluator -e ./runner
```
```bash
ruff check . && pytest && python dataset/tools/check_public.py
```
