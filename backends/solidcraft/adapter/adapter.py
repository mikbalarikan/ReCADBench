#!/usr/bin/env python3
"""recad solidcraft adapter: export | extract | set-param | replay.

L1 measurement instrument (00_DECISIONS D3, 01_ARCHITECTURE §2). Pure Python 3.11+ stdlib; every
model operation shells out to the pinned, unmodified SolidCraft CLI (env RECAD_SOLIDCRAFT_CLI,
default `solidcraft-cli` on PATH). Every subcommand prints ONE JSON object on stdout (logs go to
stderr) and exits 0 on success, 1 on failure.

Native format: a SolidCraft JSON command script `{"commands": [{"command", "params"}, ...]}`
(docs/scripts.md; the same format `solidcraft-cli script design.sc` writes). Driving dimensions
are SolidCraft user parameters named after IR parameters (`parameters.add` or a
`sketch.dimension` "name = expr"), referenced by name in feature/sketch expressions.

    adapter.py export <native.json> --out <file.step>
    adapter.py extract <native.json> --out <features.json>
    adapter.py set-param <native.json> <name> <value> --out <file.step> [--native-out <file.json>]
    adapter.py replay <case_id> --out-dir <dir>

How each instrument reads the model (never agent self-report):
- The script is rebuilt by `solidcraft-cli run <script> --results` in a fresh process, with
  read-only `document.inspect` / `parameters.list` / `timeline.inspect` commands appended; the
  feature list is SolidCraft's own timeline after the rebuild (type names from FeatureKind), so
  later `timeline.delete` / `timeline.suppress` / edits are honoured.
- Volume and face count are SolidCraft's measurement of the exported STEP re-imported in a
  second clean process (`solidcraft-cli eval output.step`). SolidCraft's volume is a numeric
  (not closed-form) integral: on the conformance cases it is within 2e-4 relative of the exact
  B-rep volume; the dispatcher may re-measure output.step with OCP for an exact value.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(HERE)
REPLAY_DIR = os.path.join(BACKEND_DIR, "replays")
TOOL = "solidcraft"
CLI_ENV = "RECAD_SOLIDCRAFT_CLI"
TIMEOUT_S = 600
VOLUME_METHOD = "solidcraft-cli eval of the re-imported STEP (numeric; <=2e-4 rel observed)"

# ---------------------------------------------------------------------------------------------
# taxonomy mapping (spec/recadbench_spec/feature_taxonomy.yaml, spec 0.3.0; names as in 0.1.0)
# ---------------------------------------------------------------------------------------------

COUNTED = {
    "primitive", "extrude", "revolve", "sweep", "loft", "hole", "fillet", "chamfer", "shell",
    "draft", "rib", "boolean", "pattern_linear", "pattern_circular", "mirror", "thread_cosmetic",
    "freeform_region",
}

# SolidCraft timeline type name (FeatureKind::type_name, v0.30.1) -> taxonomy type.
TYPE_MAP = {
    # counted
    "ExtrudeFeature": "extrude",
    "RevolveFeature": "revolve",
    "SweepFeature": "sweep",
    "PipeFeature": "sweep",  # profile swept along a path (solid.pipe)
    "CoilFeature": "sweep",  # helical sweep (solid.coil)
    "LoftFeature": "loft",
    "HoleFeature": "hole",  # counterbore/countersink/tapped are parameters of the one hole
    "FilletFeature": "fillet",
    "ChamferFeature": "chamfer",
    "ShellFeature": "shell",
    "DraftFeature": "draft",
    "RibFeature": "rib",
    "WebFeature": "rib",
    "CombineFeature": "boolean",  # may be folded into its tool's feature, see _combine_merges
    "RectangularPatternFeature": "pattern_linear",
    "PathPatternFeature": "pattern_linear",  # no taxonomy type for a path pattern (see README)
    "CircularPatternFeature": "pattern_circular",
    "MirrorFeature": "mirror",
    "ThreadFeature": "thread_cosmetic",
    "BoxFeature": "primitive",
    "CylinderFeature": "primitive",
    "SphereFeature": "primitive",
    "TorusFeature": "primitive",
    "ConeFeature": "primitive",
    # not counted
    "Sketch": "sketch",
    "ConstructionPlane": "datum_plane",
    "ConstructionAxis": "datum_axis",
    "ConstructionPoint": "datum_point",
    "MoveFeature": "transform",
    "AlignFeature": "transform",
    "BodyTransferFeature": "transform",
}

# Timeline types that are not parametric features: imported / dumb geometry.
DUMB_TYPES = {
    "BaseFeature": "imported B-rep (file.insert_step / opened STEP): not a parametric feature",
    "MeshFeature": "imported mesh body: not a parametric feature",
    "MeshConvertFeature": "mesh converted to a B-rep body: not a parametric feature",
}

# Commands that bring external geometry into the design (bodies, meshes, curves, designs).
IMPORT_COMMANDS = {
    "file.insert_step", "file.insert_mesh", "doc.open", "component.insert_design",
    "component.paste_from_design", "component.relink", "component.replace_with_link",
    "parts.insert", "parts.fastener", "library.apply", "sketch.insert_dxf", "sketch.insert_svg",
    "sketch.fit_curves_to_section", "canvas.to_sketch", "frame.profile.insert",
}
IMPORT_PREFIXES = ("pointcloud.",)


class RunError(RuntimeError):
    pass


def log(*a: object) -> None:
    print(*a, file=sys.stderr)


# ---------------------------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------------------------


def cli_path() -> str:
    exe = os.environ.get(CLI_ENV) or "solidcraft-cli"
    found = shutil.which(exe)
    if not found:
        raise RunError(f"SolidCraft CLI not found ({CLI_ENV}={exe!r})")
    return found


def cli_version() -> str:
    proc = subprocess.run([cli_path(), "--version"], capture_output=True, text=True, timeout=60)
    out = (proc.stdout or proc.stderr).strip().splitlines()
    return out[0] if out and proc.returncode == 0 else "unknown"


def design_to_script(path: str) -> dict:
    """A saved design (.sc) -> its history as a command script (`solidcraft-cli script`)."""
    tmp = tempfile.mkdtemp(prefix="recad_sc_")
    try:
        out = os.path.join(tmp, "script.json")
        proc = subprocess.run([cli_path(), "script", path, "--out", out], capture_output=True,
                              text=True, timeout=TIMEOUT_S)
        if proc.returncode != 0 or not os.path.isfile(out):
            msg = (proc.stderr or proc.stdout).strip().splitlines()
            raise RunError(f"solidcraft-cli script failed: {msg[-1] if msg else proc.returncode}")
        with open(out, encoding="utf-8") as fh:
            return json.load(fh)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def load_script(path: str) -> dict:
    if path.lower().endswith(".sc"):
        doc = design_to_script(path)
    else:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    if isinstance(doc, list):
        doc = {"commands": doc}
    if not isinstance(doc, dict) or not isinstance(doc.get("commands"), list):
        raise RunError(f"{path}: not a SolidCraft command script ({{\"commands\": [...]}})")
    for i, c in enumerate(doc["commands"]):
        if not isinstance(c, dict) or not isinstance(c.get("command"), str):
            raise RunError(f"{path}: command #{i} has no 'command' id")
    return doc


def run_cli(script: dict, outs: tuple[str, ...] = ()) -> list:
    """Rebuild `script` in a fresh headless session; return the per-command results."""
    tmp = tempfile.mkdtemp(prefix="recad_sc_")
    try:
        sp = os.path.join(tmp, "script.json")
        rp = os.path.join(tmp, "results.json")
        with open(sp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(script, fh)
        cmd = [cli_path(), "run", sp, "--results", "--quiet", "--json-out", rp]
        for o in outs:
            cmd += ["--out", o]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S, cwd=tmp)
        if proc.stderr.strip():
            log(proc.stderr.strip()[-4000:])
        if proc.returncode != 0:
            msg = (proc.stderr or proc.stdout).strip().splitlines()
            raise RunError(f"solidcraft-cli run failed: {msg[-1] if msg else proc.returncode}")
        with open(rp, encoding="utf-8") as fh:
            res = json.load(fh)
        if not isinstance(res, list) or len(res) != len(script["commands"]):
            raise RunError("solidcraft-cli run: unexpected --results shape")
        return res
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def eval_file(path: str) -> dict:
    proc = subprocess.run([cli_path(), "eval", path], capture_output=True, text=True,
                          timeout=TIMEOUT_S)
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout).strip().splitlines()
        raise RunError(f"solidcraft-cli eval failed: {msg[-1] if msg else proc.returncode}")
    return json.loads(proc.stdout)


def with_tail(script: dict, *tail: dict) -> dict:
    s = copy.deepcopy(script)
    s["commands"] = list(s["commands"]) + list(tail)
    return s


INSPECT = {"command": "document.inspect", "params": {"measure": True}}
PLIST = {"command": "parameters.list", "params": {}}


def rebuild(script: dict, outs: tuple[str, ...] = ()) -> tuple[list, dict, dict]:
    """Run the script, return (command results, document.inspect, parameters.list)."""
    res = run_cli(with_tail(script, INSPECT, PLIST), outs)
    return res[:-2], res[-2], res[-1]


# ---------------------------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------------------------


def step_is_mm(path: str) -> bool:
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    units = re.findall(r"LENGTH_UNIT\(\)\s*NAMED_UNIT\(\*\)\s*SI_UNIT\(([^)]*)\)", text)
    return bool(units) and all(u.replace(" ", "") == ".MILLI.,.METRE." for u in units)


def body_check(inspect: dict) -> str | None:
    bodies = inspect.get("bodies") or []
    if len(bodies) != 1:
        names = [b.get("name") for b in bodies]
        return f"expected exactly one body in the design, got {len(bodies)} {names}"
    if not bodies[0].get("closed"):
        return f"body {bodies[0].get('name')!r} is not a closed solid"
    return None


def export_script(script: dict, out: str) -> dict:
    out = os.path.abspath(out)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    tmp = out + ".tmp.step"
    _, inspect, _ = rebuild(script, (tmp,))
    err = body_check(inspect)
    model = inspect["bodies"][0] if len(inspect.get("bodies") or []) == 1 else {}
    if err:
        if os.path.exists(tmp):
            os.remove(tmp)
        return {"ok": False, "solids": len(inspect.get("bodies") or []), "volume_mm3": None,
                "faces": None, "error": err}
    if not os.path.isfile(tmp):
        return {"ok": False, "solids": 1, "volume_mm3": None, "faces": None,
                "error": "solidcraft-cli wrote no STEP"}
    if not step_is_mm(tmp):
        os.remove(tmp)
        return {"ok": False, "solids": 1, "volume_mm3": None, "faces": None,
                "error": "STEP length unit is not millimetre"}
    back = eval_file(tmp)  # re-import in a clean process
    bodies = back.get("bodies") or []
    vol = (back.get("total") or {}).get("volume_mm3")
    faces = (back.get("total") or {}).get("faces")
    res = {"solids": len(bodies), "volume_mm3": vol, "faces": faces,
           "volume_method": VOLUME_METHOD, "needs_measure": vol is None,
           "model_volume_mm3": model.get("volume_mm3"), "model_faces": model.get("faces")}
    if len(bodies) != 1 or not bodies[0].get("closed"):
        os.remove(tmp)
        return {"ok": False, **res, "error": f"STEP re-import: {len(bodies)} bodies / not closed"}
    mv = model.get("volume_mm3")
    if vol is not None and mv is not None and abs(vol - mv) > 1e-3 * max(1.0, abs(mv)):
        os.remove(tmp)
        return {"ok": False, **res, "error": f"STEP re-import volume {vol} != model {mv}"}
    os.replace(tmp, out)
    return {"ok": True, **res}


# ---------------------------------------------------------------------------------------------
# extract
# ---------------------------------------------------------------------------------------------


def _feature_index(results: list) -> dict:
    """timeline feature id -> (command index, command result) for feature-creating commands."""
    idx = {}
    for i, r in enumerate(results):
        if isinstance(r, dict) and isinstance(r.get("feature"), int):
            idx.setdefault(r["feature"], (i, r))
    return idx


def _fill_body_deltas(script: dict, timeline: list, by_feature: dict) -> None:
    """Add created/changed bodies to feature results that lack them, in place.

    Features replayed through `timeline.add` (a saved .sc design, or its `solidcraft-cli script`
    export) return no `created_bodies`/`changed_bodies`. Roll the history marker to after each
    feature and diff the bodies (name -> faces, volume) so the same model folds its single-tool
    join exactly like the command script that built it.
    """
    missing = [t["id"] for t in timeline
               if "created_bodies" not in by_feature.get(t["id"], (None, {}))[1]]
    if not missing:
        return
    tail = []
    for t in timeline:
        tail += [{"command": "timeline.roll_to", "params": {"feature": t["id"]}}, INSPECT]
    tail.append({"command": "timeline.roll_to", "params": {"position": "end"}})
    res = run_cli(with_tail(script, *tail))[len(script["commands"]):]
    prev: dict = {}
    for k, t in enumerate(timeline):
        bodies = (res[2 * k + 1] or {}).get("bodies") or []
        cur = {b.get("name"): (b.get("faces"), round(float(b.get("volume_mm3") or 0.0), 6))
               for b in bodies}
        if t["id"] in missing:
            cmd_i, r = by_feature.get(t["id"], (None, {}))
            r = dict(r, created_bodies=[n for n in cur if n not in prev],
                     changed_bodies=[n for n in cur if n in prev and cur[n] != prev[n]])
            by_feature[t["id"]] = (cmd_i, r)
        prev = cur


def _combine_merges(defn: dict, fid: int, timeline: list, by_feature: dict) -> str | None:
    """Name of the single feature whose result this Combine only joins into the body, if any.

    feature_taxonomy counting_rule: "A boolean that only unites the result of a single feature
    into the body is part of that feature". Holds for a join whose tools are bodies created by
    one counted feature and changed by no other feature before the combine.
    """
    if (defn.get("operation") or "join") != "join":
        return None
    tools = defn.get("tools") or []
    if len(tools) != 1:
        return None
    tool = tools[0]
    creators, changers = [], []
    for t in timeline:
        if t["id"] >= fid:
            break
        r = by_feature.get(t["id"], (None, {}))[1]
        if tool in (r.get("created_bodies") or []):
            creators.append(t)
        elif tool in (r.get("changed_bodies") or []):
            changers.append(t)
    if len(creators) == 1 and not changers:
        tax = TYPE_MAP.get(creators[0]["type"])
        if tax in COUNTED and tax != "boolean":
            return creators[0]["name"]
    return None


def _parameters(plist: dict) -> list:
    params = plist.get("parameters") or []
    out = []
    for p in params:
        if p.get("source") != "user":
            continue
        drives, seen, todo = set(), set(), [p["name"]]
        while todo:  # transitive: user params used by other params/feature inputs
            n = todo.pop()
            for q in params:
                if n in (q.get("uses") or []) and q["name"] not in seen:
                    seen.add(q["name"])
                    if q.get("feature"):
                        drives.add(q["feature"])
                    todo.append(q["name"])
        item = {"name": p["name"], "value": p.get("value"), "expression": p.get("expression"),
                "unit": p.get("unit")}
        if drives:
            item["feature"] = ",".join(sorted(drives))
        out.append(item)
    return out


def extract_features(native: str) -> dict:
    with open(native, "rb") as fh:
        raw = fh.read()
    doc: dict = {"tool": TOOL, "native_file": os.path.basename(native), "features": [],
                 "count": 0, "unmapped": [], "native_sha256": hashlib.sha256(raw).hexdigest()}
    script = load_script(native)
    unmapped = doc["unmapped"]
    for i, c in enumerate(script["commands"]):
        cid = c["command"]
        if cid in IMPORT_COMMANDS or cid.startswith(IMPORT_PREFIXES):
            unmapped.append(f"command #{i} {cid}: imports external geometry")
    results, inspect, plist = rebuild(script)
    timeline = [t for t in inspect.get("timeline") or [] if isinstance(t, dict)]
    by_feature = _feature_index(results)
    if any(t.get("type") == "CombineFeature" for t in timeline):
        _fill_body_deltas(script, timeline, by_feature)
    by_name = {r["name"]: i for i, r in enumerate(results)
               if isinstance(r, dict) and isinstance(r.get("name"), str) and "sketch" in r}
    # second clean rebuild: SolidCraft's own definition of every timeline item
    tl_inspect = run_cli(with_tail(script, *[
        {"command": "timeline.inspect", "params": {"feature": t["id"]}} for t in timeline
    ]))[len(script["commands"]):] if timeline else []
    feats, excluded = [], []
    for t, ti in zip(timeline, tl_inspect or [{}] * len(timeline), strict=True):
        ntype = t.get("type") or "?"
        label = f"{t.get('name')} ({ntype})"
        defn = (ti or {}).get("definition") or {}
        cmd_i, _ = by_feature.get(t["id"], (None, None))
        if cmd_i is None:
            cmd_i = by_name.get(t.get("name"))
        if t.get("suppressed") or t.get("rolled_back"):
            # not in the model: neither a feature nor unmapped
            excluded.append({"native_name": t.get("name"), "native_type": ntype,
                             "reason": "suppressed" if t.get("suppressed") else "rolled back"})
            continue
        if t.get("error"):
            unmapped.append(f"{label}: failed to rebuild: {t['error']}")
        if ntype in DUMB_TYPES:
            unmapped.append(f"{label}: {DUMB_TYPES[ntype]}")
            continue
        if ntype not in TYPE_MAP:
            unmapped.append(f"{label}: no feature_taxonomy type for this SolidCraft feature")
            continue
        tax = TYPE_MAP[ntype]
        f = {"native_name": t.get("name"), "native_type": ntype, "taxonomy_type": tax,
             "counted": tax in COUNTED,
             "params": {"definition": defn,
                        "command": script["commands"][cmd_i]["command"] if cmd_i is not None
                        else None}}
        if ntype == "CombineFeature":
            merged = _combine_merges(defn, t["id"], timeline, by_feature)
            if merged:
                f["counted"] = False
                f["params"]["merged_into"] = merged
        if ntype == "PathPatternFeature":
            f["params"]["note"] = "pattern on path: no taxonomy type, counted as pattern_linear"
        feats.append(f)
    if excluded:
        doc["excluded"] = excluded
    if not any(f["counted"] for f in feats):
        unmapped.append("no counted parametric SolidCraft feature in the timeline")
    doc["features"] = feats
    doc["count"] = sum(1 for f in feats if f["counted"])
    doc["parameters"] = _parameters(plist)
    bodies = inspect.get("bodies") or []
    if len(bodies) == 1 and isinstance(bodies[0].get("faces"), int):
        doc["face_count"] = bodies[0]["faces"]
    doc["bodies"] = [b.get("name") for b in bodies]
    return doc


# ---------------------------------------------------------------------------------------------
# set-param
# ---------------------------------------------------------------------------------------------

LENGTH_UNITS = {"mm", "cm", "m", "in", "ft"}
ANGLE_UNITS = {"deg", "rad"}


def param_expression(value: float, unit: str | None) -> str:
    v = repr(float(value))
    if unit in LENGTH_UNITS:
        return f"{v} mm"  # IR values are mm / deg whatever the parameter's display unit
    if unit in ANGLE_UNITS:
        return f"{v} deg"
    return v


def set_param_script(script: dict, name: str, value: float, unit: str | None) -> dict:
    """Native edit: rewrite the parameter's single definition, else append parameters.change."""
    expr = param_expression(value, unit)
    s = copy.deepcopy(script)
    hits = [c for c in s["commands"]
            if c["command"] in ("parameters.add", "parameters.change")
            and isinstance(c.get("params"), dict) and c["params"].get("name") == name
            and "expression" in c["params"] and not c["params"].get("delete")]
    if len(hits) == 1:
        hits[0]["params"]["expression"] = expr
        if hits[0]["params"].get("unit") not in (None, *LENGTH_UNITS, *ANGLE_UNITS):
            hits[0]["params"].pop("unit", None)
    else:
        s["commands"].append({"command": "parameters.change",
                              "params": {"name": name, "expression": expr}})
    return s


