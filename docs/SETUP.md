# Setup: access tokens and development

Moved from the top-level README. Back to [README](../README.md).

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
