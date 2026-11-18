"""Submission package handling (recadbench_eval.submission) and tools/sanity_check_submission.py.

All zips are built in-test; no ground truth is used.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import struct
import sys
import warnings
import zipfile
from pathlib import Path

import pytest
from recadbench_eval import submission as sm
from recadbench_eval.submission import (
    LIMITS,
    META_REQUIRED,
    SubmissionError,
    extract_task,
    inspect_zip,
    validate_meta,
)

REPO = Path(__file__).resolve().parents[2]
TASKS = ["t_alpha", "t_beta", "t_gamma"]
STEP = b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n"


def good_meta(**over) -> dict:
    m = {
        "submitter_name": "Test Lab",
        "submission_name": "agent-x v1",
        "agent_url": "https://example.org/agent",
        "model": "some-model",
        "cad_backend": "build123d",
        "harness": "custom",
        "human_in_loop": False,
        "notes": "",
        "agree_to_publish": True,
    }
    m.update(over)
    return m


def make_zip(path: Path, entries: dict, meta: dict | None = None, *, with_meta=True) -> Path:
    """``entries``: arcname -> bytes | str | ZipInfo-tuple (ZipInfo, bytes)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # duplicate-name tests
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            if with_meta:
                zf.writestr("meta.json", json.dumps(meta or good_meta()))
            for name, data in entries.items():
                if isinstance(data, tuple):
                    zf.writestr(*data)
                else:
                    zf.writestr(name, data)
    return path


def patch_bytes(path: Path, old: bytes, new: bytes) -> None:
    assert len(old) == len(new)
    data = path.read_bytes()
    assert old in data
    path.write_bytes(data.replace(old, new))


def set_flag_bits(path: Path, name: str, bit: int) -> None:
    """Set a general-purpose flag bit on ``name`` in the central directory and local header."""
    data = bytearray(path.read_bytes())
    raw = name.encode()
    i = 0
    while (i := data.find(b"PK\x01\x02", i)) >= 0:
        n = struct.unpack_from("<H", data, i + 28)[0]
        if bytes(data[i + 46:i + 46 + n]) == raw:
            struct.pack_into("<H", data, i + 8, struct.unpack_from("<H", data, i + 8)[0] | bit)
        i += 4
    i = 0
    while (i := data.find(b"PK\x03\x04", i)) >= 0:
        n = struct.unpack_from("<H", data, i + 26)[0]
        if bytes(data[i + 30:i + 30 + n]) == raw:
            struct.pack_into("<H", data, i + 6, struct.unpack_from("<H", data, i + 6)[0] | bit)
        i += 4
    path.write_bytes(bytes(data))


def patch_cd(path: Path, name: str, offset: int, fmt: str, value: int) -> None:
    """Overwrite one field of ``name``'s central-directory header (APPNOTE 4.3.12 offsets)."""
    data = bytearray(path.read_bytes())
    raw = name.encode()
    i, hit = 0, False
    while (i := data.find(b"PK\x01\x02", i)) >= 0:
        n = struct.unpack_from("<H", data, i + 28)[0]
        if bytes(data[i + 46:i + 46 + n]) == raw:
            struct.pack_into(fmt, data, i + offset, value)
            hit = True
        i += 4
    assert hit
    path.write_bytes(bytes(data))


CD_VERSION_NEEDED, CD_METHOD, CD_CRC, CD_FILE_SIZE = (6, "<H"), (10, "<H"), (16, "<L"), (24, "<L")


@pytest.fixture()
def good_zip(tmp_path):
    return make_zip(tmp_path / "sub.zip", {
        "t_alpha/output.step": STEP,
        "t_alpha/features.json": '{"features": []}',
        "t_alpha/plan/re_plan.v1.json": '{"v": 1}',
        "t_alpha/plan/re_plan.v12.json": '{"v": 12}',
        "t_alpha/native/model.py": "print(1)",
        "t_alpha/native/sub/deep.FCStd": b"x",
        "t_alpha/run.json": "{}",
        "t_alpha/readme.txt": "hello",
        "t_beta/": b"",
        "t_beta/features.json": "{}",
    })


