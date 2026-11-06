"""Spec 0.3.0 (ADR 0004): sketch refs, typed taxonomy params, pattern_circular, migration,
features.json informational keys, backend.yaml denied_tools."""

import copy
import json
from pathlib import Path

import pytest
import yaml
from recadbench_spec import (
    SPEC_VERSION,
    MigrationError,
    circular_offsets,
    load_schema,
    load_taxonomy,
    migrate_document,
    validate_document,
)
from recadbench_spec.cli import main
from recadbench_spec.taxonomy import EnumType, Taxonomy

EX = Path(__file__).parent / "examples"
VALID = json.loads((EX / "re_plan_ir/valid.json").read_text(encoding="utf-8"))
VALID_BACKEND = yaml.safe_load((EX / "backend_manifest/valid.yaml").read_text(encoding="utf-8"))
VALID_TASK = yaml.safe_load((EX / "task/valid.yaml").read_text(encoding="utf-8"))


def _ir(mutate):
    doc = copy.deepcopy(VALID)
    mutate(doc)
    return validate_document(doc, "re_plan_ir")


def _msgs(r, level=None):
    return [str(i) for i in r.issues if level is None or i.level == level]


def _circ(doc):
    return next(f for f in doc["features"] if f["type"] == "pattern_circular")


def test_version_is_030() -> None:
    assert SPEC_VERSION == "0.3.0"
    assert load_schema("re_plan_ir")["properties"]["spec_version"]["const"] == "0.3.0"
    assert load_schema("task")["properties"]["spec_version"]["const"] == "0.3.0"


# ------------------------------------------------------------------------- typed taxonomy params
def test_every_taxonomy_param_is_typed_and_every_type_has_a_schema_def() -> None:
    tax = load_taxonomy()
    defs = load_schema("re_plan_ir")["$defs"]
    for name in tax.param_types:
        assert f"ptype_{name}" in defs, name
    for t, spec in tax.types.items():
        assert spec.params, t
        for pname, ptype in spec.params.items():
            assert isinstance(ptype, EnumType) or ptype in tax.param_types, (t, pname)
    assert tax.param_type("pattern_circular", "count") == "count"
    assert tax.param_type("hole", "kind").enum == ("simple", "counterbore", "countersink")
    with pytest.raises(KeyError):
        tax.param_type("hole", "radius")


def test_taxonomy_rejects_unknown_param_type() -> None:
    raw = load_taxonomy().model_dump(mode="python")
    raw["types"]["fillet"]["params"]["radius"] = "length"
    with pytest.raises(ValueError, match="unknown param type 'length'"):
        Taxonomy.model_validate(raw)


@pytest.mark.parametrize(
    "fi,key,bad",
    [
        (3, "radius", [1, 2, 3, 4]),  # number
        (3, "radius", "5"),
        (2, "count", "two"),  # count
        (2, "count", 0),
        (2, "count", 2.5),
        (1, "kind", "tapped"),  # enum
        (1, "through", "yes"),  # boolean
        (1, "positions", [30, 20, 8]),  # vec3_list (a bare vec3)
        (1, "positions", []),
        (2, "features", "F2"),  # feature_ids
        (2, "direction", [1, 0]),  # direction: neither vec3 nor selector
        (3, "edges", ["F1:edges:outer", 3]),  # selectors
    ],
)
def test_param_values_are_type_checked(fi, key, bad) -> None:
    r = _ir(lambda d: d["features"][fi]["params"].update({key: bad}))
    assert not r.ok
    assert all(i.layer == "schema" for i in r.errors), _msgs(r)


def test_typed_params_accept_valid_forms() -> None:
    def mut(d):
        d["features"][3]["params"]["edges"] = ["F1:edges:outer", "F1:plane:+Z:max"]
        d["features"][2]["params"]["direction"] = "D2"  # selector form of a direction
        d["features"][1]["params"]["kind"] = "countersink"

    assert _ir(mut).ok


def test_count_ref_must_be_integer_parameter() -> None:
    def mut(d):
        next(p for p in d["parameters"] if p["name"] == "n_vents")["value"] = 4.5

    r = _ir(mut)
    assert any("is not an integer >= 1" in m for m in _msgs(r, "error")), _msgs(r)


# ------------------------------------------------------------------------------- sketch refs
def test_sketch_numbers_accept_refs_and_count_as_used() -> None:
    r = _ir(lambda d: None)
    assert r.ok and not r.warnings, _msgs(r)  # plate_w / plate_h are only used in the sketch
    rect = r.model.features[0].sketch.entities[0]
    assert rect.width.ref == "plate_w"

    def coords(d):
        d["features"][0]["sketch"]["entities"][0]["center"] = [{"ref": "corner_r"}, 0]

    assert _ir(coords).ok


