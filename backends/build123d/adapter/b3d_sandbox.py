"""Restricted execution of an agent-written native script (``RECAD_SANDBOX=1``).

``recadbench finish`` runs ``recad build123d export|extract`` on agent code. With
``RECAD_SANDBOX=1`` the adapter

* checks the script with the shared CAD code policy (``backends/recad_cli/code_policy.py``,
  profile ``build123d_native``: an AST allow-list of node types, imports and names; no attribute
  starting with ``_``; no file / import / export / process verbs; no string literal holding
  ``__``; build123d + math-like stdlib only, nothing from ``cad/`` or ``lib/``);
* executes it with a builtins dict that has no ``open`` / ``getattr`` / ``setattr`` / ``vars`` /
  ``globals`` / ``eval`` / ``exec`` / ``compile`` / ``chr`` / ``bytes`` / ... and whose
  ``__import__`` only imports what the policy allows;
* from an empty temporary working directory (``sandbox_cwd``), so a relative path in the script
  names nothing of the run.

Not a complete sandbox (C extensions, the CAD library's own APIs); finish adds OS-level measures
(temporary copy, ``python -I``, no secrets in the environment) and the evaluator's GT-copy check.
Stdlib only.
"""

from __future__ import annotations

import builtins
import contextlib
import importlib.util
import os
import tempfile
from pathlib import Path
from typing import Any

PROFILE = "build123d_native"
REMOVED_BUILTINS = frozenset({
    "__import__", "eval", "exec", "compile", "open", "breakpoint", "input", "getattr",
    "setattr", "delattr", "vars", "globals", "locals", "memoryview", "help", "exit", "quit",
    "chr", "bytes", "bytearray", "__loader__", "__spec__", "copyright", "credits", "license",
})  # __build_class__ stays (class statements); the AST policy denies naming it


class SandboxError(ValueError):
    pass


def _policy():
    path = Path(__file__).resolve().parents[2] / "recad_cli" / "code_policy.py"
    spec = importlib.util.spec_from_file_location("recadbench_code_policy", path)
    if spec is None or spec.loader is None:
        raise SandboxError(f"code policy missing at {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_POLICY = _policy()


def module_allowed(name: str) -> bool:
    return _POLICY.module_allowed(name, PROFILE)


def check_source(src: str, path: str = "<native>") -> None:
    """Raise SandboxError when the script uses a construct the sandbox does not run."""
    why = _POLICY.check_source(src, PROFILE, path=os.path.basename(path))
    if why:
        raise SandboxError(why)


def restricted_builtins() -> dict[str, Any]:
    safe = {k: v for k, v in vars(builtins).items() if k not in REMOVED_BUILTINS}
    real = builtins.__import__

    def _import(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: A002
        if level or not module_allowed(name):
            raise ImportError(f"import of {name!r} is not allowed in the native file")
        return real(name, globals, locals, fromlist, level)

    safe["__import__"] = _import
    return safe


@contextlib.contextmanager
def sandbox_cwd():
    """Run with an empty temporary working directory (restored afterwards)."""
    old = os.getcwd()
    with tempfile.TemporaryDirectory(prefix="recad-sbx-") as td:
        os.chdir(td)
        try:
            yield td
        finally:
            os.chdir(old)
