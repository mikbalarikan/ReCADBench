import pytest
from pydantic import ValidationError
from recadbench_spec import FeatureType, load_schema, load_taxonomy
from recadbench_spec.taxonomy import Taxonomy, check_ir_schema_sync, feature_type_enum


def test_packaged_taxonomy_loads_and_is_consistent() -> None:
    tax = load_taxonomy()
    assert "extrude" in tax.types and tax.needs_sketch("extrude")
    assert not tax.needs_sketch("hole")
    assert set(tax.counting_rule.counted) == set(tax.types)
    assert "sketch" in tax.known_types and "sketch" not in tax.types
    assert "bspline" in tax.face_types


def test_ir_schema_enum_in_sync_with_taxonomy() -> None:
    assert check_ir_schema_sync(load_schema("re_plan_ir")) == []


def test_sync_check_reports_drift() -> None:
    schema = load_schema("re_plan_ir")
    enum_ = schema["properties"]["features"]["items"]["properties"]["type"]["enum"]
    enum_.remove("rib")
    enum_.append("knurl")
    problems = check_ir_schema_sync(schema)
    assert any("rib" in p for p in problems) and any("knurl" in p for p in problems)


def test_feature_type_enum_generated_from_taxonomy() -> None:
    tax = load_taxonomy()
    assert [m.value for m in FeatureType] == list(tax.feature_types)
    assert FeatureType("pattern_circular") == "pattern_circular"
    assert [m.value for m in feature_type_enum(tax)] == list(tax.feature_types)


def test_counting_rule() -> None:
    tax = load_taxonomy()
    # pattern counts 1 (seed separately); sketches, datums, parameters are free
    seq = [
        "datum_plane",
        "sketch",
        "extrude",
        "sketch",
        "hole",
        "pattern_linear",
        "parameter",
        "fillet",
    ]
    assert tax.count(seq) == 4
    assert tax.count([]) == 0
    with pytest.raises(KeyError):
        tax.count(["extrude", "dumb_solid"])


def test_allowed_params() -> None:
    tax = load_taxonomy()
    assert "cb_diameter" in tax.allowed_params("hole")
    with pytest.raises(KeyError):
        tax.allowed_params("knurl")


def _raw() -> dict:
    tax = load_taxonomy()
    return tax.model_dump(mode="python")


def test_taxonomy_rejects_overlap_between_counted_and_not_counted() -> None:
    raw = _raw()
    raw["counting_rule"]["not_counted"] = [*raw["counting_rule"]["not_counted"], "extrude"]
    with pytest.raises(ValidationError, match="both counted and not_counted"):
        Taxonomy.model_validate(raw)


def test_taxonomy_rejects_counted_list_not_matching_types() -> None:
    raw = _raw()
    raw["counting_rule"]["counted"] = [t for t in raw["counting_rule"]["counted"] if t != "rib"]
    with pytest.raises(ValidationError, match="exactly the keys of types"):
        Taxonomy.model_validate(raw)
