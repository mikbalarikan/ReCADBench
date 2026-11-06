# Pilot estimate (H9): run count, cost, wall time

Experiment: [`pilot.yaml`](pilot.yaml). Numbers regenerate with
`python orchestrator/experiments/pilot_estimate.py orchestrator/experiments/pilot.yaml` (the
profile constants at the top of that script are the assumptions listed below). This is a
model anchored on the H8 evidence, not a measurement: the only real Claude run so far is the
7-turn H8 smoke. Nothing here has been run against a model in H9 (replay backend only).

## 1. Run count (expand.py)

`recadbench-orch expand orchestrator/experiments/pilot.yaml --count --data-dir <data>`:
**54 runs** (27 per model). Hand check: parts 3 x models 2 x replicates 1 = 6 per cell.

| Track | Strategy | Skillset | Backend | Host needs | Runs | Opus 5.5 | Sonnet 5.5 |
|---|---|---|---|---|---:|---:|---:|
| T | oracle | build123d@v1 | build123d | build123d | 6 | 3 | 3 |
| T | oracle | nx@v1 | nx | **NX host** | 6 | 3 | 3 |
| T | oracle | none@build123d (bare L1) | build123d | build123d | 6 | 3 | 3 |
| T | oracle | none@nx (bare L1) | nx | **NX host** | 6 | 3 | 3 |
| E | B | build123d@v1 | build123d | build123d | 6 | 3 | 3 |
| E | B | nx@v1 | nx | **NX host** | 6 | 3 | 3 |
| E | D | build123d@v1 | build123d | build123d | 6 | 3 | 3 |
| E | D | nx@v1 | nx | **NX host** | 6 | 3 | 3 |
| S | B (plan only) | none | none | none | 6 | 3 | 3 |
| | | | | | **54** | **27** | **27** |

By track T 24 / E 24 / S 6; by strategy oracle 24 / B 18 / D 12; by backend build123d 24 /
nx 24 / none 6. Host requirement: **24 runs need the NX host** (the ASUS, one NX licence, queue
limit nx = 1), 24 need a `build123d` worker, 6 (Track S) any worker. Every run enqueues a
`score` job, which needs `gt` = the ASUS (54 score jobs). Track T payloads carry the seed plan
made on the ASUS from `RECADBENCH_GT_DIR` at `--enqueue`.

## 2. Prices used

Anthropic first-party list prices, USD per million tokens, from
<https://platform.claude.com/docs/en/about-claude/pricing> (read 2026-11-07; found through the
claude-api skill's live-sources list). No long-context surcharge on either model (1M context at
standard price), no `inference_geo` (the 1.1x US-only multiplier does not apply).

| Model | Input | 5m cache write | 1h cache write | Cache hit | Output |
|---|---:|---:|---:|---:|---:|
| claude-opus-5-5 | 4.00 | 5.00 | 8.00 | 0.20 | 20.00 |
| claude-sonnet-5-5 | 2.00 | 2.50 | 4.00 | 0.10 | 10.00 |

Note: the claude-api skill's cached model table gives Sonnet 5.5 cache reads as $0.20; the live
pricing page says $0.10 (0.05x base). The live page is used.

## 3. Evidence and cost model

**H8 smoke run** (`claude-sonnet-5-5`, oracle x build123d@v1 x hs01, 2026-11-06, incomplete:
its subagent was backgrounded and the session ended): 7 SDK turns, 45 s, CLI
`total_cost_usd` **$0.257**; result usage: 10 input, **37,892 cache-creation (all 1-hour
TTL)**, 137,746 cache reads, 845 output. Re-priced at the list prices above, that usage is
$0.174 (writes $0.152, reads $0.014, output $0.008). The CLI total is 1.48x that; the most
likely reason is the backgrounded `build123d-modeler` subagent, whose tokens are in
`total_cost_usd` but not in the main-session `usage`. Two consequences: (1) the Claude Code
harness writes its cache at the **1-hour** price, so cache writes, not reads, dominate short
runs; (2) skillsets that ship subagents cost a factor more than the main loop shows. The
runner's cost budget (`max_cost_usd`) is checked against the CLI total, which includes the
subagents.

