# nx — install (worker host)

NX is commercial software; nothing is installed by this repo. A host qualifies as an NX worker when
the following hold (checked on the ASUS evaluator host 2026-11-06).

| Item | Requirement | How to check |
|---|---|---|
| NX | Siemens NX **2206** with `NXBIN\run_journal.exe` (batch journals, embedded Python 3.10.9) | `recad nx version` |
| Licence | FlexNet licence for `nx` + `solid_modeling`; `SPLM_LICENSE_SERVER` set (e.g. `27800@localhost`) | a `recadbench nx-run` of any journal returns `ok: true` |
| Python | CPython ≥ 3.11 for the runner and adapter (stdlib only); build123d 0.13.0 in it (or `RECAD_BUILD123D_PYTHON`) for the STEP read-back | `python -c "import build123d"` |
| Packages | `pip install -e ./spec -e ./backends` (gives the `recad` and `recadbench` commands) | `recadbench --help` |
| Hosts | Windows only (NX batch); one NX process at a time (`max_parallel: 1`) | — |

## Environment

| Variable | Meaning | Default |
|---|---|---|
| `RECADBENCH_NX_RUN_JOURNAL` | path of `run_journal.exe` | `%UGII_BASE_DIR%\NXBIN\run_journal.exe`, then `C:\Program Files\Siemens\NX2206\NXBIN\run_journal.exe` |
| `UGII_BASE_DIR` | NX install root (set by the NX installer, machine scope) | — |
| `SPLM_LICENSE_SERVER` | licence server | set by the NX installer |
| `RECADBENCH_NX_TIMEOUT_S` | default `nx-run` timeout | 120 |
| `RECADBENCH_NX_LOCK` | host-wide lock file serialising NX processes | `<temp>\recadbench-nx.lock` |
| `RECADBENCH_NX_LIB` | folder put on the journal's `sys.path` | `<run dir>\lib` when present |
| `RECAD_NX_PYTHON` | interpreter for `recad nx …` | the dispatcher's interpreter |
| `RECAD_NX_JOURNAL_TIMEOUT_S` | per NX call inside the adapter (replay, export, …) | 600 |
| `RECAD_NX_KEEP_WORK` | keep the adapter's temporary work folder | unset |

## Notes from the host check

- NX start + licence check costs about 23 s per `run_journal` process; a whole `nx-run` call of a
  small journal takes 25–30 s. Put a whole IR revision into one journal.
- The syslog of each run is `%TEMP%\<user><hex>.syslog`; the wrapper reports its path and the
  runner excerpts only what this run wrote.
- Customisation / capture add-ons registered for the interactive NX (startup Python hooks) still
  load in batch and log `Failed to invoke ufsta …` in the syslog. Harmless for journals; the runner
  strips `UGII_CUSTOM_DIRECTORY_FILE` and `UGII_STARTUP_MACRO` for the child.
- Several batch NX processes could run side by side licence-wise, but the benchmark fixes NX
  parallelism to 1 per host (00_DECISIONS D9); the lock enforces it.
