"""Runtime trace of build123d operations -> feature_taxonomy.yaml features.

Measurement instrument for `recad build123d extract` (00_DECISIONS D3). The native model is a
build123d Python script; the only reliable "feature tree" is what the script actually executes.
While the script runs, the build123d operation classes/functions are wrapped (monkeypatched for
the duration of the run only) and every top-level call is recorded:

* a call made *inside* another traced call (build123d internals) is ignored (depth counter);
* a call made from build123d's own package code outside any traced call is ignored;
* a call inside a Python loop is recorded once per execution (taxonomy note).

Counting conventions (identical across backends, see replays/README.md):

* BasePartObject placed under location contexts (Locations / GridLocations / HexLocations /
  PolarLocations) with more than one location: the op counts as its seed feature + one pattern
  per multi-location context (Polar -> pattern_circular, the others -> pattern_linear). One
  context = one pattern even if several ops run inside it. A nested BuildPart published under
  such a context is handled the same way.
* Mode=SUBTRACT/INTERSECT on a feature is part of that feature (no boolean).
* Algebra booleans (`a + b`, `a - b`, `a & b`, `fuse/cut/intersect`), builder `add()` and
  nested BuildPart transfer count a `boolean` only when they combine two bodies that each
  comprise more than one feature (subtract: the tool comprises more than one feature). Merging a
  single feature into a body is part of that feature (taxonomy note).
* `Shape.mirror` by user code on a 3D body = mirror. Placement copies (`moved/located/translate/
  rotate`, `Pos(...) *`) of one shape merged back into a body holding an instance of it = one
  pattern (no boolean), like `[locs] * shape`.
* Every traced result carries the set of features that built it; `count` covers only the
  features in the final part's provenance (dead/scratch geometry is not counted).
* Everything that is not a parametric build123d operation is reported in `unmapped` (gate fail):
  direct OCP/OCC imports, `.wrapped` access, dynamic code, topology-level constructors called
  by user code (`Solid.make_box`, `Solid.extrude`, `Shape.cast`, ...), imported geometry
  (`import_step`, `import_stl`, `Mesher`, ...), BasePartObject subclasses without a taxonomy
  mapping (custom/bd_warehouse objects), boolean/add operands with no traced provenance,
  fillet/chamfer/offset/draft/mirror applied to a body with no traced provenance, and geometry
  replaced outside build123d operations: a traced shape whose kernel handle (`_wrapped`) user code
  swapped, or a BuildPart whose part changed between traced operations (untraced ops such as
  `bounding_box(mode=ADD)`). `insert()` is traced like `add()`.

stdlib + build123d only (backend env has no recadbench_spec).
"""

from __future__ import annotations

import ast
import os
import sys
import sysconfig
from dataclasses import dataclass, field
from typing import Any

import build123d as bd
import build123d.build_common as bc

# ---------------------------------------------------------------------------------------------
# static checks
# ---------------------------------------------------------------------------------------------

_KERNEL_MODULES = ("OCP", "OCC", "cadquery", "OCCT", "pythonocc")
_DYNAMIC_CALLS = ("exec", "eval", "compile", "__import__")


def static_unmapped(source: str, filename: str) -> list[str]:
    """AST scan of one source file for direct-kernel / dynamic-code usage."""
    out: list[str] = []
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:  # pragma: no cover - script would not run anyway
        return [f"{filename}: syntax error: {exc}"]
    base = os.path.basename(filename)
    for node in ast.walk(tree):
        mods: list[str] = []
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods = [node.module]
        for m in mods:
            root = m.split(".")[0]
            if root in _KERNEL_MODULES:
                out.append(f"{base}:{node.lineno}: direct kernel import '{m}'")
            if root in ("importlib", "builtins"):
                out.append(f"{base}:{node.lineno}: dynamic code via '{m}'")
        if isinstance(node, ast.Name) and node.id == "__builtins__":
            out.append(f"{base}:{node.lineno}: dynamic code via '__builtins__'")
        if isinstance(node, ast.Attribute) and node.attr == "wrapped":
            out.append(f"{base}:{node.lineno}: raw kernel handle access '.wrapped'")
        if isinstance(node, ast.Attribute) and node.attr in _DYNAMIC_CALLS + ("__dict__",) and (
            isinstance(node.value, ast.Name) and node.value.id in ("builtins", "__builtins__")
        ):
            out.append(f"{base}:{node.lineno}: dynamic code '{node.value.id}.{node.attr}'")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _DYNAMIC_CALLS
        ):
            out.append(f"{base}:{node.lineno}: dynamic code '{node.func.id}()'")
    return out


def module_level_parameters(source: str) -> list[dict]:
    """Module-level `name = <number literal>` assignments (driving dimensions)."""
    params: list[dict] = []
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        else:
            continue
        if not isinstance(target, ast.Name):
            continue
        num = literal_number(value)
        if num is not None:
            params.append({"name": target.id, "value": float(num)})
    return params


def literal_number(node: ast.AST) -> float | int | None:
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, (ast.USub, ast.UAdd))
        and isinstance(node.operand, ast.Constant)
        and type(node.operand.value) in (int, float)
    ):
        v = node.operand.value
        return -v if isinstance(node.op, ast.USub) else v
    return None