# ------------------------------------------------------------------------------- inspect: ok
def test_inspect_valid(good_zip):
    sub = inspect_zip(good_zip, TASKS)
    assert sub.sha256 == hashlib.sha256(good_zip.read_bytes()).hexdigest()
    assert sub.meta == good_meta()
    assert list(sub.tasks) == ["t_alpha"]
    assert sub.tasks["t_alpha"] == {
        "output.step": "t_alpha/output.step",
        "features.json": "t_alpha/features.json",
        "plan/re_plan.v1.json": "t_alpha/plan/re_plan.v1.json",
        "plan/re_plan.v12.json": "t_alpha/plan/re_plan.v12.json",
        "run.json": "t_alpha/run.json",
    }
    assert sub.missing == ["t_beta", "t_gamma"]
    joined = "\n".join(sub.warnings)
    assert "readme.txt" in joined and "ignored" in joined
    assert "t_beta" in joined and "missing" in joined  # folder without output.step
    assert "t_gamma" not in joined  # no folder at all: missing, but no warning


def test_inspect_meta_extra_keys_warn(tmp_path):
    z = make_zip(tmp_path / "s.zip", {"t_alpha/output.step": STEP}, good_meta(extra=1))
    sub = inspect_zip(z, TASKS)
    assert any("unknown keys" in w for w in sub.warnings)


def test_inspect_nothing_solved_warns(tmp_path):
    z = make_zip(tmp_path / "s.zip", {})
    sub = inspect_zip(z, TASKS)
    assert sub.tasks == {} and sub.missing == TASKS
    assert any("nothing to score" in w for w in sub.warnings)


# ---------------------------------------------------------------------------- inspect: reject
@pytest.mark.parametrize(
    ("entries", "match"),
    [
        ({"extra.txt": "x"}, "unexpected top-level entry"),
        ({"other_task/output.step": STEP}, "unexpected top-level entry"),
        ({"T_ALPHA/features.json": "{}"}, "case-sensitive"),
        ({"__MACOSX/t_alpha/._output.step": "x"}, "macOS"),
        ({"sub/meta.json": "{}"}, "folder contents"),
        ({"/t_alpha/output.step": STEP}, "absolute"),
        ({"C:/t_alpha/output.step": STEP}, "drive letter"),
        ({"t_alpha/../t_alpha/output.step": STEP}, r"'\.\.'"),
        ({"t_alpha//output.step": STEP}, "empty or '.'"),
        ({"t_alpha/./output.step": STEP}, "empty or '.'"),
    ],
)
def test_inspect_rejects_layout_and_names(tmp_path, entries, match):
    z = make_zip(tmp_path / "s.zip", {"t_alpha/output.step": STEP, **entries})
    with pytest.raises(SubmissionError, match=match):
        inspect_zip(z, TASKS)


def test_rejects_backslash(tmp_path):
    z = make_zip(tmp_path / "s.zip", {"t_alpha|output.step": STEP})
    patch_bytes(z, b"t_alpha|output.step", b"t_alpha\\output.step")
    with pytest.raises(SubmissionError, match="backslash"):
        inspect_zip(z, TASKS)


def test_rejects_symlink(tmp_path):
    info = zipfile.ZipInfo("t_alpha/output.step")
    info.external_attr = (0o120777 << 16)
    z = make_zip(tmp_path / "s.zip", {"x": (info, "/etc/passwd")})
    with pytest.raises(SubmissionError, match="symbolic link"):
        inspect_zip(z, TASKS)


def test_rejects_encrypted(tmp_path):
    z = make_zip(tmp_path / "s.zip", {"t_alpha/output.step": STEP})
    set_flag_bits(z, "t_alpha/output.step", 0x1)
    with pytest.raises(SubmissionError, match="encrypted"):
        inspect_zip(z, TASKS)


