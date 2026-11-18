"""export_hf.py on synthetic temp folders (no real data, no network)."""

import hashlib
import json
import shutil
import struct
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import export_hf
import pytest
import yaml

IDS = ["p_a01", "p_b02"]


def _stl(path: Path, n: int) -> None:
    tri = struct.pack("<12fH", *([0.0] * 12), 0)
    path.write_bytes(b"\0" * 80 + struct.pack("<I", n) + tri * n)


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture
def env(tmp_path: Path) -> SimpleNamespace:
    repo = tmp_path / "repo"
    (repo / "docs" / "benchmark").mkdir(parents=True)
    (repo / "docs" / "benchmark" / "submission.md").write_text("# submission\n")
    (repo / "tools").mkdir()
    (repo / "tools" / "sanity_check_submission.py").write_text("print('ok')\n")
    (repo / "evaluator" / "recadbench_eval").mkdir(parents=True)
    (repo / "evaluator" / "recadbench_eval" / "submission.py").write_text("LIMITS = {}\n")

    data, gt = tmp_path / "data", tmp_path / "gt" / "parts"
    files = {}
    for i, pid in enumerate(IDS):
        d = data / "parts" / pid
        d.mkdir(parents=True)
        _stl(d / "input.stl", 3 + i)
        task = {"part_id": pid, "input_kind": "scan", "tolerance_class": "B", "tier": 1 + i}
        if i == 0:
            task["scan"] = {"triangles": 3, "known_defects": ["hole in the top"]}
        (d / "task.yaml").write_text(yaml.safe_dump(task))
        for name in ("input.stl", "task.yaml"):
            files[f"parts/{pid}/{name}"] = {"sha256": _sha(d / name)}

        g = gt / pid
        g.mkdir(parents=True)
        for name in export_hf.GT_FILES:
            (g / name).write_text(f"GT {name} SECRET_VALUE_{pid}\n")
        (g / "gt_native.py").write_text("secret native\n")
        (g / "gt_native_params.json").write_text("{}\n")
        (g / "scan_raw.stl").write_bytes(b"raw")
        (g / "notes.txt").write_text("not allowlisted\n")
    (data / "MANIFEST.json").write_text(json.dumps({"files": files}))
    (data / "splits").mkdir()
    (data / "splits" / "dev.txt").write_text(IDS[1] + "\n")
    prev = tmp_path / "previews"
    prev.mkdir()
    (prev / f"{IDS[0]}.png").write_bytes(b"\x89PNG")
    return SimpleNamespace(
        repo=repo, data=data, gt=gt, out=tmp_path / "staging", previews=prev, tmp=tmp_path
    )


def _args(env, *extra) -> list[str]:
    return ["--data-root", str(env.data), "--gt-root", str(env.gt), "--out", str(env.out), *extra]


def _all_names(root: Path) -> set[str]:
    return {f.name for f in root.rglob("*")}


def test_builds_both_trees(env) -> None:
    assert export_hf.main(_args(env, "--previews", str(env.previews)), repo=env.repo) == 0
    inp, gt = env.out / "inputs", env.out / "gt"
    for pid in IDS:
        src = env.data / "parts" / pid
        for name in ("input.stl", "task.yaml"):
            assert (inp / "parts" / pid / name).read_bytes() == (src / name).read_bytes()
        assert sorted(p.name for p in (gt / "parts" / pid).iterdir()) == sorted(export_hf.GT_FILES)
    assert (inp / "parts" / IDS[0] / "preview.png").is_file()
    assert not (inp / "parts" / IDS[1] / "preview.png").exists()
    assert (inp / "SUBMISSION.md").read_text() == "# submission\n"
    assert (inp / "tools" / "sanity_check_submission.py").is_file()
    assert (inp / "tools" / "submission.py").read_text() == "LIMITS = {}\n"
    rows = json.loads((inp / "tasks.json").read_text())
    assert rows == [
        {
            "id": IDS[0],
            "tolerance_class": "B",
            "tier": 1,
            "triangles": 3,
            "known_defects": ["hole in the top"],
        },
        {"id": IDS[1], "tolerance_class": "B", "tier": 2, "triangles": 4, "known_defects": []},
    ]
    assert "PRIVATE" in (gt / "README.md").read_text()
    card = (inp / "README.md").read_text()
    assert card.startswith("---\n") and IDS[0] in card
    assert "tools/submission.py" in card


