"""Guard against drift between the pydantic models and the JSON Schemas (source of truth)."""

import pytest
from pydantic import BaseModel
from recadbench_spec import MODELS, load_schema
from recadbench_spec import models as m


def _fields(model: type[BaseModel]) -> tuple[set[str], set[str]]:
    names, required = set(), set()
    for name, info in model.model_fields.items():
        key = info.alias or name
        names.add(key)
        if info.is_required():
            required.add(key)
    return names, required


def _assert_same(model: type[BaseModel], node: dict) -> None:
    names, required = _fields(model)
    assert names == set(node.get("properties", {})), model.__name__
    assert required == set(node.get("required", [])), model.__name__
    closed = node.get("additionalProperties") is False
    assert (model.model_config.get("extra") == "forbid") == closed, model.__name__


@pytest.mark.parametrize("name", list(MODELS))
def test_top_level_model_matches_schema(name: str) -> None:
    _assert_same(MODELS[name], load_schema(name))


def test_ir_nested_models_match_schema() -> None:
    s = load_schema("re_plan_ir")
    p = s["properties"]
    _assert_same(m.Datum, p["datums"]["items"])
    _assert_same(m.Parameter, p["parameters"]["items"])
    _assert_same(m.Feature, p["features"]["items"])
    _assert_same(m.Evidence, p["features"]["items"]["properties"]["evidence"])
    _assert_same(m.ReferencePlane, p["reference_planes"]["items"])
    _assert_same(m.Sketch, s["$defs"]["sketch"])
    entities = s["$defs"]["sketch"]["properties"]["entities"]["items"]["oneOf"]
    by_kind = {e["properties"]["kind"]["const"]: e for e in entities}
    for kind, model in {
        "line": m.Line,
        "arc": m.Arc,
        "circle": m.Circle,
        "rect": m.Rect,
        "slot": m.Slot,
        "polygon": m.Polygon,
        "spline": m.Spline,
    }.items():
        _assert_same(model, by_kind[kind])
    assert len(by_kind) == 7


def test_other_nested_models_match_schema() -> None:
    kd = load_schema("key_dims")["properties"]["dims"]["items"]
    _assert_same(m.KeyDim, kd)
    _assert_same(m.KeyDimMeasure, kd["properties"]["measure"])
    _assert_same(m.ExtractedFeature, load_schema("features")["properties"]["features"]["items"])
    be = load_schema("backend_manifest")["properties"]
    _assert_same(m.BackendInterface, be["interface"])
    _assert_same(m.AdapterCli, be["adapter_cli"])
    rm = load_schema("run_manifest")["properties"]
    _assert_same(m.ModelPin, rm["model"])
    _assert_same(m.RunBackend, rm["backend"])
    res = load_schema("result")["properties"]
    _assert_same(m.Metrics, res["metrics"])
    _assert_same(m.Scores, res["scores"])