def test_undeclared_ref_in_sketch() -> None:
    def mut(d):
        d["features"][0]["sketch"]["entities"][0]["height"] = {"ref": "nope"}

    r = _ir(mut)
    assert any(
        "undeclared parameter 'nope'" in i.message and "/sketch/entities/0/height" in i.path
        for i in r.errors
    ), _msgs(r)


def test_bad_ref_object_in_sketch_is_schema_error() -> None:
    r = _ir(lambda d: d["features"][0]["sketch"]["entities"][0].update(width={"ref": 3}))
    assert not r.ok and r.errors[0].layer == "schema"


# ----------------------------------------------------------------------- pattern_circular
@pytest.mark.parametrize(
    "params,expected",
    [
        ({"count": 4}, [0, 90, 180, 270]),
        ({"count": 4, "angle_deg": 360}, [0, 90, 180, 270]),
        ({"count": 3, "angle_deg": 180}, [0, 60, 120]),  # total sweep, not first-to-last
        ({"count": 3, "pitch_deg": 90}, [0, 90, 180]),
        ({"count": 3, "pitch_deg": -90}, [0, -90, -180]),
        ({"count": {"ref": "n"}, "angle_deg": 360}, [0, 72, 144, 216, 288]),
    ],
)
def test_circular_offsets(params, expected) -> None:
    assert circular_offsets(params, {"n": 5}) == pytest.approx(expected)


def test_circular_offsets_rejects_both_angles() -> None:
    with pytest.raises(ValueError, match="at most one"):
        circular_offsets({"count": 3, "angle_deg": 360, "pitch_deg": 90})


def test_pattern_circular_rules() -> None:
    def both(d):
        _circ(d)["params"]["pitch_deg"] = 90

    assert any("at most one" in m for m in _msgs(_ir(both), "error"))

    def overlap(d):
        p = _circ(d)["params"]
        p.pop("angle_deg")
        p["pitch_deg"] = 100  # 4 x 100 = 400 deg

    assert any("exceeds 360" in m for m in _msgs(_ir(overlap), "error"))

    def zero(d):
        _circ(d)["params"]["angle_deg"] = 0

    assert any("non-zero" in m for m in _msgs(_ir(zero), "error"))

    def pitch_like(d):  # the 0.2.0 pilot habit: angle_deg = pitch
        _circ(d)["params"]["angle_deg"] = 90

    r = _ir(pitch_like)
    assert r.ok and any("looks like a pitch" in m for m in _msgs(r, "warning")), _msgs(r)

    def full_pitch(d):
        p = _circ(d)["params"]
        p.pop("angle_deg")
        p["pitch_deg"] = 90

    r = _ir(full_pitch)
    assert r.ok and any("full circle" in m for m in _msgs(r, "warning")), _msgs(r)

    def partial(d):
        p = _circ(d)["params"]
        p.pop("angle_deg")
        p["pitch_deg"] = 60

    r = _ir(partial)
    assert r.ok and not r.warnings, _msgs(r)


# ------------------------------------------------------------------------------- migration
def _v020(mutate=None) -> dict:
    """The valid example as a 0.2.0 document (no sketch refs: 0.2.0 did not allow them)."""
    d = copy.deepcopy(VALID)
    d["spec_version"] = "0.2.0"
    ent = d["features"][0]["sketch"]["entities"][0]
    ent["width"], ent["height"] = 80, 60
    d["parameters"] = [p for p in d["parameters"] if p["name"] not in ("plate_w", "plate_h")]
    if mutate:
        mutate(d)
    return d


def test_020_document_is_rejected_with_a_hint_without_migrate() -> None:
    r = validate_document(_v020(), "re_plan_ir")
    assert not r.ok
    assert any("recadbench-spec migrate" in m for m in _msgs(r, "error")), _msgs(r)


def test_migrate_unambiguous_020_plan() -> None:
    old = _v020()
    snapshot = copy.deepcopy(old)
    mig = migrate_document(old, "re_plan_ir")
    assert old == snapshot  # input untouched
    assert (mig.from_version, mig.to_version) == ("0.2.0", "0.3.0")
    assert mig.document["spec_version"] == "0.3.0"
    assert _circ(mig.document)["params"]["angle_deg"] == 360  # 360 needs no policy
    r = validate_document(old, "re_plan_ir", migrate=True)
    assert r.ok and r.model.spec_version == "0.3.0", _msgs(r)
    assert any(i.layer == "migration" for i in r.warnings)


def test_migrate_ambiguous_angle_needs_a_policy() -> None:
    old = _v020(lambda d: _circ(d)["params"].update(angle_deg=90))
    with pytest.raises(MigrationError, match="ambiguous"):
        migrate_document(old, "re_plan_ir")
    r = validate_document(old, "re_plan_ir", migrate=True)
    assert not r.ok and r.errors[0].layer == "migration"


