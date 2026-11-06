#!/usr/bin/env python3
"""recad build123d adapter: export | extract | set-param | replay | execute-ir.

L1 measurement instrument (00_DECISIONS D3, 01_ARCHITECTURE §2). Run with the build123d backend's
interpreter; stdlib + build123d only. Every subcommand prints ONE JSON object on stdout (logs go
to stderr) and exits 0 on success, 1 on failure.

Native format: a Python script using build123d. Driving dimensions are module-level numeric
assignments named after IR parameters. The final solid is taken from, in order: module variable
`part`, module variable `result` (a Shape or a BuildPart), the last `show()`n object, the last
top-level BuildPart that was exited.

    adapter.py export <native.py> --out <file.step>
    adapter.py extract <native.py> --out <features.json>
    adapter.py set-param <native.py> <name> <value> --out <file.step> [--native-out <file.py>]
    adapter.py replay <case_id> --out-dir <dir>
    adapter.py execute-ir <re_plan.vN.json> --out <file.step> [--native-out <part.py>]
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(HERE)
REPLAY_DIR = os.path.join(BACKEND_DIR, "replays")
TOOL = "build123d"
STEP_TIMESTAMP = "2000-01-01T00:00:00"  # deterministic STEP header


def log(*a: object) -> None:
    print(*a, file=sys.stderr)


# ---------------------------------------------------------------------------------------------
# running a native script
# ---------------------------------------------------------------------------------------------


class RunError(RuntimeError):
    pass


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def run_script(path: str, source: str | None = None, trace: bool = False):
    """Execute a native script in a fresh namespace. Returns (part, tracer|None, how)."""
    import build123d as bd
    from b3d_trace import Tracer

    path = os.path.abspath(path)
    src = _read(path) if source is None else source
    shown: list = []
    tracer = Tracer(path) if trace else None

    def show(*objs, **_kw):
        shown.extend(o for o in objs if o is not None)

    g = {"__name__": "__recad_native__", "__file__": path, "__builtins__": __builtins__,
         "show": show, "show_object": show}
    code = compile(src, path, "exec")
    script_dir = os.path.dirname(path)
    sys.path.insert(0, script_dir)
    before = set(sys.modules)
    stdout = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout):
            if tracer is not None:
                with tracer:
                    exec(code, g)  # executing the native model is the job
            else:
                exec(code, g)
    except Exception as exc:
        raise RunError(f"native script failed: {type(exc).__name__}: {exc}\n"
                       f"{traceback.format_exc(limit=6)}") from exc
    finally:
        if sys.path and sys.path[0] == script_dir:
            sys.path.pop(0)
        if stdout.getvalue():
            log(stdout.getvalue().rstrip())
    if tracer is not None:
        tracer.new_modules = [m for m in sys.modules if m not in before]
        tracer.module_snapshot = before

    def as_shape(v):
        if isinstance(v, bd.BuildPart):
            return v.part
        return v if isinstance(v, bd.Shape) else None

    def found(v, how):
        if tracer is not None and isinstance(v, bd.BuildPart):
            tracer.final_source = v  # its .part is read after the trace ended
        return as_shape(v), tracer, how

    for key in ("part", "result"):
        if as_shape(g.get(key)) is not None:
            return found(g[key], key)
    if shown and as_shape(shown[-1]) is not None:
        return found(shown[-1], "show()")
    bp = tracer.last_buildpart if tracer is not None else None
    if bp is None:
        bps = [v for v in g.values() if isinstance(v, bd.BuildPart)]
        bp = bps[-1] if bps else None
    if bp is not None and bp.part is not None:
        if tracer is not None:
            tracer.final_source = bp
        return bp.part, tracer, "last BuildPart"
    raise RunError("no final part: assign the solid to `part` (or `result`, or show() it)")


def measure(shape) -> dict:
    solids = shape.solids()
    return {"solids": len(solids), "volume_mm3": float(shape.volume),
            "faces": len(shape.faces())}


def export_shape(shape, out: str) -> dict:
    """Canonical export: mm, exactly one solid; verified by re-importing the STEP."""
    import build123d as bd

    m = measure(shape)
    if m["solids"] != 1:
        return {"ok": False, **m, "error": f"expected exactly one solid, got {m['solids']}"}
    solid = shape.solids()[0]
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    tmp = out + ".tmp.step"
    ok = bd.export_step(solid, tmp, unit=bd.Unit.MM, timestamp=STEP_TIMESTAMP)
    if not ok:
        return {"ok": False, **m, "error": "export_step failed"}
    back = bd.import_step(tmp)
    rm = measure(back)
    if rm["solids"] != 1 or abs(rm["volume_mm3"] - m["volume_mm3"]) > 1e-6 * max(
        1.0, abs(m["volume_mm3"])
    ):
        os.remove(tmp)
        return {"ok": False, **rm, "error": f"STEP re-import mismatch: {rm} vs {m}"}
    os.replace(tmp, out)
    return {"ok": True, **rm}


# ---------------------------------------------------------------------------------------------
# extract
# ---------------------------------------------------------------------------------------------


def extract_features(native: str) -> dict:
    from b3d_trace import module_level_parameters, scan_user_modules, static_unmapped

    src = _read(native)
    doc: dict = {"tool": TOOL, "native_file": os.path.basename(native), "features": [],
                 "count": 0, "unmapped": [],
                 "native_sha256": hashlib.sha256(src.encode("utf-8")).hexdigest()}
    unmapped = static_unmapped(src, native)
    try:
        doc["parameters"] = module_level_parameters(src)
    except SyntaxError:
        doc["parameters"] = []
    part, tracer, how = run_script(native, src, trace=True)
    unmapped += scan_user_modules(tracer.module_snapshot, native)
    unmapped += tracer.unmapped
    final = tracer.final_features(tracer.final_source or part)
    if final is None:
        # gate fails anyway; report everything that was traced
        live = [f for i, f in enumerate(tracer.features) if i not in tracer.dropped]
        dead = []
    else:
        live, dead = final
    feats = [f.as_json() for f in live]
    if not tracer.features:
        unmapped.append("no parametric build123d feature was traced")
    elif final is None:
        unmapped.append(f"final part ({how}) is not the result of traced build123d operations")
    elif not feats:
        unmapped.append(f"no traced build123d feature reaches the final part ({how})")
    doc["features"] = feats
    if dead:
        # traced but not in the final part's provenance (scratch / exploratory geometry):
        # informational only, never counted
        doc["not_in_final_part"] = [f.native_name for f in dead]
    doc["count"] = sum(1 for f in feats if f["counted"])
    doc["unmapped"] = unmapped
    doc["face_count"] = len(part.faces())
    doc["final_part"] = how
    return doc


# ---------------------------------------------------------------------------------------------
# set-param
# ---------------------------------------------------------------------------------------------


def rewrite_param(src: str, name: str, value: float) -> str:
    """Rewrite the single module-level numeric assignment `name = <literal>`."""
    from b3d_trace import literal_number

    tree = ast.parse(src)
    hits = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, val = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, val = node.target, node.value
        else:
            continue
        if isinstance(target, ast.Name) and target.id == name:
            hits.append(val)
    if not hits:
        raise RunError(f"parameter {name!r}: no module-level assignment found")
    if len(hits) > 1:
        raise RunError(f"parameter {name!r}: assigned {len(hits)} times at module level")
    val = hits[0]
    old = literal_number(val)
    if old is None:
        raise RunError(f"parameter {name!r}: assignment is not a numeric literal")
    if isinstance(old, int) and float(value).is_integer():
        new = repr(int(value))
    else:
        new = repr(float(value))
    lines = src.splitlines(keepends=True)
    if val.lineno != val.end_lineno:
        raise RunError(f"parameter {name!r}: literal spans several lines")
    i = val.lineno - 1
    line = lines[i]
    b = len(line.encode("utf-8")[: val.col_offset].decode("utf-8"))
    e = len(line.encode("utf-8")[: val.end_col_offset].decode("utf-8"))
    lines[i] = line[:b] + new + line[e:]
    return "".join(lines)


# ---------------------------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------------------------


def cmd_export(a) -> dict:
    part, _, _ = run_script(a.native)
    return export_shape(part, a.out)


def cmd_extract(a) -> dict:
    try:
        doc = extract_features(a.native)
    except RunError as exc:
        doc = {"tool": TOOL, "native_file": os.path.basename(a.native), "features": [],
               "count": 0, "unmapped": [f"native model did not run: {exc}".splitlines()[0]]}
        _write_json(a.out, doc)
        raise
    _write_json(a.out, doc)
    return {"ok": True, "out": a.out, "count": doc["count"], "unmapped": len(doc["unmapped"]),
            "types": [f["taxonomy_type"] for f in doc["features"]]}


def cmd_set_param(a) -> dict:
    src = _read(a.native)
    before, _, _ = run_script(a.native, src)
    vb = float(before.volume)
    new_src = rewrite_param(src, a.name, float(a.value))
    target = a.native_out
    if target:
        with open(target, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(new_src)
        after, _, _ = run_script(target)
    else:
        after, _, _ = run_script(a.native, new_src)
    res = export_shape(after, a.out)
    out = {"ok": res["ok"], "name": a.name, "value": float(a.value), "volume_before_mm3": vb,
           "volume_after_mm3": res.get("volume_mm3"), "solids": res.get("solids"),
           "faces": res.get("faces")}
    if not res["ok"]:
        out["error"] = res.get("error")
    return out


def cmd_replay(a) -> dict:
    src = os.path.join(REPLAY_DIR, f"{a.case_id}.py")
    if not os.path.isfile(src):
        raise RunError(f"no replay for case {a.case_id!r} ({src})")
    os.makedirs(a.out_dir, exist_ok=True)
    native = os.path.join(a.out_dir, "native.py")
    shutil.copyfile(src, native)
    part, _, _ = run_script(native)
    res = export_shape(part, os.path.join(a.out_dir, "output.step"))
    doc = extract_features(native)
    _write_json(os.path.join(a.out_dir, "features.json"), doc)
    out = {"ok": res["ok"] and not doc["unmapped"], "volume_mm3": res.get("volume_mm3"),
           "faces": res.get("faces"), "count": doc["count"], "solids": res.get("solids"),
           "unmapped": doc["unmapped"], "types": [f["taxonomy_type"] for f in doc["features"]]}
    if not res["ok"]:
        out["error"] = res.get("error")
    return out


def cmd_execute_ir(a) -> dict:
    from b3d_ir import IRError, generate

    with open(a.ir, encoding="utf-8") as fh:
        ir = json.load(fh)
    try:
        script, warnings = generate(ir)
    except IRError as exc:
        return {"ok": False, "error": str(exc)}
    native = a.native_out
    tmpdir = None
    if not native:
        tmpdir = tempfile.mkdtemp(prefix="recad_b3d_ir_")
        native = os.path.join(tmpdir, "part.py")
    with open(native, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(script)
    try:
        part, _, _ = run_script(native)
        res = export_shape(part, a.out)
        doc = extract_features(native)
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)
    out = {"ok": res["ok"], "volume_mm3": res.get("volume_mm3"), "faces": res.get("faces"),
           "solids": res.get("solids"), "count": doc["count"],
           "ir_feature_count": len(ir["features"]), "unmapped": doc["unmapped"],
           "warnings": warnings, "native": a.native_out}
    if not res["ok"]:
        out["error"] = res.get("error")
    return out


def _write_json(path: str, doc: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")


class UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    """Usage errors become {"ok": false} JSON + exit 1 (contract), not argparse's exit 2."""

    def error(self, message: str):  # type: ignore[override]
        raise UsageError(f"{self.prog}: {message}")


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="recad build123d", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=_Parser)
    s = sub.add_parser("export")
    s.add_argument("native")
    s.add_argument("--out", required=True)
    s = sub.add_parser("extract")
    s.add_argument("native")
    s.add_argument("--out", required=True)
    s = sub.add_parser("set-param")
    s.add_argument("native")
    s.add_argument("name")
    s.add_argument("value", type=float)
    s.add_argument("--out", required=True)
    s.add_argument("--native-out")
    s = sub.add_parser("replay")
    s.add_argument("case_id")
    s.add_argument("--out-dir", required=True)
    s = sub.add_parser("execute-ir")
    s.add_argument("ir")
    s.add_argument("--out", required=True)
    s.add_argument("--native-out")
    return p


COMMANDS = {"export": cmd_export, "extract": cmd_extract, "set-param": cmd_set_param,
            "replay": cmd_replay, "execute-ir": cmd_execute_ir}


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except UsageError as exc:
        log(str(exc))
        print(json.dumps({"ok": False, "error": f"usage: {exc}"}))
        return 1
    sys.path.insert(0, HERE)
    try:
        import build123d  # noqa: F401
    except ImportError as exc:
        print(json.dumps({"ok": False, "error": f"build123d not importable: {exc}"}))
        return 1
    try:
        res = COMMANDS[args.cmd](args)
    except Exception as exc:
        log(traceback.format_exc())
        res = {"ok": False, "error": f"{type(exc).__name__}: {exc}".splitlines()[0]}
    print(json.dumps(res))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
