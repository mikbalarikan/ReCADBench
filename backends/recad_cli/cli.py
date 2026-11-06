"""`recad` command line — dispatcher for the L1 backend adapters.

    recad backends [--json]                    # list backends + whether each environment is usable
    recad <backend> <subcommand> [args…]       # run backends/<backend>/adapter/adapter.py

Subcommands are passed through unchanged (export | extract | set-param | replay, plus
execute-ir for build123d). The adapter runs in a fresh subprocess with the backend's interpreter:

    build123d   RECAD_BUILD123D_PYTHON   (default: this interpreter)
    freecad     RECAD_FREECAD_PYTHON     (required: a Python that can import FreeCAD)
    solidcraft  RECAD_SOLIDCRAFT_PYTHON  (default: this interpreter, >= 3.11)
                RECAD_SOLIDCRAFT_CLI     (default: `solidcraft-cli` on PATH)
    nx          no adapter yet (P4)

Other environment: RECAD_BACKENDS_DIR (folder holding <backend>/adapter/adapter.py; default:
the source tree this package lives in), RECAD_TIMEOUT_S (per adapter call, default 1800).

After the adapter returns, the dispatcher
  * reads its single JSON result (last non-empty stdout line; adapter stderr is passed through),
  * validates features.json against the recadbench-spec `features` schema (extract, replay),
  * measures the STEP itself (volume, faces, solids) when the adapter reported
    `needs_measure: true` (build123d in this interpreter, else in RECAD_BUILD123D_PYTHON),
  * prints ONE JSON object on stdout.

Exit codes: the adapter's exit code is propagated; 1 if the dispatcher turns a result into a
failure (invalid features.json, not exactly one solid, no JSON, timeout, environment missing);
2 for usage errors (unknown backend, no subcommand).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__

DEFAULT_TIMEOUT_S = 1800.0

# Snippets run in the backend interpreter by `recad backends` (print one version string).
_PROBE_BUILD123D = "import build123d; print('build123d', build123d.__version__)"
_PROBE_FREECAD = (
    "import os, sys\n"
    "try:\n    import FreeCAD\n"
    "except ImportError:\n"
    "    for d in (os.environ.get('FREECAD_LIB'), os.path.join(sys.prefix, 'lib'),\n"
    "              os.path.join(sys.prefix, 'Library', 'bin'), os.path.dirname(sys.executable)):\n"
    "        if d and os.path.isdir(d):\n            sys.path.append(d)\n"
    "    import FreeCAD\n"
    "print('FreeCAD', '.'.join(FreeCAD.Version()[:3]))"
)
_PROBE_PY311 = "import sys; assert sys.version_info >= (3, 11), sys.version; print(sys.version)"

# STEP measuring snippet (run in a build123d interpreter); prints {solids, volume_mm3, faces}.
_MEASURE_SNIPPET = (
    "import json, sys\n"
    "from build123d import import_step\n"
    "s = import_step(sys.argv[1])\n"
    "print(json.dumps({'solids': len(s.solids()), 'volume_mm3': float(s.volume),\n"
    "                  'faces': len(s.faces())}))"
)


@dataclass(frozen=True)
class Backend:
    id: str
    python_env: str | None  # env var naming the backend interpreter
    python_required: bool  # True: no default interpreter
    probe: str | None  # snippet that proves the interpreter can drive the backend
    native_ext: str | None
    note: str = ""


BACKENDS: dict[str, Backend] = {
    "build123d": Backend("build123d", "RECAD_BUILD123D_PYTHON", False, _PROBE_BUILD123D, "py"),
    "freecad": Backend("freecad", "RECAD_FREECAD_PYTHON", True, _PROBE_FREECAD, "FCStd"),
    "solidcraft": Backend("solidcraft", "RECAD_SOLIDCRAFT_PYTHON", False, _PROBE_PY311, "json"),
    "nx": Backend("nx", None, True, None, "prt", note="adapter not implemented yet (P4)"),
}


class EnvError(RuntimeError):
    """The backend's environment is not usable on this host."""


# ---------------------------------------------------------------------------- environment


def backends_dir() -> Path:
    env = os.environ.get("RECAD_BACKENDS_DIR")
    if env:
        return Path(env).resolve()
    here = Path(__file__).resolve().parent.parent  # source tree / editable install
    if (here / "build123d" / "adapter" / "adapter.py").is_file():
        return here
    for base in (Path.cwd(), *Path.cwd().parents):  # non-editable install: find the checkout
        cand = base / "backends"
        if (cand / "build123d" / "adapter" / "adapter.py").is_file():
            return cand
    return here


def adapter_path(b: Backend) -> Path:
    return backends_dir() / b.id / "adapter" / "adapter.py"


