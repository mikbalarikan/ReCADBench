# Review notes — v1 → v2

v1 was reviewed by an independent agent with no prior context. Accepted findings and resulting changes:

| # | Finding | Change |
|---|---|---|
| 1 | S1 scored vs STL = agent can see its own score; tessellation error blocks class A | S1 now samples `gt.step` B-rep; `make_task` chord ≤ ⅓ tight band (03, D7) |
| 2 | History-free solid (raw OCP, FreeCAD Part::Feature, NX dumb body) beats S2 with r≈0.1 | `unmapped` ⇒ gate fail; S2 lower bound (r<0.5 → 0); parametric-presence check from v1 (03, taxonomy) |
| 3 | No `primitive` type ⇒ Box/Block uncountable, cross-tool counts diverge | `primitive` added; conformance asserts identical count per case across tools |
| 4 | IR could not describe geometry (sketch entities without coordinates, no x_dir, free-text type, undefined selectors) | Full sketch entity geometry, `x_dir`, `reference_planes`, type enum, selector grammar, STL-frame coordinates, required `revision` |
| 5 | "CAD locked until plan" was not enforceable (Bash bypass, interactive sessions, v1-only check) | `gate_hook.py` as PreToolUse hook + LiteLLM dispatcher; Bash denied; revision N+1 required after CAD changes; `cad_prefixes` / `allowed_commands` in backend.yaml (D6) |
| 6 | Track T leaks GT plans to workers | Track T restricted to dev + conformance parts (D4, run_manifest if/then) |
| 7 | No concrete mechanism for "strategy X with tool Y" | `recadbench compose` → self-contained run dir with skills, settings, .mcp.json (D6) |
| 8 | Evaluator called NX/Solidcraft adapters on a host that lacks them | Extraction post-run on worker; S5 as queue job (D7, 01) |
| 9 | build123d on Termux unverified | P1 spike reusing PoCAD's install method |
| 10–14 | Naming/ordering/versioning inconsistencies; N_ref leak via hint and strategy D | Folders = ids; skills/ and MIGRATION_NOTES in layout; key_dims + gt_meta schemas; prompt order by dependency; spec 0.x until M3; per-repo STATUS.md; single plan naming; harness_level defined; D uses own estimate |
| 15 | S3 ±0.02 mm for all classes, ICP error counted, "snap everything" rewarded | Class-scaled tolerance, intrinsic dims, standard credit only where GT is standard |
| 16 | McNemar on replicates as independent items; "seed" not a Claude parameter; plan-only biased against D | Part = unit of analysis, Wilcoxon / cluster bootstrap; `replicate`; D ranked only in Track E |
| 17 | Over-engineering | Track S+build123d merged into Track E; S4 reduced to b1 + face types; FreeCAD deferred to M4; execute_ir required only for build123d; NX throughput budgeted |
| 18 | Schema nits | features.count derived; result has track/shas; run_manifest if/then; budget precedence; tier required; taxonomy location fixed |

Not adopted: none rejected outright. Item 17 suggestion to drop render/HTML entirely: render kept as optional H2 tool, leaderboard starts as markdown/CSV.

# v2 → v3 — three-layer restructure (user decision)

| Change | Where |
|---|---|
| Tool layer split into **L1 backend** (external, pinned, not evaluated) and **L2 skillset** (ours, evaluated) | D1, 01, 05 |
| Repo `recadbench-cad-tools` → `recadbench-cad-skills` (pure agent-facing content + helper libs) | D3, 02 |
| Backend pins + adapters moved into `recadbench/backends/` — adapters are measurement instruments, kept away from skillset authors | D3, 02 |
| L1 choices: pzfreo/build123d-mcp, `solidcraft-cli mcp`, FreeCAD MCP selected in P3, NX = journals via `recadbench nx-run` (NX_MCP only covers sketch line/rectangle + extrude in its 0.2.0.dev0 default surface) | D1, 05 |
| Gate: shell denied except `backend.yaml: allowed_commands` (NX journal runner) | D6 |
| Track T now measures skillsets incl. `none` baseline; Δ(skillset − none) is the L2 headline metric | D4, 03 |
| Skillset improvement loop with versions `<b>-vN`, dev-only | D8, P12 |
| FreeCAD in scope from the start | 05, 06, 07 |
| Schemas: `tool_manifest` → `backend_manifest` + `skillset_manifest`; run/result manifests carry backend + skillset | contracts/ |
| Prompts renumbered P1–P12 in dependency order; NX backend + desktop port merged into P4 | 06 |

# v3 → v4 — repo visibility

| Change | Where |
|---|---|
| `ReCADBench` is public; skills, strategies, data, GT are private | D3, 02 |
| Agent-visible inputs moved out of the public repo into private `recadbench-data` (contamination + client-data protection) | D3, 02, P6 |
| Fine-grained read-only tokens: workers → skills/strategies/data; evaluator host additionally → gt | D3 |
| CI guard against committing part data / GT to the public repo | D3, P1 |
| Track T GT plan travels in the job payload, not via repo access | D7 |
