# backends/nx/replays

Hand-written NX Open replays of the 10 conformance IR cases (`../../conformance/ir_cases/`), one
journal per case (`C01_block.py` ... `C10_draft_pocket.py`) plus the builder helper `_nxr.py`.

Each replay is an ordinary `recadbench nx-run` journal: `recad nx replay <case> --out-dir D` runs it
through `../runner/nx_wrapper.py` on `D/native.prt`, then exports and extracts the saved part.
Every IR parameter is a user-named NX expression (sketch dimension or feature value), so
`recad nx set-param` edits it. `_nxr.py` is self-contained on purpose: the conformance suite must
not depend on the L2 skillset. Results: `../../conformance/CONFORMANCE.md`.
