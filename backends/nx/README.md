# backends/nx — L1 backend pin + adapter

**Purpose:** Pins and wraps Siemens NX via `run_journal.exe` (CLI, no MCP). Upstream is never modified. The adapter is a measurement instrument (00_DECISIONS D3).

**Filled by:** P4 (see `recadbench/docs/architecture/`06_SESSION_PROMPTS.md).

**Expected contents:**

- `backend.yaml` — conforms to `spec/recadbench_spec/schemas/backend_manifest.schema.json` (not created in P1: a placeholder would fail validation)
- `install.md`
- `adapter/`, `replays/`
- `runner/`

Scaffolded in P1; no logic yet.
