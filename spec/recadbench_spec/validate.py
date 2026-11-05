"""Validation engine: JSON Schema → pydantic model → semantic rules.

Three layers, reported separately so a failure is easy to attribute:

1. ``schema``   — the packaged JSON Schema (Draft 2020-12). For the RE Plan IR this is the
   *compiled* schema: the on-disk schema plus per-type rules generated from the taxonomy
   (allowed ``params`` keys, ``sketch`` required when ``needs_sketch``).
2. ``model``    — parsing into the pydantic model. Should never fail after layer 1; a failure
   here means the models drifted from the schemas.
3. ``semantic`` — cross-field rules JSON Schema cannot express (references resolve, ids are
   unique, counts are consistent, track rules, folder name == id …). Listed in spec/README.md.

Semantic warnings never make a document invalid.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ValidationError

from .models import MODELS, SPEC_VERSION
from .taxonomy import Taxonomy, load_taxonomy

SCHEMA_NAMES: tuple[str, ...] = tuple(MODELS)

# file name → schema (exact basenames first, then patterns)
_BASENAMES = {
    "task.yaml": "task",
    "gt_plan.json": "re_plan_ir",
    "key_dims.yaml": "key_dims",
    "key_dims.yml": "key_dims",
    "meta.yaml": "gt_meta",
    "features.json": "features",
    "backend.yaml": "backend_manifest",
    "skillset.yaml": "skillset_manifest",
    "strategy.yaml": "strategy_manifest",
    "run_manifest.json": "run_manifest",
    "result.json": "result",
}
_PLAN_FILE = re.compile(r"^re_plan\.v([0-9]+)\.json$")

# IR params whose string values must be selectors / feature ids (checked semantically)
_SELECTOR_PARAMS = {"edges", "faces", "open_faces", "neutral", "plane", "axis", "direction", "path"}
_FEATURE_LIST_PARAMS = {"features"}
_FEATURE_ID_PARAMS = {"tool_feature"}
_SELECTOR_RE = re.compile(
    r"^[A-Za-z][A-Za-z0-9_]*(:(plane|cylinder|cone|sphere|torus|bspline|any):([+-][XYZ]|axis|any)"
    r":(max|min|any)(:[0-9]+)?|:edges:(all|outer|inner|convex|concave))?$"
)
_SKILLSET_ID = re.compile(r"^(none|(nx|build123d|solidcraft|freecad)@v[0-9]+)$")


# ------------------------------------------------------------------------------- reporting
@dataclass(frozen=True)
class Issue:
    layer: str  # schema | model | semantic
    level: str  # error | warning
    path: str  # JSON-pointer-like location, "/" = document root
    message: str

    def __str__(self) -> str:
        return f"{self.level.upper():7} [{self.layer}] {self.path}: {self.message}"


@dataclass
class Report:
    schema: str
    source: str | None = None
    issues: list[Issue] = field(default_factory=list)
    model: BaseModel | None = None

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "schema": self.schema,
            "ok": self.ok,
            "issues": [i.__dict__ for i in self.issues],
        }


class _Sink:
    def __init__(self) -> None:
        self.issues: list[Issue] = []

    def error(self, path: str, msg: str) -> None:
        self.issues.append(Issue("semantic", "error", path, msg))

    def warn(self, path: str, msg: str) -> None:
        self.issues.append(Issue("semantic", "warning", path, msg))


# --------------------------------------------------------------------------------- schemas
def schema_path(name: str) -> Path:
    _check_name(name)
    return Path(str(resources.files("recadbench_spec").joinpath(f"schemas/{name}.schema.json")))


@cache
def _raw_schema(name: str) -> str:
    _check_name(name)
    return (
        resources.files("recadbench_spec")
        .joinpath(f"schemas/{name}.schema.json")
        .read_text(encoding="utf-8")
    )


def load_schema(name: str) -> dict[str, Any]:
    """The packaged JSON Schema exactly as on disk."""
    return json.loads(_raw_schema(name))


def compiled_schema(name: str, taxonomy: Taxonomy | None = None) -> dict[str, Any]:
    """The schema used for validation. For ``re_plan_ir``: on-disk schema + taxonomy rules.

    Generated per taxonomy type ``t``: ``if type == t then params.propertyNames ∈ params(t)``,
    and ``required: [sketch]`` when ``needs_sketch(t)``. The ``type`` enum itself is replaced by
    the taxonomy's list (the on-disk enum is kept in sync by a test).
    """
    schema = load_schema(name)
    if name != "re_plan_ir":
        return schema
    tax = taxonomy or load_taxonomy()
    item = schema["properties"]["features"]["items"]
    item["properties"]["type"]["enum"] = list(tax.feature_types)
    rules = []
    for t, spec in tax.types.items():
        then: dict[str, Any] = {
            "properties": {"params": {"propertyNames": {"enum": list(spec.params)}}}
        }
        if spec.needs_sketch:
            then["required"] = ["sketch"]
        rules.append(
            {"if": {"properties": {"type": {"const": t}}, "required": ["type"]}, "then": then}
        )
    item["allOf"] = list(item.get("allOf", [])) + rules
    return schema


@cache
def _validator(name: str) -> Draft202012Validator:
    schema = compiled_schema(name)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _check_name(name: str) -> None:
    if name not in MODELS:
        raise KeyError(f"unknown schema {name!r}; known: {', '.join(SCHEMA_NAMES)}")


# ------------------------------------------------------------------------- loading / detect
def detect_schema(path: str | Path) -> str | None:
    """Schema name from a file name (``re_plan.v3.json`` → ``re_plan_ir``), else None."""
    name = Path(path).name.lower()
    if _PLAN_FILE.match(name):
        return "re_plan_ir"
    return _BASENAMES.get(name)


def load_document(path: str | Path) -> Any:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() == ".json":
        return json.loads(text)
    return yaml.safe_load(text)


# ------------------------------------------------------------------------------- validation
def validate_document(
    doc: Any, schema: str, *, source: str | Path | None = None, taxonomy: Taxonomy | None = None
) -> Report:
    """Validate an already-loaded document. ``source`` enables file-name rules."""
    report = Report(schema=schema, source=str(source) if source is not None else None)
    validator = (
        _validator(schema)
        if taxonomy is None
        else Draft202012Validator(compiled_schema(schema, taxonomy))
    )
    for err in sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path)):
        report.issues.append(Issue("schema", "error", _pointer(err.absolute_path), err.message))
    if report.errors:
        return report
    try:
        report.model = MODELS[schema].model_validate(doc)
    except ValidationError as exc:
        for e in exc.errors():
            report.issues.append(Issue("model", "error", _pointer(e["loc"]), e["msg"]))
        return report
    sink = _Sink()
    _SEMANTIC[schema](doc, sink, taxonomy or load_taxonomy(), Path(source) if source else None)
    report.issues.extend(sink.issues)
    return report


def validate_file(path: str | Path, schema: str | None = None) -> Report:
    """Load and validate a JSON/YAML file; the schema is detected from its name if omitted."""
    name = schema or detect_schema(path)
    if name is None:
        raise ValueError(
            f"cannot detect the schema of {Path(path).name!r}; pass schema= (one of "
            f"{', '.join(SCHEMA_NAMES)})"
        )
    return validate_document(load_document(path), name, source=path)


def _pointer(parts: Any) -> str:
    return "/" + "/".join(str(p) for p in parts)


# ---------------------------------------------------------------------------- semantic rules
def _dupes(values: list[Any]) -> list[Any]:
    return sorted(v for v, n in Counter(values).items() if n > 1)


def _norm(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v))


def _sem_re_plan(doc: dict, s: _Sink, tax: Taxonomy, src: Path | None) -> None:
    # file name ↔ revision (gt_plan.json has no revision in its name)
    if src is not None and (m := _PLAN_FILE.match(src.name.lower())):
        if int(m.group(1)) != doc["revision"]:
            s.error(
                "/revision", f"revision {doc['revision']} != N={m.group(1)} in file name {src.name}"
            )
    if doc["revision"] > 1 and not doc.get("change_log"):
        s.error("/change_log", "revision > 1 requires a non-empty change_log")

    datums = doc["datums"]
    for d in _dupes([d["role"] for d in datums]):
        s.error("/datums", f"datum role {d!r} used more than once")
    if "primary" not in {d["role"] for d in datums}:
        s.error("/datums", "a primary datum is required")
    for i, d in enumerate(datums):
        for key in ("direction", "x_dir"):
            if key in d and _norm(d[key]) == 0:
                s.error(f"/datums/{i}/{key}", "zero-length vector")
    for i, rp in enumerate(doc.get("reference_planes") or []):
        for key in ("normal", "x_dir"):
            if _norm(rp[key]) == 0:
                s.error(f"/reference_planes/{i}/{key}", "zero-length vector")

    frame_ids = [d["id"] for d in datums] + [r["id"] for r in doc.get("reference_planes") or []]
    feature_ids = [f["id"] for f in doc["features"]]
    for dup in _dupes(frame_ids + feature_ids):
        s.error("/", f"id {dup!r} is not unique across datums, reference_planes and features")

    params = doc.get("parameters") or []
    param_names = [p["name"] for p in params]
    for dup in _dupes(param_names):
        s.error("/parameters", f"parameter name {dup!r} is not unique")
    used_params: set[str] = set()

    known = set(frame_ids)  # grows as features are declared: refs may only point backwards
    for i, f in enumerate(doc["features"]):
        base = f"/features/{i}"

        def resolve(sel: str, where: str, _known: set[str] = known, _fid: str = f["id"]) -> None:
            target = sel.split(":", 1)[0]
            if target == _fid:
                s.error(where, f"{sel!r} refers to the feature itself")
            elif target not in _known:
                hint = (
                    " (defined later — refs must point to earlier features)"
                    if target in feature_ids
                    else ""
                )
                s.error(where, f"{sel!r}: unknown datum/plane/feature id {target!r}{hint}")

        if f.get("sketch"):
            resolve(f["sketch"]["plane"], f"{base}/sketch/plane")
        if f.get("sketch") and not tax.needs_sketch(f["type"]):
            s.warn(
                f"{base}/sketch", f"type {f['type']!r} does not use a sketch (needs_sketch=false)"
            )
        for j, sel in enumerate(f.get("refs") or []):
            resolve(sel, f"{base}/refs/{j}")
        for key, val in f["params"].items():
            where = f"{base}/params/{key}"
            if isinstance(val, dict) and "ref" in val:
                used_params.add(val["ref"])
                if val["ref"] not in param_names:
                    s.error(where, f"references undeclared parameter {val['ref']!r}")
            elif key in _FEATURE_ID_PARAMS:
                if not isinstance(val, str):
                    s.error(where, "must be a feature id")
                else:
                    resolve(val, where)
            elif key in _FEATURE_LIST_PARAMS:
                if not (isinstance(val, list) and val and all(isinstance(v, str) for v in val)):
                    s.error(where, "must be a non-empty list of feature ids")
                else:
                    for v in val:
                        resolve(v, where)
            elif key in _SELECTOR_PARAMS:
                for v in val if isinstance(val, list) else [val]:
                    if isinstance(v, str):
                        if not _SELECTOR_RE.match(v):
                            s.error(where, f"{v!r} is not a valid selector")
                        else:
                            resolve(v, where)
        known.add(f["id"])

    for unused in sorted(set(param_names) - used_params):
        s.warn("/parameters", f"parameter {unused!r} is not referenced by any feature")
    if doc["spec_version"] != SPEC_VERSION:  # pragma: no cover - schema const pins it today
        s.error("/spec_version", f"expected {SPEC_VERSION}")


def _sem_task(doc: dict, s: _Sink, tax: Taxonomy, src: Path | None) -> None:
    for d in _dupes(doc["deliverables"]):
        s.error("/deliverables", f"duplicate deliverable {d!r}")
    if not doc["input_stl"].lower().endswith(".stl"):
        s.error("/input_stl", "must be an .stl path")
    for key in ("max_wall_s", "max_turns", "max_tokens"):
        if key in doc["budgets"] and doc["budgets"][key] <= 0:
            s.error(f"/budgets/{key}", "must be positive")


def _sem_key_dims(doc: dict, s: _Sink, tax: Taxonomy, src: Path | None) -> None:
    for d in _dupes([d["name"] for d in doc["dims"]]):
        s.error("/dims", f"dim name {d!r} is not unique")
    for i, d in enumerate(doc["dims"]):
        axis = d["measure"].get("axis_dir")
        if axis is not None and _norm(axis) == 0:
            s.error(f"/dims/{i}/measure/axis_dir", "zero-length vector")
        if d["kind"] in ("diameter", "radius") and d["nominal"] <= 0:
            s.error(f"/dims/{i}/nominal", f"{d['kind']} must be positive")


def _sem_gt_meta(doc: dict, s: _Sink, tax: Taxonomy, src: Path | None) -> None:
    if unknown := sorted(set(doc["face_types"]) - set(tax.face_types)):
        s.error("/face_types", f"unknown face types {unknown}; allowed {list(tax.face_types)}")
    total = sum(doc["face_types"].values())
    if any(v < 0 for v in doc["face_types"].values()):
        s.error("/face_types", "area fractions must be ≥ 0")
    elif abs(total - 1.0) > 0.01:
        s.error("/face_types", f"area fractions must sum to 1 (got {total:.3f})")
    for key in ("n_ref", "faces_gt", "b1"):
        if doc[key] < (0 if key == "b1" else 1):
            s.error(f"/{key}", "out of range")
    if (chord := doc.get("tessellation_chord_mm")) is not None and chord <= 0:
        s.error("/tessellation_chord_mm", "must be positive")


def _sem_features(doc: dict, s: _Sink, tax: Taxonomy, src: Path | None) -> None:
    for i, f in enumerate(doc["features"]):
        t, where = f["taxonomy_type"], f"/features/{i}"
        if t not in tax.known_types:
            s.error(
                f"{where}/taxonomy_type",
                f"unknown taxonomy type {t!r} (non-parametric items go in unmapped)",
            )
            continue
        rule = tax.is_counted(t)
        if f["counted"] and not rule:
            s.error(f"{where}/counted", f"{t!r} is not counted by the counting rule")
        elif not f["counted"] and rule and t != "boolean":
            # only a boolean merging a single feature into the body may be folded into that feature
            s.error(f"{where}/counted", f"{t!r} is counted by the counting rule")
    derived = sum(1 for f in doc["features"] if f["counted"])
    if doc["count"] != derived:
        s.error("/count", f"count={doc['count']} but {derived} features have counted=true")
    if doc.get("unmapped"):
        s.warn("/unmapped", f"{len(doc['unmapped'])} unmapped item(s) ⇒ the run fails the gate")


def _sem_backend(doc: dict, s: _Sink, tax: Taxonomy, src: Path | None) -> None:
    _folder_is_id(doc["id"], src, s)
    iface = doc["interface"]
    if iface["kind"] == "mcp":
        pre = iface["cad_prefixes"]
        if not pre.get("all"):
            s.error("/interface/cad_prefixes/all", "MCP backend must list its CAD tool prefixes")
        for m in pre.get("mutating") or []:
            if not any(m.startswith(a) for a in pre.get("all") or []):
                s.error(
                    "/interface/cad_prefixes/mutating", f"{m!r} is not covered by cad_prefixes.all"
                )
        if iface.get("allowed_commands"):
            s.warn("/interface/allowed_commands", "ignored for kind=mcp")
    else:
        if not iface["allowed_commands"]:
            s.error("/interface/allowed_commands", "CLI backend must allow at least one command")
    if doc["id"] == "nx" and doc["max_parallel"] != 1:
        s.warn("/max_parallel", "NX has one licence per host (00_DECISIONS D9); expected 1")
    if doc["id"] == "build123d" and "execute_ir" not in doc["adapter_cli"]:
        s.error("/adapter_cli/execute_ir", "required for build123d (01_ARCHITECTURE §2)")
    conf = doc.get("conformance") or {}
    if {"passed", "total"} <= conf.keys() and conf["passed"] > conf["total"]:
        s.error("/conformance", "passed > total")


def _sem_skillset(doc: dict, s: _Sink, tax: Taxonomy, src: Path | None) -> None:
    if doc["id"] != doc["backend"]["id"]:
        s.error("/id", f"skillset id {doc['id']!r} must equal backend.id {doc['backend']['id']!r}")
    _folder_is_id(doc["id"], src, s)
    for key in ("skills", "agents", "lib"):
        for d in _dupes(doc.get(key) or []):
            s.error(f"/{key}", f"duplicate entry {d!r}")


def _sem_strategy(doc: dict, s: _Sink, tax: Taxonomy, src: Path | None) -> None:
    _folder_is_id(doc["id"], src, s)
    if doc.get("inject_gt_plan") and doc["id"] != "oracle":
        s.error("/inject_gt_plan", "only the oracle strategy may inject the GT plan")
    if doc["id"] == "oracle" and not doc.get("inject_gt_plan"):
        s.warn("/inject_gt_plan", "oracle normally sets inject_gt_plan: true")
    for d in _dupes(doc["toolkit_tools"]):
        s.error("/toolkit_tools", f"duplicate tool {d!r}")
    if not doc["stages"]:
        s.error("/stages", "at least one stage is required")


def _sem_run_manifest(doc: dict, s: _Sink, tax: Taxonomy, src: Path | None) -> None:
    sk, be, track = doc["skillset"]["id"], doc["backend"]["id"], doc["track"]
    if not _SKILLSET_ID.match(sk):
        s.error("/skillset/id", f"{sk!r} must be '<backend>@vN' or 'none'")
    elif sk != "none" and sk.split("@")[0] != be:
        s.error(
            "/skillset/id", f"skillset {sk!r} targets a different backend than backend.id {be!r}"
        )
    if be == "none" and sk != "none":
        s.error("/skillset/id", "backend 'none' (plan-only) requires skillset 'none'")
    if not re.match(r"^(oracle|[A-Z])$", doc["strategy"]["id"]):
        s.error("/strategy/id", "must be 'oracle' or a single capital letter (folder name)")
    if track == "T" and doc.get("split") != "dev":
        s.error(
            "/split",
            "Track T exposes GT plans ⇒ dev parts only; split must be 'dev' (00_DECISIONS D4)",
        )
    if doc["spec_version"] != SPEC_VERSION:
        s.warn(
            "/spec_version", f"pins spec {doc['spec_version']} but this validator is {SPEC_VERSION}"
        )
    for key, val in doc["budgets"].items():
        if isinstance(val, (int, float)) and val <= 0:
            s.error(f"/budgets/{key}", "must be positive")


def _sem_result(doc: dict, s: _Sink, tax: Taxonomy, src: Path | None) -> None:
    if doc["pass"] and not doc["gate"]["ok"]:
        s.error("/pass", "pass=true with a failed gate")
    for key, val in (doc.get("metrics") or {}).items():
        if key in ("p_tight", "p_wide", "key_dims_ok", "param_coverage") and not 0 <= val <= 1:
            s.error(f"/metrics/{key}", "fraction must be within [0, 1]")
    if doc["track"] == "S" and doc["scores"].get("plan_score") is None:
        s.warn("/scores/plan_score", "Track S results are expected to carry plan_score")


_BUNDLE_FILES = {"backend.yaml", "skillset.yaml", "strategy.yaml"}


def _folder_is_id(doc_id: str, src: Path | None, s: _Sink) -> None:
    """Bundle manifests under their canonical name must sit in a folder named after their id."""
    if src is None or src.name.lower() not in _BUNDLE_FILES:
        return
    folder = src.resolve().parent.name
    if folder != doc_id:
        s.error("/id", f"folder name {folder!r} must equal id {doc_id!r}")


_SEMANTIC: dict[str, Callable[[dict, _Sink, Taxonomy, Path | None], None]] = {
    "task": _sem_task,
    "re_plan_ir": _sem_re_plan,
    "key_dims": _sem_key_dims,
    "gt_meta": _sem_gt_meta,
    "features": _sem_features,
    "backend_manifest": _sem_backend,
    "skillset_manifest": _sem_skillset,
    "strategy_manifest": _sem_strategy,
    "run_manifest": _sem_run_manifest,
    "result": _sem_result,
}
