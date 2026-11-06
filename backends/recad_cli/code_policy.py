"""CAD code policy: an AST allow-list for agent-written CAD code (stdlib only, Python >= 3.10).

One check, shared by every place that lets agent code reach a CAD program:

* the run gate (``runner/recadbench_run/gate_hook.py``): build123d ``execute`` code, the body of
  an ``execute_file`` script and every ``.py`` under ``cad/`` (and ``lib/`` unless pinned), NX
  journals and every ``.py`` of the run dir;
* ``recadbench nx-run`` (``backends/nx/runner/nx_run.check_journal``): the same NX check again,
  right before NX starts;
* ``recad build123d export|extract`` under ``RECAD_SANDBOX=1``
  (``backends/build123d/adapter/b3d_sandbox.py``, the finish-time native file).

Stance: allow-list. Code may use the CAD library, a few pure stdlib modules and the skillset lib;
it may not touch files, processes, the import system or Python's introspection:

* node types: an allow-list (no ``match``, ``async``, ``yield``, ``await``, ``global`` tricks);
* imports: only allow-listed roots / sub-modules (no ``io``, ``os``, ``sys``, ``pathlib``,
  ``shutil``, ``glob``, ``hashlib``, ``zipfile``, ``zipimport``, ``importlib``, ``operator``,
  ``string``, ``inspect``, ``codecs``, ...); relative imports only to the run's own ``cad/``
  modules where the profile allows them;
* names: no ``open`` / ``exec`` / ``eval`` / ``compile`` / ``__import__`` / ``getattr`` /
  ``setattr`` / ``delattr`` / ``vars`` / ``globals`` / ``locals`` / ``chr`` / ``bytes`` / ...,
  no name starting with ``__`` other than ``__name__``;
* attributes: none starting with ``_``; no frame / code / generator introspection attributes
  (``gi_frame``, ``f_globals``, ``f_builtins``, ``tb_frame``, ...); no attribute or imported name
  whose words (camelCase / snake_case split) include a file, import / export, process or
  journal verb (``import_step``, ``export_stl``, ``Read_s``, ``DexManager``, ``Execute``,
  ``Open``, ``Save``, ``ListingWindow.WriteLine`` ...);
* constants: no string or bytes literal containing ``__`` (dunder names built as strings), except
  the literal ``"__main__"``.

Files that compose copied from the pinned skillset (``lib/``, ``.claude/``) are trusted when their
sha256 still equals the compose manifest (``.recadbench/compose_files.json``); :func:`pinned`
reads it. Everything else the agent wrote is checked.

This is defence in depth, not a proof: CPython cannot be fully confined by inspecting source
(C extensions, the CAD libraries' own APIs). Unattended runs need OS-level isolation as well
(runner/recadbench_run/README.md "Security model").
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path

MANIFEST = ".recadbench/compose_files.json"

COMMON_ROOTS = frozenset({
    "math", "cmath", "decimal", "fractions", "statistics", "numbers", "random", "collections",
    "itertools", "functools", "copy", "typing", "abc", "dataclasses", "enum", "re", "textwrap",
    "warnings", "time", "__future__",
})
OCP_ALLOWED = frozenset({
    "gp", "TopAbs", "TopExp", "TopLoc", "TopTools", "TopoDS", "BRep", "BRepTools", "BRepLib",
    "BRepAdaptor", "BRepBndLib", "BRepCheck", "BRepClass", "BRepClass3d", "BRepExtrema",
    "BRepGProp", "BRepIntCurveSurface", "BRepBuilderAPI", "BRepPrimAPI", "BRepFeat", "BRepFill",
    "BRepFilletAPI", "BRepOffsetAPI", "BRepSweep", "BRepProj", "BRepAlgoAPI", "Geom", "Geom2d",
    "GeomAbs", "GeomAPI", "GeomAdaptor", "GeomConvert", "GeomFill", "GeomLProp", "GeomProjLib",
    "Adaptor2d", "Adaptor3d", "GProp", "GCPnts", "Bnd", "IntCurvesFace", "IntTools", "Extrema",
    "ShapeAnalysis", "ShapeExtend", "ShapeFix", "ShapeUpgrade", "TColgp", "TColGeom", "TColStd",
    "TCollection", "Approx", "Convert", "BSpl", "ProjLib", "Precision", "Standard",
})
# build123d sub-packages that read or write files
B3D_DENIED_SUB = frozenset({"importers", "exporters", "exporters3d", "mesher", "jupyter_tools",
                            "vtk_tools"})
NX_ALLOWED_SUB = frozenset({
    "Features", "GeometricUtilities", "GeometricAnalysis", "Annotations", "Preferences",
    "Positioning", "Assemblies", "Layer", "Display", "Drawings", "Drafting", "Gateway",
})

PROFILES: dict[str, dict] = {
    # build123d-mcp session code, execute_file scripts and their cad/ + lib/ imports
    "build123d": {"roots": COMMON_ROOTS | {"build123d", "bd_warehouse", "OCP", "b3dhelpers"},
                  "local": True},
    # the finish-time native file (cad/part.py): self-contained, build123d + math only
    "build123d_native": {"roots": COMMON_ROOTS | {"build123d", "bd_warehouse", "OCP"},
                         "local": False},
    # NX journals (recadbench nx-run) and the cad/ modules they import
    "nx": {"roots": COMMON_ROOTS | {"NXOpen", "nxhelpers"}, "local": True},
}

BLOCKED_NAMES = frozenset({
    "__import__", "eval", "exec", "compile", "open", "breakpoint", "input", "getattr",
    "setattr", "delattr", "vars", "globals", "locals", "memoryview", "help", "exit", "quit",
    "chr", "bytes", "bytearray", "__builtins__", "__loader__", "__spec__", "__file__",
    "__dict__", "__build_class__", "copyright", "credits", "license",
})
ALLOWED_DUNDER_NAMES = frozenset({"__name__"})
# introspection attributes without a leading underscore
FRAME_ATTRS = frozenset({
    "gi_frame", "gi_code", "gi_yieldfrom", "gi_running", "cr_frame", "cr_code", "cr_await",
    "ag_frame", "ag_code", "ag_await", "f_globals", "f_builtins", "f_locals", "f_back",
    "f_code", "f_trace", "tb_frame", "tb_next", "co_code", "co_consts", "co_names",
    "func_globals", "func_code", "mro", "format_map", "get_field", "decode", "fromhex",
    "get_type_hints", "make_dataclass", "ForwardRef", "with_traceback",
})
# words of an attribute / imported / called name (camelCase and snake_case split, lower case)
DENIED_WORDS = frozenset({
    "import", "importer", "imports", "export", "exporter", "exports", "execute", "executor",
    "exec", "eval", "dex", "open", "save", "load", "loads", "read", "reader", "write", "writer",
    "file", "files", "journal", "macro", "playback", "spawn", "popen", "environ", "getenv",
    "putenv", "uf", "facet", "faceted", "mesher", "os", "sys", "io", "builtins", "globals",
    "locals", "subprocess", "pickle", "marshal", "ctypes", "importlib", "zipimport",
    "zipfile", "pathlib", "shutil", "glob", "tempfile", "socket", "unlink", "rmtree",
    "chdir", "listdir", "scandir", "walk", "stl", "brep", "step", "iges", "vtk", "gltf",
    "threemf",
})
# words that also deny a bare Name (from a star import): file / IO verbs only
DENIED_NAME_WORDS = frozenset({
    "import", "importer", "export", "exporter", "execute", "exec", "eval", "open", "save",
    "load", "read", "reader", "write", "writer", "popen", "spawn", "environ", "getenv",
    "mesher", "unlink", "rmtree", "listdir", "scandir", "chdir", "pickle", "marshal",
})
# Name words that are common CAD variable names: allowed as bare names
NAME_WORD_EXEMPT = frozenset({"step", "file"})
# identifiers whose word split is a false hit (the NX Open package itself)
EXEMPT_IDENTIFIERS = frozenset({"NXOpen"})

_NODE_TYPES: tuple[type, ...] = tuple(t for t in (
    ast.Module, ast.Expr, ast.Assign, ast.AugAssign, ast.AnnAssign, ast.For, ast.While, ast.If,
    ast.With, ast.withitem, ast.Break, ast.Continue, ast.Pass, ast.FunctionDef, ast.Return,
    ast.ClassDef, ast.Lambda, ast.IfExp, ast.BoolOp, ast.BinOp, ast.UnaryOp, ast.Compare,
    ast.Call, ast.keyword, ast.Attribute, ast.Subscript, ast.Slice, ast.Name, ast.Constant,
    ast.List, ast.Tuple, ast.Set, ast.Dict, ast.ListComp, ast.SetComp, ast.DictComp,
    ast.GeneratorExp, ast.comprehension, ast.Starred, ast.JoinedStr, ast.FormattedValue,
    ast.Import, ast.ImportFrom, ast.alias, ast.arguments, ast.arg, ast.Assert, ast.Raise,
    ast.Try, getattr(ast, "TryStar", None), ast.ExceptHandler, ast.Delete, ast.Nonlocal,
    ast.NamedExpr, ast.expr_context, ast.operator, ast.cmpop, ast.boolop, ast.unaryop,
    getattr(ast, "Index", None),
) if t is not None)

_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z0-9])|[A-Z]?[a-z0-9]+|[A-Z]+")


class PolicyError(ValueError):
    """Code the policy does not run (``str(exc)`` is the reason)."""


def words(name: str) -> set[str]:
    out = set()
    for part in name.split("_"):
        out.update(w.lower() for w in _CAMEL.findall(part))
    return out


def _bad_attr(attr: str) -> str | None:
    if attr.startswith("_"):
        return f"attribute {attr!r} (names starting with '_' are not available)"
    if attr in FRAME_ATTRS:
        return f"attribute {attr!r} (introspection)"
    if attr in EXEMPT_IDENTIFIERS:
        return None
    hit = words(attr) & DENIED_WORDS
    if hit:
        return f"attribute {attr!r} (file / import / export / process access: {sorted(hit)[0]})"
    return None


def _bad_name(name: str, profile: str) -> str | None:
    if name in BLOCKED_NAMES:
        return f"name {name!r}"
    if name.startswith("__") and name not in ALLOWED_DUNDER_NAMES:
        return f"name {name!r} (dunder names are not available)"
    if name in EXEMPT_IDENTIFIERS:
        return None
    hit = (words(name) & DENIED_NAME_WORDS) - NAME_WORD_EXEMPT
    if hit:
        return f"name {name!r} (file / import / export access: {sorted(hit)[0]})"
    return None


def module_allowed(name: str, profile: str, local: frozenset[str] = frozenset()) -> bool:
    prof = PROFILES[profile]
    parts = name.split(".")
    if any(p.startswith("_") and p != "__future__" for p in parts):
        return False
    root = parts[0]
    if prof["local"] and root in local and root not in prof["roots"]:
        return True
    if root not in prof["roots"]:
        return False
    if root == "OCP" and len(parts) >= 2:
        return parts[1] in OCP_ALLOWED
    if root == "build123d" and len(parts) >= 2:
        return parts[1] not in B3D_DENIED_SUB
    if root == "NXOpen" and len(parts) >= 2:
        return parts[1] in NX_ALLOWED_SUB
    return True


def _const_ok(v) -> bool:
    if isinstance(v, bytes):
        return b"__" not in v
    if isinstance(v, str):
        return "__" not in v or v == "__main__"
    return True


def check_source(src: str, profile: str, *, local_modules=(), path: str = "<code>") -> str | None:
    """The reason the policy refuses ``src``, or None. ``local_modules``: module names of the
    run's own ``cad/`` files a profile with ``local`` may import (they are checked too)."""
    if profile not in PROFILES:
        raise KeyError(f"unknown code-policy profile {profile!r}")
    local = frozenset(local_modules)
    try:
        tree = ast.parse(src, path)
    except SyntaxError as exc:
        return f"does not parse ({exc.msg}, line {exc.lineno})"
    except (ValueError, RecursionError, MemoryError) as exc:
        return f"does not parse ({type(exc).__name__})"
    for node in ast.walk(tree):
        if not isinstance(node, _NODE_TYPES):
            return f"construct {type(node).__name__} is not available in CAD code"
        line = getattr(node, "lineno", "?")
        why = None
        if isinstance(node, ast.Import):
            for a in node.names:
                if not module_allowed(a.name, profile, local):
                    why = f"import of {a.name!r}"
                    break
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level:  # agent code is a script, not a package: import cad modules by name
                why = f"relative import from {'.' * node.level}{mod!r}"
            elif not mod or not module_allowed(mod, profile, local):
                why = f"import from {mod!r}"
            if why is None:
                for a in node.names:
                    if a.name != "*":
                        why = _bad_attr(a.name) or _bad_name(a.name, profile)
                    if why is None and a.asname:
                        why = _bad_name(a.asname, profile)
                    if why:
                        break
        elif isinstance(node, ast.alias):
            pass
        elif isinstance(node, ast.Name):
            why = _bad_name(node.id, profile)
        elif isinstance(node, ast.Attribute):
            why = _bad_attr(node.attr)
        elif isinstance(node, ast.Constant):
            if not _const_ok(node.value):
                why = "a string literal containing '__' (dunder names built as strings)"
        elif isinstance(node, ast.keyword):
            if node.arg is not None and node.arg.startswith("__"):
                why = f"keyword {node.arg!r}"
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            for d in node.decorator_list:
                if not isinstance(d, (ast.Name, ast.Attribute, ast.Call)):
                    why = "decorator expression"
        if why:
            return f"{path}:{line}: {why} is not allowed in CAD code"
    return None


