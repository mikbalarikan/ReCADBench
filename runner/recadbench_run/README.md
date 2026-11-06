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
8. Gate state integrity: `.recadbench/gate_state.json` is authenticated by an HMAC-SHA256 under
   a per-run key (`gate_key.py`) kept in the runner-private key store, never in the run dir; a
   state that does not verify (overwritten, deleted, forged, no key) closes CAD execution and
   makes finish refuse. compose creates the key.
9. CAD code policy (`backends/recad_cli/code_policy.py`): an AST allow-list over build123d
   session code, `execute_file` bodies and every `.py` under `cad/` / unpinned `lib/`, and over
   NX journals plus every `.py` of the run dir (the same code runs in `recadbench nx-run` and in
   the finish sandbox). Skillset files compose copied are trusted while their sha256 equals the
   MAC-checked compose manifest (`.recadbench/compose_files.json`).
10. Glob / Grep: a glob pattern (Glob `pattern`, Grep `glob`) with `..`, `~`, a drive, UNC, `//`
    or an absolute part anywhere is denied; a path field with a `..` segment too.

## Security model

The benchmark is only meaningful if the agent reconstructs the part from the scan, so the
runner keeps the agent away from the ground truth (GT) and from the scoring machinery. Agent
code runs as the same OS user as the runner, and Python cannot be confined by inspecting its
source, so the runner layers independent checks; each layer has its own tests
(`runner/tests/test_review_h8_fix2.py`, `backends/tests/test_code_policy.py`,
`evaluator/tests/test_gt_copy.py`).

| Layer | Covers | Residual risk |
|---|---|---|
| 1. Gate (`gate_hook.py`) on every tool call | shell, web, foreign MCP tools; every path argument inside the run dir; Glob / Grep patterns in full; writes only under `plan/ cad/ out/ work/`; plan before CAD, N+1 revision rule; scan-reading code (`denied_tools`, scan patterns) | it sees tool arguments, not what code does at run time (layer 2) |
| 2. CAD code policy (`code_policy.py`) in the gate, `nx-run` and the finish sandbox | AST allow-list: allow-listed imports only (no `io os sys pathlib shutil glob hashlib zipfile zipimport importlib operator string inspect ...`), no `open exec eval compile __import__ getattr setattr vars globals locals chr bytes`, no attribute starting with `_`, no frame / generator / code attributes, no file / import / export / process / journal verbs in attribute or imported names (`import_step`, `export_stl`, `Read_s`, `DexManager`, `Execute`, `Open`, `Save`, `WriteLine`), no string literal holding `__`, no `match` / `async` / relative imports | the CAD libraries' own APIs are large (build123d, OCP, NX Open): a function that reads or writes files under a name the word list does not catch, or a C-level gadget, would pass. The policy narrows the surface; it is not a proof |
| 3. Gate state integrity (`gate_key.py`) | the D6 revision rule cannot be bypassed by rewriting `gate_state.json`: HMAC under a per-run key in `RECADBENCH_GATE_KEY_DIR` (random entry names), never in the run dir; the key-store location is removed from every process that runs agent code (CAD MCP servers via `.mcp.json`, the dispatcher, `nx-run`'s NX process, finish's export / extract); the compose manifest is MAC'd too | code that escapes layer 2 runs as the same user and can list the key dir (default `~/.recadbench-private/gate-keys` is guessable; a random `RECADBENCH_GATE_KEY_DIR` only raises the bar) |
| 4. finish (`finish.py`) | extracts only after `finish_check` (state verifies, plan valid, source executed through the gate and unchanged, native + importables pass layer 2, no file appeared or changed outside `plan/ cad/ out/ work/` since compose); a COPY of `cad/` + `lib/`, `python -I`, cwd an empty temp dir, no secrets in the env, `RECAD_SANDBOX=1` (AST check + builtins without `open` / `getattr` / real `__import__`, empty cwd) | the export process is still an ordinary process of the same user |
| 5. Evaluator GT-copy backstop (`recadbench_eval.gate.gt_copy_gate`) | a prediction whose volume (1e-3) and face count (5 %) equal the GT's AND whose two-way P95 is below 0.2 x max(scan floor P95, class tight band) fails the gate as `suspected GT copy` (outside Track T); measurements always in `result.json` `details.gt_copy` | a copy that is deliberately perturbed (scaled, re-faceted, a feature added) is not caught; conversely a perfect design-intent rebuild of a simple part whose B-rep matches the GT face for face would be flagged (review it by hand) |

**Owner action required before unattended runs** (not done by the runner, and not by this PR):
run the agent session, its MCP servers and NX under a separate Windows user that has NO read
access to `ReCADBench-gt` (and to the gate key dir), or keep the GT store off the agent hosts
entirely (GT only on the evaluator host, scoring via the queue). Set `RECADBENCH_GATE_KEY_DIR`
to a random runner-private folder. Without that, layers 1-5 are defence in depth against a
benchmark-gaming agent, not isolation against a determined one.

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