def test_migrate_policies() -> None:
    full = _v020(lambda d: _circ(d)["params"].update(angle_deg=90))  # 4 x 90 pitch
    p = _circ(migrate_document(full, "re_plan_ir", circular_angle="pitch").document)["params"]
    assert p["angle_deg"] == 360 and "pitch_deg" not in p and p["count"] == {"ref": "n_vents"}

    def partial(d):
        _circ(d)["params"].update(count=3, angle_deg=90)

    m = migrate_document(_v020(partial), "re_plan_ir", circular_angle="pitch")
    p = _circ(m.document)["params"]
    assert p == {"features": ["F5"], "axis": "D2", "count": 3, "pitch_deg": 90}
    assert any("pitch_deg 90" in n for n in m.notes)

    def span(d):
        _circ(d)["params"].update(count=3, angle_deg=180)

    p = _circ(migrate_document(_v020(span), "re_plan_ir", circular_angle="span").document)["params"]
    assert p["pitch_deg"] == 90 and "angle_deg" not in p
    p = _circ(migrate_document(_v020(span), "re_plan_ir", circular_angle="sweep").document)[
        "params"
    ]
    assert p["angle_deg"] == 180
    for policy in ("span", "sweep"):  # (pitch: 3 x 180 overlaps, see below)
        m = migrate_document(_v020(span), "re_plan_ir", circular_angle=policy)
        assert validate_document(m.document, "re_plan_ir").ok
    with pytest.raises(MigrationError, match="overlap"):
        migrate_document(
            _v020(lambda d: _circ(d)["params"].update(count=5, angle_deg=100)),
            "re_plan_ir",
            circular_angle="pitch",
        )
    with pytest.raises(MigrationError, match="circular_angle must be"):
        migrate_document(_v020(), "re_plan_ir", circular_angle="degrees")


def test_migrate_keeps_ref_bound_pitch_and_notes_dropped_bindings() -> None:
    def ref_pitch(d):
        d["parameters"].append({"name": "hole_pitch", "value": 30})
        _circ(d)["params"].update(count=3, angle_deg={"ref": "hole_pitch"})

    m = migrate_document(_v020(ref_pitch), "re_plan_ir", circular_angle="pitch")
    p = _circ(m.document)["params"]
    assert p["pitch_deg"] == {"ref": "hole_pitch"} and "angle_deg" not in p
    r = validate_document(m.document, "re_plan_ir")
    assert r.ok and not any("hole_pitch" in w for w in _msgs(r, "warning")), _msgs(r)

    def ref_span(d):
        d["parameters"].append({"name": "arc", "value": 180})
        _circ(d)["params"].update(count=3, angle_deg={"ref": "arc"})

    m = migrate_document(_v020(ref_span), "re_plan_ir", circular_angle="span")
    assert _circ(m.document)["params"]["pitch_deg"] == 90
    assert any("'arc' dropped" in n for n in m.notes), m.notes


def _with_feature(feat):
    def mutate(d):
        d["features"].append(feat)

    return mutate


def test_migrate_wraps_scalar_number_list_and_rejects_narrowed_params() -> None:
    sphere = {"id": "F90", "type": "primitive", "operation": "new",
              "params": {"shape": "sphere", "size": 5, "center": [0, 0, 0]}}
    m = migrate_document(_v020(_with_feature(sphere)), "re_plan_ir")
    assert m.document["features"][-1]["params"]["size"] == [5]
    assert any("size 5 -> [5]" in n for n in m.notes), m.notes
    assert validate_document(m.document, "re_plan_ir").ok
    tapped = {"id": "F91", "type": "hole", "operation": "cut",
              "params": {"diameter": 3, "through": True, "kind": "tapped",
                         "positions": [[2, 2, 5]]}}
    with pytest.raises(MigrationError, match=r"typed taxonomy params.*kind"):
        migrate_document(_v020(_with_feature(tapped)), "re_plan_ir")
    r = validate_document(_v020(_with_feature(tapped)), "re_plan_ir", migrate=True)
    assert not r.ok and r.errors[0].layer == "migration", _msgs(r)


def test_migrate_task_and_version_free_schemas() -> None:
    old = dict(VALID_TASK, spec_version="0.2.0")
    m = migrate_document(old, "task")
    assert m.document["spec_version"] == "0.3.0"
    assert validate_document(m.document, "task").ok
    assert validate_document(old, "task", migrate=True).ok
    # current documents and version-free schemas are returned unchanged
    assert not migrate_document(VALID_TASK, "task").changed
    assert not migrate_document(VALID_BACKEND, "backend_manifest").changed
    run = json.loads((EX / "run_manifest/valid.json").read_text(encoding="utf-8"))
    run["spec_version"] = "0.2.0"  # a pin record: never rewritten
    assert migrate_document(run, "run_manifest").document["spec_version"] == "0.2.0"


