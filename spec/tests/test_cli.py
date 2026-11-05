import json
import shutil
from pathlib import Path

import pytest
from recadbench_spec.cli import main
from recadbench_spec.validate import detect_schema, validate_file

EX = Path(__file__).parent / "examples"


@pytest.mark.parametrize(
    ("name", "schema"),
    [
        ("re_plan.v12.json", "re_plan_ir"),
        ("gt_plan.json", "re_plan_ir"),
        ("task.yaml", "task"),
        ("meta.yaml", "gt_meta"),
        ("key_dims.yaml", "key_dims"),
        ("features.json", "features"),
        ("backend.yaml", "backend_manifest"),
        ("skillset.yaml", "skillset_manifest"),
        ("strategy.yaml", "strategy_manifest"),
        ("run_manifest.json", "run_manifest"),
        ("result.json", "result"),
        ("notes.yaml", None),
        ("re_plan.json", None),
    ],
)
def test_detect_schema(name: str, schema: str | None) -> None:
    assert detect_schema(Path("some/dir") / name) == schema


def test_validate_ok_and_invalid_exit_codes(capsys) -> None:
    assert main(["validate", "--schema", "task", str(EX / "task/valid.yaml")]) == 0
    assert "OK (task)" in capsys.readouterr().out
    assert main(["validate", "--schema", "task", str(EX / "task/invalid_units_inch.yaml")]) == 1
    assert "INVALID" in capsys.readouterr().out


def test_detection_by_file_name(tmp_path: Path) -> None:
    shutil.copy(EX / "re_plan_ir/valid.json", tmp_path / "re_plan.v2.json")
    shutil.copy(EX / "task/valid.yaml", tmp_path / "task.yaml")
    assert main(["validate", str(tmp_path / "re_plan.v2.json"), str(tmp_path / "task.yaml")]) == 0


def test_undetectable_name_is_usage_error(tmp_path: Path, capsys) -> None:
    f = tmp_path / "x.yaml"
    f.write_text("a: 1\n")
    assert main(["validate", str(f)]) == 2
    assert "--schema" in capsys.readouterr().err


def test_json_output(capsys) -> None:
    rc = main(
        [
            "validate",
            "--json",
            "--schema",
            "features",
            str(EX / "features/invalid_count_mismatch.json"),
        ]
    )
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out[0]["ok"] is False
    assert out[0]["issues"][0]["layer"] == "semantic"


def test_strict_turns_warnings_into_failure(tmp_path: Path) -> None:
    doc = json.loads((EX / "re_plan_ir/valid.json").read_text())
    doc["parameters"].append({"name": "unused_dim", "value": 1})
    f = tmp_path / "re_plan.v2.json"
    f.write_text(json.dumps(doc))
    assert main(["validate", str(f)]) == 0
    assert main(["validate", "--strict", str(f)]) == 1


def test_syntax_error_reported(tmp_path: Path, capsys) -> None:
    f = tmp_path / "result.json"
    f.write_text("{not json")
    assert main(["validate", str(f)]) == 1
    assert "result.json" in capsys.readouterr().err


def test_bundle_folder_must_equal_id(tmp_path: Path) -> None:
    good = tmp_path / "B" / "strategy.yaml"
    bad = tmp_path / "C" / "strategy.yaml"
    for p in (good, bad):
        p.parent.mkdir()
        shutil.copy(EX / "strategy_manifest/valid.yaml", p)
    assert validate_file(good).ok
    assert any("folder name" in i.message for i in validate_file(bad).errors)


def test_other_commands(capsys) -> None:
    assert main(["schemas"]) == 0
    assert "re_plan.vN.json" in capsys.readouterr().out
    assert main(["taxonomy"]) == 0
    assert "pattern_circular" in capsys.readouterr().out
    assert main(["compile-schema", "re_plan_ir"]) == 0
    assert '"propertyNames"' in capsys.readouterr().out
