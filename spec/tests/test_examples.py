"""1 valid + 2 invalid examples per schema; valid ones must round-trip through the models."""

from pathlib import Path

import pytest
from recadbench_spec import SCHEMA_NAMES, load_schema, validate_document
from recadbench_spec.validate import load_document

EXAMPLES = Path(__file__).parent / "examples"

# invalid example → (layer that must catch it, substring of the error message)
EXPECTED = {
    "task/invalid_units_inch": ("schema", "'mm' was expected"),
    "task/invalid_duplicate_deliverable": ("semantic", "duplicate deliverable"),
    "re_plan_ir/invalid_param_not_in_taxonomy": ("schema", "'radius' is not one of"),
    "re_plan_ir/invalid_ref_to_later_feature": ("semantic", "defined later"),
    "key_dims/invalid_too_few_dims": ("schema", "is too short"),
    "key_dims/invalid_duplicate_name": ("semantic", "is not unique"),
    "gt_meta/invalid_tier_4": ("schema", "is not one of"),
    "gt_meta/invalid_face_fractions": ("semantic", "unknown face types"),
    "features/invalid_missing_tool": ("schema", "'tool' is a required property"),
    "features/invalid_count_mismatch": ("semantic", "count=5 but 4"),
    "backend_manifest/invalid_mcp_without_prefixes": ("schema", "'cad_prefixes' is a required"),
    "backend_manifest/invalid_mutating_not_in_all": ("semantic", "not covered by cad_prefixes.all"),
    "skillset_manifest/invalid_version_format": ("schema", "does not match"),
    "skillset_manifest/invalid_id_backend_mismatch": ("semantic", "must equal backend.id"),
    "strategy_manifest/invalid_lowercase_id": ("schema", "does not match"),
    "strategy_manifest/invalid_non_oracle_injects_gt": ("semantic", "only the oracle"),
    "run_manifest/invalid_track_t_not_oracle": ("schema", "'oracle' was expected"),
    "run_manifest/invalid_skillset_backend_mismatch": ("semantic", "different backend"),
    "result/invalid_missing_pass": ("schema", "'pass' is a required property"),
    "result/invalid_pass_with_failed_gate": ("semantic", "failed gate"),
}


def _files(prefix: str) -> list[Path]:
    return sorted(p for p in EXAMPLES.glob(f"*/{prefix}*") if p.suffix in (".json", ".yaml"))


def _key(p: Path) -> str:
    return f"{p.parent.name}/{p.stem}"


def test_every_schema_has_one_valid_and_two_invalid_examples() -> None:
    for name in SCHEMA_NAMES:
        files = [p.stem for p in (EXAMPLES / name).iterdir()]
        assert files.count("valid") == 1, name
        assert len([f for f in files if f.startswith("invalid_")]) >= 2, name
    assert {_key(p) for p in _files("invalid_")} == set(EXPECTED)


@pytest.mark.parametrize("path", _files("valid"), ids=_key)
def test_valid_example(path: Path) -> None:
    doc = load_document(path)
    report = validate_document(doc, path.parent.name)
    assert report.ok, [str(i) for i in report.errors]
    assert not report.warnings, [str(i) for i in report.warnings]


@pytest.mark.parametrize("path", _files("valid"), ids=_key)
def test_round_trip(path: Path) -> None:
    """document → model → document is lossless, and the dump is schema-valid again."""
    doc = load_document(path)
    report = validate_document(doc, path.parent.name)
    dumped = report.model.to_document()
    assert dumped == doc
    again = validate_document(dumped, path.parent.name)
    assert again.ok, [str(i) for i in again.errors]


@pytest.mark.parametrize("path", _files("invalid_"), ids=_key)
def test_invalid_example(path: Path) -> None:
    layer, needle = EXPECTED[_key(path)]
    report = validate_document(load_document(path), path.parent.name)
    assert not report.ok
    hits = [i for i in report.errors if i.layer == layer and needle in i.message]
    assert hits, [str(i) for i in report.issues]


def test_schemas_are_valid_draft_2020_12() -> None:
    from jsonschema import Draft202012Validator

    for name in SCHEMA_NAMES:
        Draft202012Validator.check_schema(load_schema(name))
