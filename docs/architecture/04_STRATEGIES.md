# 04 — Strategies (seed definitions for `recadbench-strategies`)

All strategies are CAD-tool-agnostic, share `shared/ir_authoring.md` + `shared/design_intent_rules.md`, and must produce a schema-valid `re_plan.v1.json` before any CAD call. They differ in **how the plan is derived** and **how verification loops back**. Each maps to an observable industrial or research practice so results are interpretable.

| ID | Name | Core idea | RE toolkit allowed | Analogue |
|---|---|---|---|---|
| oracle | Oracle | GT plan injected; no analysis | none | Track T control |
| A | Direct | Look at stats + renders, write the plan in one go, execute, single self-check | mesh_stats, render, self_deviation | Naive baseline |
| B | Region segmentation | Segment mesh into primitive regions → datums from functional regions → map regions to features → snap → execute → deviation loop | all | Geomagic Design X / NX RE workflow |
| C | Section-driven | Choose datum CS → slice along principal axes → fit 2D profiles (lines/arcs) → classify as extrude/revolve/pocket → snap → execute → loop | mesh_stats, slice, snap_nominal, detect_holes, self_deviation, render | Design X Auto Sketch, SolidWorks slices |
| D | Hypothesis-refine | Classify part family → propose minimal feature tree from priors → execute coarse → deviation heatmap drives targeted corrections, bounded by the strategy's **own** feature estimate (never GT N_ref) | all | Expert "design-intent first" workflow |

## Common stage skeleton (each strategy fills it differently)

1. **Intake** — read `task.yaml`; units, class, budgets.
2. **Analyze** — strategy-specific use of RE toolkit.
3. **Datum** — choose primary/secondary/tertiary from function (largest planar mounting face, main bore axis, symmetry plane), not best-fit to scan.
4. **Plan** — write `plan/re_plan.v1.json`: ordered features, nominal values (snapped), evidence (measured value, residual, inlier %).
5. **Execute** — hand IR to the CAD skillset (L2 takes over the "how").
6. **Verify** — `self_deviation` vs STL; per-region residuals.
7. **Refine** — edit IR (→ v2, v3…) and re-execute; stop when targets met or budget/iteration cap hit. Each refinement must reference the region/feature it fixes (prevents blind patching).
8. **Deliver** — final STEP + native + final IR.

## Harness levels (orthogonal to strategy, set in `run_manifest.harness_level`)

| Level | Agent gets |
|---|---|
| H0 | One shot: mesh_stats only, no self-check loop |
| H1 | + `self_deviation` loop (default) |
| H2 | + `render` / vision |
| H3 | + full RE toolkit (fit_primitives, slice, detect_holes, snap_nominal) |

A strategy's `toolkit_tools` is intersected with the level's allowance. Strategy B/D at H0 is legal but degenerate — use it only as an ablation.

## Exit-criteria knobs (in `strategy.yaml`)

- `max_refine_iters` (A: 1, B/C: 4, D: 6)
- `feature_budget_policy`: `none` | `soft` (warn above 1.2× estimate) | `hard`
- `snap_policy`: snap if residual < 0.05 mm (class A/B) or < 0.15 mm (class C)
- Note: D needs execution feedback, so it is ranked only in Track E, not plan-only Track S.
- `vision`: on/off (for model families without vision)

## Anti-patterns every playbook must forbid

- Converting/importing the STL as a body or surfaces in the CAD tool.
- Adding features to chase residuals below the class tight band.
- Non-nominal values left when a standard value is within the snap residual.
- Freeform surfaces on regions the toolkit classified as plane/cylinder/cone with >90% inliers.