def test_rejects_duplicates(tmp_path):
    z = tmp_path / "s.zip"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("meta.json", json.dumps(good_meta()))
            zf.writestr("t_alpha/output.step", STEP)
            zf.writestr("t_alpha/output.step", STEP)
    with pytest.raises(SubmissionError, match="duplicate"):
        inspect_zip(z, TASKS)


def test_rejects_case_duplicates(tmp_path):
    z = make_zip(tmp_path / "s.zip", {"t_alpha/output.step": STEP, "t_alpha/OUTPUT.step": STEP})
    with pytest.raises(SubmissionError, match="only by case"):
        inspect_zip(z, TASKS)


def test_rejects_not_a_zip(tmp_path):
    p = tmp_path / "s.zip"
    p.write_bytes(b"not a zip at all" * 10)
    with pytest.raises(SubmissionError, match="not a readable zip"):
        inspect_zip(p, TASKS)
    with pytest.raises(SubmissionError, match="not found"):
        inspect_zip(tmp_path / "nope.zip", TASKS)


def test_rejects_empty_and_missing_meta(tmp_path):
    z = tmp_path / "e.zip"
    zipfile.ZipFile(z, "w").close()
    with pytest.raises(SubmissionError, match="empty"):
        inspect_zip(z, TASKS)
    z2 = make_zip(tmp_path / "m.zip", {"t_alpha/output.step": STEP}, with_meta=False)
    with pytest.raises(SubmissionError, match="no top-level meta.json"):
        inspect_zip(z2, TASKS)


def test_rejects_bad_meta(tmp_path):
    z = tmp_path / "s.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("meta.json", "{not json")
    with pytest.raises(SubmissionError, match="not valid UTF-8 JSON"):
        inspect_zip(z, TASKS)
    z2 = make_zip(tmp_path / "s2.zip", {}, good_meta(agree_to_publish=False))
    with pytest.raises(SubmissionError, match="agree_to_publish"):
        inspect_zip(z2, TASKS)


def test_limits(tmp_path, monkeypatch):
    z = make_zip(tmp_path / "s.zip", {"t_alpha/output.step": STEP * 4, "t_beta/output.step": STEP})
    monkeypatch.setitem(LIMITS, "step_bytes", len(STEP) * 2)
    with pytest.raises(SubmissionError, match="STEP limit"):
        inspect_zip(z, TASKS)
    monkeypatch.setitem(LIMITS, "step_bytes", 50 * 2**20)
    monkeypatch.setitem(LIMITS, "total_uncompressed", len(STEP) * 3)
    with pytest.raises(SubmissionError, match="expands to more than"):
        inspect_zip(z, TASKS)
    monkeypatch.setitem(LIMITS, "total_uncompressed", 1200 * 2**20)
    monkeypatch.setitem(LIMITS, "max_entries", 2)
    with pytest.raises(SubmissionError, match="entries, limit"):
        inspect_zip(z, TASKS)
    monkeypatch.setitem(LIMITS, "max_entries", 2000)
    monkeypatch.setitem(LIMITS, "zip_bytes", 10)
    with pytest.raises(SubmissionError, match="limit is"):
        inspect_zip(z, TASKS)


def test_rejects_compression_bomb(tmp_path):
    z = make_zip(tmp_path / "s.zip", {"t_alpha/output.step": b"\0" * (4 * 2**20)})
    with pytest.raises(SubmissionError, match="compression ratio"):
        inspect_zip(z, TASKS)


def test_small_highly_compressible_entry_ok(tmp_path):
    z = make_zip(tmp_path / "s.zip", {"t_alpha/output.step": STEP,
                                      "t_alpha/features.json": " " * 50_000})
    assert "t_alpha" in inspect_zip(z, TASKS).tasks


def test_error_messages_are_ascii_and_pathless(tmp_path):
    z = make_zip(tmp_path / "s.zip", {"\u00fcber/output.step": STEP})
    with pytest.raises(SubmissionError) as ei:
        inspect_zip(z, TASKS)
    msg = str(ei.value)
    msg.encode("ascii")
    assert str(tmp_path) not in msg and "\\xfc" in msg


# ----------------------------------------------------------- inspect: crafted / hostile zips
@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        (CD_VERSION_NEEDED, 99, "not a readable zip"),  # zipfile: NotImplementedError 9.9
        (CD_VERSION_NEEDED, 150, "not a readable zip"),
        (CD_METHOD, 99, "unsupported compression method"),  # e.g. AES
        (CD_METHOD, 1, "unsupported compression method"),  # shrink
    ],
)
def test_rejects_unsupported_zip_features(tmp_path, field, value, match):
    z = make_zip(tmp_path / "s.zip", {"t_alpha/output.step": STEP})
    patch_cd(z, "t_alpha/output.step", field[0], field[1], value)
    with pytest.raises(SubmissionError, match=match):
        inspect_zip(z, TASKS)


