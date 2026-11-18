"""export_hf.py on synthetic temp folders (no real data, no network)."""

import hashlib
import json
import shutil
import struct
import subprocess
import sys
import zipfile
import zlib
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


def _png(w: int, h: int, extra: tuple[tuple[bytes, bytes], ...] = (), tail: bytes = b"") -> bytes:
    """A valid RGB PNG (stdlib only) with optional extra chunks before IDAT and bytes after IEND."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    rows = b"".join(b"\0" + b"\xc8\x28\x28" * w for _ in range(h))
    body = chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    body += b"".join(chunk(k, d) for k, d in extra)
    body += chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")
    return export_hf.PNG_SIGNATURE + body + tail


LEAK = b"C:\\Users\\someone\\renders\\part.blend"
LEAKY_CHUNKS = (
    (b"tEXt", b"File\0" + LEAK),
    (b"iTXt", b"Host\0\0\0\0\0" + LEAK),
    (b"eXIf", b"MM\0*\0\0\0\x08\0\0\0\0\0\0" + LEAK),  # empty IFD, then the path
)


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
    (prev / f"{IDS[0]}.png").write_bytes(_png(40, 20))
    return SimpleNamespace(
        repo=repo, data=data, gt=gt, out=tmp_path / "staging", previews=prev, tmp=tmp_path
    )


def _args(env, *extra) -> list[str]:
    return ["--data-root", str(env.data), "--gt-root", str(env.gt), "--out", str(env.out), *extra]


def _all_names(root: Path) -> set[str]:
    return {f.name for f in root.rglob("*")}


def test_builds_both_trees(env) -> None:
    pytest.importorskip("PIL")
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
    """create_repo with exist_ok keeps an existing repo's visibility, like the Hub."""

    def __init__(self, private_on_hub: bool = True, existing: dict | None = None) -> None:
        self.calls: list[tuple] = []
        self.ignores: list[list[str]] = []
        self.private_on_hub = private_on_hub  # False: the Hub reports every repo public
        self.repos: dict[str, bool] = dict(existing or {})  # repo id -> private

    def create_repo(self, repo_id, repo_type, private, exist_ok):
        self.calls.append(("create", repo_id, private))
        self.repos.setdefault(repo_id, private)

    def repo_info(self, repo_id, repo_type):
        return SimpleNamespace(private=self.repos[repo_id] and self.private_on_hub)

    def upload_folder(self, repo_id, repo_type, folder_path, ignore_patterns, commit_message):
        self.calls.append(("upload", repo_id, Path(folder_path).name))
        self.ignores.append(list(ignore_patterns))


def _public_table(env, entries: list[dict]) -> None:
    (env.repo / "docs" / "benchmark" / "tasks_public.json").write_text(json.dumps(entries))


def test_tasks_json_enriched_from_public_table(env) -> None:
    entry = {"id": IDS[1], "no": 1, "title": "Part B", "family": "Fam", "difficulty": "hard"}
    _public_table(env, [{**entry, "secret": "not copied"}, {"id": "p_other", "no": 2}])
    export_hf.main(_args(env), repo=env.repo)
    raw = (env.out / "inputs" / "tasks.json").read_text(encoding="utf-8")
    rows = json.loads(raw)
    # sorted by the public number; a task without a table entry keeps its fields only
    assert rows == [
        {
            **entry,
            "tolerance_class": "B",
            "tier": 2,
            "triangles": 4,
            "known_defects": [],
        },
        {
            "id": IDS[0],
            "tolerance_class": "B",
            "tier": 1,
            "triangles": 3,
            "known_defects": ["hole in the top"],
        },
    ]
    assert list(rows[0]) == [
        "id",
        "no",
        "title",
        "family",
        "difficulty",
        "tolerance_class",
        "tier",
        "triangles",
        "known_defects",
    ]
    assert "secret" not in raw and "p_other" not in raw


@pytest.mark.parametrize(
    "table",
    [
        {"id": IDS[0]},
        [{"no": 1}],
        [{"id": IDS[0], "difficulty": "easy"}],
        [{"id": IDS[0], "no": True}],
        [{"id": IDS[0], "title": ""}],
        [{"id": IDS[0]}, {"id": IDS[0]}],
    ],
)
def test_bad_public_table_refused(env, table) -> None:
    (env.repo / "docs" / "benchmark" / "tasks_public.json").write_text(json.dumps(table))
    with pytest.raises(export_hf.ExportError, match="tasks_public.json"):
        export_hf.main(_args(env), repo=env.repo)


