"""RE Plan IR (spec 0.3.0) reading for the NX conformance replays. Pure Python, no NXOpen.

Runs inside NX 2206 (embedded Python 3.10, through `_nxr.py`) and in the CI tests. Stdlib only:
the replays never import recadbench_spec (it is not installed in NX's Python).

0.3.0 rules used here (ADR 0004):
  * any number may be ``{"ref": parameter}``; a ref binds the whole value -> it becomes the
    name of the NX expression (``formula``), so set-param edits it;
  * ``pattern_circular``: ``angle_deg`` = TOTAL SWEEP (step = angle_deg / count, 360 = full
    circle and the default) or ``pitch_deg`` = step; at most one of the two; zero step and
    |count * step| > 360 are errors (same as ``recadbench_spec.circular_offsets``);
  * sketch entities and list-valued params are literals in the replays, so ``load`` rejects a
    ``{"ref"}`` there (``check_refs_are_read``) instead of ignoring it.
"""

import json
import os
import sys

SPEC_VERSION = "0.3.0"
CASES = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "conformance",
    "ir_cases",
)


class IRError(ValueError):
    pass


def load(path):
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    if doc.get("spec_version") != SPEC_VERSION:
        raise IRError(
            f"{path}: spec_version {doc.get('spec_version')!r}, replays read {SPEC_VERSION}"
        )
    check_refs_are_read(doc)
    return doc


def _refs(v, path):
    if is_ref(v):
        yield path
    elif isinstance(v, dict):
        for k, x in v.items():
            yield from _refs(x, f"{path}.{k}")
    elif isinstance(v, list):
        for i, x in enumerate(v):
            yield from _refs(x, f"{path}[{i}]")


def check_refs_are_read(doc):
    """Raise IRError for a {"ref"} the replays would ignore.

    The replays draw sketch entities and list-valued params (hole positions, polygon points) as
    literals and read only the top-level feature params through value / formula /
    count_formula. A ref inside a sketch or inside a list would leave that number fixed while
    set-param edits the expression - a silent divergence - so it is an error until the replay
    reads that value from the IR."""
    for f in doc.get("features") or []:
        fid = f.get("id")
        bad = list(_refs(f.get("sketch"), "sketch"))
        for k, v in (f.get("params") or {}).items():
            if isinstance(v, list):
                bad += list(_refs(v, f"params.{k}"))
        if bad:
            raise IRError(
                f"feature {fid}: {{'ref'}} at {', '.join(bad)} is not read by the NX replays "
                "(sketch entities and list values are literals there)"
            )


def case_path(case_id):
    return os.path.join(CASES, case_id, "re_plan.v1.json")


def for_journal(journal=None):
    """IR of the running replay: ir_cases/<journal stem>/re_plan.v1.json."""
    journal = journal or sys.argv[0]
    return load(case_path(os.path.splitext(os.path.basename(journal))[0]))


def parameters(doc):
    return {p["name"]: float(p["value"]) for p in doc.get("parameters") or []}


def feature(doc, fid):
    for f in doc["features"]:
        if f["id"] == fid:
            return f
    raise IRError(f"no feature {fid!r}")


def is_ref(v):
    return isinstance(v, dict) and set(v) == {"ref"}


def value(doc, v):
    """Numeric value of a number or {"ref"}."""
    if is_ref(v):
        params = parameters(doc)
        if v["ref"] not in params:
            raise IRError(f"ref to undeclared parameter {v['ref']!r}")
        return params[v["ref"]]
    return float(v)


def formula(doc, v):
    """NX expression formula: the parameter name for a {"ref"} (the replay creates that
    expression), else the number."""
    if is_ref(v):
        value(doc, v)  # declared?
        return v["ref"]
    return repr(float(v))


def count_formula(doc, v):
    """Instance-count formula: the parameter name for a {"ref"}, else the integer."""
    n = value(doc, v)
    if n < 1 or n != int(n):
        raise IRError(f"count {n!r} must be an integer >= 1")
    return v["ref"] if is_ref(v) else str(int(n))


def circular_spacing(doc, params):
    """(mode, angle formula, count formula) of a pattern_circular for NX's Angular Spacing.

    ("span", "360", count) for a full circle (angle_deg 360, the default): NX Span 360 keeps the
    circle closed when the count changes; ("pitch", step, count) otherwise, with step =
    angle_deg / count (total sweep) or pitch_deg."""
    if "angle_deg" in params and "pitch_deg" in params:
        raise IRError("pattern_circular: give at most one of angle_deg / pitch_deg")
    count_v = params["count"]
    count = value(doc, count_v)
    count_f = count_formula(doc, count_v)
    if "pitch_deg" in params:
        step = value(doc, params["pitch_deg"])
        step_f = formula(doc, params["pitch_deg"])
    else:
        sweep_v = params.get("angle_deg", 360)
        sweep = value(doc, sweep_v)
        step = sweep / count
        if sweep == 360 and not is_ref(sweep_v):
            return "span", "360", count_f
        step_f = f"({formula(doc, sweep_v)})/({count_f})"
    if step == 0:
        raise IRError("pattern_circular: zero step")
    if abs(count * step) > 360 + 1e-9:
        raise IRError(f"pattern_circular: count x step = {count * step:g} deg > 360 (overlap)")
    return "pitch", step_f, count_f
