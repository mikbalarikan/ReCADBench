# recadbench_run.agents

Every backend has `run(run_dir, model=None, **options) -> AgentResult` (ok, stop_reason,
efficiency, transcript `.recadbench/transcript.jsonl`, details) and puts every tool call through
the same gate. `finish` writes the efficiency dict to `efficiency.json`.

| Backend | Harness | Gate | Budgets |
|---|---|---|---|
| `claude_agent_sdk` | Claude Code via claude-agent-sdk: `cwd` = run dir, `setting_sources=["project"]` (CLAUDE.md, `.claude/skills`, `.claude/agents`; no user plugins / MCP / memory), `strict_mcp_config` with `.mcp.json`, `permission_mode=bypassPermissions`, no WebFetch / WebSearch | in-process PreToolUse hook (`on_failure: block`), plus the project command hook | `max_turns`, `max_budget_usd`, `task_budget` (= max_tokens) to the SDK; turns / tokens / cost re-checked per assistant message (interrupt) and `max_wall_s` as an asyncio timeout |
| `litellm_loop` | OpenAI-style function calling through LiteLLM (`completion` injectable), system prompt = `system_prompt.md`, tools = `Dispatcher.tool_schemas()` | `Dispatcher.call` -> `Gate` before every dispatch; a denial is a tool error | checked after every response |
| `replay` | scripted tool calls (`replay_scripts`), no model; `expect: allow / deny` steps assert gate decisions | same dispatcher | same tracker (turns = steps) |

`dispatcher.py` implements Read / Write / Edit / Glob / Grep / TodoWrite locally, Bash only for a
cli backend's allowed command (no shell), and MCP tools through `mcp_client.py` (stdlib
JSON-RPC stdio client; no `mcp` package needed). Agent processes get `compose.agent_env`: no
`RECADBENCH_GT_DIR` / tokens, the runner's scripts dir first on PATH.

Models: `claude-opus-5-5`, `claude-sonnet-5-5` (SDK); any LiteLLM model id for the loop.
Auth is whatever the local Claude Code / provider SDK has; nothing here obtains credentials.

`replay_scripts.oracle_build123d` is the zero-cost Track T dry run: read `input/task.yaml` and
the seeded plan, probe a denied scan import, write `plan/analysis.md` and `cad/part.py`
(`ir_build123d.write_part_py`: the plan turned into a skillset-contract script - module-level
`# IR parameter` literals, build123d only, final `part`), `execute_file` it in the build123d-mcp
session, `validate`, `measure`. The writer covers the constructs of the HS01 pilot plan and the
CI synthetic plans; it is a test harness, not a reference executor (coarse plan edge sets are
filleted on the subset the kernel accepts, the rest stays sharp and is noted in the script).
