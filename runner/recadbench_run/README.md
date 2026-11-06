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
recadbench run      <compose args> [--score]                                       # all three
recadbench run      --strategy oracle --skillset build123d@v1 --part P --seed-plan seed.json \
                    --agent-backend replay --script oracle_build123d --score --out runs/dry
                    # zero-cost dry run; labelled model replay / provider replay (run_id too)
recadbench run      --strategy oracle --skillset nx@v1 ... --agent-backend replay --script nx_smoke
                    # NX pipeline check (one cylinder journal; NOT an oracle: gate fail on geometry)
recadbench render-prompt --run-dir runs/X
recadbench gate     --run-dir runs/X     # Claude Code PreToolUse hook (stdin JSON)
recadbench nx-run   cad/step_01.py [--timeout S]
```

Content repos: `--skills-dir` / `RECADBENCH_SKILLS_DIR`, `--strategies-dir` /
`RECADBENCH_STRATEGIES_DIR`, `--data-dir` / `RECADBENCH_DATA_DIR`. With a git ref
(`--skillset-ref`, default tag `<b>-vN`; `--strategy-ref`, default: the working tree) compose
reads the committed tree (`git archive`), so the pin in `run_manifest.json` is exact; a working
tree with local changes is pinned as `<sha>-dirty`. When tag `<b>-vN` exists, `--skillset-ref`
must resolve to the same commit (otherwise the skillset id would name other content); without
the tag the ref is used and `compose.warnings` records it. Harness level default: H1
(04_STRATEGIES). `--agent-backend replay` always records model / provider `replay`; another
`--model` with replay is refused.

## Modules

| Module | What |
|---|---|
| `compose.py` | D6 run dir: `input/` (byte copy), `.claude/skills/strategy-<id>/` (SKILL.md, playbook, stages, shared), skillset skills / agents / examples, `lib/`, `_shared/`, `.mcp.json` (retk with `toolkit_tools` x harness level, omitted when empty; the L1 MCP server), `.claude/settings.json` (gate hook), `.recadbench/{gate.json, backend.yaml}`, `run_manifest.json` (schema-valid), `CLAUDE.md`, `system_prompt.md`. Track rules (T = oracle + seed plan + dev split; S = no backend; E = no oracle) |
| `frames.py` | pure rigid mapping of an IR (`map_plan`, `seed_from_gt`): points / vectors by taxonomy param type, face-selector axes |
| `seed.py` | Track T seed plan from `RECADBENCH_GT_DIR` (evaluator host; the orchestrator ships it in the job payload) |
| `gate_hook.py` + `gate_policy.yaml` | the gate (see below) |
| `render_prompt.py` | `system_prompt.md` = CLAUDE.md + every skill body / shared / agent file, verbatim; files under a skill's `references/` are indexed (path, size, title) and read with Read (nx@v1: 798 KB -> index) |
| `agents/` | `claude_agent_sdk`, `litellm_loop`, `replay` (see `agents/README.md`) |
| `budgets.py` | task.yaml budgets overridden by the manifest; tracker (turns, tokens, cost, wall) |
| `finish.py` | post-run `recad <b> export` then `extract`, only after `Gate.finish_check` (gate state is the gate's own, valid plan, the CAD source was executed through the gate and is unchanged since, native + importables pass the code checks); on a temp COPY of `cad/` + `lib/`, `python -I`, cwd outside the run, no secrets in the env, `RECAD_SANDBOX=1` (build123d adapter: AST check, restricted builtins, import allow-list, `b3d_sandbox.py`); results written to `out/` from memory after the last native run. Hashes incl. `native_sha256`, optional score / enqueue |
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
   `import_stl` / `Mesher`, load calls on mesh-file literals, `input/`; dynamic code (`exec` /
   `eval` / `compile`, also as `builtins.exec`, `runpy`, `importlib`, `__import__`), processes and
   foreign code (`subprocess`, `os.system`, `ctypes`, `pickle`, ...). String literals naming a path
   outside the run dir (absolute, `~`, `..`), a protected run-dir path or the GT store
   (`gate_policy.yaml: gt_name_pattern`) are denied.
5. Paths: path arguments (`path_args`: any length, max 1024 chars, no control characters) and
   every other short single-line path-like string inside the run dir. Writes (file tools AND MCP
   write arguments, `write_args`: build123d export / script / render_view / ..., retk render
   `out_dir`) only under `plan/`, `cad/`, `out/`, `work/`; never to protected paths; seeded or
   executed plan revisions are read-only. `install_skill` is blocked.
6. Plan before CAD: any backend tool or allowed command needs a schema-valid, contiguous
   `plan/re_plan.vN.json` of this part.
7. Revision rule: calls that execute the CAD source (`execute_file`, `nx-run`) record
   (N, sha256 of `cad/` sources). After the sources changed, the next such call AND every
   model-mutating tool (`backend.yaml: cad_prefixes.mutating`: `execute`, `restore_snapshot`,
   `edit_feature`, `reset`, ...) needs N+1. Read-only tools need only a valid plan.
8. Gate state integrity: `.recadbench/gate_state.json` plus its sha256 outside the run dir
   (`RECADBENCH_GATE_DIGEST_DIR`, default `<runs>/.recadbench-gate/`); a mismatch (overwritten,
   deleted) closes CAD execution and makes finish refuse. compose resets it.

## Track T frames

`gt_plan.json` is in the gt.step frame; agents work in the scanner frame. `seed_from_gt(gt_plan,
T_scan_to_gt)` maps it with `inv(T)` (`frames.map_plan`): datum / reference-plane origins and
directions, `vec3` / `vec3_list` params as points, `direction` vectors as vectors, face-selector
axes rewritten when they stay frame axes. A selector axis that a general rotation turns into an
arbitrary direction cannot be written in spec 0.3.0 (`BODY:plane:+Z:max`): it becomes
`BODY:plane:any:any` and the feature's `evidence.note` states the mapped direction (both R385
pilots that use one: the counterbore / bore entry faces). When the mapped axis flips sign the
extremum flips too (executors take max / min of the world coordinate: `+Z:max` -> `-Z:min`), and
a face index `:n` is dropped under any rotation (executors index after sorting by world X, Y, Z;
the note says so). Known-answer tests:
`tests/test_frames.py`; round trip: `plan_score(seed, gt, ..., T)` equals `plan_score(gt, gt, ...)`
(synthetic in CI, the three pilots on the evaluator host).