def test_real_public_table_matches_readme() -> None:
    table = export_hf.load_tasks_public(export_hf.REPO / export_hf.TASKS_PUBLIC)
    assert len(table) == 10
    assert sorted(t["no"] for t in table.values()) == list(range(1, 11))
    readme = (export_hf.REPO / "README.md").read_text(encoding="utf-8")
    families = {"R385 diaphragm pump", "Open Dedica espresso machine"}
    for pid, t in table.items():
        assert set(t) == set(export_hf.PUBLIC_KEYS) and t["family"] in families
        cell = f"<b>{t['no']:02d} · {t['title']}</b><br><sub><code>{pid}</code></sub>"
        assert cell in readme and f"<code>{pid}</code></sub><br><sub>" in readme
        after = readme.split(f"<code>{pid}</code></sub><br><sub>", 1)[1]
        assert after.split("</sub>", 1)[0].split(" ", 1)[1] == t["difficulty"]


def _image(path: Path, size, mode: str = "RGB") -> None:
    image = pytest.importorskip("PIL.Image")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.new(mode, size, (200, 40, 40, 128)[: len(mode)]).save(path)  # half-transparent


def _png_size(path: Path):
    image = pytest.importorskip("PIL.Image")
    data = path.read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    with image.open(path) as im:
        return im.size, im.mode


def test_webp_previews_converted_to_png(env) -> None:
    prev = env.tmp / "webp_previews"
    _image(prev / f"{IDS[0]}.webp", (1200, 600))
    _image(prev / IDS[1] / "preview.webp", (100, 50), "RGBA")
    export_hf.main(_args(env, "--previews", str(prev)), repo=env.repo)
    parts = env.out / "inputs" / "parts"
    assert _png_size(parts / IDS[0] / "preview.png") == ((768, 384), "RGB")
    assert _png_size(parts / IDS[1] / "preview.png") == ((100, 50), "RGBA")
    assert not list((env.out / "inputs").rglob("*.webp"))


def test_png_preview_preferred_and_reencoded(env) -> None:
    _image(env.previews / f"{IDS[0]}.webp", (50, 50))
    big = env.previews / IDS[1] / "preview.png"
    big.parent.mkdir()
    big.write_bytes(_png(1000, 10))
    export_hf.main(_args(env, "--previews", str(env.previews)), repo=env.repo)
    parts = env.out / "inputs" / "parts"
    assert _png_size(parts / IDS[0] / "preview.png") == ((40, 20), "RGB")  # the .png, not .webp
    assert _png_size(parts / IDS[1] / "preview.png") == ((768, 8), "RGB")  # resized like webp


@pytest.mark.parametrize("suffix", [".png", ".webp"])
def test_preview_metadata_stripped(env, suffix) -> None:
    """A local path in PNG text / EXIF chunks or webp EXIF / ICC never reaches the dataset."""
    image = pytest.importorskip("PIL.Image")
    src = env.tmp / "leaky" / f"{IDS[0]}{suffix}"
    src.parent.mkdir()
    if suffix == ".png":
        src.write_bytes(_png(30, 30, LEAKY_CHUNKS))
    else:
        exif = image.Exif()
        exif[0x010F] = LEAK.decode()  # Make
        exif[0x0131] = LEAK.decode()  # Software
        icc = b"\0" * 64 + LEAK
        image.new("RGB", (30, 30), (1, 2, 3)).save(src, exif=exif, icc_profile=icc)
    assert LEAK in src.read_bytes()
    export_hf.main(_args(env, "--previews", str(src.parent)), repo=env.repo)
    staged = env.out / "inputs" / "parts" / IDS[0] / "preview.png"
    data = staged.read_bytes()
    assert LEAK not in data and b"Users" not in data
    assert export_hf.png_problem(staged) is None
    assert _png_size(staged) == ((30, 30), "RGB")
    with image.open(staged) as im:
        assert not im.info and not im.getexif()


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        (_png(8, 8, LEAKY_CHUNKS[:1]), "'tEXt' chunk"),
        (_png(8, 8, LEAKY_CHUNKS[1:2]), "'iTXt' chunk"),
        (_png(8, 8, ((b"zTXt", b"k\0\0" + zlib.compress(LEAK)),)), "'zTXt' chunk"),
        (_png(8, 8, LEAKY_CHUNKS[2:]), "'eXIf' chunk"),
        (_png(8, 8, ((b"iCCP", b"p\0\0" + zlib.compress(LEAK)),)), "'iCCP' chunk"),
        (_png(8, 8, tail=LEAK), "data after the IEND"),
        (_png(8, 8)[:-12], "truncated"),
        (b"GIF89a", "not a PNG"),
    ],
)
def test_audit_refuses_preview_metadata(env, payload, match) -> None:
    export_hf.main(_args(env), repo=env.repo)
    export_hf.audit(env.out / "inputs", env.out / "gt")
    (env.out / "inputs" / "parts" / IDS[1] / "preview.png").write_bytes(payload)
    with pytest.raises(export_hf.ExportError, match=match):
        export_hf.audit(env.out / "inputs", env.out / "gt")
    api = FakeApi()
    with pytest.raises(export_hf.ExportError, match=match):
        export_hf.push(env.out / "inputs", env.out / "gt", "o/in", "o/gt", api=api)
    assert api.calls == []