def resolve_python(b: Backend) -> str:
    val = os.environ.get(b.python_env) if b.python_env else None
    if not val:
        if b.python_required:
            raise EnvError(f"set {b.python_env} to the {b.id} Python interpreter"
                           if b.python_env else b.note or "no interpreter")
        return sys.executable
    exe = shutil.which(val) or (val if Path(val).is_file() else None)
    if not exe:
        raise EnvError(f"{b.python_env}={val!r} is not an executable")
    return exe


def child_env(b: Backend) -> dict[str, str]:
    env = dict(os.environ)
    if b.id == "solidcraft":
        cli = env.get("RECAD_SOLIDCRAFT_CLI") or "solidcraft-cli"
        exe = shutil.which(cli)
        if not exe:
            raise EnvError(f"SolidCraft CLI not found ({cli!r}); set RECAD_SOLIDCRAFT_CLI")
        env["RECAD_SOLIDCRAFT_CLI"] = exe
    return env


def check_backend(b: Backend, timeout: float = 120.0) -> dict[str, Any]:
    """Availability of one backend on this host (used by `recad backends` and conformance)."""
    info: dict[str, Any] = {"backend": b.id, "available": False}
    try:
        if not adapter_path(b).is_file():
            raise EnvError(b.note or f"no adapter at {adapter_path(b)}")
        py = resolve_python(b)
        info["python"] = py
        env = child_env(b)
        if b.id == "solidcraft":
            info["cli"] = env["RECAD_SOLIDCRAFT_CLI"]
        if b.probe:
            proc = subprocess.run([py, "-c", b.probe], capture_output=True, text=True,
                                  timeout=timeout, env=env)
            if proc.returncode != 0:
                tail = (proc.stderr.strip().splitlines() or ["probe failed"])[-1]
                raise EnvError(f"probe failed in {py}: {tail}")
            info["version"] = proc.stdout.strip().splitlines()[-1]
        if b.id == "solidcraft":
            proc = subprocess.run([py, str(adapter_path(b)), "version"], capture_output=True,
                                  text=True, timeout=timeout, env=env)
            res = _last_json(proc.stdout) or {}
            if proc.returncode != 0 or not res.get("ok"):
                raise EnvError(f"solidcraft-cli did not start: {res.get('error', proc.stderr)}")
            info["version"] = res.get("version")
        info["available"] = True
    except (EnvError, OSError, subprocess.TimeoutExpired) as exc:
        info["reason"] = str(exc)
    return info


# ---------------------------------------------------------------------------- helpers


def _last_json(stdout: str) -> dict[str, Any] | None:
    for line in reversed([ln for ln in stdout.splitlines() if ln.strip()]):
        try:
            val = json.loads(line)
        except json.JSONDecodeError:
            return None
        return val if isinstance(val, dict) else None
    return None