**Per-turn model** (Claude Code re-sends the cached context every turn): turn t reads
`C0 + g(t-1)` tokens at the cache-hit price, writes `g` new tokens at the 1-hour write price
(C0 on turn 1), and emits `o` output tokens (thinking included), all times a subagent factor
`m` (1.0 for none@ baselines and Track S, which have no subagents).

| Assumption | Low | Expected | Cap (budget ceiling) | Basis |
|---|---|---|---|---|
| C0 initial context | build123d@v1 38k, nx@v1 45k, none@b 30k, Track S 33k | same | same | 37.9k measured (build123d@v1); others scaled by skill count |
| Turns T: Track T / E / S | 15 / 40 / 15 | 40 / 100 / 35 | **200** (task.yaml `max_turns`) | oracle executes a given plan; B/D analyse + plan + execute + 3-4 refine loops |
| Context growth g / turn | 2.0-2.5k | 2.5-3.0k | 3.0k | tool results (retk tables, measure output, journals) |
| Output o / turn | 0.8-1.0k | 1.2-1.5k | 2.0k | smoke 120 / turn on trivial turns; thinking on both models |
| Subagent factor m | 1.0 | 1.3 | 1.5 | smoke 1.48 |
| Seconds / turn (Sonnet / Opus) | 6 / 8 | 12 / 18 | wall = `max_wall_s` 3600 | smoke 6.4 s / turn |
| CAD executions per run (T / E) | 2 / 4 | 4 / 10 | 20 / 30 | build123d 3 s, NX 25 s each (run_journal start, H8 nx_smoke) |
| finish / score job | 30 s (NX 60 s) / 1.5 min | 30 s (NX 60 s) / 3.5 min | 30 s (NX 60 s) / 7.5 min | H9 replay hs01: oracle run + finish + score 100 s in all; H9 NX smoke (wrong geometry, alignment falls back to the PCA search): score about 450 s; H8 quoted about 4.5 min for hs01 |

At the cap the context reaches C0 + 600k tokens, still inside the 1M window (no compaction
assumed). The cap is the analytic ceiling of a run that exhausts `max_turns` 200; a subagent
loop is not counted by `max_turns` (main-loop turns only), which is why pilot.yaml adds the
runaway guard `max_cost_usd: 60`, above the highest analytic ceiling (Opus Track E $40).

## 4. Cost

| Scenario | claude-opus-5-5 (27 runs) | claude-sonnet-5-5 (27 runs) | **Total (54 runs)** |
|---|---:|---:|---:|
| Low | $45 | $22 | **$67** |
| Expected | $199 | $100 | **$299** |
| Cap (every run hits 200 turns) | $957 | $478 | **$1,435** |
| Hard guard (every run hits `max_cost_usd` 60) | $1,620 | $1,620 | $3,240 |

Per run (USD, Opus / Sonnet):

| Run class | Low | Expected | Cap |
|---|---|---|---|
| T oracle x build123d@v1 | 0.92 / 0.46 | 4.17 / 2.08 | 39.80 / 19.90 |
| T oracle x nx@v1 | 0.99 / 0.50 | 4.31 / 2.15 | 40.30 / 20.15 |
| T oracle x none@b (bare L1) | 0.83 / 0.41 | 3.08 / 1.54 | 26.15 / 13.07 |
| E B or D x build123d@v1 | 2.57 / 1.29 | 12.22 / 6.11 | 39.80 / 19.90 |
| E B or D x nx@v1 | 2.68 / 1.34 | 12.48 / 6.24 | 40.30 / 20.15 |
| S B plan only | 0.86 / 0.43 | 2.31 / 1.15 | 26.29 / 13.15 |