def test_corrupt_webp_preview_refused(env) -> None:
    (env.previews / f"{IDS[1]}.webp").write_bytes(b"RIFF....WEBPjunk")
    pytest.importorskip("PIL")
    with pytest.raises(export_hf.ExportError, match="could not be converted"):
        export_hf.main(_args(env, "--previews", str(env.previews)), repo=env.repo)


def test_default_previews_from_repo_assets(env) -> None:
    export_hf.main(_args(env), repo=env.repo)
    assert not list((env.out / "inputs" / "parts").rglob("preview.png"))
    _image(env.repo / "docs" / "assets" / "tasks" / f"{IDS[1]}.webp", (64, 32))
    export_hf.main(_args(env), repo=env.repo)
    parts = env.out / "inputs" / "parts"
    assert _png_size(parts / IDS[1] / "preview.png") == ((64, 32), "RGB")
    assert not (parts / IDS[0] / "preview.png").exists()


def test_previews_not_a_folder_refused(env) -> None:
    with pytest.raises(SystemExit, match="--previews is not a folder"):
        export_hf.main(_args(env, "--previews", str(env.tmp / "nope")), repo=env.repo)


def _card(env, *extra) -> str:
    assert export_hf.main(_args(env, *extra), repo=env.repo) == 0
    return (env.out / "inputs" / "README.md").read_text(encoding="utf-8")


def test_card_links_and_content(env) -> None:
    pytest.importorskip("PIL")
    _public_table(env, [{"id": IDS[0], "no": 1, "title": "Part A", "family": "Fam"}])
    card = _card(
        env,
        "--previews",
        str(env.previews),
        "--inputs-repo",
        "o/in",
        "--gt-repo",
        "o/hidden-gt-repo",
        "--space",
        "o/space",
    )
    front = card.split("---\n")[1]
    assert "license: cc-by-4.0" in front and "pretty_name:" in front and "size_categories" in front
    assert "https://huggingface.co/spaces/o/space" in card
    assert export_hf.GITHUB_URL in card and export_hf.LIVE_URL in card
    assert "hf download o/in --repo-type dataset --local-dir recadbench" in card
    assert "pip install -U huggingface_hub\n" in card and "[cli]" not in card
    sanity = "recadbench/tools/sanity_check_submission.py submission.zip --inputs recadbench/parts"
    assert sanity in card and "pip install build123d" in card
    assert 'snapshot_download("o/in", repo_type="dataset"' in card
    url = "https://huggingface.co/datasets/o/in/resolve/main/parts/{}/preview.png"
    assert url.format(IDS[0]) in card and url.format(IDS[1]) not in card
    assert "<b>01 · Part A</b>" in card
    for comp in ("**G0**", "**S1** | Deviation | **40**", "**S2-face**", "**25**", "**S3**"):
        assert comp in card
    assert "**S4** | Topology | **10**" in card and "**/95**" in card
    assert "human_in_loop" in card and "@misc{recadbench2026," in card
    assert "hole in the top" in card  # known defects (task.yaml, agent-visible)
    # nothing that names or points to the private GT side
    for name in (*export_hf.GT_FILES, "gt_native", "scan_raw", "hidden-gt-repo", "SECRET"):
        assert name not in card, name


def test_card_default_repos(env) -> None:
    card = _card(env)
    assert f"hf download {export_hf.DEFAULT_INPUTS_REPO} --repo-type dataset" in card
    assert f"https://huggingface.co/spaces/{export_hf.DEFAULT_SPACE}" in card
    assert "/resolve/main/parts/" not in card  # no previews staged -> no gallery images


def test_card_escapes_public_table_text(env) -> None:
    title = '<script>alert(1)</script> | "x"'
    _public_table(env, [{"id": IDS[0], "no": 1, "title": title, "family": "A & B"}])
    card = _card(env)
    assert "<script>" not in card and "&lt;script&gt;" in card
    assert "A &amp; B" in card
    table_rows = [ln for ln in card.splitlines() if ln.startswith("| 01 |")]
    assert len(table_rows) == 1 and table_rows[0].count("|") == 9
    rows = json.loads((env.out / "inputs" / "tasks.json").read_text(encoding="utf-8"))
    assert rows[0]["title"] == title


