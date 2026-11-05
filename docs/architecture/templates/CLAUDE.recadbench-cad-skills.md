# recadbench-cad-skills — CLAUDE.md

L2 of ReCADBench: our CAD know-how, one skillset per backend (`nx/`, `build123d/`, `solidcraft/`, `freecad/`): `skills/`, `agents/`, `lib/`, `examples/`, `LESSONS.md`. Architecture lives in the `recadbench` repo: `docs/architecture/` (00_DECISIONS D1/D8, 05_TOOLS.md).

## Hard rules
- Skillsets teach HOW to operate one CAD program to execute an RE Plan IR faithfully. No reverse-engineering methodology (mesh segmentation, slicing, datum choice from scans…). Found while porting → `<b>/MIGRATION_NOTES.md` for recadbench-strategies.
- L1 backends are external and pinned in `recadbench/backends/`; do not fork or modify them here.
- No measurement code here (feature extraction, export checks, set-param) — that lives in `recadbench/backends/<b>/adapter` so skillsets cannot be tuned against the counter.
- Never instruct importing/converting the STL into a body or surfaces. `lib/` helpers must create native parametric features only.
- Name model parameters/expressions after IR `parameters[].name`.
- Versions are git tags `<b>-vN`; every change to skills/agents/lib bumps N and gets a changelog line in skillset.yaml.
- Improve skillsets only from **dev**-split Track T results. Never read test-split results.
- Update root `STATUS.md` at the end of every session.
