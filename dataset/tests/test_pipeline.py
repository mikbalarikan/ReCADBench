"""register_gt.py → make_task.py on a synthetic scan_rebuild part (no real data)."""

import json
import shutil
from pathlib import Path

import make_task
import numpy as np
import pytest
import register_gt
import yaml
from recadbench_spec import validate_file

FAST = ["--samples", "6000", "--icp-samples", "3000"]


def _meta(part_id: str, split: str = "dev") -> dict:
    return {
        "part_id": part_id,
        "tolerance_class": "B",
        "tier": 1,
        "split": split,
        "n_ref": 2,
        "faces_gt": 12,
        "face_types": {"plane": 1.0},
        "b1": 0,
        "family": "test blocks",
        "source": "synthetic test fixture",
        "source_kind": "scan_rebuild",
        "gt_author": "human",
        "contamination": {"public_scan": False, "public_cad": False, "commercial_replica": False},
        "scan": {
            "file": "scan_raw.stl",
            "sha256": "TODO",
            "scanner": "Creality CR-Scan Raptor",
            "mode": "laser",
            "sessions": 2,
            "known_defects": ["hole in the top face"],
        },
    }


def _new_part(root: Path, gt_mesh, scan_case) -> Path:
    part = root / "gt" / "parts" / "p_test01"
    part.mkdir(parents=True)
    gt_mesh.export(part / "gt_cad.stl")
    scan_case[0].export(part / "scan_raw.stl")
    (part / "meta.yaml").write_text(yaml.safe_dump(_meta("p_test01"), sort_keys=False))
    return part


@pytest.fixture
def gt_part(tmp_path: Path, gt_mesh, scan_case) -> Path:
    return _new_part(tmp_path, gt_mesh, scan_case)


@pytest.fixture(scope="session")
def _registered_template(tmp_path_factory, gt_mesh, scan_case) -> Path:
    part = _new_part(tmp_path_factory.mktemp("tpl"), gt_mesh, scan_case)
    rc = register_gt.main(
        [str(part), "--init", "pca", "--gt-mesh", str(part / "gt_cad.stl"), "--write", *FAST]
    )
    assert rc == 0
    return part


@pytest.fixture
def registered(_registered_template: Path, tmp_path: Path) -> Path:
    dst = tmp_path / "gt" / "parts" / _registered_template.name
    shutil.copytree(_registered_template, dst)
    return dst


def test_register_writes_valid_meta(registered: Path, scan_case) -> None:
    gt_part = registered
    report = validate_file(gt_part / "meta.yaml")
    assert report.ok, [str(i) for i in report.errors]
    meta = yaml.safe_load((gt_part / "meta.yaml").read_text())
    T = np.array(meta["registration"]["T_scan_to_gt"])
    assert np.abs(T - scan_case[1]).max() < 0.05
    assert meta["scan"]["sha256"] != "TODO" and meta["scan"]["triangles"] > 0
    assert 0.03 < meta["scan_floor"]["scan_to_gt_p95_mm"] < 0.1
    assert 0.9 < meta["observability"]["observable_fraction"] < 1.0
    assert (gt_part / "observability_mask.npz").is_file()
    assert json.loads((gt_part / "registration_report.json").read_text())["registration"][
        "converged"
    ]


def test_register_refuses_sha_mismatch(gt_part: Path) -> None:
    meta = yaml.safe_load((gt_part / "meta.yaml").read_text())
    meta["scan"]["sha256"] = "0" * 64
    (gt_part / "meta.yaml").write_text(yaml.safe_dump(meta))
    with pytest.raises(SystemExit, match="does not match"):
        register_gt.main(
            [
                str(gt_part),
                "--init",
                "pca",
                "--gt-mesh",
                str(gt_part / "gt_cad.stl"),
                "--write",
                *FAST,
            ]
        )


def test_make_task_packages_scan_only(registered: Path, tmp_path: Path) -> None:
    gt_part = registered
    data = tmp_path / "data"
    assert make_task.main(["--gt-root", str(gt_part.parent), "--data-root", str(data)]) == 0
    out = data / "parts" / "p_test01"
    assert sorted(p.name for p in out.iterdir()) == ["input.stl", "task.yaml"]
    assert (out / "input.stl").read_bytes() == (gt_part / "scan_raw.stl").read_bytes()
    report = validate_file(out / "task.yaml")
    assert report.ok, [str(i) for i in report.errors]
    task = yaml.safe_load((out / "task.yaml").read_text())
    assert task["input_kind"] == "scan" and task["scan"]["frame"] == "scanner"
    assert task["scan"]["known_defects"] == ["hole in the top face"]
    leaked = {
        "n_ref",
        "family",
        "registration",
        "scan_floor",
        "sha256",
        "gt_author",
        "contamination",
    }
    assert not leaked & (set(task) | set(task["scan"]))
    assert (data / "splits" / "dev.txt").read_text() == "p_test01\n"
    assert (data / "splits" / "test.txt").read_text() == ""
    manifest = json.loads((data / "MANIFEST.json").read_text())
    assert set(manifest["files"]) == {"parts/p_test01/input.stl", "parts/p_test01/task.yaml"}


def test_make_task_refuses_tampered_scan(registered: Path, tmp_path: Path) -> None:
    gt_part = registered
    with (gt_part / "scan_raw.stl").open("ab") as f:
        f.write(b"\0")
    with pytest.raises(SystemExit, match="does not match"):
        make_task.main(["--gt-root", str(gt_part.parent), "--data-root", str(tmp_path / "data")])


def test_make_task_refuses_public_repo_as_data_root(gt_part: Path) -> None:
    public = Path(make_task.__file__).resolve().parents[2]
    with pytest.raises(SystemExit, match="public ReCADBench"):
        make_task.main(["--gt-root", str(gt_part.parent), "--data-root", str(public / "tmp_data")])


def test_make_task_refuses_unregistered_meta(gt_part: Path, tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="gt_meta invalid"):
        make_task.main(["--gt-root", str(gt_part.parent), "--data-root", str(tmp_path / "data")])


def test_splits_merge_with_existing(registered: Path, tmp_path: Path) -> None:
    gt_part = registered
    data = tmp_path / "data"
    (data / "splits").mkdir(parents=True)
    (data / "splits" / "dev.txt").write_text("p_old\n")
    make_task.main(["--gt-root", str(gt_part.parent), "--data-root", str(data)])
    assert (data / "splits" / "dev.txt").read_text() == "p_old\np_test01\n"
    shutil.rmtree(data)