@pytest.mark.parametrize(
    "extra",
    [
        ("--space", "no-slash"),
        ("--space", 'o/x"><img src=y>'),
        ("--inputs-repo", "a/b/c"),
        ("--gt-repo", "o/x y"),
    ],
)
def test_invalid_repo_id_refused(env, extra) -> None:
    with pytest.raises(SystemExit, match="invalid Hugging Face repo id"):
        export_hf.main(_args(env, *extra), repo=env.repo)
    assert not (env.out / "inputs").exists()


@pytest.mark.parametrize(
    ("rel", "side"),
    [
        ("inputs/parts/{pid}/notes.txt", "inputs allowlist"),
        ("inputs/tools/helper.py", "inputs allowlist"),
        ("inputs/extra.json", "inputs allowlist"),
        ("inputs/parts/{pid}/sub/input.stl", "inputs allowlist"),
        ("gt/parts/{pid}/notes.txt", "GT allowlist"),
        ("gt/extra.md", "GT allowlist"),
    ],
)
def test_audit_allowlist(env, rel, side) -> None:
    pytest.importorskip("PIL")
    export_hf.main(_args(env, "--previews", str(env.previews)), repo=env.repo)
    export_hf.audit(env.out / "inputs", env.out / "gt")  # the staged trees pass
    f = env.out / rel.format(pid=IDS[0])
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("x")
    with pytest.raises(SystemExit, match=side):
        export_hf.audit(env.out / "inputs", env.out / "gt")


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


def test_push_reports_inputs_visibility(env, capsys) -> None:
    export_hf.main(
        _args(env, "--push", "--inputs-repo", "o/in", "--gt-repo", "o/gt", "--inputs-public"),
        repo=env.repo,
        api=FakeApi(),
    )
    out = capsys.readouterr().out
    assert "o/in (inputs) is public on the Hub" in out and "pushed: o/in (inputs, public)" in out
    assert "warning" not in out


def test_push_warns_existing_private_inputs(env, capsys) -> None:
    api = FakeApi(existing={"o/in": True})
    export_hf.main(
        _args(env, "--push", "--inputs-repo", "o/in", "--gt-repo", "o/gt", "--inputs-public"),
        repo=env.repo,
        api=api,
    )
    out = capsys.readouterr().out
    assert ("create", "o/in", False) in api.calls  # asked public, but the repo already existed
    assert "warning: o/in is still PRIVATE although --inputs-public" in out
    assert "pushed: o/in (inputs, PRIVATE)" in out


def test_push_private_inputs_without_flag_no_warning(env, capsys) -> None:
    export_hf.main(
        _args(env, "--push", "--inputs-repo", "o/in", "--gt-repo", "o/gt"),
        repo=env.repo,
        api=FakeApi(),
    )
    out = capsys.readouterr().out
    assert "o/in (inputs) is PRIVATE on the Hub" in out and "warning" not in out


DOC = """# Submitting to ReCADBench Open

Rationale: [ADR 0006](../adr/0006-open-track.md), rubric [03](../architecture/03_SCORING.md#s1).
Same folder: [x](other.md). Kept: [web](https://example.org/a), [anchor](#rules), [abs](/x).
"""


def test_submission_doc_links_made_absolute(env, capsys) -> None:
    (env.repo / "docs" / "benchmark" / "submission.md").write_text(DOC, encoding="utf-8")
    export_hf.main(_args(env), repo=env.repo)
    staged = (env.out / "inputs" / "SUBMISSION.md").read_text(encoding="utf-8")
    blob = f"{export_hf.GITHUB_URL}/blob/main/docs"
    assert f"[ADR 0006]({blob}/adr/0006-open-track.md)" in staged
    assert f"[03]({blob}/architecture/03_SCORING.md#s1)" in staged
    assert f"[x]({blob}/benchmark/other.md)" in staged
    assert "(https://example.org/a)" in staged and "(#rules)" in staged and "(/x)" in staged
    assert "](../" not in staged
    assert export_hf.submission_doc_problems(env.out / "inputs" / "SUBMISSION.md") == []
    assert "draft" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "text",
    [
        "# Submitting (draft)\n",
        "# S\n\n> Draft. Something.\n",
        "The submission Space is not open yet.\n",
        "Limits (planned): zip <= 300 MB\n",
        "`tools/x.py` (coming with the Space) checks it\n",
    ],
)
def test_draft_submission_doc_warned_and_push_refused(env, capsys, text) -> None:
    (env.repo / "docs" / "benchmark" / "submission.md").write_text(text, encoding="utf-8")
    assert export_hf.main(_args(env), repo=env.repo) == 0  # staging still works
    assert "still reads like a draft" in capsys.readouterr().out
    api = FakeApi()
    with pytest.raises(export_hf.ExportError, match="draft"):
        export_hf.main(
            _args(env, "--push", "--inputs-repo", "o/in", "--gt-repo", "o/gt"),
            repo=env.repo,
            api=api,
        )
    assert api.calls == []
