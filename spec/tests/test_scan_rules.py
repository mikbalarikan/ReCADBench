"""ADR 0002: scan inputs — task scan metadata, GT provenance, registration, test eligibility."""

import copy
from pathlib import Path

import pytest
from recadbench_spec import validate_document
from recadbench_spec.validate import load_document

EX = Path(__file__).parent / "examples"
META = load_document(EX / "gt_meta/valid.yaml")
TASK = load_document(EX / "task/valid.yaml")


def _meta(mutate):
    doc = copy.deepcopy(META)
    mutate(doc)
    return validate_document(doc, "gt_meta")


def _msgs(report):
    return [f"{i.layer}:{i.message}" for i in report.issues]


@pytest.mark.parametrize(
    ("T", "needle"),
    [
        ([[2, 0, 0, 0], [0, 2, 0, 0], [0, 0, 2, 0], [0, 0, 0, 1]], "not orthonormal"),
        ([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, -1, 0], [0, 0, 0, 1]], "reflection"),
        ([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 1, 1]], "last row"),
    ],
)
def test_registration_must_be_rigid(T, needle) -> None:
    r = _meta(lambda d: d["registration"].update(T_scan_to_gt=T))
    assert not r.ok and any(needle in m for m in _msgs(r)), _msgs(r)


def test_identity_and_rotated_registration_ok() -> None:
    eye = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    assert _meta(lambda d: d["registration"].update(T_scan_to_gt=eye)).ok
    assert validate_document(META, "gt_meta").ok  # 90° about Z + translation


def test_unconverged_registration_rejected() -> None:
    r = _meta(lambda d: d["registration"].update(converged=False))
    assert any("did not converge" in m for m in _msgs(r))


def test_scan_rebuild_requires_scan_floor_and_observability() -> None:
    for key in ("scan", "scan_floor", "observability"):
        r = _meta(lambda d, k=key: d.pop(k))
        assert any(f"'{key}' is a required property" in m for m in _msgs(r)), key


def test_agent_gt_allowed_in_dev_but_not_test() -> None:
    assert _meta(lambda d: d.update(gt_author="agent")).ok
    r = _meta(lambda d: d.update(gt_author="agent", split="test"))
    assert any("human-reviewed" in m for m in _msgs(r))
    assert _meta(lambda d: d.update(gt_author="agent_human_reviewed", split="test")).ok


@pytest.mark.parametrize("flag", ["public_scan", "public_cad", "commercial_replica"])
def test_any_contamination_blocks_test(flag) -> None:
    assert _meta(lambda d: d["contamination"].update({flag: True})).ok  # dev is fine
    r = _meta(lambda d: (d.update(split="test"), d["contamination"].update({flag: True})))
    assert any("may not be in test" in m for m in _msgs(r))


def _synthetic(d):
    for k in ("scan", "registration", "scan_floor", "observability"):
        d.pop(k)
    d.update(source_kind="synthetic", tessellation_chord_mm=0.03)


def test_synthetic_meta() -> None:
    assert _meta(_synthetic).ok
    r = _meta(lambda d: (_synthetic(d), d.pop("tessellation_chord_mm")))
    assert any("'tessellation_chord_mm' is a required property" in m for m in _msgs(r))
    r = _meta(lambda d: (_synthetic(d), d.update(split="test")))
    assert any("calibration only" in m for m in _msgs(r))


def test_task_synthetic_input_must_not_carry_scan_metadata() -> None:
    doc = copy.deepcopy(TASK)
    doc["input_kind"] = "synthetic_clean"
    r = validate_document(doc, "task")
    assert any("scan metadata given" in m for m in _msgs(r))
    doc.pop("scan")
    assert validate_document(doc, "task").ok


def test_task_requires_input_kind_and_scan_frame() -> None:
    doc = copy.deepcopy(TASK)
    doc.pop("input_kind")
    assert not validate_document(doc, "task").ok
    doc = copy.deepcopy(TASK)
    doc["scan"].pop("frame")
    assert not validate_document(doc, "task").ok