def cmd_set_param(a) -> dict:
    script = load_script(a.native)
    _, inspect, plist = rebuild(script)
    vb = ((inspect.get("bodies") or [{}])[0] or {}).get("volume_mm3")
    params = {p["name"]: p for p in plist.get("parameters") or []}
    if a.name not in params:
        users = sorted(n for n, p in params.items() if p.get("source") == "user")
        return {"ok": False, "error": f"parameter {a.name!r} not in the model (user: {users})"}
    new = set_param_script(script, a.name, a.value, params[a.name].get("unit"))
    if a.native_out:
        with open(a.native_out, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(new, fh, indent=1)
            fh.write("\n")
    _, _, plist2 = rebuild(new)
    got = {p["name"]: p for p in plist2.get("parameters") or []}.get(a.name, {}).get("value")
    if got is None or abs(float(got) - float(a.value)) > 1e-9 * max(1.0, abs(a.value)):
        return {"ok": False, "error": f"parameter {a.name!r} is {got} after the edit"}
    res = export_script(new, a.out)
    out = {"ok": res["ok"], "name": a.name, "value": float(a.value), "volume_before_mm3": vb,
           "volume_after_mm3": res.get("volume_mm3"), "solids": res.get("solids"),
           "faces": res.get("faces"), "volume_method": VOLUME_METHOD,
           "needs_measure": res.get("needs_measure", False)}
    if not res["ok"]:
        out["error"] = res.get("error")
    return out


# ---------------------------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------------------------


def cmd_export(a) -> dict:
    return export_script(load_script(a.native), a.out)


def cmd_extract(a) -> dict:
    try:
        doc = extract_features(a.native)
    except (RunError, OSError, ValueError) as exc:
        doc = {"tool": TOOL, "native_file": os.path.basename(a.native), "features": [],
               "count": 0, "unmapped": [f"native model did not rebuild: {exc}".splitlines()[0]]}
        _write_json(a.out, doc)
        raise
    _write_json(a.out, doc)
    return {"ok": True, "out": a.out, "count": doc["count"], "unmapped": len(doc["unmapped"]),
            "types": [f["taxonomy_type"] for f in doc["features"] if f["counted"]]}


def cmd_replay(a) -> dict:
    src = os.path.join(REPLAY_DIR, f"{a.case_id}.json")
    if not os.path.isfile(src):
        raise RunError(f"no replay for case {a.case_id!r} ({src})")
    os.makedirs(a.out_dir, exist_ok=True)
    native = os.path.join(a.out_dir, "native.json")
    shutil.copyfile(src, native)
    res = export_script(load_script(native), os.path.join(a.out_dir, "output.step"))
    doc = extract_features(native)
    _write_json(os.path.join(a.out_dir, "features.json"), doc)
    out = {"ok": res["ok"] and not doc["unmapped"], "volume_mm3": res.get("volume_mm3"),
           "faces": res.get("faces"), "count": doc["count"], "solids": res.get("solids"),
           "unmapped": doc["unmapped"],
           "types": [f["taxonomy_type"] for f in doc["features"] if f["counted"]],
           "volume_method": VOLUME_METHOD, "needs_measure": res.get("needs_measure", False)}
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
    p = _Parser(prog="recad solidcraft", description=__doc__.splitlines()[0])
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
    sub.add_parser("version")
    return p


def cmd_version(_a) -> dict:
    return {"ok": True, "cli": cli_path(), "version": cli_version()}


COMMANDS = {"export": cmd_export, "extract": cmd_extract, "set-param": cmd_set_param,
            "replay": cmd_replay, "version": cmd_version}


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except UsageError as exc:
        log(str(exc))
        print(json.dumps({"ok": False, "error": f"usage: {exc}"}))
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
