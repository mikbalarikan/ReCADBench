# Brief for the ReCADBench main session (paste into a new Claude Code session on the ASUS)

```
You are the MAIN session for ReCADBench development on this machine (ASUS TUF, the evaluator host:
NX 2206, Google Drive H:, GitHub over SSH). Your job: manage all remaining development until the
benchmark is READY for its first test run, then stop and hand back to me. You coordinate; delegate the
real work to subagents/workflows, and keep your own context for planning, review and merging.

Start:
1. git clone (SSH) into one folder: mikbalarikan/ReCADBench, ReCADBench-cad-skills, ReCADBench-strategies,
   ReCADBench-data, ReCADBench-gt, agentic_STL-to-CAD (pull if already cloned).
2. Read fully: ReCADBench/docs/handover/HANDOVER.md, PROMPTS_v2.md, ReCADBench-gt/HANDOVER_PRIVATE.md,
   ReCADBench/docs/architecture/00_DECISIONS.md (D1-D10), 03_SCORING.md, docs/adr/0002-*.md, and
   every repo's CLAUDE.md + STATUS.md.
3. Run PROMPTS_v2 H0 (bootstrap, review and merge the two handover PRs).

Then work through PROMPTS_v2 H1-H9 in the dependency order shown there, running independent prompts in
parallel (one subagent/workflow each; use git worktrees when two agents touch the same repo).
For every prompt:
- give the subagent the prompt text plus pointers to the files it needs;
- require tests with known ground truth (synthetic in CI; the R385 pilots on this host);
- have a fresh agent adversarially review the result (it must reproduce each defect), then fix;
- PR per repo, CI green, then merge;
- update STATUS.md in every touched repo; push spec tags from here (cloud sessions cannot push tags).

Hard rules:
- Never copy scans, STEP/PRT or GT files into the public ReCADBench repo (check_public.py).
- GT is read only via RECADBENCH_GT_DIR.
- Schema changes need an ADR and a spec version bump.
- Don't re-open decisions listed in HANDOVER.md §3 without asking me.
- Never modify originals on C:/H:/D:. Copy.
- Ask me before: anything needing physical action (phone, scanner, printer), clearing new client parts,
  spending on large model runs, or deleting anything.

Done = HANDOVER.md §6 checklist all true with evidence. Then STOP: give me a short report (what was built,
test/conformance tables, pilot.yaml run count and cost/time estimate, open questions) and wait for me
to start the first benchmark test (P11).

Keep me informed briefly at each merged milestone.
```