# ------------------------------------------------------------------------ compose pins
def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def pinned(run_dir: Path) -> dict[str, str]:
    """``{run-relative posix path: sha256}`` of the files compose wrote (empty when the run dir
    was not composed). The gate verifies this manifest's MAC; nx-run trusts it as written."""
    p = Path(run_dir) / MANIFEST
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    files = doc.get("files") if isinstance(doc, dict) else None
    return {str(k): str(v) for k, v in files.items()} if isinstance(files, dict) else {}


def is_pinned(run_dir: Path, path: Path, pins: dict[str, str]) -> bool:
    """True when ``path`` is a compose-written skillset file (``lib/`` or ``.claude/``) whose
    content is unchanged."""
    try:
        rel = Path(path).resolve().relative_to(Path(run_dir).resolve()).as_posix()
    except ValueError:
        return False
    if not rel.startswith(("lib/", ".claude/")) or rel not in pins:
        return False
    try:
        return sha256_bytes(Path(path).read_bytes()) == pins[rel]
    except OSError:
        return False


def local_modules(run_dir: Path) -> frozenset[str]:
    """Module names the run's ``cad/`` folder provides (``cad/<name>.py``, packages)."""
    cad = Path(run_dir) / "cad"
    if not cad.is_dir():
        return frozenset()
    out = {p.stem for p in cad.glob("*.py")}
    out |= {p.name for p in cad.iterdir() if p.is_dir() and (p / "__init__.py").is_file()}
    return frozenset(n for n in out if n.isidentifier())


def check_file(run_dir: Path, path: Path, profile: str, text: str,
               pins: dict[str, str] | None = None) -> str | None:
    """:func:`check_source` for one run-dir file (pinned skillset files are trusted)."""
    if pins is not None and is_pinned(run_dir, path, pins):
        return None
    try:
        rel = Path(path).resolve().relative_to(Path(run_dir).resolve()).as_posix()
    except ValueError:
        rel = Path(path).name
    return check_source(text, profile, local_modules=local_modules(run_dir), path=rel)
