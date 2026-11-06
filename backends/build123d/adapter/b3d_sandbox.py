"""Restricted execution of an agent-written native script (``RECAD_SANDBOX=1``).

``recadbench finish`` runs ``recad build123d export|extract`` on agent code. With
``RECAD_SANDBOX=1`` the adapter executes the script the way build123d-mcp's sandbox does (AST check,
restricted builtins, import allow-list), and stricter, because the native-file contract of the
build123d skillset (``cad/part.py``: build123d + math only, self-contained, no file access) allows
less than an interactive session:

* AST: no import outside the allow-list, no call of ``__import__`` / ``eval`` / ``exec`` /
  ``compile`` / ``open`` / ``getattr`` / ``vars`` / ``globals`` / ``locals`` / ``breakpoint`` /
  ``input``, no dunder attribute other than ``__name__`` / ``__doc__`` / ``__class__``;
* builtins: the same names removed; ``__import__`` accepts only the allow-list roots (no
  ``io``, ``os``, ``inspect``, no module from ``cad/`` or ``lib/``: the script is self-contained);
* OCP: only the geometric sub-modules build123d-mcp allows (no STEP/IGES/STL readers, no OSD).

Not a complete sandbox (C extensions, build123d's own file-writing functions); finish adds OS-level
measures (temporary copy, ``python -I``, no secrets in the environment). Stdlib only.
"""

from __future__ import annotations

import ast
import builtins
from typing import Any

ALLOWED_ROOTS = frozenset({
    "build123d", "bd_warehouse", "math", "cmath", "numpy", "decimal", "fractions", "statistics",
    "numbers", "random", "collections", "itertools", "functools", "copy", "operator", "typing",
    "abc", "dataclasses", "enum", "re", "string", "textwrap", "warnings", "OCP",
})
OCP_ALLOWED = frozenset({
    "gp", "TopAbs", "TopExp", "TopLoc", "TopTools", "TopoDS", "BRep", "BRepTools", "BRepLib",
    "BRepAdaptor", "BRepBndLib", "BRepCheck", "BRepClass", "BRepClass3d", "BRepExtrema",
    "BRepGProp", "BRepIntCurveSurface", "BRepBuilderAPI", "BRepPrimAPI", "BRepFeat", "BRepFill",
    "BRepFilletAPI", "BRepOffsetAPI", "BRepSweep", "BRepProj", "BRepAlgoAPI", "Geom", "Geom2d",
    "GeomAbs", "GeomAPI", "GeomAdaptor", "GeomConvert", "GeomFill", "GeomLProp", "GeomProjLib",
    "Adaptor2d", "Adaptor3d", "GProp", "GCPnts", "Bnd", "IntCurvesFace", "IntTools", "Extrema",
    "ShapeAnalysis", "ShapeExtend", "ShapeFix", "ShapeUpgrade", "TColgp", "TColGeom", "TColStd",
    "TCollection", "collections", "Approx", "Convert", "BSpl", "ProjLib",
})
BLOCKED_NAMES = frozenset({"__import__", "eval", "exec", "compile", "open", "breakpoint", "input",
                           "getattr", "vars", "globals", "locals", "delattr", "setattr",
                           "memoryview", "help", "exit", "quit"})
ALLOWED_DUNDERS = frozenset({"__name__", "__doc__", "__class__"})


class SandboxError(ValueError):
    pass


def module_allowed(name: str) -> bool:
    parts = name.split(".")
    if parts[0] not in ALLOWED_ROOTS:
        return False
    if parts[0] == "OCP" and len(parts) >= 2:
        return parts[1] in OCP_ALLOWED
    return True


def check_source(src: str, path: str = "<native>") -> None:
    """Raise SandboxError when the script uses a construct the sandbox does not run."""
    try:
        tree = ast.parse(src, path)
    except SyntaxError:
        return  # compile() reports it with a better message
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if not module_allowed(a.name):
                    raise SandboxError(f"import of {a.name!r} is not allowed in the native file")
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                raise SandboxError("relative imports are not allowed in the native file")
            if not node.module or not module_allowed(node.module):
                raise SandboxError(f"import from {node.module!r} is not allowed in the native "
                                   "file")
        elif isinstance(node, ast.Name) and node.id in BLOCKED_NAMES:
            raise SandboxError(f"{node.id!r} is not allowed in the native file")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__") and \
                node.attr.endswith("__") and node.attr not in ALLOWED_DUNDERS:
            raise SandboxError(f"dunder attribute {node.attr!r} is not allowed in the native "
                               "file")


def restricted_builtins() -> dict[str, Any]:
    safe = dict(vars(builtins))
    for n in BLOCKED_NAMES:
        safe.pop(n, None)
    real = builtins.__import__

    def _import(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: A002
        if level or not module_allowed(name):
            raise ImportError(f"import of {name!r} is not allowed in the native file")
        return real(name, globals, locals, fromlist, level)

    safe["__import__"] = _import
    return safe
