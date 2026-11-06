# 00 — Key Decisions

## D1. Three agent layers, evaluated separately

| Layer | What it is | Who owns it | Evaluated? | Where |
|---|---|---|---|---|
| **L1 — CAD backend** | The software interface: MCP server or CLI. Off-the-shelf, pinned, **not modified by us** | External repos | No (fixed infrastructure) | Pinned in `recadbench/backends/<b>/backend.yaml` |
| **L2 — CAD skillset** | Our accumulated know-how for *operating* one CAD program: skills, subagent files, helper libraries, worked examples, error→fix tables. Built from past projects and trial-and-error | Us | **Yes — Track T** (incl. `none` baseline) | `recadbench-cad-skills/<b>/` |
| **L3 — RE strategy** | CAD-agnostic reverse-engineering method (A–D): stages, analysis, decisions | Us | **Yes — Track S / E** | `recadbench-strategies/strategies/<id>/` |

Rationale: L1 is a solved/outsourced problem we don't spend time on. L2 and L3 fail in different ways — a good strategy with a weak skillset produces the right plan but broken geometry; a strong skillset with a weak strategy produces clean but wrong parts. Mixing them makes results un-attributable.

### L1 backends (v1)

| Backend | Interface | Source | Notes |
|---|---|---|---|
| `build123d` | MCP | [pzfreo/build123d-mcp](https://github.com/pzfreo/build123d-mcp) (Apache-2.0, active) | Persistent session, execute, measure, hole/pattern finding, STL import/compare, STEP export, snapshots |
| `solidcraft` | MCP | `solidcraft-cli mcp` from tangency-labs/solidcraft | Headless or `--connect`; JSON command scripts; STEP export; ARM64 builds |
| `freecad` | MCP | Selected in P4 from existing servers (e.g. neka-nat/freecad-mcp, freecad-robust-mcp) | Selection criteria: headless/FreeCADCmd support, code execution, STEP export, activity |
| `nx` | **CLI (no MCP)** | Siemens NX `run_journal.exe` + our thin runner `recadbench nx-run <journal.py>` | Agent writes NX Open Python journals into `cad/`; the only shell command it may run is `recadbench nx-run`. DreamEnding/NX_MCP is not used in v1 (default surface is sketch line/rectangle + extrude only, `0.2.0.dev0`); re-evaluate later |

## D2. The L3→L2 seam is a file: the **RE Plan IR**

Every strategy must emit `plan/re_plan.v1.json` (schema: `contracts/re_plan_ir.schema.json`) **before** any CAD call. It is a tool-neutral feature plan: units, datums, ordered features with types, nominal parameters and references, plus measured-vs-nominal evidence.

- Strategies can be scored **without any CAD tool** (plan vs GT).
- Skillsets can be scored **without any strategy** (feed the GT plan, check the output).
- End-to-end failures are attributable: wrong plan vs wrong execution.

The IR describes *intent*, not tool commands. The L2 skillset owns the translation IR → native operations. Refinements write `re_plan.v2.json`, … with `change_log`.

## D3. Repo topology: 1 public + 4 private repos

GitHub name of the benchmark repo is **`ReCADBench`**; docs write it as `recadbench` (GitHub URLs are case-insensitive; pip package names stay lowercase: `recadbench-spec`, `recadbench-eval`, `recadbench-run`).

| Repo | Visibility | Owns |
|---|---|---|
| `ReCADBench` | **Public** | Contracts (`spec/`), **L1 backend pins + adapters** (`backends/`), dataset *tooling* (no part data), evaluator, runner/compose, orchestrator, leaderboard code. Results stay out of git (Drive) |
| `recadbench-cad-skills` | Private | **L2** — one skillset per backend: `skills/`, `agents/`, `lib/` (helper code the agent may call, e.g. NX Open helpers), `examples/`, `LESSONS.md`. Pure agent-facing content + agent-callable helpers; no benchmark infrastructure |
| `recadbench-strategies` | Private | **L3** — strategies A–D + oracle, and the tool-agnostic **RE toolkit** MCP (`retk`: mesh analysis, primitive fit, slicing, self-deviation vs STL) |
| `recadbench-data` | Private | Agent-visible inputs: `parts/<part_id>/{input.stl, task.yaml}`, `splits/{dev,test}.txt`. Pulled by workers |
| `recadbench-gt` | Private (mandatory) | Ground truth: `parts/<part_id>/{gt.step, gt_native.*, gt_plan.json, key_dims.yaml, meta.yaml}`. Only on the evaluator host; never on workers |

Why inputs are not in the public repo: (1) public STLs end up in future training data and destroy the benchmark's main advantage, contamination-freedom; (2) parts derived from client work must not be public. Inputs and GT are separate repos so workers can clone inputs without ever having GT access.

Access: the public repo's `compose`/`worker` read the private repos. Each worker host gets a **fine-grained GitHub token, read-only, scoped to `recadbench-cad-skills`, `recadbench-strategies`, `recadbench-data`** (env `RECADBENCH_GH_TOKEN`). The evaluator host additionally has read access to `recadbench-gt`. No token ever has GT access on a worker.

Public-repo hygiene: CI rejects commits containing `*.stl`, `*.step`, `*.stp`, `*.prt` outside `backends/conformance/` and `evaluator/golden/` (synthetic only), and any `gt_plan.json` / `key_dims.yaml` file.

Why adapters (feature extraction, STEP export, `set_param`, conformance replays) live in the benchmark repo, not the skills repo: they are **measurement instruments**. If the team that writes skillsets also controls feature counting, the skillset could be tuned against the counter. Keeping them in `recadbench` keeps L2 purely "how the agent works".

Contracts: `recadbench/spec/` is the single source of truth, published as pip package `recadbench-spec`, tagged `spec-v0.1.0` … Stays `0.x` until the M3 pilot, then `1.0.0`. The other repos pin a tag.

## D4. Evaluation tracks

| Track | Agent input | Fixed | Varies | Measures |
|---|---|---|---|---|
| **T — Skillset** | GT plan IR + STL | strategy = `oracle`, backend | skillset version (incl. **`none`** = bare L1), model | Execution fidelity, IR adherence, cleanliness. `skillset − none` = value of our know-how |
| **S — Strategy (plan-only)** | STL | backend = `none` | strategy, model | Plan quality vs GT, no CAD cost |
| **E — End-to-end** | STL | — | strategy × skillset × model | The real task. Strategy comparison with geometry = Track E with skillset `build123d@latest` |

- Track T exposes GT plans to workers → **dev parts and conformance cases only, never `test`**.
- Plan-only Track S is biased against strategies needing execution feedback (D) → rank D only in Track E.
- Interpretation: E ≲ min(T, S). E ≪ min(T, S) for a pairing ⇒ strategy/skillset interaction problem (itself a finding).

## D5. A run is fully pinned by its manifest

`run_manifest.json` pins: strategy@sha, skillset@sha (or `none`), backend + software version, spec version, model + provider + sampling params, part, replicate, track, `harness_level`, budgets (manifest overrides task.yaml). Same manifest ⇒ reproducible run.

## D6. Composition: how "strategy B + NX" becomes a session

```
recadbench compose --strategy B --skillset nx@v3 --part 017 [--track E] [--harness-level H1] --out runs/<run_id>/
recadbench compose --strategy oracle --skillset none --backend nx --part 017 --track T   # bare-L1 baseline
```

Writes a self-contained run dir:

```
runs/<run_id>/
  CLAUDE.md                     # task + STRATEGY section + SKILLSET section + conflict rule
  .claude/skills/strategy-B/    # from recadbench-strategies/strategies/B/skills/
  .claude/skills/<skillset>/    # from recadbench-cad-skills/nx/skills/
  .claude/agents/               # from recadbench-cad-skills/nx/agents/ (subagent files)
  .claude/settings.json         # PreToolUse hook = gate_hook.py; shell denied except backend allowlist
  .mcp.json                     # retk (strategy-allowed subset) + L1 MCP (if backend is MCP)
  lib/                          # skillset helper code (e.g. NX Open helpers), read/import only
  run_manifest.json
  input/ (input.stl, task.yaml)  plan/ (re_plan.vN.json)  cad/ (journals, native files)  out/ (output.step)
```

- Interactive: "Run `recadbench compose --strategy B --skillset nx@v3 --part 017`, then work inside that run dir."
- Batch: the runner does the same and starts the agent backend in it.
- Non-Claude models (LiteLLM loop): compose also renders `system_prompt.md` = CLAUDE.md + inlined skills + agents, and the dispatcher exposes the same tools. Skill content is identical across model families.

**Conflict rule** (in every strategy and skillset): strategy decides **what** and the reasoning order; skillset decides **how** to operate the software. Skillsets contain no RE methodology; strategies name no CAD command.

### Gate enforcement
- One `gate_hook.py`: Claude Code PreToolUse hook (SDK + interactive) and inside the LiteLLM dispatcher.
- Shell denied, except the backend's allowlisted commands from `backend.yaml: allowed_commands` (NX: `recadbench nx-run cad/*.py` only).
- Tools in `backend.yaml: denied_tools` (scan import / mesh analysis inside the CAD program; name prefix + optional argument regex) are always denied (ADR 0004).
- CAD calls (MCP prefixes or allowlisted commands, from `backend.yaml`) are allowed only if a schema-valid `plan/re_plan.vN.json` exists; after a CAD-side change, the next execute needs revision N+1.
- Track T: hook pre-seeds `re_plan.v1.json` from GT (oracle).

## D7. Ground-truth isolation by topology

Workers clone/pull `recadbench-data` and receive only `input.stl` + `task.yaml` (Track T on dev parts: the orchestrator ships that part's `gt_plan.json` inside the job payload, never repo access to GT). Evaluator runs on one fixed host (ASUS) with the GT store mounted read-only. Agent self-checks use the **STL** (since ADR 0002: the raw scan); the evaluator scores geometry against **`gt.step`**, mapped through the stored scan→GT registration.

Post-run extraction (`extract_features`, `export_step`, hashes) runs **on the worker** in a clean process after the session, because NX/Solidcraft/FreeCAD are not on the evaluator host. S5 `set_param` is a queue job to a host with that backend.

## D8. Skillset development loop (L2 is a living artifact)

- Skillsets are versioned (`nx@v1`, `nx@v2` …, git tags `nx-v2`).
- Each Track T campaign on **dev** parts produces failure logs; the skill-improvement session turns recurring failures into `LESSONS.md` entries and skill/lib changes → new version.
- Every version is re-run on Track T dev; versions are compared against each other and against `none`. Never tune on `test`.

## D9. Assumptions / facts to keep current

- Solidcraft = tangency-labs/solidcraft; early-stage, pin a release, rerun conformance on every bump.
- build123d reference backend = pzfreo/build123d-mcp; Termux install via the method PoCAD already uses (P1 spike).
- NX via journals: one NX licence ⇒ NX parallelism = 1 per host.
- ~~50 parts, user-authored GT; 10 dev / 40 frozen test.~~ Superseded by D10: v1 is dev-only (scan-rebuild parts); the frozen test set waits for uncontaminated `print_scan` parts.
- "Seed" = `replicate` index (Claude APIs have no seed parameter); 3 replicates per config × part.

## D10. Input is a real 3D scan (ADR 0002, spec 0.2.0)

The benchmark task is industrial reverse engineering: **raw scan STL (scanner frame, noisy, holed,
partly unobserved) → clean parametric CAD/STEP recovering design intent.** `input.stl` is the scan
itself, never a tessellation of `gt.step` (synthetic inputs remain only for calibration and evaluator
tests, `input_kind: synthetic_*`, never ranked).

| `source_kind` | Input | GT | Status |
|---|---|---|---|
| `scan_rebuild` | scan of a physical part | a reconstruction (R385: owner's NX rebuild; open-dedica: agent-made) | **v1 — dev only** |
| `print_scan` | owner's scan of a print of an own design (OGUZ) | the original design CAD | later — the uncontaminated test set |
| `synthetic` | generated from GT | exact | calibration only |

- GT authoring adds **registration** (`T_scan_to_gt`), the **scan floor** (GT's own deviation from the scan)
  and **observability** (GT surface covered by the scan) to `meta.yaml`.
- `meta.yaml` records `gt_author` and `contamination`; the validator refuses `split: test` for contaminated
  parts, agent-made GT or synthetic inputs. Agent-made GT (open-dedica) is circular for this benchmark and
  stays dev/calibration until the owner reviews it.
- **v1 therefore has no frozen test split and no public leaderboard.**