def test_rejects_deeply_nested_meta(tmp_path):
    deep = "[" * 30000 + "]" * 30000  # under the 64 kB cap; json raises RecursionError
    z = tmp_path / "s.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("meta.json", deep)
    with pytest.raises(SubmissionError, match="not valid UTF-8 JSON"):
        inspect_zip(z, TASKS)


def test_meta_with_utf8_bom_ok(tmp_path):
    z = tmp_path / "s.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("meta.json", b"\xef\xbb\xbf" + json.dumps(good_meta()).encode())
        zf.writestr("t_alpha/output.step", STEP)
    sub = inspect_zip(z, TASKS)
    assert sub.meta == good_meta() and list(sub.tasks) == ["t_alpha"]


def test_plan_version_is_bounded(tmp_path):
    long_plan = "t_alpha/plan/re_plan.v" + "9" * 300 + ".json"
    z = make_zip(tmp_path / "s.zip", {"t_alpha/output.step": STEP,
                                      "t_alpha/plan/re_plan.v123456.json": "{}",
                                      "t_alpha/plan/re_plan.v1234567.json": "{}",
                                      long_plan: "{}"})
    sub = inspect_zip(z, TASKS)
    assert set(sub.tasks["t_alpha"]) == {"output.step", "plan/re_plan.v123456.json"}
    ignored = [w for w in sub.warnings if "not a recognised" in w]
    assert len(ignored) == 2
    run = tmp_path / "r"
    extract_task(z, sub, "t_alpha", run)
    assert sorted(p.name for p in run.rglob("*") if p.is_file()) == [
        "output.step", "re_plan.v123456.json"]


def _many_entries_zip(path: Path, n: int, name_len: int = 8) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("meta.json", json.dumps(good_meta()))
        for k in range(n):
            zf.writestr(f"t_alpha/native/{k:0{name_len}d}", b"")
    return path