# ---------------------------------------------------------------------------------------------
# taxonomy mapping
# ---------------------------------------------------------------------------------------------

PART_OBJECTS = {
    "Box": ("primitive", {"shape": "box"}),
    "Cylinder": ("primitive", {"shape": "cylinder"}),
    "Cone": ("primitive", {"shape": "cone"}),
    "Sphere": ("primitive", {"shape": "sphere"}),
    "Torus": ("primitive", {"shape": "torus"}),
    "Wedge": ("primitive", {"shape": "wedge"}),
    "Hole": ("hole", {"kind": "simple"}),
    "CounterBoreHole": ("hole", {"kind": "counterbore"}),
    "CounterSinkHole": ("hole", {"kind": "countersink"}),
}

# function name -> taxonomy type for a 3D result; None => unmapped (no taxonomy type)
FUNCTION_OPS = {
    "extrude": "extrude",
    "revolve": "revolve",
    "sweep": "sweep",
    "loft": "loft",
    "fillet": "fillet",
    "chamfer": "chamfer",
    "offset": "shell",
    "draft": "draft",
    "mirror": "mirror",
    "thicken": None,
    "split": None,
    "make_brake_formed": None,
}
TRANSFORM_OPS = ("scale",)  # taxonomy not_counted: transform
MODIFIER_OPS = ("fillet", "chamfer", "offset", "draft")  # results comprise the whole body

# topology-level constructors / modifiers that bypass the operation API when called by user code
RAW_SOLID_METHODS = (
    "make_box", "make_cone", "make_cylinder", "make_loft", "make_sphere", "make_torus",
    "make_wedge", "extrude", "extrude_linear_with_rotation", "extrude_taper", "extrude_until",
    "from_bounding_box", "revolve", "sweep", "sweep_multi", "thicken", "draft", "fillet",
    "chamfer", "hollow", "offset_3d", "dprism",
)
RAW_IMPORTERS = ("import_brep", "import_step", "import_stl", "import_threemf")

_PKG_DIR = os.path.dirname(os.path.abspath(bd.__file__))
_THIS_FILE = os.path.abspath(__file__)
_STDLIB_DIRS = tuple(
    {
        os.path.abspath(p)
        for p in (sysconfig.get_paths().get("stdlib"), sysconfig.get_paths().get("purelib"),
                  sysconfig.get_paths().get("platlib"), sys.prefix, sys.base_prefix)
        if p
    }
)


def _jsonable(v: Any, depth: int = 0) -> Any:
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float, str)):
        return v
    if hasattr(v, "name") and type(v).__module__.startswith("build123d.build_enums"):
        return v.name
    if isinstance(v, (tuple, list)) and depth < 2 and len(v) <= 8:
        items = [_jsonable(x, depth + 1) for x in v]
        if all(x is not None for x in items):
            return items
        return None
    if type(v).__name__ in ("Vector",):
        return [round(v.X, 6), round(v.Y, 6), round(v.Z, 6)]
    if type(v).__name__ in ("Axis", "Plane", "Location"):
        return type(v).__name__
    return None


def _params_from_call(args: tuple, kwargs: dict, names: tuple[str, ...] = ()) -> dict:
    out: dict = {}
    for i, a in enumerate(args):
        key = names[i] if i < len(names) else f"arg{i}"
        j = _jsonable(a)
        if j is not None:
            out[key] = j
    for k, a in kwargs.items():
        j = _jsonable(a)
        if j is not None:
            out[k] = j
    return out


def _is_3d(obj: Any) -> bool:
    if isinstance(obj, (bd.Sketch, bd.Curve)):
        return False
    if isinstance(obj, bd.Part):
        return True
    if isinstance(obj, bd.Shape):
        try:
            return len(obj.solids()) > 0
        except Exception:
            return False
    return False


# ---------------------------------------------------------------------------------------------
# tracer
# ---------------------------------------------------------------------------------------------


@dataclass
class Feature:
    native_type: str
    taxonomy_type: str
    native_name: str
    params: dict = field(default_factory=dict)
    counted: bool = True

    def as_json(self) -> dict:
        return {
            "native_name": self.native_name,
            "native_type": self.native_type,
            "taxonomy_type": self.taxonomy_type,
            "counted": self.counted,
            "params": self.params,
        }


@dataclass(frozen=True, eq=False)
class Prov:
    """Provenance of a traced shape (or of a BuildPart's accumulated part).

    n:     number of features the body comprises, for the boolean rule (a pattern/mirror result
           merged into a body counts 1: it is part of that pattern/mirror feature);
    fs:    indices into Tracer.features of every feature that built the body (what `count` uses:
           only features in the final part's provenance are counted);
    roots: copy lineage, id(copied shape) -> (instances, pattern feature index or None,
           orientations), used to recognise hand-made copies (`Shape.moved/located/translate/
           rotate`) of one shape merged back together as a pattern.
    """

    n: int
    fs: frozenset = frozenset()
    roots: dict = field(default_factory=dict)