Track E is about 75 % of the expected spend (24 runs x $6-12). Opus 5.5 is exactly 2x Sonnet 5.5
per token on every price line, so with the same token profile it costs 2x; real Opus runs may
use fewer turns (not assumed).

## 5. Wall time per host

ASUS: Intel i7-14650HX (16 cores / 24 threads), 15.6 GB RAM (4.9 GB free with the desktop
apps open), NX 2206 with one licence. Concurrency is RAM-bound, not CPU-bound: per non-NX run
about 1.5 GB (Claude Code CLI 0.15-0.35 GB, build123d-mcp / OCP, retk holding the scan), a
score job 1-2 GB peak, NX 2-3 GB. **N = 3** non-NX lanes + the NX lane (4 worker processes,
`--caps build123d,nx,gt`; the queue keeps NX at 1) fits after closing the desktop apps; use
N = 2 otherwise. CPU is not the limit (the agent waits on the API most of the time).

Makespan (hours; list scheduling, longest first; the 54 score jobs on ASUS lanes):

| Scenario | NX lane (24 runs, serial) | non-NX work alone, N=3 | **ASUS alone, N=3** | ASUS N=2 / N=4 | **ASUS N=3 + phone** |
|---|---:|---:|---:|---:|---:|
| Low | 2.2 | 0.8 | **2.2** | 2.2 / 2.2 | **2.2** |
| Expected | 8.6 | 3.2 | **8.6** | 8.6 / 8.6 | **8.6** |
| Cap | 24.4 | 11.1 | **24.5** | 24.5 / 24.5 | **24.5** |