def test_cli_migrate_writes_lf_and_keeps_yaml_layout(tmp_path: Path, capsys) -> None:
    src = (EX / "task/valid.yaml").read_text(encoding="utf-8").replace("\r\n", "\n")
    old = src.replace("spec_version: 0.3.0", "spec_version: 0.2.0")
    assert old != src
    f = tmp_path / "task.yaml"
    f.write_bytes(("# keep me\n" + old).replace("\n", "\r\n").encode())
    assert main(["validate", str(f)]) == 1
    assert main(["validate", "--migrate", str(f)]) == 0
    assert main(["migrate", "--write", str(f)]) == 0
    data = f.read_bytes()
    assert b"\r\n" not in data
    assert data.decode() == "# keep me\n" + src
    assert main(["migrate", str(f)]) == 0
    assert "up to date" in capsys.readouterr().out

    plan = tmp_path / "gt_plan.json"
    plan.write_text(
        json.dumps(_v020(lambda d: _circ(d)["params"].update(angle_deg=90)), indent=2) + "\n"
    )
    assert main(["migrate", "--write", str(plan)]) == 1  # ambiguous: nothing written
    assert json.loads(plan.read_text())["spec_version"] == "0.2.0"
    assert main(["migrate", "--circular-angle", "pitch", "--write", str(plan)]) == 0
    new = json.loads(plan.read_text())
    assert new["spec_version"] == "0.3.0" and _circ(new)["params"]["angle_deg"] == 360
    out = tmp_path / "out.json"
    assert main(["migrate", "-o", str(out), str(plan)]) == 0 and not out.exists()  # up to date


# ------------------------------------------------------------------- features.json new keys
def test_features_informational_keys() -> None:
    doc = json.loads((EX / "features/valid.json").read_text(encoding="utf-8"))
    doc.update(
        not_in_final_part=["Box@scratch.py:3"],
        excluded=[
            {"native_name": "Fillet2", "native_type": "FilletFeature", "reason": "suppressed"}
        ],
        final_part="part",
        bodies=["Body"],
    )
    r = validate_document(doc, "features")
    assert r.ok, _msgs(r)
    assert r.model.not_in_final_part == ["Box@scratch.py:3"]
    bad = dict(doc, not_in_final_part="Box")
    assert not validate_document(bad, "features").ok
    bad = dict(doc, excluded=[{"native_name": "x"}])
    assert not validate_document(bad, "features").ok


# ----------------------------------------------------------------- backend.yaml denied_tools
def _be(mutate):
    doc = copy.deepcopy(VALID_BACKEND)
    mutate(doc)
    return validate_document(doc, "backend_manifest")


def test_denied_tools() -> None:
    assert _be(lambda d: None).ok
    r = _be(lambda d: d.pop("denied_tools"))
    assert r.ok and any("no denied_tools" in m for m in _msgs(r, "warning"))
    r = _be(lambda d: d["denied_tools"].append({"tool": "mcp__other__x", "reason": "r"}))
    assert any("not covered by cad_prefixes.all" in m for m in _msgs(r, "error"))
    r = _be(lambda d: d["denied_tools"][2].update(args_regex="import_stl("))
    assert any("not a valid regex" in m for m in _msgs(r, "error"))
    r = _be(lambda d: d["denied_tools"].append(dict(d["denied_tools"][0])))
    assert any("duplicate" in m for m in _msgs(r, "error"))
    r = _be(lambda d: d["denied_tools"][0].pop("reason"))
    assert not r.ok and r.errors[0].layer == "schema"
    r = _be(lambda d: d["denied_tools"][0].update(why="x"))
    assert not r.ok and r.errors[0].layer == "schema"


# --------------------------------------------------------------------- docs contract mirror
def test_docs_contract_mirror_in_sync() -> None:
    repo = Path(__file__).resolve().parents[2]
    mirror = repo / "docs/architecture/contracts"
    if not mirror.is_dir():
        pytest.skip("docs mirror not present (installed package)")
    pkg = Path(__file__).resolve().parents[1] / "recadbench_spec"
    files = [*(pkg / "schemas").glob("*.schema.json"), pkg / "feature_taxonomy.yaml"]
    for f in files:
        a = f.read_text(encoding="utf-8").replace("\r\n", "\n")
        b = (mirror / f.name).read_text(encoding="utf-8").replace("\r\n", "\n")
        assert a == b, f"docs/architecture/contracts/{f.name} differs from spec/"
