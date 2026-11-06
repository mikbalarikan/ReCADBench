# STATUS — recadbench (public)

Checklist of the session prompts (`ReCADBench/docs/architecture/06_SESSION_PROMPTS.md`) that touch this repo.
Update at the end of every session.

| Prompt | Scope in this repo | State |
|---|---|---|
| P1 | Scaffold all five repos, contracts copied to spec/, CI (ruff + pytest + check_public), token docs, Termux spike | done |
| P2 | recadbench-spec: models, taxonomy, validate CLI, tag spec-v0.1.0 | done — merged (#2); spec 0.2.0 via ADR 0002 (tag `spec-v0.2.0`), superseded by 0.3.0 (ADR 0004, tag `spec-v0.3.0`) |
| P3 | Backends build123d / solidcraft / freecad + conformance suite | done (#8); H1: conformance 10/10 on all three on windows too, MCP tool-list snapshots + CI check (PR h1-desktop-backends) |
| H3 | Spec 0.3.0 (ADR 0004): sketch refs, typed params, pattern_circular, features keys, denied_tools, 0.2.0 migration | done (#11, tag `spec-v0.3.0`) |
| P4 | NX backend (runner, adapter, replays) | done (H4, PR h4-nx-backend): `recadbench nx-run`, `recad nx` adapter, 10 replays, conformance 10/10 on NX 2206 (Windows), counts identical to build123d |
| P6 | register_gt.py + make_task.py (real scans, ADR 0002), AUTHORING.md, 3 R385 pilots | done: tooling + 3 R385 pilots (gt #3, data #3; migrated to 0.3.0 in gt #5 / data #4) |
| P7 | Evaluator + golden tests | done (H5, PR h5-evaluator): recadbench-eval 0.1.0, synthetic golden tests in CI, R385 pilot goldens on the evaluator host (9/9) |
| P10 | Runner, compose, gate_hook, orchestrator | done (H8 + review fixes + hardening round fix2: HMAC gate state, shared AST CAD code policy, GT-copy backstop; OS isolation = owner action, PR #13 h8-runner): `recadbench` CLI (compose / seed-plan / agent / finish / run / gate / nx-run), gate (backend.yaml denied_tools, journal-source + script-body matching, plan + N+1 rule), agent backends claude_agent_sdk / litellm_loop / replay, SQLite queue + HTTP front + pull worker, expand; dry run oracle x build123d@v1 x r385_hs01 -> result.json (PASS, 81.7/95, plan 100) |
| H9 | First-test plan: `orchestrator/experiments/pilot.yaml`, run count, cost / wall-time estimate | done (#14): 54 runs (T 24 / E 24 / S 6; 24 need the NX host), expected $299 (low $67, cap $1,435), ASUS about 8.6 h expected (NX lane bound) - `orchestrator/experiments/PILOT_ESTIMATE.md`; all 27 distinct composes OK; nx-v1 tag decision pending (owner) |
| P11 | Pilot + calibration, threshold ADR, spec-v1.0.0 | todo |
| P12 | Skillset improvement loop (reads results/) | recurring |

## Log
- 2026-11-07 - H9 (PR h9-pilot, first-test plan; no model run, replay backend only): `orchestrator/experiments/pilot.yaml` = 3 R385 dev pilots x {claude-opus-5-5, claude-sonnet-5-5} x 1 replicate: Track T oracle x {build123d@v1, nx@v1, none@build123d, none@nx}, Track E {B, D} x {build123d@v1, nx@v1}, Track S B plan-only. Harness level **H3** (one value per experiment; at H1 B / D lose the retk tools their stages call; oracle has no retk at any level), budgets = task.yaml (3600 s / 200 turns) + `max_cost_usd: 60` runaway guard; refs strategy 6cc9798, build123d@v1 = tag build123d-v1 (846414c), nx@v1 = tag nx-v1 (a21f3fa), none@ = 846414c; informational `pins:` spec-v0.3.0, data 6e72944, gt 9f0142a. `expand --count`: **54 runs** (T 24 / E 24 / S 6; oracle 24 / B 18 / D 12; build123d 24 / nx 24 / none 6; 27 per model), hand-checked (9 cells x 3 parts x 2 models) and pinned by `orchestrator/tests/test_pilot.py`. Estimate (`orchestrator/experiments/PILOT_ESTIMATE.md`, reproducible with `pilot_estimate.py`): list prices from platform.claude.com/docs/en/about-claude/pricing (Opus 5.5 $4 in / $8 1h write / $0.20 hit / $20 out; Sonnet 5.5 $2 / $4 / $0.10 / $10), per-turn cache model calibrated on the H8 smoke (37.9k 1-hour cache writes; CLI total 1.48x its usage at list price = subagent factor): **low $67 / expected $299 / cap $1,435** (Opus 45 / 199 / 957, Sonnet 22 / 100 / 478), hard guard 54 x $60; wall time on the ASUS (i7-14650HX 16 C / 24 T, 15.6 GB; N = 3 non-NX lanes + NX lane) **2.2 / 8.6 / 24.5 h**, NX-lane bound (24 NX runs serial); the phone (untested, H2 skipped) only shortens the non-NX half (3.2 -> 2.6 h). Pipeline check: all **27** distinct composes OK (H3, retk 7 tools for B / D, none for oracle, seeds present, pins as above; nx@v1: 2 `_shared` warnings x 9); replay oracle x build123d@v1: hs01 PASS 81.70 (= H8, 100 s), dr02 / hs04 replay-writer failure "Face can only be created with closed wires" -> finish export / extract fail -> result.json gate fail (known `ir_build123d` limit); nx_smoke x nx@v1 x hs01 on NX 2206: export 1 solid / 3 faces, extract 1, result.json (gate fail on geometry, as designed), 489 s (score about 450 s in the alignment fallback). Recommendation: move tag nx-v1 to 846414c (nx/ unchanged; adds the `_shared` files its skillset.yaml lists; removes the none@nx confound) or tag nx-v2 there - not done here. STOP: the owner starts P11.
- 2026-11-06 - H8 hardening round (fix2, verifier findings N1-N5 on 85b049c): text matching cannot confine Python, so the runner now layers independent, separately tested checks (runner README "Security model"). (1) **Gate state integrity** (N4): `gate_state.json` is authenticated by an HMAC-SHA256 under a per-run key (`runner/recadbench_run/gate_key.py`) in a runner-private key store (`RECADBENCH_GATE_KEY_DIR`, default `~/.recadbench-private/gate-keys`, random entry names, never in the run dir); the MAC lives with the key; the old derivable digest file is gone; the key-store location is blanked in `.mcp.json` for every MCP server, dropped by the dispatcher for servers and nx-run, stripped by `nx_run` before NX and by finish's `clean_env`; a lock in the key store keeps concurrent hook commits consistent; finish verifies. (2) **CAD code policy** (N2, N3, N5): `backends/recad_cli/code_policy.py`, an AST allow-list shared by the gate (build123d execute code, execute_file bodies, `cad/**` + unpinned `lib/**`; NX journals + every run-dir `.py`), `nx_run.check_journal` and the finish sandbox (`b3d_sandbox.py`, profile `build123d_native`, builtins without open / getattr / chr / bytes / real `__import__`, empty temp cwd; finish also runs export / extract from an empty cwd): allow-listed imports only (no io / os / sys / pathlib / shutil / glob / hashlib / zipfile / zipimport / importlib / operator / string ...), no exec / eval / compile / getattr / setattr / vars / globals / locals / chr / bytes, no `_` attributes, no frame / generator / code attributes, no file / import / export / process / journal verbs in attribute or imported names (`import_step`, `export_stl`, `Read_s`, `DexManager`, `Execute`, `Open`, `Save`, `WriteLine`), no `__` in string literals, no match / async / relative imports. Skillset files compose copied (`lib/`, `.claude/`) are trusted while their sha256 equals the MAC'd compose manifest `.recadbench/compose_files.json`. (3) **GT-copy backstop** (evaluator gate `gt_copy`): volume within 1e-3, face count within 5 % AND two-way P95 < 0.2 x max(scan floor P95, class tight band) = gate fail `suspected GT copy` outside Track T; always reported in `details.gt_copy`; golden / regression tests score with `gt_copy_check=False` (their candidates ARE gt.step) and new tests check the flag. (4) **Glob / Grep** (N1): `..`, `~`, drive, UNC, `//` or an absolute part anywhere in a Glob pattern / Grep glob is denied, `..` in any path field too; the dispatcher's own Glob / Grep never list outside the run. (5) **Writers** (N2): finish refuses when files appeared or changed outside `plan/ cad/ out/ work/` since compose (manifest; runtime files excepted). Verifier repros copied to `C:\Users\marya\rcb\work\fix2-h8` (paths only; `digest_path` import dropped): N1 Glob denied (3 forms) and dispatcher denied; N2 / N2b `import_step` / `export_step` denied in session and in part.py, finish refuses, no stray `_c.step`; N3 `import of 'operator'` denied at the gate and by finish, no marker file; N4 `import of 'io'` denied, N+1 rule holds, finish refuses; N5 `import of 'os'` denied by the gate and by `nx_run.check_journal`. Against 85b049c (`old_check.py`) the old code allowed 5/9 N1 globs, 8/8 N2 lines, 22/27 N3 gadgets at the gate and 11/27 in the sandbox, the N4 forge, 6/8 N5 journals at the gate and 8/8 in nx-run. Dry run oracle x build123d@v1 x r385_hs01 (replay): unchanged, PASS 81.695653 (S1 40, S2 12.5, S3 20, S4 9.195653, plan 100); `gt_copy` reported, not enforced (Track T): volume 6.6e-4, faces 96/38, P95 0.117 vs limit 0.039. GT-copy margins on the 3 pilots (golden = gt.step, Track E, default config): hs01 good / wrong_dim flagged (P95 0.0000 vs 0.039, faces 38/38, volume 8e-6 / 1.2e-4); dr02 good flagged (volume 2.6e-4, faces 83/83), dr02 wrong_dim NOT flagged (volume 1.3e-3, faces 84/83: a one-face edit of a copy escapes); hs04 good / wrong_dim flagged (limit 0.069). The replay oracle cannot build dr02 / hs04 at all ("Face can only be created with closed wires", same without the sandbox: an `ir_build123d` limit, not this round). NX 2206: conformance nx 10/10 + build123d 10/10, cross-backend identical; nx@v1 ex01-ex04 through gate + nx-run in a composed run dir: counts 5/4/4/3, volumes = examples README; NX host tests that run HARNESS journals (DexManager STEP import, NX Open UF, the replay helpers) now use `run_wrapped` directly like the adapter replays (the agent CLI denies exactly those means).
- 2026-11-06 - H8 fixer (review of #13): every blocker / major and the real-defect minors fixed, each with a regression test that fails on 5b26f8b (`runner/tests/test_review_h8.py`, `orchestrator/tests/test_review_h8_orch.py`, updated `test_frames.py`; 71 of the new tests fail on the old code, the rest are guards / mutation-gap tests). (1) **finish** (blocker): extracts only after `Gate.finish_check` (gate state is the gate's own, valid plan, the CAD source was executed through the gate and is unchanged since, native + importables pass the code checks); runs `python -I -m recad_cli` with cwd = a temp dir on a COPY of `cad/` + `lib/`, environment without secrets (`RECADBENCH_GT_DIR`, tokens, PYTHONPATH) and `RECAD_SANDBOX=1` (build123d adapter: `b3d_sandbox.py` AST check + restricted builtins + import allow-list; all 17 skillset examples and the oracle part.py give identical volumes with and without it); export first, extract last, each into a fresh folder, results written to `out/` from memory; `run_score` also `-I` from a temp cwd. (2) **Revision rule**: every `cad_prefixes.mutating` tool (execute, restore_snapshot, edit_feature, reset, ...) is held by N+1 after a source change; finish refuses a source changed after the last gated execution (NX: warning, the .prt is what the gated run saved). (3) **Writes**: allow-list `plan/ cad/ out/ work/` for file tools AND MCP write arguments (`gate_policy.yaml: write_args`: build123d export / script / render_view / bank_candidate / drawing tools, retk render `out_dir` incl. its `<mesh dir>/renders` default); `install_skill` blocked; gate state digest kept outside the run dir (`RECADBENCH_GATE_DIGEST_DIR`, default `<runs>/.recadbench-gate/`), a changed / deleted state closes CAD and finish. (4) **Paths**: path arguments checked whatever their length (cap 1024, no control chars). (5) **Code literals**: absolute / `~` / `..` paths outside the run, protected run-dir paths and GT names (`gt_name_pattern`) in code are denied (MCP code, execute_file bodies, NX journals). (6) NX journals: `builtins`, `runpy`, `compile(`, subprocess / ctypes / pickle / os.system etc. denied. (7) planted `recad_cli/` no longer shadows the adapter (-I, temp cwd, root not writable). (8) export can no longer rewrite features.json (temp copies, order, sha check). (9) worker: heartbeat retries transient errors, lease loss / claim errors no longer crash `once()`, claim retried with backoff, one run dir per attempt (`<run_id>.a<N>`). (10) queue: expired leases fail at max_attempts (`error = lease expired`), ROLLBACK only inside a transaction, `AND state='queued'` on claim, WAL, connections closed. (11) frames: sign-flipped axis swaps max/min, `:n` dropped under rotation (+ note). (12) compose: `--skillset-ref` must be the tag's commit when the tag exists (warning when it does not); expand `refs.skillset` is a per-skillset map; harness default H1 (04_STRATEGIES); replay runs labelled model/provider `replay` (other model refused, also in expand). (13) expand: unknown exclude keys / replicates < 1 refused. (14) every agent backend writes efficiency.json when it ends (two-step agent + finish keeps the counters). (15) built-in `nx_smoke` replay script; unknown script = clean CLI error. (16) system_prompt.md indexes skill `references/` instead of inlining them (warning above 300 KB). Reviewer repros re-run on copies in `C:\Users\marya\rcb\work\fix-h8`: all escapes now denied / refused. Dry run (replay, oracle x build123d@v1 846414c x r385_hs01, strategies origin/main 6cc9798): gate ok, PASS, total 81.70 (S1 40, S2 12.5, S3 20, S4 9.20, plan 100), labelled replay. NX smoke (nx@v1, NX 2206): gate allowed the journal, finish export 1 solid / 3 faces, extract 1, result.json written (gate fail on geometry, as expected).
- 2026-11-06 - H8 / P10 (PR h8-runner): `recadbench-run` 0.1.0 owns the `recadbench` CLI (compose, seed-plan, agent, finish, run, render-prompt, gate, nx-run forwarded to backends/nx/runner; backends no longer declares the script). compose per D6 (git-ref pinned content via `git archive`, strategy SKILL/playbook/stages/shared per the strategies compose contract, skills/agents/examples/lib/_shared, `.mcp.json` with retk = toolkit_tools x harness level and no retk for oracle, settings.json hook, schema-valid run_manifest, CLAUDE.md, system_prompt.md, GT leak check). Track T seed = gt_plan mapped with inv(T_scan_to_gt) (`frames.py`, taxonomy-type driven; world-axis selectors -> `any` + note), made on the evaluator host and shipped in the job payload. Gate: shell denied except allowed_commands, denied_tools read from the run's backend.yaml (cli: journal source via nx_run.journal_source over all run-dir .py; build123d execute_file bodies + cad/lib siblings matched as `execute`), runner scan-code patterns (mesh libs, OCP/VTK readers, input/), paths inside the run dir, protected files, plan before CAD, N+1 after a CAD-source change, no background tasks. Agents: claude_agent_sdk (in-process PreToolUse gate, project settings only, strict MCP, budgets), litellm_loop (gate before every dispatch), replay (scripted, zero cost). finish: recad extract + export in a clean process, hashes + native_sha256, optional score / enqueue. Orchestrator package `recadbench_orch`: SQLite queue (caps, per-host NX limit 1, leases, retries) + stdlib HTTP front + RemoteQueue, pull worker (Termux-safe imports, host lock, artifact upload, score jobs), expand (D4 rules, `--count`). Tests: runner + orchestrator 115 passed / 4 skipped (CI mode), 119 passed (host mode); full suite CI mode 615 passed, 19 skipped. Dry run oracle x build123d@v1 (846414c) x r385_hs01 via the replay agent: gate ok, PASS, total 81.70 / 95 (S1 40, S2 12.5, S3 20, S4 9.20), plan 100, p_tight 0.996. Claude smoke run (claude-sonnet-5-5, 40 turns / 20 min caps): 7 turns, $0.257, 45 s, gate fail (its build123d-modeler subagent ran in the background and the session ended first) -> fixed (see open questions), not re-run.
- 2026-11-06 - H5 fixer (review of #12): merged main (H4 #10; STATUS conflict resolved, both logs kept). Fixes, each with a regression test in `evaluator/tests/test_regressions.py` that fails on the old code (10 of 14; the other 4 pin 03_SCORING rules that 5 reviewer mutations used to survive: param-coverage halving, face-ratio halving, broken coaxial relation, standard-credit fallback, S1 in the PASS conjunction - all 5 mutants now killed): (1) gate b2: the single solid must have exactly one shell (internal voids / 0.15 mm hollow skin / 12 spherical voids on the pilots were PASS, now gate fail); (2) scale gate: vertex-PCA extents (tilted by tessellation density; zeroed a part with one small hole missing) replaced by the rotation-invariant volume scale `cbrt(V_pred/V_gt)` within 20% (also the volume sanity check; inch parts still fail "units?") plus a per-axis bbox check after alignment in the GT frame; (3) features.json read with utf-8-sig (PowerShell BOM), unreadable -> gate fail instead of a crash, validated against the spec `features` schema (forged `count` = gate fail, was PASS 82.5), and S2 N_pred = rows with `counted: true`; `face_count` vs the STEP is recorded in details only (adapters count native faces); (4) key-dim / relation faces must lie within max(1 mm, 3 x wide band_eff) of the GT near point (a missing hole is no longer measured on its twin 14 mm away); (5) S1 masks only GT samples as 03_SCORING says; every pred->GT sample is scored (material over unobserved GT was hidden: hs04 thicken 1.0 mm now S1 34.46, p 0.945). Not changed: PASS ignores topology (hs04 with two lug holes plugged under unobserved bore middles still PASS, S4 b1 4 vs 6) - rubric gap for P11; native_sha256 cross-check needs the P10 manifest. Rerun: ruff OK, check_public OK, pytest 503 passed / 15 skipped (no GT), evaluator suite with RECADBENCH_GT_DIR 46 passed; R385 pilot golden table below unchanged (same 18 rows, all gates/scores/PASS identical).
- 2026-11-06 — H5 / P7: **recadbench-eval 0.1.0** (`evaluator/`). Gate (load, 1 solid, valid, closed, faceted, volume-scale check vs GT (fixer: was principal extents), b2 one-shell check, features.json present + `unmapped` empty; aligned freeform cover-up for tier 1-2); alignment = `T_scan_to_gt` + trimmed point-to-plane ICP (fixed seed), 24-PCA fallback only when the 1 mm two-way F1 < 0.8; S1 two-way 50k samples, exact distances, observable region by nearest-neighbour lookup in `observability_mask.npz`, `band_eff = max(class band, k * floor p95)` (`k` = 1.5 in `EvalConfig`), inferred region reported; S2 from features.json (face-ratio + parametric-presence halvings, r > 3 caps the total at 40); S3 intrinsic key dims (nearest same-type face, axis within 2 deg), standard credit, coaxial / symmetry relations; S4 b1 (B-rep Euler characteristic) + face-type TVD; S5 behind `--s5` (reads `out/s5/edits.json`); plan score (Hungarian F1, key dims, datum rotated by T, count ratio, order); result.json validated against the spec schema, byte-identical on rerun; markdown/CSV report; CLI `recadbench-eval score|plan-score|report|golden`. GT only via `RECADBENCH_GT_DIR` (`gtstore.py`); no backend imports (tested). Golden tests: CI = 2 synthetic parts generated in-test (clean + scan rules with known T, floor, mask) x 6 candidates + unit tests (23 tests); evaluator host = 3 R385 pilots x 6 candidates (9 tests, ~2 min), results:

  | part | candidate | gate | S1 | S2 | S3 | S4 | total | PASS | p_tight / p_wide obs | p_tight / p_wide inferred | band_eff tight / wide |
  |---|---|---|---|---|---|---|---|---|---|---|---|
  | hs01 | good | ok | 40 | 25 | 20 | 10 | 95.0 | yes | 1.000 / 1.000 | 1.000 / 1.000 | 0.293 / 0.300 |
  | hs01 | wrong_dim (cbore_d 5.82 vs 5.85) | ok | 40 | 25 | 18.0 | 10 | 93.0 | yes* | 1.000 / 1.000 | 1.000 / 1.000 | 0.293 / 0.300 |
  | hs01 | snap_everything (6 dims snapped) | ok | 40 | 25 | 10.0 | 9.9 | 84.9 | no | 1.000 / 1.000 | 1.000 / 1.000 | 0.293 / 0.300 |
  | hs01 | lego (r = 4) | ok | 40 | 0 | 20 | 10 | 40.0 (capped) | no | 1.000 / 1.000 | 1.000 / 1.000 | 0.293 / 0.300 |
  | hs01 | faceted | FAIL | 0 | 0 | 0 | 0 | 0 | no | - | - | 0.293 / 0.300 |
  | hs01 | dumb_solid | FAIL | 0 | 0 | 0 | 0 | 0 | no | - | - | 0.293 / 0.300 |
  | dr02 | good | ok | 40 | 25 | 20 | 10 | 95.0 | yes | 1.000 / 1.000 | 1.000 / 1.000 | 0.302 / 0.302 |
  | dr02 | wrong_dim (pin_bore_d 2.17 vs 2.20) | ok | 40 | 25 | 18.0 | 10 | 93.0 | yes* | 1.000 / 1.000 | 1.000 / 1.000 | 0.302 / 0.302 |
  | dr02 | snap_everything (2 dims snapped, 5 not movable) | ok | 40 | 25 | 16.0 | 10 | 91.0 | no | 1.000 / 1.000 | 1.000 / 1.000 | 0.302 / 0.302 |
  | dr02 | lego | ok | 40 | 0 | 20 | 10 | 40.0 (capped) | no | 1.000 / 1.000 | 1.000 / 1.000 | 0.302 / 0.302 |
  | dr02 | faceted | FAIL | 0 | 0 | 0 | 0 | 0 | no | - | - | 0.302 / 0.302 |
  | dr02 | dumb_solid | FAIL | 0 | 0 | 0 | 0 | 0 | no | - | - | 0.302 / 0.302 |
  | hs04 | good | ok | 40 | 25 | 20 | 10 | 95.0 | yes | 1.000 / 1.000 | 1.000 / 1.000 | 0.519 / 0.519 |
  | hs04 | wrong_dim (boss_d 6.01 vs 6.04) | ok | 40 | 25 | 18.4 | 10 | 93.4 | yes* | 1.000 / 1.000 | 1.000 / 1.000 | 0.519 / 0.519 |
  | hs04 | snap_everything (6 dims snapped) | ok | 40 | 25 | 13.6 | 10 | 88.6 | no | 1.000 / 1.000 | 1.000 / 1.000 | 0.519 / 0.519 |
  | hs04 | lego | ok | 40 | 0 | 20 | 10 | 40.0 (capped) | no | 1.000 / 1.000 | 1.000 / 1.000 | 0.519 / 0.519 |
  | hs04 | faceted | FAIL | 0 | 0 | 0 | 0 | 0 | no | - | - | 0.519 / 0.519 |
  | hs04 | dumb_solid | FAIL | 0 | 0 | 0 | 0 | 0 | no | - | - | 0.519 / 0.519 |

  \* wrong_dim passes under the 03_SCORING PASS rule (one wrong key dim of 8-10 is >= 80%); see the open questions.
  Totals are out of 95 (S5 off). Candidates are generated in pytest temp dirs from gt.step (scanner frame via
  inv(T_scan_to_gt)) and never written to the repo.
- 2026-11-06 - H4 fixer 3 (review of #10): (1) `recadbench nx-run` deny check covered only cad/**/*.py, so a scan-reading module in lib/ (on the journal's sys.path) ran in NX unrefused (reproduced: volume 52x34x17 read from input/input.stl); now it checks every .py/.pyw anywhere in the run dir and in `RECADBENCH_NX_LIB`, refuses compiled / zipped modules there (.pyc .pyo .pyd .so .zip .egg .whl .pyz), purges `__pycache__` before each run and sets PYTHONDONTWRITEBYTECODE; the reviewer's repro is now refused before NX starts. (2) the deny matcher sees the journal decoded as Python decodes it (PEP 263 cookie, so utf-7 cannot hide code) with `#` comments removed (tokenize; CR line ends normalised; untokenizable source matched as is); docstrings / strings still count (reachable via `__doc__`); comment false positives (Convergent, FitSurface, input/) gone; backend.yaml comment says so. (3) `replays/_ir.load` rejects a `{"ref"}` inside a sketch or a list-valued param (the replays draw those as literals) instead of ignoring it. Regression tests fail on the old code (13 in test_nx_spec030, 9 nx cases in test_denied_tools). Skillset lib/nxhelpers + examples ex01-ex04 pass the wider check. Rerun: pytest 467 passed, 6 skipped; ruff + check_public OK; conformance nx 10/10 + build123d 10/10, counts identical (C01 1, C02 2, C03 2, C04 2, C05 3, C06 3, C07 3, C08 2, C09 3, C10 3), set-param ok.
- 2026-11-06 - H4 fixer 2 (NX backend on spec 0.3.0, after merging main): features.json validates against 0.3.0 (`bodies` = solid body names, was an int; `final_part`; suppressed rows -> `excluded`); replays read their 0.3.0 IR via the pure `replays/_ir.py` (parameters -> expressions, `{"ref"}` -> expression names, `pattern_circular` angle_deg = total sweep: 360 -> NX Span 360, partial -> Offset pitch angle/count, `pitch_deg` -> pitch; 0.3.0 errors enforced); backend.yaml `denied_tools` for nx (kind cli: `tool: recadbench nx-run`, args = {command, journal source}; NXOpen.Facet / faceted / convergent bodies, Fit Surface, STL/OBJ/PLY/3MF import, mesh libs, input/), enforced by `recadbench nx-run` itself before NX starts on the journal and every .py under cad/. Counter gap: measured on NX 2206 - BoundedPlane/SKIN, Sew/SEW, Thicken/THICKEN_SHEET, TrimSheet/TRIMMED_SH, ExtendSheet/EXTEND_SHEET, LawExtensionEx/LAWEXTENSION, TrimAndExtend/TRIM_AND_EXTEND stay unmapped with an explicit "not countable in v1" reason (no fitting taxonomy type; adding one needs an ADR); MirrorBody/MIRROR -> mirror (counted). Tests: CI taxonomy + _ir + deny-list + runner refusal tests, host NX tests (surface classes unmapped, Mirror Body + Unite count 2, pitch_deg partial pattern volume). Conformance nx 10/10 + build123d 10/10, counts identical (C01 1, C02 2, C03 2, C04 2, C05 3, C06 3, C07 3, C08 2, C09 3, C10 3), set-param ok; pytest 444 passed, 6 skipped; ruff + check_public OK.
- 2026-11-06 — H4 review fixes (NX backend): (1) boolean folding measures the body operand as the target's parent closure, not every earlier feature (unrelated earlier body gave NX 5 vs build123d 4; now 4 on NX 2206, CI + host test); (2) user expressions = `IsUserSpecified` (measured, survives reopen), so IR names like `p1_width` / `Pattern_pitch` stay in parameters[] (host test); (3) STL imported as a JT facet body (no feature row, no solid) is now unmapped via `part.FacetedBodies` without an associated body, `facet_bodies` in last_run.json / features.json (host tests Jt + Psm). Re-run: conformance nx 10/10 + build123d 10/10, counts identical (C01 1, C02 2, C03 2, C04 2, C05 3, C06 3, C07 3, C08 2, C09 3, C10 3), set-param ok; full suite 259 passed, 1 skipped (NX host); ruff + check_public OK.
- 2026-11-06 — H4/P4 NX backend (ASUS, NX 2206): `backends/nx/` runner (`recadbench nx-run cad/<j>.py` over run_journal.exe: part create/open, journal via runpy, save only on success = transaction per run, timeout + child-tree kill, host-wide NX lock for max_parallel 1, logs, syslog excerpt of this run, `cad/last_run.json` with the measured feature list), adapter (`recad nx export|extract|set-param|replay|version`; NX-side `nx_measure.py`, pure `nx_taxonomy.py` mapping measured on NX 2206; set-param on a copy; build123d STEP read-back in a child process), 10 replays (`_nxr.py`, self-contained), backend.yaml (cli, `allowed_commands: recadbench nx-run cad/*.py`, max_parallel 1). Conformance nx 10/10 + build123d 10/10 same run, counts/types identical, all errors < 3e-9, faces = expected. Unmapped proven on NX: Remove Parameters body, STEP-imported body (also when united into a parametric body), assembly components. `recad` routes nx (`RECAD_NX_PYTHON`); minimal `recadbench` console script (nx-run only) in the backends package. Tests: backends/nx/tests (9 taxonomy + 4 CI runner/adapter + 5 NX-host, skip without NX) in testpaths; full suite 253 passed, 1 skipped on the ASUS.
- 2026-11-06 — H3 review fixes: freecad `denied_tools` now covers `create_macro` (same args_regex as `execute_python`; the code is visible) and denies `run_macro` outright (list re-derived from freecad-robust-mcp 0.6.1 `freecad_mcp/tools/*.py`); FreeCAD / build123d code regexes are case-sensitive and import-shaped (a variable `points`, "mesh" in a comment, `export_stl` to an output path stay allowed), JSON-aware word boundaries, + `input/` path references; ADR 0004 says args_regex on free-form code is best-effort; +21 deny cases. Migration keeps a `{ref}` pitch binding (`pitch_deg: {ref}`), notes dropped bindings, wraps scalar `number_list` values and fails with a named error on other values the 0.3.0 typed params reject (docstring/ADR no longer claim pure widening).
- 2026-11-06 — H3 / ADR 0004, recadbench-spec **0.3.0**: `{"ref"}` for every sketch number; taxonomy `params: {name: type}` (12 types + enums, `$defs/ptype_*`, compiled IR schema type-checks them, semantic id resolution by type); `pattern_circular.angle_deg` = total sweep (step = angle/count, 360 = full circle) + new `pitch_deg`, `recadbench_spec.circular_offsets`; features.schema `not_in_final_part` / `excluded` / `final_part` / `bodies`; backend.yaml `denied_tools` [{tool prefix, args_regex?, reason}] filled for build123d / freecad / solidcraft; `recadbench_spec.migrate` + `recadbench-spec migrate` / `validate --migrate` (0.2.0 circular angle needs pitch|span|sweep). build123d execute-ir on 0.3.0 (sketch refs drive set-param, new pattern semantics). `dataset/tools/check_gt_patterns.py` + host-only test: all 6 pilot patterns match gt.step after migration (it caught a wrong HS04 pad seed, fixed in ReCADBench-gt). Re-validated: spec examples, 10 conformance IRs, 3 backend.yaml (strict), pilot gt_plan/meta/key_dims, data task.yaml. No contract files exist yet in -cad-skills / -strategies.
- 2026-11-06 — H1 fixer (review of PR #9): the FreeCAD pin was mis-described as "the one reproducible 26.3": conda-forge `freecad=2026.09.16` is upstream tag `weekly-2026.09.16` (a4ce44d33), a development snapshot older than 26.3rc1 (2026-10-08) / rc2 (2026-10-18); stable is 1.1.4. Recorded honestly in backend.yaml (`version: 26.3.0-dev`), install.md, READMEs, CONFORMANCE.md; pin kept, re-pin is an open question below. `mcp_tools.py`: a set but nonexistent `RECAD_*_MCP` / `RECAD_SOLIDCRAFT_CLI` / `RECAD_FREECAD_CMD|BRIDGE` now raises `MisconfiguredError` and the host-only live test fails instead of skipping; new check rule 5 (a `mutating` tool the server annotates `readOnlyHint=true`); synthetic tests for rule 4 (`cad_prefixes.all`) and rule 5, each failing on the old code / killing the rule-4 mutation. Reruns on windows: ruff clean, pytest 263 passed (venv, all three backend envs set), live MCP tools/list 3/3 match snapshots, freecad adapter tests 43 passed, conformance 30/30 identical, check_public OK.
- 2026-11-06 — H1 (P3 on the desktop, Windows 11 x64): conformance 30/30 through `recad` on windows (build123d 0.13.0 venv, FreeCAD = the pinned conda-forge `freecad=2026.09.16` win-64 build (weekly-2026.09.16 dev snapshot, reports 26.3.0; see the fixer line above), solidcraft-cli 0.30.1 release portable zip with sha256 matching SHA256SUMS.txt), volumes/counts identical to the Linux run; also FreeCAD 1.1.1 bundle 10/10 (not the pin). FreeCAD pin kept (corrected justification: same conda-forge build a4ce44d33 on both OSes; it is a pre-RC dev build, not 26.3). Adapter suites on windows: 142 + freecad 43 + solidcraft 40 pass. New `backends/conformance/mcp_tools.py` + `backends/<b>/mcp_tools.json` snapshots (build123d-mcp 48 tools, freecad-robust-mcp 0.6.1 83 tools, solidcraft 21 tools) with a CI check against `cad_prefixes.mutating` and a host-only live refresh test; found freecad `recompute_document` missing from `mutating` (added). Windows fixes: freecad tests find FreeCAD.pyd in conda `Library\bin`/bundle `bin` (skipped before), explicit UTF-8 reads in backend tests/runner, `.gitattributes` keeps snapshots LF under autocrlf, conda `freecadcmd.exe` needs the env DLL dirs on PATH (documented, handled by mcp_tools.py).
- 2026-11-06 — PR #8 adversarial review on the ASUS (Windows 11, build123d 0.13.0, FreeCAD 1.1.1): build123d extract fooled 4 ways (kernel handle swapped via setattr/`__dict__`, `insert()` of an untraced solid, `bounding_box(mode=ADD)`) -> now fingerprints shape handles + BuildPart parts and reports `unmapped`; freecad extract counted features of a shapeless scratch body (count 3 for a 1-pad part) -> counts the result body (+ Boolean tool bodies) only; adapters/run_conformance wrote CRLF -> LF; `recad --help` crashed on a cp437 console -> ASCII; last two brand names in ADR 0002 removed. Conformance on this host: build123d 10/10, freecad 10/10 (FreeCAD 1.1.1, not the pinned 26.3); solidcraft not installed. build123d-mcp 0.3.91 `tools/list` matches backend.yaml `mutating`.
- 2026-11-06 — Handover to a desktop main session: `docs/handover/` (HANDOVER.md state/decisions/rules/definition of done, PROMPTS_v2.md H0–H9, MAIN_SESSION_BRIEF.md); private companion `ReCADBench-gt/HANDOVER_PRIVATE.md`. Client names in current docs replaced by neutral ids GE-01…GE-05 (mapping private); they remain in git history (rewrite = owner decision).
- 2026-11-06 — P3: L1 backends build123d (0.13.0 + build123d-mcp 0.3.91, reference, `execute-ir`), freecad (FreeCAD 26.3 + freecad-robust-mcp 0.6.1, ADR 0003), solidcraft (solidcraft-cli 0.30.1): `backend.yaml`, install.md, adapters (export / extract / set-param / replay), 10 replays each, review fixes (dumb-solid fillet/mirror → unmapped, dead ops not counted, Boolean tool bodies, C09/C10 solidcraft replays, usage errors → one JSON). `recad` dispatcher (`backends/recad_cli`, package recadbench-backends 0.1.0): subprocess per call with the backend interpreter, features.json validation, needs_measure STEP measuring, `recad backends`. `backends/conformance/run_conformance.py`: 30/30 case×backend pass, cross-backend counts identical (CONFORMANCE.md). CI now installs ./backends + build123d 0.13.0 and runs backends/tests (28) + backends/build123d/tests (64); full suite 223 passed. FreeCAD (41) and SolidCraft (40) adapter tests pass locally, not in CI.
- 2026-11-06 — P3 completeness review: conformance rerun 30/30 (counts identical); count is derived (one extra fillet in a C10 copy → n 3→4 on all three backends, same volume 34349.98 mm³); `cad_prefixes.mutating` checked against live `tools/list` of build123d-mcp 0.3.91, solidcraft-cli 0.30.1 `mcp --headless` and freecad-robust-mcp 0.6.1. Fixed: solidcraft adapter usage errors now one JSON + exit 1 (was argparse exit 2), +4 tests (usage, added-fillet count); ADR 0003 test count 35→41; build123d backend.yaml note on mesh-analysis tools.
- 2026-11-06 — dataset tools on Windows: `make_task.py` / `register_gt.py` write LF (`newline="\n"`) so MANIFEST.json hashes of task.yaml survive git autocrlf; runtime messages are ASCII (the final summary `print` crashed on a cp1252 console). Tests pin both.
- 2026-11-06 — scanreg: robust shrinking ICP trim + coarse ICP after PCA (raw scans that include the scanner table / fixtures), KD-tree prefilter for far points (was pathologically slow), floor excludes extraneous scan geometry (> 1.5 mm, reported separately). Desktop survey: R385 scans and CAD are in different frames (no saved transform) → `--init pca`; pilots HS01, DR02, HS04.
- 2026-11-05 — P6 tooling: `dataset/tools/scanreg.py` (PCA coarse + trimmed point-to-plane ICP, floor, observability with open-boundary/opposite-normal masks), `register_gt.py`, `make_task.py` (byte-identical scan copy, agent-visible metadata only, MANIFEST + splits); 13 synthetic tests (ICP 4 µm in 6 iterations). `recadbench-gt/AUTHORING.md` written.
- 2026-11-05 — ADR 0002: inputs are REAL SCANS (scanner frame); spec 0.2.0 (task `input_kind` + scan metadata; gt_meta provenance/contamination, registration, scan floor, observability; result inferred-region metrics; test-split eligibility rules); architecture docs, P6/P7 prompts and roadmap updated. v1 = scan-rebuild parts, dev only.
- 2026-11-05 — P1 Termux spike PASSED on a phone (Xiaomi 23117RA68G, Android 13, aarch64): STEP export via build123d-mcp, also via `proot-distro` stdio from the Termux side. Details: `backends/build123d/install.md`.
- 2026-11-05 — P2: recadbench-spec 0.1.0 — pydantic models for all 10 schemas, taxonomy loader + counting rule, taxonomy-compiled IR schema (type enum, per-type params, needs_sketch), semantic rules, `recadbench-spec validate|schemas|taxonomy|compile-schema`, 101 tests (1 valid + 2 invalid examples per schema, round-trip, model↔schema drift). ADR 0001 fixes the IR `params` oneOf bug.
- 2026-11-05 — P1 Termux spike: PoCAD-method recipe + build123d-mcp 0.3.91 verified on x86_64 Ubuntu 24.04 (STEP export via MCP OK). Phone run still pending.
- 2026-11-05 — P1: scaffold created per 02_REPO_LAYOUTS.md. No logic.

## Open questions

### From H9 (first-test plan)
- **nx-v1 tag** (owner decision before the NX half of the pilot; supersedes the H8 item below):
  nx-v1 = a21f3fa lacks `_shared/ir_execution.md` + `_shared/single_solid_rules.md`, which
  `nx/skillset.yaml` lists; all 9 nx@v1 pilot composes record the two warnings. The bare-L1
  `none@nx` baseline DOES get `_shared/ir_execution.md`, so Track T `nx@v1 - none@nx` is
  confounded. `nx/` is identical at nx-v1 and 846414c (nx-v1 is its ancestor). Recommended:
  move `nx-v1` to 846414c (= build123d-v1). `nx-v1.1` is not addressable (skillset ids are
  `<b>@v<int>`, tags `<b>-v<int>`); the alternative is `nx-v2` at 846414c + `nx@v2` in
  pilot.yaml. Not moved here.
- **Harness level for the pilot = H3** (pilot.yaml, documented there): harness_level is one
  value per experiment, and at H1 B / D lose slice / fit_primitives / detect_holes /
  snap_nominal / render that their stages call (the degenerate ablation of 04_STRATEGIES).
  Oracle has no retk at any level, so Track T is unaffected. The runner default stays H1.
- **NX lane is the pilot's critical path**: 24 NX runs serialised as whole sessions (queue limit
  nx = 1 + worker host lock) = about 8.6 h expected of the 8.6 h total; `nx_run` already
  serialises the journals themselves (`RECADBENCH_NX_LOCK`). Allowing 2-3 concurrent NX
  sessions with journals still serial would roughly halve the pilot, but lock waits would eat
  `max_wall_s` of the waiting run (fairness): keep 1 for the pilot, decide for P11+ (D9 says one
  licence => one NX process, not one session).
- **Cost accounting**: the H8 smoke's CLI `total_cost_usd` ($0.257) is 1.48x its main-session
  usage at list price ($0.174); most likely the backgrounded subagent's tokens. `max_cost_usd`
  and efficiency.json use the CLI total (includes subagents); usage token counts do not. The
  estimate uses a subagent factor 1.3 (expected) / 1.5 (cap). Confirm on the canary run.
- **`max_cost_usd: 60` in pilot.yaml** is a runaway guard (subagent turns are not counted by
  `max_turns`), above the analytic per-run ceiling ($40 Opus Track E). Owner may drop or lower it.
- **Data / GT pins are not enforced**: pilot.yaml `pins:` (data 6e72944, gt 9f0142a, spec-v0.3.0)
  is informational; expand / compose ignore it (compose records `data_sha`). A worker-side check
  (refuse when `data_sha` differs from the experiment pin) would make it binding.
- **Phone worker untested** (H2 skipped): the estimate does not rely on it; it would only shorten
  the non-NX half (3.2 h -> 2.6 h expected).
- **Code policy residual (H8 verifier, minor)**: `str.format` / `format_map` resolve attribute
  and index fields at run time, so a format string whose field names are built at run time
  (no `__` literal, no `_` attribute in the source) can read attributes the AST policy forbids
  (`"{0.<attr>}".format(obj)`). Read-only information disclosure (no call, no write); the AST
  check cannot see it. Close by denying `.format` / `.format_map` on non-literal receivers, or
  by a format-field check on literal format strings, or accept and document (OS isolation is the
  real boundary, runner README "Security model").
- **Replay oracle writer limit (unchanged)**: `ir_build123d` builds hs01 only; dr02 / hs04 fail in
  the replay `cad/part.py` with "Face can only be created with closed wires" (finish: export /
  extract fail, result.json gate fail "output.step: file not found"). A test-harness limit, not
  a pipeline blocker; real Track T runs write their own part.py.

### From H8 (P10 runner)
- **Selectors with a world axis cannot be mapped** into the scanner frame (`BODY:plane:+Z:max`
  in hs01, `HUB:plane:-Z:min` in dr02): the seed keeps feature + face type, direction/extremum
  become `any` and `evidence.note` carries the mapped vector. A datum-relative selector form
  (e.g. `<F>:plane:D1+:max`) needs an ADR + spec bump (H7 raised the same for strategies).
- ~~**nx-v1 has no `_shared/`**~~: see "From H9" (confound with none@nx, tag recommendation).
- **run_manifest `agent_backend` enum** has no `replay`: replay runs omit the key and record
  `agent_driver: replay` (extra keys are allowed). Add `replay` in the next spec bump?
- **Harness level default** (H8 fixer): now `H1` as 04_STRATEGIES says (B/D get mesh_stats +
  self_deviation unless the experiment sets `harness_level: H3`). Revisit in P11 if B/D need H3
  by default (decision-log change). H9: pilot.yaml sets H3 (see "From H9").
- **Revision rule semantics** (implemented, confirm): only calls that execute the CAD source
  (`execute_file`, `nx-run`) record (N, sha256 of `cad/*.py`); a changed source then needs N+1,
  even after a failed first execution (matches the oracle playbook's "re-execute after <CAD
  error>" copy); every `cad_prefixes.mutating` tool is held by the same rule (H8 fixer), read-only
  tools need only a valid plan. Session code itself (`execute`) is not fingerprinted: it is not
  graded, finish exports `cad/part.py` only if it is exactly what the last gated execution ran.
  The gate records an execution when it ALLOWS the call (also when the tool then fails). FreeCAD /
  SolidCraft keep the model in the session (no `cad/` source), so the N+1 rule never triggers
  there yet and finish requires no gated execution for them.
- **GT isolation needs the OS** (H8 fixer; hardening round fix2): the layers are now gate +
  shared AST CAD code policy (gate, `nx-run`, finish sandbox) + HMAC'd gate state + finish's
  foreign-file check + the evaluator's GT-copy backstop (runner README "Security model"). Still
  defence in depth, not isolation: the CAD libraries' own APIs (build123d, OCP, NX Open) are too
  large to audit by name, and escaped code runs as the same OS user (can read the gate key
  store and the GT store). **Owner action before unattended runs**: run the agent session, its
  MCP servers and NX as a separate Windows user without read access to `ReCADBench-gt` and to
  `RECADBENCH_GATE_KEY_DIR` (or keep GT off agent hosts), and set `RECADBENCH_GATE_KEY_DIR` to
  a random runner-private folder. Not done by the runner (OS security settings are the
  owner's job).
- **GT-copy backstop false positives** (fix2): a perfect design-intent rebuild of a simple part
  whose B-rep matches the GT face for face (volume 1e-3, faces 5 %, two-way P95 < 0.2 x floor)
  is indistinguishable from a copy and fails the gate in Track E (not in Track T). None of the
  3 pilots' replay-oracle runs comes close on faces / P95 (margins in the log); review any
  `suspected GT copy` by hand before P11 numbers are published, and revisit the thresholds in
  the P11 calibration ADR.
- **CAD code policy vs. agent habits** (fix2): agent code may not use `os` / `sys` / `pathlib` /
  `open` / `getattr` / `operator` / `string`, `_` attributes, dunder strings (also in
  docstrings), relative imports, `match`, build123d import / export functions or NX Open
  `Open` / `Save` / `Execute` / `ListingWindow.WriteLine` (journals print instead). The 17
  build123d examples, ex01-ex04 and the replay part.py pass; real agents may trip on it (the
  denial reason names the construct). Watch the first P11 runs' `gate_log.jsonl`
  (`category: code_policy`).
- **retk path policy** (H8 fixer, not fixed here): retk-server (strategies repo) has no run-dir
  root; the gate checks its path arguments. A `_paths.py`-style root in retk is a strategies-repo
  change.
- **Copied run dirs** (H8 fixer; fix2): the gate key is looked up by the run dir's absolute
  path; a copied or moved run dir has no key and counts as tampered (compose creates a new one).
  The hook command in `.claude/settings.json` also names the absolute run dir. finish refuses
  any file that appeared or changed outside `plan/ cad/ out/ work/` since compose (runtime files
  excepted: gate state / log, transcript, run_job, efficiency, result, run_manifest as the last
  finish wrote it); a tool that drops files elsewhere in the run dir (a future Claude Code
  version writing `.claude/settings.local.json`, say) would make finish refuse - add it to
  `gate_hook.RUNTIME_FILES` then.
- **Replay oracle is a pipeline check, not a Track T ceiling** (H8 fixer): on hs01 it scores
  81.70 vs 95.0 for the GT-good golden because `ir_build123d` fillets every convex / concave
  edge of the coarse GT selectors (`BODY:edges:convex`, `LUG_PATTERN:edges:concave`): 96 faces vs
  38 (torus 30 vs 3, bspline 19 vs 4) -> S2 halved by `face_ratio` 2.53 (12.5 of the 13.3-point
  gap) and S4 face-type TVD 0.134 (0.8). Runner and evaluator are not the cause.
- **`--model` default**: compose records `claude-sonnet-5-5` / `anthropic` unless given; replay is
  always `replay` / `replay`. result.json still has no agent-driver field (schema change: ADR).
- **NX `--timeout`**: the gate accepts `recadbench nx-run cad/*.py --timeout N` (the nx skill
  teaches it) although `allowed_commands` lists only the bare form; `--part` is refused.
- **Residual scan access** stays best-effort: the regexes (ADR 0004) miss runtime-built names;
  since fix2 the CAD code policy removes the generic means (open, os, importlib, getattr, CAD
  import functions), but the input dir is not hidden from the CAD process (OS isolation).
- **Claude Code subagents run in the background by default** (CLI 2.x): the H8 smoke run ended
  while its modeler subagent was still working (no cad/part.py, gate fail, $0.26). Fixed after
  the run, not re-run (one smoke run allowed): `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`, the gate
  denies `run_in_background`, the SDK loop waits for active tasks, the prompt says so. First P11
  run must confirm.
- **Token accounting**: `tokens_in` = input + cache-creation tokens (cache reads separately as
  `cache_read_tokens`); budgets check live per-message counts, efficiency.json takes the CLI's
  session totals. `max_tokens` therefore ignores cache reads. Confirm.
- **GT exposure on the evaluator host**: agents run with the GT store on the same disk; the gate
  denies any path outside the run dir and the agent env drops `RECADBENCH_GT_DIR`, but the Claude
  CLI itself (not its tools) is not sandboxed. Run P11 agents on hosts without the GT clone where
  possible.
- **replay `ir_build123d`** fillets coarse GT edge sets (`BODY:edges:convex`) on the subset the
  kernel accepts; on hs01 SHOULDER_IN_R (same selector as BASE_INNER_R) finds no edges. A
  reference executor that resolves such sets is not part of v1.
- ~~gate_hook for kind cli~~ / ~~recadbench CLI name~~ / ~~gt_plan frame~~ / ~~NX gate surface
  (journal review)~~: answered by H8 (see the log).
- ~~NX surface features~~ (H4 fixer 2): measured and kept unmapped with the reason "not countable in v1"; the cad-skills nx skills say so. Counting them needs a taxonomy type (e.g. a `thicken` / `sew` or surface type) = an ADR + spec bump. Other sheet tools (Ruled class `Ruled`/SKIN, Studio / Section / N-Sided Surface, Fill, Bridge, Offset Surface, Enlarge, Untrim, Delete Edge, Patch) are unknown classes -> unmapped too; Ruled is arguably a `loft` but was not mapped (not measured here).
- ~~Mirror Body~~: measured `MirrorBody` / MIRROR, mapped to `mirror`.
- ~~gate_hook for kind cli~~ (answered H8: the gate builds `{"command", "journal"}` with `nx_run.journal_source` over the journal and every .py of the run dir; nx-run re-checks) (P10): the nx deny entry needs the journal SOURCE as `journal` in the matcher arguments (the command line only names the file), as `nx_run.journal_source(bytes)` gives it (comments removed); `recadbench nx-run` already enforces the list itself on every .py of the run dir and lib/, so gate_hook may rely on that or re-check. Journal code that builds names / paths at runtime or exec()s a non-.py file is not caught (best-effort, like the MCP code regexes); hiding `input/` from the NX process (P10) would close that. Compose could also re-copy lib/ from the skillset before each run.

### From H5 (P7 evaluator)
- **wrong_dim PASSes** on all three pilots and on the synthetic parts: one key dim off of 8-10 keeps
  `key_dims_ok` >= 80%, which the 03_SCORING PASS rule accepts. The S3 and total ordering is as expected. The tests
  assert the rule (PASS iff >= 80%) rather than "only good passes". Should PASS require all key dims (or all
  `editable`/critical dims)? That would change the scoring rubric (P11 / threshold ADR).
- **Key-dim tolerance limit:** |err| <= tol counts as within, so a 9.98 vs 10.00 error (exactly 0.02, class B) passes.
  The golden wrong_dim uses 1.5 x tol (0.03). Confirm the inclusive reading.
- **No pilot key dim has `standard: true`.** The 4 standard-value points follow the key-dim fraction, so the
  snap_everything penalty comes only from key-dim credit. Mark the standard dims during GT authoring.
- **HS04 floor dominates the bands:** 1.5 x 0.346 = 0.519 mm is wider than the class B wide band (0.30), so tight = wide
  there, and the same holds for DR02 (0.302). Calibrate `k` in P11.
- **Scale gate uses the GT, not the scan:** sorted principal extents vs gt.step (raw scans carry stray patches).
  03_SCORING says "STL bbox". Confirm.
- **Golden edits on DR02:** the sphere / partial-cylinder / torus faces (dome_r, plate_t, arm_end_r, web_r) cannot be
  moved by the boolean face-offset tool, so snap_everything snaps only 2 dims there (still FAIL at 0.75). The HS01 faceted
  candidate loses its solid in the STEP round trip (gate fails on solids/closed and faceted together).
- **Run dir contract** (`out/output.step`, `out/features.json`, `plan/re_plan.vN.json`, `out/s5/edits.json`,
  `efficiency.json`) is defined in `evaluator/recadbench_eval/README.md`. P10 `finish` must produce it.
- **CI time:** the evaluator + spec tests take about 2 min on the desktop (B-rep booleans plus 20k-sample scoring).
  Mark them slow if CI grows.

### From H1 (P3 on the desktop)
- **FreeCAD pin is a pre-RC development build** (main session to decide): conda-forge `freecad=2026.09.16` = upstream `weekly-2026.09.16` (git a4ce44d33, reports 26.3.0 dev), three weeks older than 26.3rc1; not a release. Options: keep it (same build on linux-64 + win-64, both conformance runs), re-pin to 26.3rc2, to stable 1.1.4 (2026-09-28; only 1.1.1 was tested, 10/10), or to 26.3 final once released. Any re-pin: rerun conformance + `mcp_tools.py refresh` on both hosts. ADR 0003 still says "FreeCAD 26.3" for the same build (historical wording, not edited).
- **freecad-robust-mcp 0.6.1 needs a running bridge even for `tools/list`** (it connects during initialize and exits otherwise). The runner must start the bridge before the agent's MCP client; the snapshot refresh starts a private one.
- **freecad-robust-mcp 0.6.1 has no tool annotations**: the 31 non-mutating tools are listed by hand in `backends/freecad/mcp_tools.json` `not_mutating`. Any new tool after a pin bump fails the check until classified. Its serverInfo reports `freecad-mcp 1.30.0` (the mcp library version), not 0.6.1.
- **Stale SolidCraft 0.19.0** is installed in Program Files on the evaluator host and is not on PATH; workers must set `RECAD_SOLIDCRAFT_CLI` to the 0.30.1 binary explicitly.
- **git autocrlf=true** on the Windows host: working-tree files are CRLF. Only the MCP snapshots are forced to LF (`.gitattributes`); a repo-wide `* text=auto eol=lf` would make Windows checkouts byte-equal to Linux ones (owner decision, touches every file).

### From P3 (backends)
- ~~CAD MCPs expose RE analysis~~ → ADR 0004 `backend.yaml: denied_tools`. **P10 gate_hook must
  implement the matcher** (name prefix + `re.search(args_regex, json.dumps(args))`, see
  `backends/tests/test_denied_tools.py`). **args_regex on free-form code is best-effort**: code
  that builds module names / paths at runtime (e.g. `getattr(b, 'import_' + 'stl')` on a computed
  path) and build123d `execute_file` script bodies are not caught. Residual-risk options for P10:
  hide `input/` from the CAD process (only retk reads the scan), and/or fail a run whose
  `extract` reports imported shells as `unmapped` / whose session holds Mesh::/Points:: objects.
  FreeCAD `run_macro` is denied outright. The solidcraft list was not checked against a live
  `tools/list` on this host (not installed).
- **SolidCraft on Termux ARM64 is untested.** `termux` is not in solidcraft `hosts`; the test steps
  are in `backends/solidcraft/install.md` ("Termux test"). Run them on the phone before listing it.
- **build123d on Termux stays on 0.10.0** (PoCAD route, install.md B) while the desktop/CI pin is
  0.13.0. The Termux worker can run the agent, but adapter calls need a route-A host. Conformance
  ran only on 0.13.0. Decided for P3; revisit if aarch64 OCP 8 wheels work on the phone.
- **FreeCAD MCP timeout:** with the 0.6.1 pin, `FREECAD_TIMEOUT_MS` has no effect, and
  `execute_python` allows 30 s per call unless the call passes `timeout_ms` (wired upstream from
  0.7.0-rc.3). The recadbench-cad-skills freecad skillset must tell agents to pass `timeout_ms` on long calls.
- **CI Python 3.11 vs local 3.13:** the build123d adapter suite ran on 3.13.18 here. CI uses 3.11 with
  `build123d==0.13.0`; the first CI run is the first 3.11 check.
- **FreeCAD / SolidCraft adapter tests are not in CI** (no FreeCAD / CLI on the runner). They skip
  without their environment. Conformance for those two must be rerun by hand on each pin bump
  (`run_conformance.py`). Add a CI job with conda-forge FreeCAD + a cached solidcraft build?
- **`needs_measure` path is unused** by the current adapters (all three report volumes). It is
  tested with a fake adapter only.
- **Non-editable `pip install ./backends`** finds the adapters only from a checkout's `./backends`
  (cwd search) or `RECAD_BACKENDS_DIR`. CI and docs use `-e`.
- ~~NX (P4)~~ (answered H8: the runner owns `recadbench` and forwards `nx-run`; backends no longer declares the script): `recadbench` console script now lives in the backends package with only `nx-run`;
  P10's runner CLI must take over the `recadbench` name and forward `nx-run` (or import
  `nx_run.main`), otherwise two packages define the same entry point.
- **NX gate surface (P10)** (H8: journals + run-dir .py are matched against denied_tools and the runner scan-code patterns, `open(` on input/ and mesh libs denied; runtime-built names remain best-effort): `allowed_commands` is `recadbench nx-run cad/*.py`, but a journal can
  still call any NX Open API (STEP import, file I/O, os/subprocess). The counter reports imports
  and dumb bodies as unmapped (gate fail), yet a journal could also read the scan from disk and
  analyse it in Python. gate_hook / compose should review journals (deny `import_*` importers,
  `open(` on input/, subprocess) or run NX with a restricted working directory.
- **NX feature types not in the taxonomy** (Trim / Split Body, Extract, Move Object, synchronous
  edits, Spiral / Along / General pattern layouts) are unmapped by design (gate fail). If dev
  runs show agents need Trim Body, that is a taxonomy change (ADR).
- **NX boolean folding** uses `Feature.GetParents()` (target first, measured on 2206) and the
  ancestry of the tool; only Unite/Subtract of primitives and the C09 mirror unite were checked on
  NX. Multi-feature tool bodies are covered by synthetic tests only.
- **NX conformance is Windows/host-only** (no NX in CI); rerun by hand on NX updates. A full run
  is 30 NX processes, ~15 min with the lock.
- Unsupported / unmapped by design (gate fail): direct OCP calls and custom `BasePartObject`s
  (build123d), `Part::*` / imported shapes / binders (freecad); see each backend's mapping table.

### From ADR 0002 (real scans)
- **Golden-example scans over 100 MB** (GE-02 209 MB, GE-04 250 MB; GE-03 92 MB) cannot go in plain git. Options: Git LFS for those files only, Drive, or a lossless compressed copy. Decide before adding them (R385 pilots are all < 40 MB).
- R385 folder also holds **downloaded public RS-385 CAD** → R385 meta: `public_cad: true`.
- Dropped from the v1 pool: **M02** (its CAD was not fitted to the scan; scan is a segment of a client scan), **R385 SL02** (no scan), **R385 HS03** as a pilot (90° merge error between its two scan passes). **OD-H12** stays calibration-only (fused assembly, guessed interior, gate failed).
- Decided: golden examples **cleared** (v1 dev pool, ADR 0002 table); **open-dedica kept aside** (later: calibrated reference STEPs once agent rebuilds deviate less).
- M02-02-03 gear: is `M02-02-03.STL` a real scan? (provenance of the mesh)
- Full-res originals of the golden-example scans are on Drive (`H:\My Drive\…`, see each MANIFEST.json `source_path`) — P6 must use those, not the decimated copies.
- Decided: **scan storage = plain git** (no LFS) for v1 — scans < 100 MB/file, dev pool < 1 GB; LFS free bandwidth (1 GB/month) would not survive several workers. Revisit at M5.
- ~~gt_plan.json frame~~ (answered H8: `recadbench seed-plan` maps it with inv(T_scan_to_gt), `frames.py`, round-trip tested): authored in the gt.step frame (AUTHORING.md §6); P10 compose must map it into the scanner frame with inv(T_scan_to_gt) before Track T hands it to an agent.
- **Tier definitions** used in AUTHORING.md are still provisional (threads alone ≠ tier 3) — confirm in P11.
- **`k` for the scan-floor band widening** (1.5 provisional) and the observability threshold — calibrate in P11.
- **recadbench-data / recadbench-gt READMEs** still describe tessellated inputs — update in P6.
- **No frozen test split** until `print_scan` (OGUZ print + scan) parts exist.

### From H3 (spec 0.3.0, ADR 0004)
- **Tag + pins:** after the H3 PRs merge, the main session tags `spec-v0.3.0` from the desktop and bumps
  the pins (checklist in the PR body). No downstream pyproject pins `spec-v0.2.0` today.
- **Scanner scale never verified** against a reference artifact (gauge block / calibrated sphere) —
  physical step, not done; affects every scan-rebuild GT floor.
- **IR has no expressions** (`center_y = shaft_len / 2`), so a ref'd rect grows about its centre; C04
  keeps the `sketch.constraints` binding. Hole `positions` (vec3) cannot be refs.
- **Pilot sketch dims are not bound yet** (75 unused-parameter warnings over the 3 pilots): GT authoring
  pass with the new sketch refs (needed for S5 on pilots).
- **retk ↔ IR:** `detect_holes.hole_groups` (polar groups) must be mapped to `pattern_circular` with the
  0.3.0 meaning (total sweep / pitch_deg) in the P9 strategies.

### From P2 (spec)
- ~~Sketch dimensions can't be parameters~~ / ~~param values are untyped~~ → ADR 0004 (spec 0.3.0).
- **Semantic rules are stricter than the schemas** in a few places (listed in `spec/README.md`), e.g.
  Track T requires `split: dev` (the schema only checks it when present), and refs must point to
  *earlier* features. Confirm these match intent.
- `docs/architecture/contracts/` mirrors `spec/` by hand; keep or drop the mirror?

### From P1
- **Pin choice for P3** (decided in P3: desktop/CI build123d 0.13.0, Termux stays 0.10.0): build123d-mcp works with build123d 0.10 only if `bd_warehouse==0.2.0` is pinned
  (0.3+/0.4 need build123d ≥0.11.1/≥0.12). PyPI `cadquery-ocp-novtk` 7.9/8.0 now has aarch64 wheels, so a
  shim-free build123d 0.13 path may be possible on phones. Decide in P3 (`backend.yaml`).
- **evaluator/golden vs data hygiene:** P7 builds golden candidates from the 3 *pilot GT parts*, but D3 says no
  real-part STEP/GT in this repo and `check_public.py` allows STEP in `evaluator/golden/` as "synthetic only".
  Either golden parts must be synthetic stand-ins, or the golden tests read pilot GT via `RECADBENCH_GT_DIR`
  on the evaluator host. Decide before P7.
- **Write access for make_task.py:** D3 defines only read-only tokens, but `make_task.py` on the evaluator host
  writes to `recadbench-data`. Who pushes, with which credential (operator interactive vs a separate
  Contents: read-write token scoped to `recadbench-data` only)?
- **Unassigned folders:** `leaderboard/build.py` is in the layout but in no prompt of 06_SESSION_PROMPTS.
- **check_public coverage:** D3 lists STL/STEP/STP/PRT + gt_plan.json/key_dims.yaml. The guard additionally
  rejects `gt.step`/`gt_native.*`. Other CAD/mesh formats (`.3mf`, `.obj`, `.fcstd`, `.sldprt`, `.x_t`, `.igs`,
  `.iges`) are *not* rejected — extend?
- **Credential helper scope:** the README's helper applies to all of github.com on a host; fine for dedicated
  workers, but a shared dev machine may want a path-scoped helper (`credential.useHttpPath`).

- **Placeholder content files not created.** Files whose content is authored by later prompts and validated
  against a schema (`backend.yaml`, `skillset.yaml`, `strategy.yaml`, `SKILL.md`, agent `.md`, `LESSONS.md`,
  `MIGRATION_NOTES.md`, `AUTHORING.md`, `MANIFEST.json`, splits) were *not* stubbed — an empty stub would fail
  validation or be loaded by compose. Each folder's README.md lists the expected files and the filling prompt.
- **Prompt-number inconsistencies in the plan:** 00_DECISIONS D1 and 05_TOOLS say the FreeCAD MCP is selected
  in P4, but 06_SESSION_PROMPTS assigns it to P3; 05_TOOLS "NX skillset seed (P3 ports…)" and the
  02_REPO_LAYOUTS `MIGRATION_NOTES.md` comment say P3, but the NX port is P4. READMEs follow 06_SESSION_PROMPTS.