def _forbid_zipfile(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("zipfile.ZipFile must not be reached")
    monkeypatch.setattr(sm.zipfile, "ZipFile", boom)


def test_entry_count_checked_before_parsing(tmp_path, monkeypatch):
    z = _many_entries_zip(tmp_path / "s.zip", 30)
    monkeypatch.setitem(LIMITS, "max_entries", 10)
    _forbid_zipfile(monkeypatch)
    with pytest.raises(SubmissionError, match="31 entries, limit is 10"):
        inspect_zip(z, TASKS)


def test_lying_entry_count_caught_by_cd_size(tmp_path, monkeypatch):
    z = _many_entries_zip(tmp_path / "s.zip", 60, name_len=100)
    data = bytearray(z.read_bytes())
    i = data.rfind(b"PK\x05\x06")
    struct.pack_into("<HH", data, i + 8, 1, 1)  # claim one entry; zipfile ignores the count
    z.write_bytes(bytes(data))
    monkeypatch.setitem(LIMITS, "max_entries", 5)
    _forbid_zipfile(monkeypatch)
    with pytest.raises(SubmissionError, match="central directory is too large"):
        inspect_zip(z, TASKS)


def test_entry_count_read_from_zip64_record(tmp_path, monkeypatch):
    monkeypatch.setattr(zipfile, "ZIP_FILECOUNT_LIMIT", 1)  # force a zip64 end record
    z = _many_entries_zip(tmp_path / "s.zip", 30)
    monkeypatch.undo()
    data = bytearray(z.read_bytes())
    assert b"PK\x06\x06" in data
    i = data.rfind(b"PK\x05\x06")
    struct.pack_into("<HH", data, i + 8, 0xFFFF, 0xFFFF)  # classic record defers to zip64
    z.write_bytes(bytes(data))
    assert "t_alpha" not in inspect_zip(z, TASKS).tasks  # still a readable zip
    monkeypatch.setitem(LIMITS, "max_entries", 10)
    _forbid_zipfile(monkeypatch)
    with pytest.raises(SubmissionError, match="31 entries"):
        inspect_zip(z, TASKS)


# ------------------------------------------------------------------------------- validate_meta
def test_validate_meta_ok():
    assert validate_meta(good_meta()) == []
    assert validate_meta(good_meta(agent_url="")) == []
    assert validate_meta(good_meta(notes="x" * 500, submitter_name="n" * 80)) == []


@pytest.mark.parametrize(
    ("over", "match"),
    [
        ({"human_in_loop": "no"}, "human_in_loop"),
        ({"human_in_loop": 0}, "human_in_loop"),
        ({"agree_to_publish": False}, "agree_to_publish"),
        ({"agree_to_publish": 1}, "agree_to_publish"),
        ({"notes": "x" * 501}, "notes"),
        ({"submitter_name": "n" * 81}, "submitter_name"),
        ({"submission_name": "  "}, "submission_name"),
        ({"agent_url": "ftp://x"}, "agent_url"),
        ({"agent_url": "javascript:alert(1)"}, "agent_url"),
        ({"model": 5}, "model"),
        ({"harness": "a\x07b"}, "control"),
    ],
)
def test_validate_meta_problems(over, match):
    problems = validate_meta(good_meta(**over))
    assert problems and any(match in p for p in problems)


def test_validate_meta_missing_keys():
    problems = validate_meta({})
    assert len(problems) == len(META_REQUIRED)
    assert validate_meta([]) == ["meta.json must be a JSON object"]


# ------------------------------------------------------------------------------------ extract
def test_extract_task(good_zip, tmp_path):
    sub = inspect_zip(good_zip, TASKS)
    run = tmp_path / "runs" / "t_alpha"
    step = extract_task(good_zip, sub, "t_alpha", run)
    assert step == run / "out" / "output.step" and step.read_bytes() == STEP
    assert (run / "out" / "features.json").read_text() == '{"features": []}'
    assert (run / "plan" / "re_plan.v1.json").read_text() == '{"v": 1}'
    assert (run / "plan" / "re_plan.v12.json").is_file()
    assert not (run / "native").exists() and not (run / "run.json").exists()
    assert sorted(p.name for p in run.rglob("*") if p.is_file()) == [
        "features.json", "output.step", "re_plan.v1.json", "re_plan.v12.json"]


def test_extract_unknown_or_missing_task(good_zip, tmp_path):
    sub = inspect_zip(good_zip, TASKS)
    for tid in ("t_beta", "nope"):
        with pytest.raises(SubmissionError, match="no output.step"):
            extract_task(good_zip, sub, tid, tmp_path / tid)


def test_extract_detects_changed_zip(good_zip, tmp_path):
    sub = inspect_zip(good_zip, TASKS)
    with zipfile.ZipFile(good_zip, "a") as zf:
        zf.writestr("t_gamma/output.step", STEP)
    with pytest.raises(SubmissionError, match="changed"):
        extract_task(good_zip, sub, "t_alpha", tmp_path / "r")


def test_extract_enforces_size_while_streaming(good_zip, tmp_path, monkeypatch):
    sub = inspect_zip(good_zip, TASKS)
    monkeypatch.setitem(LIMITS, "step_bytes", 10)  # header lied / limit tightened
    with pytest.raises(SubmissionError, match="size limit"):
        extract_task(good_zip, sub, "t_alpha", tmp_path / "r")
    assert not list((tmp_path / "r").rglob("*.part"))


@pytest.mark.parametrize(
    ("field", "value"),
    [(CD_CRC, 0xDEADBEEF), (CD_FILE_SIZE, 5)],  # bad CRC / lying uncompressed size
)
def test_extract_corrupt_entry(tmp_path, field, value):
    z = make_zip(tmp_path / "s.zip", {"t_alpha/features.json": "{}",
                                      "t_alpha/output.step": STEP * 50})
    patch_cd(z, "t_alpha/output.step", field[0], field[1], value)
    sub = inspect_zip(z, TASKS)
    run = tmp_path / "r"
    with pytest.raises(SubmissionError, match="cannot be read") as ei:
        extract_task(z, sub, "t_alpha", run)
    assert str(tmp_path) not in str(ei.value)
    assert not [p for p in run.rglob("*") if p.is_file()]  # nothing half-extracted


def test_extract_host_oserror_is_submission_error(good_zip, tmp_path):
    sub = inspect_zip(good_zip, TASKS)
    run = tmp_path / "r"
    run.mkdir()
    (run / "plan").write_text("in the way")  # plan/ cannot be created
    with pytest.raises(SubmissionError, match="cannot be written") as ei:
        extract_task(good_zip, sub, "t_alpha", run)
    assert str(tmp_path) not in str(ei.value)
    assert not (run / "out" / "output.step").exists()
    assert not (run / "out" / "features.json").exists()


def test_module_is_stdlib_only():
    src = Path(sm.__file__).read_text(encoding="utf-8")
    for mod in ("numpy", "OCP", "build123d", "scipy", "yaml"):
        assert f"import {mod}" not in src and f"from {mod}" not in src


# ------------------------------------------------------------------------------------ tool
def _tool():
    path = REPO / "tools" / "sanity_check_submission.py"
    spec = importlib.util.spec_from_file_location("sanity_check_submission", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sanity_check_submission"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_tool_task_ids_match_dev_split():
    tool = _tool()
    assert len(tool.TASK_IDS) == 10 and len(set(tool.TASK_IDS)) == 10
    for short in ("hs01", "dr02", "hs04", "hs02", "dr01"):
        assert any(f"_{short}_" in t for t in tool.TASK_IDS)
    assert sum(t.startswith("od_") for t in tool.TASK_IDS) == 5


def test_tool_structure_only(good_zip, tmp_path, capsys):
    tool = _tool()
    tasks = tmp_path / "tasks.txt"
    tasks.write_text("\n".join(TASKS) + "\n", encoding="utf-8")
    assert tool.main([str(good_zip), "--tasks-file", str(tasks), "--no-geometry"]) == 0
    out = capsys.readouterr().out
    assert "RESULT: OK" in out and "1 of 3" in out
    out.encode("ascii")
    bad = make_zip(tmp_path / "bad.zip", {"junk.txt": "x"})
    assert tool.main([str(bad), "--tasks-file", str(tasks)]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_tool_bom_meta_ok(tmp_path, capsys):
    tool = _tool()
    z = tmp_path / "bom.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("meta.json", b"\xef\xbb\xbf" + json.dumps(good_meta()).encode())
        zf.writestr("t_alpha/output.step", STEP)
    tasks = tmp_path / "tasks.txt"
    tasks.write_text("\n".join(TASKS), encoding="utf-8")
    assert tool.main([str(z), "--tasks-file", str(tasks), "--no-geometry"]) == 0
    assert "RESULT: OK" in capsys.readouterr().out


def test_tool_reports_extract_error(tmp_path, capsys, monkeypatch):
    tool = _tool()
    monkeypatch.setattr(tool, "_kernel_available", lambda: True)
    monkeypatch.setattr(tool, "check_step", lambda step, stl: [])  # extraction fails first
    z = make_zip(tmp_path / "crc.zip", {"t_alpha/output.step": STEP,
                                        "t_beta/output.step": STEP})
    patch_cd(z, "t_alpha/output.step", CD_CRC[0], CD_CRC[1], 0xDEADBEEF)
    tasks = tmp_path / "tasks.txt"
    tasks.write_text("\n".join(TASKS), encoding="utf-8")
    assert tool.main([str(z), "--tasks-file", str(tasks)]) == 1
    out = capsys.readouterr().out
    assert "ERROR  t_alpha:" in out and "cannot be read" in out
    assert "ok     t_beta" in out and "RESULT: FAIL" in out


def test_tool_inputs_without_stl_falls_back_to_task_ids(tmp_path, capsys):
    tool = _tool()
    inputs = tmp_path / "inputs"
    (inputs / "empty_dir").mkdir(parents=True)
    z = make_zip(tmp_path / "s.zip", {})
    assert tool.main([str(z), "--inputs", str(inputs), "--no-geometry"]) == 0
    out = capsys.readouterr().out
    assert "using the built-in task list" in out
    assert f"0 of {len(tool.TASK_IDS)}" in out


def _ascii_box_stl(path: Path, sx: float, sy: float, sz: float) -> None:
    v = [(x, y, z) for x in (0, sx) for y in (0, sy) for z in (0, sz)]
    tris = [(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1),
            (2, 3, 7), (2, 7, 6), (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)]
    lines = ["solid box"]
    for t in tris:
        lines += ["facet normal 0 0 0", "outer loop"]
        lines += [f"vertex {v[i][0]} {v[i][1]} {v[i][2]}" for i in t]
        lines += ["endloop", "endfacet"]
    path.write_text("\n".join(lines + ["endsolid box"]), encoding="ascii")


def test_stl_bbox_ascii_and_binary(tmp_path):
    tool = _tool()
    a = tmp_path / "a.stl"
    _ascii_box_stl(a, 10, 20, 30)
    assert tool.stl_bbox(a) == ((0, 0, 0), (10, 20, 30))
    b = tmp_path / "b.stl"
    tri = struct.pack("<12fH", 0, 0, 1, -1, 2, 3, 4, -5, 6, 7, 8, -9, 0)
    b.write_bytes(b"\0" * 80 + struct.pack("<I", 1) + tri)
    assert tool.stl_bbox(b) == ((-1, -5, -9), (7, 8, 6))


def test_tool_geometry(tmp_path, capsys):
    pytest.importorskip("build123d")
    from build123d import Box, Pos, export_step

    tool = _tool()
    one = tmp_path / "one.step"
    export_step(Pos(5, 10, 15) * Box(10, 20, 30), str(one))
    two = tmp_path / "two.step"
    export_step(Box(1, 1, 1) + Pos(5, 0, 0) * Box(1, 1, 1), str(two))
    z = make_zip(tmp_path / "s.zip", {"t_alpha/output.step": one.read_bytes(),
                                      "t_beta/output.step": two.read_bytes()})
    inputs = tmp_path / "inputs"
    for tid, s in (("t_alpha", 1.0), ("t_beta", 1.0), ("t_gamma", 1.0)):
        (inputs / tid).mkdir(parents=True)
        _ascii_box_stl(inputs / tid / "input.stl", 10 * s, 20 * s, 30 * s)
    assert tool.main([str(z), "--inputs", str(inputs)]) == 1
    out = capsys.readouterr().out
    assert "ok     t_alpha" in out
    assert "t_beta: expected exactly one solid, got 2" in out

    _ascii_box_stl(inputs / "t_alpha" / "input.stl", 100, 200, 300)  # units mismatch
    z2 = make_zip(tmp_path / "s2.zip", {"t_alpha/output.step": one.read_bytes()})
    assert tool.main([str(z2), "--inputs", str(inputs)]) == 1
    assert "wrong units or frame" in capsys.readouterr().out