_EMPTY = Prov(0)


def _merge(*ps: Prov, n: int | None = None) -> Prov:
    fs: set[int] = set()
    roots: dict = {}
    for p in ps:
        fs |= p.fs
        for r, (c, pi, o) in p.roots.items():
            if r in roots:
                c0, p0, o0 = roots[r]
                roots[r] = (c0 + c, p0 if p0 is not None else pi, o0 | o)
            else:
                roots[r] = (c, pi, o)
    return Prov(sum(p.n for p in ps) if n is None else n, frozenset(fs), roots)


def _orientation(shape: Any) -> frozenset:
    try:
        o = shape.location.orientation
        return frozenset({(round(o.X, 6), round(o.Y, 6), round(o.Z, 6))})
    except Exception:
        return frozenset()


COPY_OPS = ("moved", "located", "translate", "rotate")  # placement copies (copy lineage)


def _fingerprint(shape: Any) -> tuple:
    """(shape object, its kernel handle object, a handle copy sharing the TShape)."""
    w = getattr(shape, "_wrapped", None) if shape is not None else None
    try:
        twin = w.Located(w.Location()) if w is not None else None
    except Exception:
        twin = None
    return shape, w, twin


def _same(shape: Any, fp: tuple) -> bool:
    """True when `shape` still holds the geometry fingerprinted in `fp`.

    In-place placement changes (`move`, `locate`) keep the TShape and pass; a replaced
    `_wrapped` handle, or a handle whose TShape was swapped, fails.
    """
    _, w0, twin = fp  # the Part object may be re-wrapped by build123d; the handle may not
    w = getattr(shape, "_wrapped", None) if shape is not None else None
    if w is not w0:
        return False
    if w is None or twin is None:
        return True
    try:
        return bool(w.IsPartner(twin))
    except Exception:
        return True