- **The NX lane is the critical path.** 24 NX runs are serialised as whole sessions (queue
  limit nx = 1 per host plus the worker's host lock), although the agent spends most of a
  session thinking, not in NX. More non-NX lanes or the phone do not shorten the pilot; they
  only finish the build123d / plan-only half earlier (3.2 h -> 2.6 h expected with the phone).
- **Phone worker: untested.** H2 (P5 on the phone) was skipped, so `retk` / build123d-mcp under
  Termux, the worker's artifact upload and the remote queue have never run on the phone. The
  table assumes it takes build123d and Track S runs at 1.5x the ASUS CPU time and never scores
  (no `gt`). Do not rely on it for the pilot; at best it is a parallel H2 check.
- API concurrency: at most 4-5 simultaneous sessions; no rate-limit problem expected at these
  token rates.

## 6. nx-v1 and `_shared/`

`nx-v1` (a21f3fa) was tagged in H4, before H6 added `_shared/ir_execution.md` and
`_shared/single_solid_rules.md`. `nx/skillset.yaml` at that tag lists both, so compose keeps the
pin and records two warnings in every nx@v1 run's `run_manifest.json` (`compose.warnings`),
verified on all 9 nx@v1 composes. What it means for the nx runs:

- nx@v1 agents get **no `_shared/` IR execution guide** (IR semantics: references, selectors,
  frames, feature counting) and no single-solid rules; they rely on the NX skill's own IR type
  -> NX command table. Nothing in the composed nx@v1 skills or CLAUDE.md names those two files,
  so there is no dangling reference - the guidance is simply absent.
- **Confound in Track T:** the bare-L1 baseline `none@nx` DOES get `_shared/ir_execution.md`
  (compose copies it for every bare-L1 run, here from 846414c). So `nx@v1 - none@nx` would
  measure the NX skillset minus a document the baseline has, and `nx@v1` vs `build123d@v1` would
  compare skillsets that differ in shared content, not only in backend know-how.
- `nx/` itself is byte-identical between nx-v1 and build123d-v1 (`git diff --stat nx-v1
  build123d-v1 -- nx` is empty; nx-v1 is an ancestor of 846414c).

**Recommendation (owner action, not done here): move tag `nx-v1` to 846414c** (= build123d-v1).
Since `nx/` is unchanged, the id nx@v1 then names exactly what its own skillset.yaml lists, and
the warnings disappear; no run has used nx-v1 yet except the H8 nx_smoke replay. The
alternative `nx-v1.1` does not work as written: skillset ids must match `<backend>@v<integer>`
(expand.py `SKILLSET_RE`, compose `<b>-vN` tags), so a new tag would have to be `nx-v2` at
846414c and pilot.yaml would say `nx@v2`. `refs.skillset: {nx@v1: 846414c}` is refused by
compose while the tag exists elsewhere (by design). Run the pilot's NX half only after the tag
decision.

## 7. Pipeline check on the matrix (H9, no model, zero cost)

**Compose:** every distinct (track, strategy, skillset, backend, part) of pilot.yaml, 27 in all,
composed exactly as a worker would (expand -> `attach_seed_plans` from `RECADBENCH_GT_DIR` ->
`run.request_from_job` -> `compose`, real model ids, GT leak check on): **27 / 27 OK**. Checked
per run dir: harness H3; retk = all 7 tools for B / D (E and S), no retk for oracle (T); L1 MCP
build123d for build123d runs, none for nx (CLI) and S; Track T seed plan present (2 seed notes:
the world-axis selectors of hs01 LUG_HOLES_CBORE and dr02 HUB_BORE become `any`, as known);
pins strategy 6cc9798, build123d@v1 846414c, nx@v1 a21f3fa, none@b skills 846414c, data
6e72944; budgets 3600 s / 200 turns / $60. Warnings: the two nx-v1 `_shared` warnings on all 9
nx@v1 composes, and on the 6 Track E nx@v1 composes `system_prompt.md` > 300 KB (LiteLLM loop
only; the pilot uses the Agent SDK, which does not send that file).

**Replay end to end** (`recadbench run ... --agent-backend replay --score`, H3, same pins,
serial):

| Run | Agent | finish | result.json | Wall |
|---|---|---|---|---:|
| oracle x build123d@v1 x hs01 (`oracle_build123d`) | ok, 1 expected gate denial | export + extract ok | gate ok, **PASS, 81.70** (S1 40, S2 12.5, S3 20, S4 9.20, plan 100) - same as H8 | 100 s |
| oracle x build123d@v1 x dr02 | ok | export / extract fail: replay `part.py` raises "Face can only be created with closed wires" | gate fail "output.step: file not found", plan 100 | 24 s |
| oracle x build123d@v1 x hs04 | ok | same writer failure | gate fail, plan 100 | 19 s |
| oracle x nx@v1 x hs01 (`nx_smoke`, NX 2206) | ok, 5 expected gate denials | export 1 solid / 3 faces / 3141.59 mm3, extract count 1, unmapped 0 | gate fail on geometry (extents vs GT), as designed | 489 s |

dr02 / hs04: the replay oracle writer (`ir_build123d`) covers the hs01 plan constructs only; this
is the known test-harness limit from H8, not a pipeline blocker - compose, gate, agent loop,
finish's refusal path and scoring all ran; a real Track T agent writes its own `part.py`.

## 8. Before `--enqueue` (P11)

1. Decide the nx-v1 tag (section 6).
2. On every worker host: `git rev-parse HEAD` of ReCADBench-data = 6e72944 and (ASUS)
   ReCADBench-gt = 9f0142a; expand / compose do not enforce `pins:` (compose records
   `data_sha` in each manifest).
3. HANDOVER / runner README "Owner action required before unattended runs": separate Windows
   user without read access to ReCADBench-gt and the gate key dir, random
   `RECADBENCH_GATE_KEY_DIR`. (`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` is set by the runner
   since the H8 fix; the canary below is its first real confirmation.)
4. Start with a canary: one Sonnet Track T build123d@v1 run on hs01 (expected about $2), read
   its efficiency.json against the profile above, then enqueue the rest, NX lane first.
