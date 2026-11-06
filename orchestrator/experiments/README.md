# orchestrator/experiments

Experiment definitions `*.yaml`, expanded by `recadbench-orch expand` (P10). The pilot
(`pilot.yaml`) is written in H9 / P11.

```yaml
name: pilot                       # run-id prefix
parts: [r385_hs01_motor_mount]    # or  split: dev  (reads <data>/splits/dev.txt)
replicates: 1
harness_level: H3                 # optional; compose default H3
budgets: {max_turns: 200}         # optional overrides of task.yaml budgets
refs: {strategy: origin/main}     # optional git refs; skillsets default to tag <b>-vN
models:
  - {id: claude-opus-5-5, provider: anthropic, agent_backend: claude_agent_sdk}
  - {id: claude-sonnet-5-5, provider: anthropic, agent_backend: claude_agent_sdk}
tracks:
  T: {strategies: [oracle], skillsets: [build123d@v1, nx@v1, none@build123d]}
  E: {strategies: [B, D], skillsets: [build123d@v1]}
  S: {strategies: [B]}            # plan only
exclude:
  - {track: E, strategy: D, skillset: nx@v1}
```

Rules: Track T runs only `oracle`, on dev parts, with a CAD backend; Track S has no skillset or
backend; Track E never runs `oracle`. Breaking a rule is an error. `none@<backend>` is the
bare-L1 baseline. `recadbench-orch expand <file> --count` prints the run count by track,
backend, model and strategy.