class Tracer:
    """Install with `with Tracer(script_path) as t: run(...)`; read t.features / t.unmapped."""

    def __init__(self, script_path: str | None = None):
        self.script_path = os.path.abspath(script_path) if script_path else None
        self.features: list[Feature] = []
        self.unmapped: list[str] = []
        self.depth = 0
        self._patches: list[tuple[Any, str, Any]] = []
        self._prov: dict[int, Prov] = {}  # id(shape) -> provenance
        self._keep: list[Any] = []  # keep provenance objects alive (stable ids)
        self._patterns: dict[int, int] = {}  # id(location context) -> pattern feature index
        self._builder: dict[int, Prov] = {}  # id(BuildPart) -> provenance of its part
        self._copy_root: dict[int, int] = {}  # id(placement copy) -> id(original shape)
        self._pure: set[int] = set()  # shapes that are only instances of one copied shape
        self.dropped: set[int] = set()  # feature indices merged into another pattern
        self.last_buildpart: Any = None
        self.final_source: Any = None
        self.shown: list[Any] = []
        # kernel-handle fingerprints: a traced shape (or a BuildPart's part) whose geometry is
        # replaced outside build123d operations loses its provenance (gate fail)
        self._wsnap: dict[int, Any] = {}  # id(shape) -> fingerprint at registration
        self._bsnap: dict[int, Any] = {}  # id(BuildPart) -> fingerprint of its part
        self._tampered: set[int] = set()  # ids already reported

    # -- helpers -------------------------------------------------------------------------------
    def _caller(self, skip: int = 2) -> tuple[str, int]:
        f = sys._getframe(skip)
        while f is not None and os.path.abspath(f.f_code.co_filename) == _THIS_FILE:
            f = f.f_back
        if f is None:
            return "", 0
        return os.path.abspath(f.f_code.co_filename), f.f_lineno

    def _user_call(self) -> tuple[bool, str]:
        fn, line = self._caller(3)
        internal = fn.startswith(_PKG_DIR + os.sep) or fn == _THIS_FILE
        return (not internal), f"{os.path.basename(fn)}:{line}"

    def _register(self, obj: Any, p: Prov) -> None:
        if obj is None:
            return
        self._prov[id(obj)] = p
        self._keep.append(obj)
        if isinstance(obj, bd.Shape):
            self._wsnap[id(obj)] = _fingerprint(obj)
        if isinstance(obj, (list, tuple)):
            for o in obj:
                self._register(o, p)

    def _tamper(self, key: int, what: str) -> None:
        if key not in self._tampered:
            self._tampered.add(key)
            self.unmapped.append(f"{what}: geometry replaced outside build123d operations "
                                 "(kernel handle / builder part changed by user code)")

    def _sync_builder(self, b: Any) -> None:
        """Record the builder's part after a traced change (legitimate state)."""
        if isinstance(b, bd.BuildPart):
            self._bsnap[id(b)] = _fingerprint(b._obj)

    def _builder_intact(self, b: Any) -> bool:
        if _same(b._obj, self._bsnap.get(id(b), _fingerprint(None))):
            return True
        self._tamper(id(b), "BuildPart")
        return False

    def _checked(self, obj: Any) -> Prov | None:
        """Registered provenance of `obj`, None if untraced or its geometry was replaced."""
        p = self._prov.get(id(obj))
        if p is None:
            return None
        snap = self._wsnap.get(id(obj))
        if snap is not None and not _same(obj, snap):
            self._tamper(id(obj), f"traced {type(obj).__name__}")
            return None
        return p

    def _info(self, obj: Any) -> Prov | None:
        if obj is None:
            return _EMPTY
        if id(obj) in self._prov:
            return self._checked(obj)
        if isinstance(obj, bd.BuildPart):
            if not self._builder_intact(obj):
                return None
            return self._builder.get(id(obj), _EMPTY)
        if isinstance(obj, (list, tuple)):
            ps = [self._info(o) for o in obj]
            if any(p is None for p in ps):
                return None
            return _merge(*ps)
        return None

    def _n_of(self, obj: Any) -> int | None:
        p = self._info(obj)
        return None if p is None else p.n

    def _current_buildpart(self) -> Any:
        b = bd.Builder._get_context(log=False)
        return b if isinstance(b, bd.BuildPart) else None

    def _attribute(self, p: Prov, builder: Any = None) -> None:
        b = builder if builder is not None else self._current_buildpart()
        if b is not None:
            if id(b) not in self._builder:
                self._keep.append(b)  # keep alive: ids must not be reused within a run
            self._builder[id(b)] = _merge(self._builder.get(id(b), _EMPTY), p)

    def _new(self, f: Feature, attribute: bool = True) -> int:
        """Record a feature; attribute it to the enclosing BuildPart unless told otherwise."""
        self.features.append(f)
        idx = len(self.features) - 1
        if attribute:
            self._attribute(Prov(1, frozenset({idx})))
        return idx

    def _patch(self, owner: Any, name: str, new: Any) -> None:
        self._patches.append((owner, name, owner.__dict__[name] if isinstance(owner, type)
                              else getattr(owner, name)))
        setattr(owner, name, new)

    def _location_patterns(self, scope: Any, where: str, attribute: bool = True) -> list[int]:
        """Multi-location contexts between `scope` and its builder -> pattern feature indices.

        A context's pattern is created (and attributed) once; later ops under the same context
        get the existing index in their provenance.
        """
        ctxs: list[Any] = []
        s = scope
        while s is not None:
            lc = s.location_context
            if lc is not None and (not ctxs or ctxs[-1] is not lc):
                ctxs.append(lc)
            if isinstance(s.owner, bd.Builder):
                break
            s = s.parent
        found: list[int] = []
        for i, lc in enumerate(ctxs):
            outer = ctxs[i + 1] if i + 1 < len(ctxs) else None
            n_in = len(lc.local_locations)
            n_out = len(outer.local_locations) if outer is not None else 1
            if n_in <= n_out:
                continue
            key = id(lc)
            if key not in self._patterns:
                circ = isinstance(lc, bd.PolarLocations)
                ptype = "pattern_circular" if circ else "pattern_linear"
                pf = Feature(type(lc).__name__, ptype, f"{type(lc).__name__}@{where}",
                             {"count": n_in // max(n_out, 1)})
                self._patterns[key] = self._new(pf, attribute)
                self._keep.append(lc)
            found.append(self._patterns[key])
        return found

    # -- installation --------------------------------------------------------------------------
    def __enter__(self) -> Tracer:
        self._install()
        return self

    def __exit__(self, *exc: Any) -> None:
        for owner, name, old in reversed(self._patches):
            setattr(owner, name, old)
        self._patches.clear()

    def _install(self) -> None:
        tracer = self
        if not (hasattr(bc, "BaseObjectMeta") and hasattr(bc, "_get_build_scope")):
            raise RuntimeError(
                f"extract needs build123d 0.13.x internals (BuildScope); found {bd.__version__}"
            )

        # BasePartObject classes: wrap the metaclass call (placement scope is visible there)
        meta = bc.BaseObjectMeta
        orig_call = meta.__call__

        def meta_call(cls, *args, **kwargs):
            if tracer.depth or not issubclass(cls, bd.BasePartObject):
                tracer.depth += 1
                try:
                    return orig_call(cls, *args, **kwargs)
                finally:
                    tracer.depth -= 1
            user, where = tracer._user_call()
            scope = bc._get_build_scope()
            if user and tracer._current_buildpart() is not None:
                tracer._builder_intact(tracer._current_buildpart())
            tracer.depth += 1
            try:
                obj = orig_call(cls, *args, **kwargs)
            finally:
                tracer.depth -= 1
            tracer._sync_builder(tracer._current_buildpart())
            if not user:
                return obj
            name = cls.__name__
            mapping = None
            for klass in cls.__mro__:
                if klass.__name__ in PART_OBJECTS and klass.__module__.startswith("build123d"):
                    mapping = PART_OBJECTS[klass.__name__]
                    break
            if mapping is None:
                tracer.unmapped.append(
                    f"{where}: BasePartObject subclass '{name}' has no taxonomy mapping"
                )
                tracer._register(obj, Prov(1))
                return obj
            ttype, extra = mapping
            params = dict(extra)
            params.update(_params_from_call(args, kwargs))
            attribute = kwargs.get("mode") is not bd.Mode.PRIVATE
            idx = tracer._new(Feature(name, ttype, f"{name}@{where}", params), attribute)
            pats = tracer._location_patterns(scope, where, attribute) if scope is not None else []
            tracer._register(obj, Prov(1, frozenset([idx, *pats])))
            return obj

        self._patch(meta, "__call__", meta_call)

        # operation functions
        modules = [m for m in (bd, sys.modules.get("build123d.operations_part"),
                               sys.modules.get("build123d.operations_generic")) if m]
        for fname in list(FUNCTION_OPS) + list(TRANSFORM_OPS) + ["add", "insert"]:
            orig = getattr(bd, fname, None)
            if orig is None:
                continue
            wrapper = self._wrap_function(fname, orig)
            for m in modules:
                if getattr(m, fname, None) is orig:
                    self._patch(m, fname, wrapper)

        # raw topology constructors / importers (only flagged when called by user code)
        for cls in (bd.Solid, bd.Shell):
            for mname in RAW_SOLID_METHODS:
                if mname in cls.__dict__:
                    self._patch_raw(cls, mname, f"{cls.__name__}.{mname}")
        for owner, mname in ((bd.Shape, "cast"),):
            if mname in owner.__dict__:
                self._patch_raw(owner, mname, f"{owner.__name__}.{mname}")
        imp = sys.modules.get("build123d.importers")
        for fname in RAW_IMPORTERS:
            orig = getattr(bd, fname, None)
            if orig is None:
                continue
            w = self._wrap_raw_function(orig, f"{fname}() imported geometry")
            for m in (bd, imp):
                if m is not None and getattr(m, fname, None) is orig:
                    self._patch(m, fname, w)
        if hasattr(bd, "Mesher"):
            self._patch_raw(bd.Mesher, "read", "Mesher.read() imported geometry")

        # booleans (algebra mode)
        for cls, ops in ((bd.Shape, ("__add__", "__sub__", "__and__", "fuse", "cut",
                                      "intersect")),
                         (bd.Compound, ("__add__", "__sub__", "__and__")),
                         (bd.Part, ("__iadd__",))):
            for op in ops:
                if op in cls.__dict__:
                    self._patch(cls, op, self._wrap_boolean(cls.__dict__[op], op))

        # provenance through placement / copies / sub-shape selection
        for op in COPY_OPS + ("__copy__", "__deepcopy__", "solid", "solids"):
            if op in bd.Shape.__dict__:
                self._patch(bd.Shape, op, self._wrap_propagate(bd.Shape.__dict__[op], op))
        if "mirror" in bd.Shape.__dict__:
            self._patch(bd.Shape, "mirror", self._wrap_shape_mirror(bd.Shape.__dict__["mirror"]))
        if "__rmul__" in bd.Shape.__dict__:
            self._patch(bd.Shape, "__rmul__", self._wrap_rmul(bd.Shape.__dict__["__rmul__"]))

        # builders: BuildPart.part provenance, nested transfer, final-part fallback
        part_prop = bd.BuildPart.__dict__["part"]

        def part_get(bp):
            res = part_prop.fget(bp)
            if res is not None and tracer._builder_intact(bp):
                tracer._register(res, tracer._builder.get(id(bp), _EMPTY))
            return res

        self._patch(bd.BuildPart, "part", property(part_get, part_prop.fset))

        wprop = bd.Shape.__dict__.get("wrapped")
        if isinstance(wprop, property) and wprop.fset is not None:

            def wrapped_set(s, value):
                wprop.fset(s, value)
                caller = os.path.abspath(sys._getframe(1).f_code.co_filename)
                if id(s) in tracer._wsnap:
                    if caller.startswith(_PKG_DIR + os.sep):
                        tracer._wsnap[id(s)] = _fingerprint(s)  # build123d's own update
                    else:
                        tracer._tamper(id(s), f"traced {type(s).__name__}")

            self._patch(bd.Shape, "wrapped", property(wprop.fget, wrapped_set, wprop.fdel,
                                                      wprop.__doc__))
        orig_exit = bd.Builder.__dict__["__exit__"]

        def builder_exit(b, et, ev, tb):
            scope = bc._get_build_scope()
            parent = b.builder_parent
            if isinstance(b, bd.BuildPart) and et is None and not tracer.depth:
                tracer._builder_intact(b)  # tampered child: reported before the transfer
                if isinstance(parent, bd.BuildPart):
                    tracer._builder_intact(parent)
            res = orig_exit(b, et, ev, tb)
            if isinstance(b, bd.BuildPart) and et is None and not tracer.depth:
                tracer._buildpart_exit(b, scope)
                tracer._sync_builder(b)
                if isinstance(parent, bd.BuildPart):
                    tracer._sync_builder(parent)
            return res

        self._patch(bd.Builder, "__exit__", builder_exit)

    # -- wrappers ------------------------------------------------------------------------------
    def _op_targets(self, fname: str, objs: Any, kwargs: dict, builder: Any) -> list[Any]:
        """The body a modifier/mirror operates on (whose provenance its result inherits)."""
        if fname in ("fillet", "chamfer") and kwargs.get("target") is not None:
            return [kwargs["target"]]
        if objs is None:
            items: list[Any] = []
        elif isinstance(objs, (list, tuple)):
            items = list(objs)
        else:
            items = [objs]
        flat: list[Any] = []
        while items:
            o = items.pop(0)
            if isinstance(o, (list, tuple)):
                items[:0] = list(o)
            else:
                flat.append(o)
        if builder is not None and not any(_is_3d(o) for o in flat):
            return [builder]  # builder mode: the op acts on (and replaces) the builder's part
        out: list[Any] = []
        for o in flat:
            t = o
            if not _is_3d(o):
                tp = getattr(o, "topo_parent", None)
                t = tp if tp is not None else o
            if all(t is not x for x in out):
                out.append(t)
        return out

    def _wrap_function(self, fname: str, orig: Any) -> Any:
        tracer = self

        def wrapper(*args, **kwargs):
            if tracer.depth:
                return orig(*args, **kwargs)
            user, where = tracer._user_call()
            builder = tracer._current_buildpart()
            targets = None
            if user and (fname in MODIFIER_OPS or fname == "mirror"):
                key = None if args else ("faces" if "faces" in kwargs else "objects")
                objs = args[0] if args else kwargs.get(key)
                if objs is not None and not isinstance(objs, (bd.Shape, list, tuple)):
                    objs = list(objs)  # one-shot iterable: materialise once, pass it on
                    if args:
                        args = (objs, *args[1:])
                    else:
                        kwargs[key] = objs
                targets = tracer._op_targets(fname, objs, kwargs, builder)
            if user and builder is not None:
                tracer._builder_intact(builder)  # report a tampered part before it is merged
            tracer.depth += 1
            try:
                res = orig(*args, **kwargs)
            finally:
                tracer.depth -= 1
            tracer._sync_builder(builder)
            if not user:
                return res
            if fname in ("add", "insert"):
                tracer._handle_add(args, kwargs, builder, where,
                                   mode_pos=1 if fname == "add" else 3)
                return res
            three_d = builder is not None or _is_3d(res)
            if not three_d:
                return res  # 1D/2D (sketch) operation: not a feature
            if fname in TRANSFORM_OPS:
                p = tracer._info(args[0]) if args else None
                if p is not None:
                    tracer._register(res, p)
                return res
            ttype = FUNCTION_OPS[fname]
            if ttype is None:
                tracer.unmapped.append(f"{where}: '{fname}()' has no taxonomy type")
                tracer._register(res, Prov(2))
                return res
            base = _EMPTY
            if targets is not None:
                p = tracer._info(targets)
                if p is None:
                    kinds = ", ".join(sorted({type(t).__name__ for t in targets})) or "?"
                    tracer.unmapped.append(
                        f"{where}: '{fname}()' applied to a body that is not the result of "
                        f"traced build123d operations ({kinds})"
                    )
                else:
                    base = p
            params = _params_from_call(args, kwargs)
            if fname == "offset":
                params["has_openings"] = kwargs.get("openings") is not None
            attribute = kwargs.get("mode") is not bd.Mode.PRIVATE
            idx = tracer._new(Feature(fname, ttype, f"{fname}@{where}", params), attribute)
            if fname in MODIFIER_OPS:  # result is the whole modified body
                prov = Prov(max(2, base.n + 1), base.fs | {idx}, base.roots)
            elif fname == "mirror":  # the mirrored copy: merging it is part of the mirror
                prov = Prov(1, base.fs | {idx})
            else:
                prov = Prov(1, frozenset({idx}))
            tracer._register(res, prov)
            return res

        wrapper.__wrapped__ = orig
        wrapper.__name__ = getattr(orig, "__name__", fname)
        wrapper.__doc__ = getattr(orig, "__doc__", None)
        return wrapper

    def _wrap_raw_function(self, orig: Any, label: str) -> Any:
        tracer = self

        def wrapper(*args, **kwargs):
            if not tracer.depth:
                user, where = tracer._user_call()
                if user:
                    tracer.unmapped.append(f"{where}: {label}")
            tracer.depth += 1
            try:
                return orig(*args, **kwargs)
            finally:
                tracer.depth -= 1

        wrapper.__wrapped__ = orig
        return wrapper

    def _patch_raw(self, cls: type, mname: str, label: str) -> None:
        raw = cls.__dict__[mname]
        if isinstance(raw, classmethod):
            func = raw.__func__
            w = self._wrap_raw_function(func, f"topology-level call {label}")
            self._patch(cls, mname, classmethod(w))
        elif isinstance(raw, staticmethod):
            w = self._wrap_raw_function(raw.__func__, f"topology-level call {label}")
            self._patch(cls, mname, staticmethod(w))
        else:
            self._patch(cls, mname, self._wrap_raw_function(raw, f"topology-level call {label}"))

    def _wrap_boolean(self, orig: Any, op: str) -> Any:
        tracer = self
        kind = {"__add__": "union", "__iadd__": "union", "fuse": "union", "__sub__": "subtract",
                "cut": "subtract", "__and__": "intersect", "intersect": "intersect"}[op]

        def wrapper(a, *others, **kwargs):
            if tracer.depth:
                return orig(a, *others, **kwargs)
            user, where = tracer._user_call()
            tracer.depth += 1
            try:
                res = orig(a, *others, **kwargs)
            finally:
                tracer.depth -= 1
            if not user:
                return res
            b = others[0] if len(others) == 1 else list(others)
            tracer._boolean(a, b, kind, res, where)
            return res

        return wrapper

    def _boolean(self, a: Any, b: Any, kind: str, res: Any, where: str) -> None:
        items = b if isinstance(b, (list, tuple)) else [b]
        if not _is_3d(a) and not any(_is_3d(x) for x in items):
            return  # 2D/1D algebra (sketch construction)
        pa, pb = self._info(a), self._info(b)
        if pa is None or pb is None:
            bad = a if pa is None else b
            self.unmapped.append(
                f"{where}: boolean {kind} with an untraced shape ({type(bad).__name__})"
            )
            self._register(res, _merge(pa or _EMPTY, pb or _EMPTY, Prov(1)))
            return
        common = [r for r in pb.roots if r in pa.roots] if kind != "intersect" else []
        if common:
            self._copies_pattern(a, b, pa, pb, res, where)
            return
        counted = self._boolean_counts(kind, pa.n, pb.n)
        idx = None
        if counted:
            idx = self._new(Feature(kind, "boolean", f"{kind}@{where}", {"operation": kind}),
                            attribute=False)
        m = _merge(pa, pb)
        self._register(res, Prov(m.n + (1 if counted else 0),
                                 m.fs | ({idx} if counted else frozenset()), m.roots))

    def _copies_pattern(self, a: Any, b: Any, pa: Prov, pb: Prov, res: Any, where: str) -> None:
        """Union/subtract of a placement copy of a shape already in the body: a pattern.

        `h + h.moved(L1) + h.moved(L2)` or `plate - h - h.moved(L1)` is the hand-written form of
        `[locs] * h` / `GridLocations`: one pattern feature (seed counted where it was made),
        no boolean. Copies that only translate -> pattern_linear, any rotation ->
        pattern_circular.
        """
        roots = dict(pa.roots)
        fs = set(pa.fs | pb.fs)
        for r, (cb, ib, ob) in pb.roots.items():
            if r not in roots:
                roots[r] = (cb, ib, ob)
                continue
            ca, ia, oa = roots[r]
            idx = ia if ia is not None else ib
            if idx is None:
                idx = self._new(Feature("placement copies", "pattern_linear",
                                        f"copies@{where}", {}), attribute=False)
            elif ia is not None and ib is not None and ia != ib:
                self.dropped.add(ib)  # two partial patterns of the same shape -> one
            orients = oa | ob
            f = self.features[idx]
            f.params["count"] = ca + cb
            f.taxonomy_type = "pattern_circular" if len(orients) > 1 else "pattern_linear"
            roots[r] = (ca + cb, idx, orients)
            fs.add(idx)
        pure = id(a) in self._pure and id(b) in self._pure
        self._register(res, Prov(1 if pure else max(pa.n, pb.n), frozenset(fs), roots))
        if pure:
            self._pure.add(id(res))

    @staticmethod
    def _boolean_counts(kind: str, n_body: int, n_tool: int) -> bool:
        if kind == "subtract":
            return n_body >= 1 and n_tool > 1
        return min(n_body, n_tool) > 1

    def _handle_add(self, args: tuple, kwargs: dict, builder: Any, where: str,
                    mode_pos: int = 1) -> None:
        if builder is None:
            return
        objs = args[0] if args else kwargs.get("objects")
        mode = kwargs.get("mode", args[mode_pos] if len(args) > mode_pos else bd.Mode.ADD)
        if mode == bd.Mode.PRIVATE:
            return
        objs = objs if isinstance(objs, (list, tuple)) else [objs]
        kind = {bd.Mode.SUBTRACT: "subtract", bd.Mode.INTERSECT: "intersect"}.get(mode, "union")
        for o in objs:
            if not _is_3d(o):
                continue
            p = self._info(o)
            if p is None:
                self.unmapped.append(f"{where}: add()/insert() of an untraced 3D shape "
                                     f"({type(o).__name__})")
                continue
            body = self._builder.get(id(builder), _EMPTY)
            if mode == bd.Mode.REPLACE:
                self._builder[id(builder)] = p
                continue
            if self._boolean_counts(kind, body.n, p.n):
                idx = self._new(Feature("add", "boolean", f"add@{where}", {"operation": kind}),
                                attribute=False)
                p = Prov(p.n + 1, p.fs | {idx}, p.roots)
            self._attribute(p, builder)

    def _buildpart_exit(self, b: Any, scope: Any) -> None:
        self._keep.append(b)
        parent = b.builder_parent
        child = self._builder.get(id(b), _EMPTY)
        if parent is None:
            self.last_buildpart = b
            return
        if not isinstance(parent, bd.BuildPart) or b.mode == bd.Mode.PRIVATE:
            return
        where = "BuildPart"
        if scope is not None and len(scope.publication_locations) > 1:
            # nested builder published at several locations: pattern of its features
            s = scope.parent
            pats = self._location_patterns(s, where, attribute=False) if s is not None else []
            if pats:
                child = Prov(child.n + len(pats), child.fs | set(pats), child.roots)
        kind = {bd.Mode.SUBTRACT: "subtract", bd.Mode.INTERSECT: "intersect"}.get(b.mode,
                                                                                  "union")
        body = self._builder.get(id(parent), _EMPTY)
        if b.mode == bd.Mode.REPLACE:
            self._builder[id(parent)] = child
            return
        if self._boolean_counts(kind, body.n, child.n):
            idx = self._new(Feature("BuildPart", "boolean", "nested BuildPart transfer",
                                    {"operation": kind}), attribute=False)
            child = Prov(child.n + 1, child.fs | {idx}, child.roots)
        self._attribute(child, parent)

    def _wrap_propagate(self, orig: Any, op: str) -> Any:
        tracer = self
        track = op in COPY_OPS
        select = op in ("solid", "solids")

        def wrapper(s, *args, **kwargs):
            res = orig(s, *args, **kwargs)
            if select and tracer.depth:
                return res
            p = tracer._checked(s)
            if p is None or res is s:
                return res
            if track and not tracer.depth and _is_3d(s):
                # placement copy made outside any traced op (user code, `Pos(...) * shape`)
                root = tracer._copy_root.get(id(s))
                if root is None:
                    root = id(s)
                    tracer._copy_root[id(s)] = root
                    tracer._pure.add(id(s))
                    if root not in p.roots:
                        p = Prov(p.n, p.fs, {**p.roots, root: (1, None, _orientation(s))})
                        tracer._prov[id(s)] = p
                tracer._register(res, Prov(p.n, p.fs, {root: (1, None, _orientation(res))}))
                tracer._copy_root[id(res)] = root
                tracer._pure.add(id(res))
                return res
            tracer._register(res, p)
            return res

        return wrapper

    def _wrap_shape_mirror(self, orig: Any) -> Any:
        """`Shape.mirror(plane)` by user code on a 3D body = the `mirror()` operation."""
        tracer = self

        def wrapper(s, *args, **kwargs):
            res = orig(s, *args, **kwargs)
            p = tracer._checked(s)
            if p is None or res is s:
                return res
            if not tracer.depth:
                user, where = tracer._user_call()
                if user and _is_3d(s):
                    params = _params_from_call(args, kwargs, ("plane",))
                    idx = tracer._new(Feature("Shape.mirror", "mirror", f"mirror@{where}",
                                              params), attribute=False)
                    tracer._register(res, Prov(1, p.fs | {idx}))
                    return res
            tracer._register(res, p)
            return res

        return wrapper

    def _wrap_rmul(self, orig: Any) -> Any:
        tracer = self

        def wrapper(s, other):
            res = orig(s, other)
            p = tracer._checked(s)
            if p is None:
                return res
            if isinstance(res, list) and len(res) > 1 and not tracer.depth:
                user, where = tracer._user_call()
                if user and _is_3d(s):
                    circ = isinstance(other, bd.PolarLocations)
                    ptype = "pattern_circular" if circ else "pattern_linear"
                    idx = tracer._new(Feature(type(other).__name__, ptype,
                                              f"{type(other).__name__}*@{where}",
                                              {"count": len(res)}), attribute=False)
                    # merging the pattern result into a body is part of the pattern feature
                    fs = p.fs | {idx}
                    for r in res:
                        tracer._register(r, Prov(0, fs))
                    tracer._prov[id(res)] = Prov(1, fs)
                    tracer._keep.append(res)
                    return res
            tracer._register(res, p)
            return res

        return wrapper

    # -- results -------------------------------------------------------------------------------
    def show(self, *objs: Any, **_: Any) -> None:
        self.shown.extend(o for o in objs if o is not None)

    def provenance_of(self, obj: Any) -> int | None:
        return self._n_of(obj)

    def final_features(self, obj: Any) -> tuple[list[Feature], list[Feature]] | None:
        """(features in obj's provenance, traced features that never reach it), or None."""
        p = self._info(obj)
        if p is None:
            return None
        live = [f for i, f in enumerate(self.features) if i in p.fs and i not in self.dropped]
        dead = [f for i, f in enumerate(self.features)
                if i not in p.fs and i not in self.dropped]
        return live, dead


def scan_user_modules(before: set[str], script_path: str) -> list[str]:
    """Static scan of user modules imported by the script (e.g. agent lib helpers)."""
    out: list[str] = []
    for name, mod in list(sys.modules.items()):
        if name in before:
            continue
        fn = getattr(mod, "__file__", None)
        if not fn or not fn.endswith(".py"):
            continue
        fn = os.path.abspath(fn)
        if fn == os.path.abspath(script_path) or fn.startswith(_STDLIB_DIRS):
            continue
        try:
            with open(fn, encoding="utf-8") as fh:
                out.extend(static_unmapped(fh.read(), fn))
        except OSError:
            continue
    return out