def test_gt_native_and_scan_never_copied(env) -> None:
    export_hf.main(_args(env), repo=env.repo)
    names = _all_names(env.out)
    assert not any(n.startswith("gt_native") for n in names)
    assert "scan_raw.stl" not in names
    assert "notes.txt" not in names


def test_inputs_side_has_no_gt_content(env) -> None:
    export_hf.main(_args(env), repo=env.repo)
    inp = env.out / "inputs"
    assert not (_all_names(inp) & set(export_hf.GT_FILES))
    for f in inp.rglob("*"):
        if f.is_file():
            assert b"SECRET_VALUE" not in f.read_bytes(), f


def test_split_selection(env) -> None:
    export_hf.main(_args(env, "--split", "dev"), repo=env.repo)
    assert sorted(p.name for p in (env.out / "inputs" / "parts").iterdir()) == [IDS[1]]
    assert sorted(p.name for p in (env.out / "gt" / "parts").iterdir()) == [IDS[1]]


def test_refuses_staging_inside_public_repo(env) -> None:
    with pytest.raises(SystemExit, match="public ReCADBench repo"):
        export_hf.main(_args(env)[:-1] + [str(env.repo / "staging")], repo=env.repo)
    assert not (env.repo / "staging").exists()


def test_refuses_staging_inside_real_repo(env) -> None:
    target = export_hf.REPO / "_hf_staging_should_not_exist"
    with pytest.raises(SystemExit, match="public ReCADBench repo"):
        export_hf.main(_args(env)[:-1] + [str(target)])
    assert not target.exists()


@pytest.mark.parametrize("where", ["data", "gt"])
def test_refuses_staging_overlapping_roots(env, where) -> None:
    root = env.data if where == "data" else env.gt
    with pytest.raises(SystemExit, match="overlaps"):
        export_hf.main(_args(env)[:-1] + [str(root / "staging")], repo=env.repo)


def test_refuses_foreign_nonempty_staging(env) -> None:
    (env.out / "gt").mkdir(parents=True)
    (env.out / "gt" / "keep.txt").write_text("mine")
    with pytest.raises(SystemExit, match="not written by export_hf"):
        export_hf.main(_args(env), repo=env.repo)
    assert (env.out / "gt" / "keep.txt").read_text() == "mine"


def test_rebuild_own_staging(env) -> None:
    export_hf.main(_args(env), repo=env.repo)
    export_hf.main(_args(env, "--parts", IDS[0]), repo=env.repo)
    assert [p.name for p in (env.out / "gt" / "parts").iterdir()] == [IDS[0]]


def test_manifest_mismatch_refused(env) -> None:
    _stl(env.data / "parts" / IDS[0] / "input.stl", 9)
    with pytest.raises(SystemExit, match="MANIFEST"):
        export_hf.main(_args(env), repo=env.repo)


def test_missing_required_gt_refused(env) -> None:
    (env.gt / IDS[1] / "gt.step").unlink()
    with pytest.raises(SystemExit, match="gt.step missing"):
        export_hf.main(_args(env), repo=env.repo)


def test_missing_repo_files_refused(env) -> None:
    (env.repo / "tools" / "sanity_check_submission.py").unlink()
    with pytest.raises(SystemExit, match="sanity_check_submission"):
        export_hf.main(_args(env), repo=env.repo)


def test_missing_submission_module_refused(env) -> None:
    (env.repo / "evaluator" / "recadbench_eval" / "submission.py").unlink()
    with pytest.raises(SystemExit, match="submission.py"):
        export_hf.main(_args(env), repo=env.repo)