def _opt(args: Sequence[str], name: str) -> str | None:
    """Value of `--name X` / `--name=X` in a passed-through argument list."""
    for i, a in enumerate(args):
        if a == name and i + 1 < len(args):
            return args[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return None


def _outputs(sub: str, args: Sequence[str]) -> tuple[Path | None, Path | None]:
    """(step, features.json) the adapter was asked to write for this subcommand."""
    if sub == "replay":
        d = _opt(args, "--out-dir")
        return (Path(d) / "output.step", Path(d) / "features.json") if d else (None, None)
    out = _opt(args, "--out")
    if sub in ("export", "set-param", "execute-ir"):
        return (Path(out) if out else None), None
    if sub == "extract":
        return None, (Path(out) if out else None)
    return None, None


def validate_features(path: Path) -> list[str]:
    """Schema + semantic errors of a features.json (recadbench-spec); [] when valid."""
    from recadbench_spec.validate import validate_file

    try:
        report = validate_file(str(path), "features")
    except (OSError, ValueError) as exc:
        return [str(exc)]
    return [str(e) for e in report.errors]


def measure_step(path: Path) -> dict[str, Any] | None:
    """{solids, volume_mm3, faces, measured_by} of a STEP file, or None if no measurer exists."""
    try:
        import build123d
    except ImportError:
        build123d = None
    if build123d is not None and hasattr(build123d, "import_step"):
        shape = build123d.import_step(str(path))
        return {"solids": len(shape.solids()), "volume_mm3": float(shape.volume),
                "faces": len(shape.faces()),
                "measured_by": f"recad (build123d {build123d.__version__})"}
    py = os.environ.get("RECAD_BUILD123D_PYTHON")
    if py and Path(py).resolve() != Path(sys.executable).resolve():
        proc = subprocess.run([py, "-c", _MEASURE_SNIPPET, str(path)], capture_output=True,
                              text=True, timeout=600)
        res = _last_json(proc.stdout)
        if proc.returncode == 0 and res:
            return {**res, "measured_by": f"recad (build123d in {py})"}
    return None


def _emit(res: dict[str, Any], rc: int) -> int:
    print(json.dumps(res))
    sys.stdout.flush()
    return rc


# ---------------------------------------------------------------------------- commands


def run_adapter(backend: str, args: Sequence[str]) -> int:
    b = BACKENDS[backend]
    sub = args[0]
    try:
        adapter = adapter_path(b)
        if not adapter.is_file():
            raise EnvError(b.note or f"no adapter at {adapter}")
        py = resolve_python(b)
        env = child_env(b)
    except EnvError as exc:
        return _emit({"ok": False, "backend": backend, "error": f"environment: {exc}"}, 1)

    if "-h" in args or "--help" in args:  # adapter help text, not a JSON result
        return subprocess.run([py, str(adapter), *args], env=env).returncode

    timeout = float(os.environ.get("RECAD_TIMEOUT_S") or DEFAULT_TIMEOUT_S)
    try:
        proc = subprocess.run([py, str(adapter), *args], stdout=subprocess.PIPE, stderr=None,
                              text=True, env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        return _emit({"ok": False, "backend": backend,
                      "error": f"adapter timed out after {timeout:g} s"}, 1)
    except OSError as exc:
        return _emit({"ok": False, "backend": backend, "error": f"cannot run adapter: {exc}"}, 1)

    res = _last_json(proc.stdout)
    if res is None:
        return _emit({"ok": False, "backend": backend, "adapter_exit": proc.returncode,
                      "error": "adapter printed no JSON result",
                      "stdout_tail": proc.stdout[-2000:]}, proc.returncode or 1)
    res.setdefault("ok", False)
    res["backend"] = backend
    rc = proc.returncode
    if not res["ok"]:
        return _emit(res, rc or 1)

    step, features = _outputs(sub, args)

    if res.get("needs_measure") and step is not None:
        if not step.is_file():
            res.update(ok=False, error=f"adapter reported needs_measure but wrote no {step}")
        else:
            m = measure_step(step)
            if m is None:
                res["measure_error"] = ("no STEP measurer: install build123d "
                                        "(pip install recadbench-backends[measure]) or set "
                                        "RECAD_BUILD123D_PYTHON")
            else:
                res["needs_measure"] = False
                res["measured_by"] = m["measured_by"]
                if sub == "set-param":
                    res["volume_after_mm3"] = m["volume_mm3"]
                else:
                    res.update(solids=m["solids"], volume_mm3=m["volume_mm3"], faces=m["faces"])
                if m["solids"] != 1:
                    res.update(ok=False, error=f"STEP holds {m['solids']} solids (need exactly 1)")

    if features is not None and res["ok"]:
        if not features.is_file():
            res.update(ok=False, features_valid=False, error=f"no features.json at {features}")
        else:
            errs = validate_features(features)
            res["features_valid"] = not errs
            if errs:
                res.update(ok=False, validation_errors=errs[:50],
                           error="features.json does not conform to features.schema.json")

    if not res["ok"]:
        rc = rc or 1
    return _emit(res, rc)


def cmd_backends(as_json: bool) -> int:
    infos = [check_backend(b) for b in BACKENDS.values()]
    if as_json:
        print(json.dumps(infos, indent=2))
    else:
        for i in infos:
            state = "available" if i["available"] else "unavailable"
            detail = i.get("version") if i["available"] else i.get("reason")
            print(f"{i['backend']:11} {state:12} {detail or ''}")
    return 0


USAGE = (
    "usage: recad backends [--json]\n"
    "       recad <backend> <export|extract|set-param|replay|execute-ir> [args...]\n"
    f"backends: {', '.join(BACKENDS)}"
)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print(USAGE, file=sys.stderr if not args else sys.stdout)
        return 2 if not args else 0
    if args[0] == "--version":
        print(f"recad {__version__}")
        return 0
    if args[0] == "backends":
        return cmd_backends("--json" in args[1:])
    if args[0] not in BACKENDS:
        return _emit({"ok": False, "error": f"unknown backend {args[0]!r}; "
                      f"known: {', '.join(BACKENDS)}"}, 2)
    if len(args) < 2 or (args[1].startswith("-") and args[1] not in ("-h", "--help")):
        return _emit({"ok": False, "error": f"usage: recad {args[0]} <subcommand> ..."}, 2)
    return run_adapter(args[0], args[1:])


if __name__ == "__main__":
    sys.exit(main())
