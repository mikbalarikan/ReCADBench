# backends/nx/runner — `recadbench nx-run`

`nx_run.py` (host side, stdlib) launches `nx_wrapper.py` (NX side) through `run_journal.exe`:
timeout, host-wide NX lock, log capture, syslog excerpt, `cad/last_run.json`. Contract and
behaviour: [`../README.md`](../README.md). Entry point: `recadbench nx-run` (backends package,
`recad_cli/recadbench.py`).
