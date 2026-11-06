# docs/adr — Architecture Decision Records

**Purpose:** One file per decision, `NNNN-short-title.md`. Required for any schema change (spec version bump), backend selection (FreeCAD MCP choice) and threshold changes.

**Filled by:** P2 (schema ADRs), P3 (FreeCAD MCP selection), P11 (threshold ADR); any later session (see `recadbench/docs/architecture/`06_SESSION_PROMPTS.md).

**Index:**

- [0001](0001-ir-params-anyof.md) — RE Plan IR `params` values: `oneOf` → `anyOf` (P2)
- [0002](0002-real-scan-input.md) — Inputs are real scans in the scanner frame; v1 dev-only (spec 0.2.0)
- [0003](0003-freecad-mcp-selection.md) — FreeCAD MCP selection: freecad-robust-mcp 0.6.1 (P3)
