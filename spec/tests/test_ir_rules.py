"""Taxonomy-compiled IR schema + IR semantic rules."""

import copy
import json
from pathlib import Path

import pytest
from recadbench_spec import compiled_schema, load_schema, validate_document

VALID = json.loads((Path(__file__).parent / "examples/re_plan_ir/valid.json").read_text())


def _check(mutate, layer=None, needle=None, ok=False):
    doc = copy.deepcopy(VALID)
    mutate(doc)
    r = validate_document(doc, "re_plan_ir")
    assert r.ok is ok, [str(i) for i in r.issues]
    if needle:
        assert any(needle in i.message and (layer is None or i.layer == layer) for i in r.issues), [
            str(i) for i in r.issues
        ]
    return r


def test_compiled_schema_adds_one_rule_per_taxonomy_type() -> None:
    raw = load_schema("re_plan_ir")
    comp = compiled_schema("re_plan_ir")
    rules = comp["properties"]["features"]["items"]["allOf"]
    assert len(rules) == len(raw["properties"]["features"]["items"]["properties"]["type"]["enum"])
    extrude = next(r for r in rules if r["if"]["properties"]["type"]["const"] == "extrude")
    assert extrude["then"]["required"] == ["sketch"]
    assert "allOf" not in raw["properties"]["features"]["items"]  # on-disk schema untouched


def test_vec3_param_values_are_valid() -> None:
    """ADR 0001: vec3 params used to match two oneOf branches and fail."""
    _check(lambda d: d["features"][1]["params"].update(positions=[[1, 2, 3]]), ok=True)
    _check(lambda d: d["features"][2]["params"].update(direction=[1, 0, 0]), ok=True)


def test_sketch_required_for_sketch_based_types() -> None:
    _check(lambda d: d["features"][0].pop("sketch"), "schema", "'sketch' is a required property")


def test_sketch_on_non_sketch_type_warns() -> None:
    sk = VALID["features"][0]["sketch"]
    r = _check(lambda d: d["features"][3].update(sketch=sk), ok=True)
    assert any("does not use a sketch" in w.message for w in r.warnings)


def test_unknown_type_rejected() -> None:
    _check(lambda d: d["features"][3].update(type="knurl"), "schema", "is not one of")


def test_undeclared_parameter_ref() -> None:
    _check(
        lambda d: d["features"][3]["params"].update(radius={"ref": "nope"}),
        "semantic",
        "undeclared",
    )


def test_duplicate_ids() -> None:
    _check(lambda d: d["features"][3].update(id="F1"), "semantic", "not unique")
    _check(lambda d: d["reference_planes"][0].update(id="D1"), "semantic", "not unique")


def test_selector_targets_must_exist() -> None:
    _check(lambda d: d["features"][0]["sketch"].update(plane="P_nope"), "semantic", "unknown")
    _check(lambda d: d["features"][3]["params"].update(edges="F9:edges:all"), "semantic", "unknown")
    _check(
        lambda d: d["features"][3]["params"].update(edges="F1:edges:sharp"),
        "schema",
        "is not valid under any of the given schemas",  # typed param (0.3.0)
    )
    _check(lambda d: d["features"][0]["sketch"].update(plane="P_mid"), ok=True)


def test_pattern_features_must_be_earlier_feature_ids() -> None:
    _check(
        lambda d: d["features"][2]["params"].update(features=["F4"]), "semantic", "defined later"
    )
    _check(
        lambda d: d["features"][2]["params"].update(features=[]), "schema", "should be non-empty"
    )
    _check(lambda d: d["features"][2]["params"].update(features=["F3"]), "semantic", "itself")


def test_datum_rules() -> None:
    _check(lambda d: d["datums"][1].update(role="primary"), "semantic", "more than once")
    _check(
        lambda d: d["datums"][0].update(role="tertiary"), "semantic", "primary datum is required"
    )
    _check(lambda d: d["datums"][0].update(direction=[0, 0, 0]), "semantic", "zero-length")
    _check(lambda d: d["datums"][0].pop("x_dir"), "schema", "'x_dir' is a required property")


def test_revision_rules(tmp_path: Path) -> None:
    _check(lambda d: d.pop("change_log"), "semantic", "change_log")
    _check(lambda d: (d.update(revision=1), d.pop("change_log")), ok=True)
    p = tmp_path / "re_plan.v3.json"
    r = validate_document(VALID, "re_plan_ir", source=p)
    assert any("file name" in i.message for i in r.errors)
    assert validate_document(VALID, "re_plan_ir", source=tmp_path / "re_plan.v2.json").ok


def test_additional_properties_closed() -> None:
    _check(lambda d: d.update(cad_commands=["Box"]), "schema", "Additional properties")
    _check(
        lambda d: d["features"][0].update(nx_feature="EXTRUDE(1)"),
        "schema",
        "Additional properties",
    )


# Invalid under the schema value union (spec 0.3.0 also types each param, see test_spec_030.py).
@pytest.mark.parametrize("bad", [None, {"ref": 1}, {"ref": "corner_r", "scale": 2}, {"value": 3}])
def test_bad_param_values(bad) -> None:
    r = _check(lambda d: d["features"][3]["params"].update(radius=bad))
    assert not r.ok