def _run_tool(tool: Path, *args: str, cwd: Path) -> subprocess.CompletedProcess:
    # -I -S: no site-packages, so the installed evaluator is hidden (a bare dataset download)
    return subprocess.run(
        [sys.executable, "-I", "-S", str(tool), *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=120,
    )


def test_staged_sanity_tool_runs_standalone(env, tmp_path) -> None:
    """The real tool + submission.py staged from this repo work without an evaluator install."""
    export_hf.main(_args(env), repo=export_hf.REPO)
    tool = env.out / "inputs" / "tools" / "sanity_check_submission.py"
    meta = {
        "submitter_name": "t",
        "submission_name": "t",
        "agent_url": "",
        "model": "m",
        "cad_backend": "b",
        "harness": "h",
        "human_in_loop": False,
        "notes": "",
        "agree_to_publish": True,
    }
    z = tmp_path / "sub.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("meta.json", json.dumps(meta))
        zf.writestr(f"{IDS[0]}/output.step", "ISO-10303-21;\n")
    ids = tmp_path / "ids.txt"
    ids.write_text("\n".join(IDS) + "\n")
    r = _run_tool(tool, str(z), "--tasks-file", str(ids), "--no-geometry", cwd=tmp_path)
    assert r.returncode == 0 and "RESULT: OK" in r.stdout, r.stdout + r.stderr
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    r = _run_tool(tool, str(bad), "--tasks-file", str(ids), cwd=tmp_path)
    assert r.returncode == 1 and "RESULT: FAIL (structure)" in r.stdout, r.stdout + r.stderr


def _fake_git(root: Path, *, worktree: bool = False, remote: bool = True) -> None:
    cfg = "[core]\n\tbare = false\n" + ('[remote "origin"]\n\turl = x\n' if remote else "")
    root.mkdir(parents=True, exist_ok=True)
    if worktree:
        main = root.parent / (root.name + "_main_git")
        (main / "worktrees" / "w").mkdir(parents=True)
        (main / "config").write_text(cfg)
        (main / "worktrees" / "w" / "commondir").write_text("../..\n")
        (root / ".git").write_text(f"gitdir: {(main / 'worktrees' / 'w').as_posix()}\n")
    else:
        (root / ".git").mkdir()
        (root / ".git" / "config").write_text(cfg)


@pytest.mark.parametrize("kind", ["remote_dir", "worktree_file", "rcb_marker_no_remote"])
def test_refuses_staging_inside_other_checkout(env, kind) -> None:
    clone = env.tmp / "other_clone"
    if kind == "remote_dir":
        _fake_git(clone)
    elif kind == "worktree_file":
        _fake_git(clone, worktree=True)
    else:
        _fake_git(clone, remote=False)
        (clone / "dataset" / "tools").mkdir(parents=True)
        (clone / "dataset" / "tools" / "export_hf.py").write_text("")
    target = clone / "deep" / "stage"
    with pytest.raises(SystemExit, match="git checkout"):
        export_hf.main(_args(env)[:-1] + [str(target)], repo=env.repo)
    assert not target.exists()


def test_local_git_without_remote_allowed(env) -> None:
    home = env.tmp / "home_repo"
    _fake_git(home, remote=False)
    target = home / "stage"
    assert export_hf.main(_args(env)[:-1] + [str(target)], repo=env.repo) == 0
    assert (target / "gt" / "parts" / IDS[0] / "gt.step").is_file()


def test_out_not_a_folder_refused(env) -> None:
    env.out.write_text("file")
    with pytest.raises(SystemExit, match="not a folder"):
        export_hf.main(_args(env), repo=env.repo)
    env.out.unlink()
    env.out.mkdir()
    (env.out / "inputs").write_text("file")
    with pytest.raises(SystemExit, match="not a folder"):
        export_hf.main(_args(env), repo=env.repo)


def test_missing_data_parts_refused(env) -> None:
    shutil.rmtree(env.data / "parts")
    with pytest.raises(SystemExit, match="no parts/ folder"):
        export_hf.main(_args(env), repo=env.repo)


def test_gt_root_not_a_folder_refused(env) -> None:
    f = env.tmp / "gt_file"
    f.write_text("x")
    args = _args(env)
    args[args.index("--gt-root") + 1] = str(f)
    with pytest.raises(SystemExit, match="--gt-root is not a folder"):
        export_hf.main(args, repo=env.repo)


def _drop_manifest_entry(env, key: str) -> None:
    m = json.loads((env.data / "MANIFEST.json").read_text())
    m["files"].pop(key)
    (env.data / "MANIFEST.json").write_text(json.dumps(m))


@pytest.mark.parametrize(
    ("case", "text"),
    [
        ("task_list", "- a\n- b\n"),
        ("scan_str", f"part_id: {IDS[1]}\nscan: oops\n"),
        ("task_bad_yaml", "a: [\n"),
    ],
)
def test_bad_task_yaml_is_export_error(env, case, text) -> None:
    (env.data / "parts" / IDS[1] / "task.yaml").write_text(text)
    _drop_manifest_entry(env, f"parts/{IDS[1]}/task.yaml")
    with pytest.raises(export_hf.ExportError):
        export_hf.main(_args(env), repo=env.repo)


def test_manifest_entry_without_sha_is_export_error(env) -> None:
    (env.data / "MANIFEST.json").write_text(
        json.dumps({"files": {f"parts/{IDS[1]}/input.stl": {"bytes": 84}}})
    )
    with pytest.raises(export_hf.ExportError, match="no sha256"):
        export_hf.main(_args(env), repo=env.repo)


def test_duplicate_ids_in_split_deduplicated(env) -> None:
    (env.data / "splits" / "dev.txt").write_text(f"{IDS[1]}\n{IDS[1]}\n")
    assert export_hf.main(_args(env, "--split", "dev"), repo=env.repo) == 0
    assert [p.name for p in (env.out / "gt" / "parts").iterdir()] == [IDS[1]]
    rows = json.loads((env.out / "inputs" / "tasks.json").read_text())
    assert [r["id"] for r in rows] == [IDS[1]]


def test_bad_part_id_refused(env) -> None:
    with pytest.raises(SystemExit, match="invalid part id"):
        export_hf.main(_args(env, "--parts", "../x"), repo=env.repo)


def test_audit_catches_forbidden(env) -> None:
    export_hf.main(_args(env), repo=env.repo)
    (env.out / "gt" / "parts" / IDS[0] / "gt_native.py").write_text("x")
    with pytest.raises(SystemExit, match="forbidden"):
        export_hf.audit(env.out / "inputs", env.out / "gt")
    (env.out / "gt" / "parts" / IDS[0] / "gt_native.py").unlink()
    (env.out / "inputs" / "parts" / IDS[0] / "gt.step").write_text("x")
    with pytest.raises(SystemExit, match="inputs side"):
        export_hf.audit(env.out / "inputs", env.out / "gt")


def test_stl_triangles_ascii(tmp_path) -> None:
    f = tmp_path / "a.stl"
    f.write_text(
        "solid x\n facet normal 0 0 1\n endfacet\n facet normal 0 0 1\n endfacet\nendsolid\n"
    )
    assert export_hf.stl_triangles(f) == 2


class FakeApi:
    def __init__(self, private_on_hub: bool = True) -> None:
        self.calls: list[tuple] = []
        self.ignores: list[list[str]] = []
        self.private_on_hub = private_on_hub

    def create_repo(self, repo_id, repo_type, private, exist_ok):
        self.calls.append(("create", repo_id, private))

    def repo_info(self, repo_id, repo_type):
        return SimpleNamespace(private=self.private_on_hub)

    def upload_folder(self, repo_id, repo_type, folder_path, ignore_patterns, commit_message):
        self.calls.append(("upload", repo_id, Path(folder_path).name))
        self.ignores.append(list(ignore_patterns))


def test_no_push_by_default(env) -> None:
    api = FakeApi()
    export_hf.main(_args(env, "--inputs-repo", "o/in", "--gt-repo", "o/gt"), repo=env.repo, api=api)
    assert api.calls == []


def test_push_forces_private_gt(env) -> None:
    api = FakeApi()
    export_hf.main(
        _args(env, "--push", "--inputs-repo", "o/in", "--gt-repo", "o/gt"), repo=env.repo, api=api
    )
    assert ("create", "o/gt", True) in api.calls
    assert ("upload", "o/gt", "gt") in api.calls
    assert ("upload", "o/in", "inputs") in api.calls
    assert ("create", "o/in", True) in api.calls  # inputs private unless --inputs-public
    assert len(api.ignores) == 2 and all(export_hf.MARKER in ig for ig in api.ignores)


def test_push_inputs_public(env) -> None:
    api = FakeApi()
    export_hf.main(
        _args(env, "--push", "--inputs-repo", "o/in", "--gt-repo", "o/gt", "--inputs-public"),
        repo=env.repo,
        api=api,
    )
    assert ("create", "o/in", False) in api.calls
    assert ("create", "o/gt", True) in api.calls


def test_push_refused_if_gt_repo_public(env) -> None:
    api = FakeApi(private_on_hub=False)
    with pytest.raises(SystemExit, match="not private"):
        export_hf.main(
            _args(env, "--push", "--inputs-repo", "o/in", "--gt-repo", "o/gt"),
            repo=env.repo,
            api=api,
        )
    assert not any(c[0] == "upload" for c in api.calls)


def test_push_needs_repos_and_distinct(env) -> None:
    with pytest.raises(SystemExit, match="needs"):
        export_hf.main(_args(env, "--push", "--gt-repo", "o/gt"), repo=env.repo, api=FakeApi())
    with pytest.raises(SystemExit, match="must differ"):
        export_hf.main(
            _args(env, "--push", "--inputs-repo", "o/x", "--gt-repo", "o/x"),
            repo=env.repo,
            api=FakeApi(),
        )
