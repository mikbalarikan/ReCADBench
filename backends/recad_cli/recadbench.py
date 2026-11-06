"""`recadbench` console script (minimal, P4): only the agent-facing `nx-run` subcommand.

    recadbench nx-run cad/step_01.py [--timeout S] [--part cad/part.prt]

Delegates to backends/nx/runner/nx_run.py (stdlib only, run in this interpreter). The rest of the
`recadbench` CLI (compose, finish, ...) belongs to the runner package (P10); when it lands it
should forward `nx-run` here (or import `nx_run.main`) instead of shipping a second entry point.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Sequence

from .cli import backends_dir

USAGE = "usage: recadbench nx-run <cad/journal.py> [--timeout S] [--part cad/part.prt]"


def _load_nx_run():
    path = backends_dir() / "nx" / "runner" / "nx_run.py"
    spec = importlib.util.spec_from_file_location("recadbench_nx_run", path)
    if spec is None or spec.loader is None or not path.is_file():
        raise FileNotFoundError(f"no NX runner at {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print(USAGE)
        return 0 if args else 2
    if args[0] != "nx-run":
        print(json.dumps({"ok": False, "error": f"unknown subcommand {args[0]!r}; {USAGE}"}))
        return 2
    try:
        mod = _load_nx_run()
    except FileNotFoundError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    return int(mod.main(args[1:]))


if __name__ == "__main__":
    sys.exit(main())
