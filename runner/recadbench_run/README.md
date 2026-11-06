# recadbench_run

The ReCADBench runner (pip `recadbench-run`, P10 / PROMPTS_v2 H8): composes a run dir
(00_DECISIONS D6), gates every tool call, runs an agent backend, and leaves the run-dir contract
the evaluator scores (`evaluator/recadbench_eval/README.md`). It owns the `recadbench` command;
`recadbench nx-run` is forwarded to `backends/nx/runner/nx_run.py`.

```
recadbench seed-plan --part P --out seed.json                    # evaluator host only (GT)
recadbench compose  --strategy oracle --skillset build123d@v1 --part P --seed-plan seed.json --out runs/X
recadbench compose  --strategy B --skillset nx@v1 --part P --out runs/Y           # Track E
recadbench compose  --strategy oracle --skillset none --backend nx --part P ...   # bare-L1 baseline
recadbench compose  --strategy B --skillset none --backend none --part P ...      # Track S
recadbench agent    --run-dir runs/X [--agent-backend claude_agent_sdk|litellm_loop|replay] [--finish --score]
recadbench finish   --run-dir runs/X [--score] [--queue URL]
recadbench run      <compose args> [--script oracle_build123d] [--score]          # all three
recadbench render-prompt --run-dir runs/X
recadbench gate     --run-dir runs/X     # Claude Code PreToolUse hook (stdin JSON)
recadbench nx-run   cad/step_01.py [--timeout S]
```

Content repos: `--skills-dir` / `RECADBENCH_SKILLS_DIR`, `--strategies-dir` /
`RECADBENCH_STRATEGIES_DIR`, `--data-dir` / `RECADBENCH_DATA_DIR`. With a git ref
(`--skillset-ref`, default tag `<b>-vN`; `--strategy-ref`, default: the working tree) compose
reads the committed tree (`git archive`), so the pin in `run_manifest.json` is exact; a working
tree with local changes is pinned as `<sha>-dirty`.

## Modules

| Module | What |
|---|---|
| `compose.py` | D6 run dir: `input/` (byte copy), `.claude/skills/strategy-<id>/` (SKILL.md, playbook, stages, shared), skillset skills / agents / examples, `lib/`, `_shared/`, `.mcp.json` (retk with `toolkit_tools` x harness level, omitted when empty; the L1 MCP server), `.claude/settings.json` (gate hook), `.recadbench/{gate.json, backend.yaml}`, `run_manifest.json` (schema-valid), `CLAUDE.md`, `system_prompt.md`. Track rules (T = oracle + seed plan + dev split; S = no backend; E = no oracle) |
| `frames.py` | pure rigid mapping of an IR (`map_plan`, `seed_from_gt`): points / vectors by taxonomy param type, face-selector axes |
| `seed.py` | Track T seed plan from `RECADBENCH_GT_DIR` (evaluator host; the orchestrator ships it in the job payload) |
| `gate_hook.py` + `gate_policy.yaml` | the gate (see below) |
| `render_prompt.py` | `system_prompt.md` = CLAUDE.md + every skill / shared / agent file, verbatim |
| `agents/` | `claude_agent_sdk`, `litellm_loop`, `replay` (see `agents/README.md`) |
| `budgets.py` | task.yaml budgets overridden by the manifest; tracker (turns, tokens, cost, wall) |
| `finish.py` | post-run `recad <b> extract` + `export` in a clean process, hashes incl. `native_sha256`, optional score / enqueue |
| `artifacts.py` | hashing; `gt_leak_check` (nothing from the GT store in a run dir) |
| `run.py` | one job: compose -> agent -> finish |
| `cli.py` | the `recadbench` command |

## Gate (00_DECISIONS D6, ADR 0004)

One `Gate.check` for the Claude Code hook, the Agent SDK hook and the LiteLLM / replay
dispatcher. Backend facts come from the run's copy of `backends/<b>/backend.yaml`
(`cad_prefixes`, `allowed_commands`, `denied_tools`); `gate_policy.yaml` holds only what
backend.yaml has no field for (built-in tool allowlist, protected paths, harness levels, the
native file and CAD-source globs per backend, the tool whose deny rule applies to a script body,
runner-level scan-code patterns).

1. Shell denied, except a backend `allowed_commands` match (NX: `recadbench nx-run cad/*.py`,
   optional `--timeout N`), no metacharacters, no `..`, the journal must exist under `cad/`.
2. MCP: retk only for the composed subset; the L1 server's tools; anything else denied.
   `WebFetch` / `WebSearch` / unknown built-ins denied.
3. `denied_tools`: prefix + `re.search(args_regex, json.dumps(args))`. kind `cli`: arguments
   `{"command", "journal"}` with the journal source from `nx_run.journal_source`, over the journal
   and every `.py` of the run dir (+ opaque modules refused), exactly like `recadbench nx-run`.
   build123d `execute_file`: the script body (and every `.py` / opaque module under `cad/`, `lib/`)
   is matched as `execute(code=<source>)`.
4. Scan-code patterns on code (comments removed): mesh libraries (trimesh, open3d, numpy-stl,
   pymeshlab, meshio, pyvista, vtk), OCP / VTK mesh readers (`RWStl`, `StlAPI`, `RWObj`, ...),
   `import_stl` / `Mesher`, load calls on mesh-file literals, `input/`, `exec` / `eval` /
   `importlib` / `__import__`.
5. Paths: every path (file tools; any absolute path in any argument) inside the run dir; writes
   never to `input/`, `lib/`, `_shared/`, `.claude/`, `.recadbench/`, the manifests; seeded or
   executed plan revisions are read-only.
6. Plan before CAD: any backend tool or allowed command needs a schema-valid, contiguous
   `plan/re_plan.vN.json` of this part.
7. Revision rule: calls that execute the CAD source (`execute_file`, `nx-run`) record
   (N, sha256 of `cad/` sources). After the sources changed, the next such call needs N+1.
   `execute(code)` probes and reads need only a valid plan.

## Track T frames

`gt_plan.json` is in the gt.step frame; agents work in the scanner frame. `seed_from_gt(gt_plan,
T_scan_to_gt)` maps it with `inv(T)` (`frames.map_plan`): datum / reference-plane origins and
directions, `vec3` / `vec3_list` params as points, `direction` vectors as vectors, face-selector
axes rewritten when they stay frame axes. A selector axis that a general rotation turns into an
arbitrary direction cannot be written in spec 0.3.0 (`BODY:plane:+Z:max`): it becomes
`BODY:plane:any:any` and the feature's `evidence.note` states the mapped direction (both R385
pilots that use one: the counterbore / bore entry faces). Known-answer tests:
`tests/test_frames.py`; round trip: `plan_score(seed, gt, ..., T)` equals `plan_score(gt, gt, ...)`
(synthetic in CI, the three pilots on the evaluator host).
