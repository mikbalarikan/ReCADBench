"""Tests of the `recad` dispatcher (backends/recad_cli) against a fake adapter.

The fake adapter lives in a temporary RECAD_BACKENDS_DIR and does what the environment says:
FAKE_RESULT (JSON printed on stdout), FAKE_RC (exit code), FAKE_FEATURES (JSON written to the
features.json path the subcommand implies), FAKE_STEP_SRC (file copied to the STEP path),
FAKE_STDOUT (raw stdout instead of FAKE_RESULT).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap

import pytest
from recad_cli import cli

FAKE_ADAPTER = textwrap.dedent('''
    import json, os, shutil, sys
    from pathlib import Path
    args = sys.argv[1:]
    def opt(name):
        return args[args.index(name) + 1] if name in args else None
    sub = args[0]
    print("fake adapter log line", file=sys.stderr)
    Path(os.environ["FAKE_ARGV_LOG"]).write_text(json.dumps(args))
    feats = os.environ.get("FAKE_FEATURES")
    if feats is not None:
        dest = (Path(opt("--out-dir")) / "features.json") if sub == "replay" else Path(opt("--out"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(feats)
    step = os.environ.get("FAKE_STEP_SRC")
    if step:
        dest = (Path(opt("--out-dir")) / "output.step") if sub == "replay" else Path(opt("--out"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(step, dest)
    if "FAKE_STDOUT" in os.environ:
        sys.stdout.write(os.environ["FAKE_STDOUT"])
    else:
        print(os.environ.get("FAKE_RESULT", '{"ok": true}'))
    sys.exit(int(os.environ.get("FAKE_RC", "0")))
''')


def valid_features(count: int = 1) -> dict:
    return {
        "tool": "build123d",
        "native_file": "native.py",
        "features": [{"native_name": f"extrude@native.py:{i}", "native_type": "extrude",
                      "taxonomy_type": "extrude", "counted": True, "params": {}}
                     for i in range(count)],
        "count": count,
        "unmapped": [],
    }


@pytest.fixture
def fake(tmp_path, monkeypatch):
    root = tmp_path / "backends"
    for b in ("build123d", "freecad", "solidcraft"):
        (root / b / "adapter").mkdir(parents=True)
        (root / b / "adapter" / "adapter.py").write_text(FAKE_ADAPTER)
    monkeypatch.setenv("RECAD_BACKENDS_DIR", str(root))
    monkeypatch.setenv("RECAD_BUILD123D_PYTHON", sys.executable)
    monkeypatch.setenv("RECAD_SOLIDCRAFT_CLI", sys.executable)  # any executable: never called
    monkeypatch.setenv("FAKE_ARGV_LOG", str(tmp_path / "argv.json"))
    for var in ("FAKE_RESULT", "FAKE_RC", "FAKE_FEATURES", "FAKE_STEP_SRC", "FAKE_STDOUT"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def recad(capsys, *args: str) -> tuple[int, dict]:
    rc = cli.main(list(args))
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 1, f"dispatcher must print exactly one JSON line, got {out!r}"
    return rc, json.loads(lines[0])


# ---------------------------------------------------------------- usage / exit codes


def test_unknown_backend_exits_2(fake, capsys):
    rc, res = recad(capsys, "catia", "export", "x", "--out", "y.step")
    assert rc == 2 and res["ok"] is False and "unknown backend" in res["error"]


def test_missing_subcommand_exits_2(fake, capsys):
    rc, res = recad(capsys, "build123d")
    assert rc == 2 and res["ok"] is False


def test_no_arguments_exits_2(fake, capsys):
    assert cli.main([]) == 2


def test_args_pass_through_and_result_is_forwarded(fake, capsys, monkeypatch):
    monkeypatch.setenv("FAKE_RESULT", json.dumps({"ok": True, "solids": 1, "volume_mm3": 8.0,
                                                  "faces": 6}))
    rc, res = recad(capsys, "build123d", "export", "part.py", "--out", str(fake / "o.step"))
    assert rc == 0 and res == {"ok": True, "solids": 1, "volume_mm3": 8.0, "faces": 6,
                               "backend": "build123d"}
    assert json.loads((fake / "argv.json").read_text()) == ["export", "part.py", "--out",
                                                            str(fake / "o.step")]


def test_adapter_exit_code_propagates(fake, capsys, monkeypatch):
    monkeypatch.setenv("FAKE_RESULT", '{"ok": false, "error": "boom"}')
    monkeypatch.setenv("FAKE_RC", "3")
    rc, res = recad(capsys, "build123d", "export", "p.py", "--out", "o.step")
    assert rc == 3 and res["error"] == "boom"


def test_not_ok_with_exit_0_becomes_exit_1(fake, capsys, monkeypatch):
    monkeypatch.setenv("FAKE_RESULT", '{"ok": false, "error": "two solids"}')
    rc, res = recad(capsys, "build123d", "export", "p.py", "--out", "o.step")
    assert rc == 1 and res["ok"] is False


def test_adapter_without_json_is_a_failure(fake, capsys, monkeypatch):
    monkeypatch.setenv("FAKE_STDOUT", "Traceback: something went wrong\n")
    rc, res = recad(capsys, "build123d", "export", "p.py", "--out", "o.step")
    assert rc == 1 and res["ok"] is False and "no JSON" in res["error"]


def test_last_json_line_wins_over_chatter(fake, capsys, monkeypatch):
    monkeypatch.setenv("FAKE_STDOUT", 'warming up\n{"ok": true, "solids": 1}\n')
    rc, res = recad(capsys, "build123d", "export", "p.py", "--out", "o.step")
    assert rc == 0 and res["solids"] == 1


def test_freecad_requires_its_interpreter(fake, capsys, monkeypatch):
    monkeypatch.delenv("RECAD_FREECAD_PYTHON", raising=False)
    rc, res = recad(capsys, "freecad", "export", "p.FCStd", "--out", "o.step")
    assert rc == 1 and "RECAD_FREECAD_PYTHON" in res["error"]


def test_solidcraft_requires_the_cli(fake, capsys, monkeypatch):
    monkeypatch.setenv("RECAD_SOLIDCRAFT_CLI", str(fake / "no-such-solidcraft-cli"))
    rc, res = recad(capsys, "solidcraft", "export", "p.json", "--out", "o.step")
    assert rc == 1 and "RECAD_SOLIDCRAFT_CLI" in res["error"]


def test_nx_without_adapter_folder(fake, capsys):
    rc, res = recad(capsys, "nx", "export", "p.prt", "--out", "o.step")
    assert rc == 1 and res["ok"] is False and "no adapter" in res["error"]


def test_nx_is_routed_to_its_adapter(fake, capsys, monkeypatch):
    (fake / "backends" / "nx" / "adapter").mkdir(parents=True)
    (fake / "backends" / "nx" / "adapter" / "adapter.py").write_text(FAKE_ADAPTER)
    monkeypatch.setenv("RECAD_NX_PYTHON", sys.executable)
    monkeypatch.setenv("FAKE_RESULT", json.dumps({"ok": True, "count": 1}))
    feats = valid_features(1) | {"tool": "nx", "native_file": "native.prt"}
    monkeypatch.setenv("FAKE_FEATURES", json.dumps(feats))
    rc, res = recad(capsys, "nx", "extract", "native.prt", "--out", str(fake / "f.json"))
    assert rc == 0 and res["backend"] == "nx" and res["features_valid"] is True
    assert json.loads((fake / "argv.json").read_text()) == ["extract", "native.prt", "--out",
                                                            str(fake / "f.json")]


def test_timeout(fake, capsys, monkeypatch):
    (fake / "backends" / "build123d" / "adapter" / "adapter.py").write_text(
        "import time; time.sleep(30)\n")
    monkeypatch.setenv("RECAD_TIMEOUT_S", "0.5")
    rc, res = recad(capsys, "build123d", "export", "p.py", "--out", "o.step")
    assert rc == 1 and "timed out" in res["error"]


# ---------------------------------------------------------------- features.json validation


def test_extract_valid_features(fake, capsys, monkeypatch):
    monkeypatch.setenv("FAKE_FEATURES", json.dumps(valid_features(2)))
    monkeypatch.setenv("FAKE_RESULT", '{"ok": true, "count": 2}')
    rc, res = recad(capsys, "build123d", "extract", "p.py", "--out", str(fake / "f.json"))
    assert rc == 0 and res["features_valid"] is True


@pytest.mark.parametrize("breakage", ["missing_tool", "bad_type", "not_json"])
def test_extract_invalid_features_fails(fake, capsys, monkeypatch, breakage):
    doc = valid_features(1)
    if breakage == "missing_tool":
        del doc["tool"]
        text = json.dumps(doc)
    elif breakage == "bad_type":
        doc["features"][0]["taxonomy_type"] = "sculpt"
        text = json.dumps(doc)
    else:
        text = "{not json"
    monkeypatch.setenv("FAKE_FEATURES", text)
    monkeypatch.setenv("FAKE_RESULT", '{"ok": true}')
    rc, res = recad(capsys, "build123d", "extract", "p.py", "--out", str(fake / "f.json"))
    assert rc == 1 and res["ok"] is False and res["features_valid"] is False
    assert res["validation_errors"]


def test_extract_without_features_file_fails(fake, capsys, monkeypatch):
    monkeypatch.setenv("FAKE_RESULT", '{"ok": true}')
    rc, res = recad(capsys, "build123d", "extract", "p.py", "--out", str(fake / "none.json"))
    assert rc == 1 and res["features_valid"] is False


def test_replay_validates_features_in_out_dir(fake, capsys, monkeypatch):
    monkeypatch.setenv("FAKE_FEATURES", json.dumps(valid_features(1)))
    monkeypatch.setenv("FAKE_RESULT", '{"ok": true, "volume_mm3": 1.0, "faces": 6, "count": 1}')
    rc, res = recad(capsys, "build123d", "replay", "C01_block", "--out-dir", str(fake / "r"))
    assert rc == 0 and res["features_valid"] is True


def test_failed_adapter_result_is_not_validated(fake, capsys, monkeypatch):
    monkeypatch.setenv("FAKE_RESULT", '{"ok": false, "error": "replay script failed"}')
    monkeypatch.setenv("FAKE_RC", "1")
    rc, res = recad(capsys, "build123d", "replay", "C01_block", "--out-dir", str(fake / "r"))
    assert rc == 1 and "features_valid" not in res


# ---------------------------------------------------------------- needs_measure


@pytest.fixture
def step_files(tmp_path_factory):
    b3d = pytest.importorskip("build123d")
    d = tmp_path_factory.mktemp("steps")
    one, two = d / "box.step", d / "two.step"
    b3d.export_step(b3d.Box(10, 10, 10), str(one))
    pair = b3d.Compound([b3d.Box(1, 1, 1), b3d.Pos(5, 0, 0) * b3d.Box(1, 1, 1)])
    b3d.export_step(pair, str(two))
    return one, two


def test_needs_measure_fills_export_result(fake, capsys, monkeypatch, step_files):
    monkeypatch.setenv("FAKE_STEP_SRC", str(step_files[0]))
    monkeypatch.setenv("FAKE_RESULT", '{"ok": true, "volume_mm3": null, "needs_measure": true}')
    rc, res = recad(capsys, "solidcraft", "export", "p.json", "--out", str(fake / "o.step"))
    assert rc == 0 and res["ok"] and res["needs_measure"] is False
    assert res["volume_mm3"] == pytest.approx(1000.0, rel=1e-9)
    assert res["solids"] == 1 and res["faces"] == 6 and res["measured_by"].startswith("recad")


def test_needs_measure_fills_set_param_volume_after(fake, capsys, monkeypatch, step_files):
    monkeypatch.setenv("FAKE_STEP_SRC", str(step_files[0]))
    monkeypatch.setenv("FAKE_RESULT", json.dumps({"ok": True, "volume_before_mm3": 900.0,
                                                  "volume_after_mm3": None,
                                                  "needs_measure": True}))
    rc, res = recad(capsys, "build123d", "set-param", "p.py", "w", "3",
                    "--out", str(fake / "o.step"))
    assert rc == 0 and res["volume_after_mm3"] == pytest.approx(1000.0, rel=1e-9)
    assert res["volume_before_mm3"] == 900.0


def test_needs_measure_rejects_two_solids(fake, capsys, monkeypatch, step_files):
    monkeypatch.setenv("FAKE_STEP_SRC", str(step_files[1]))
    monkeypatch.setenv("FAKE_RESULT", '{"ok": true, "needs_measure": true}')
    rc, res = recad(capsys, "build123d", "export", "p.py", "--out", str(fake / "o.step"))
    assert rc == 1 and res["ok"] is False and res["solids"] == 2


def test_needs_measure_without_step_fails(fake, capsys, monkeypatch):
    monkeypatch.setenv("FAKE_RESULT", '{"ok": true, "needs_measure": true}')
    rc, res = recad(capsys, "build123d", "export", "p.py", "--out", str(fake / "none.step"))
    assert rc == 1 and res["ok"] is False


def test_needs_measure_without_measurer_is_reported(fake, capsys, monkeypatch, tmp_path):
    step = tmp_path / "any.step"
    step.write_text("ISO-10303-21;\n")
    monkeypatch.setattr(cli, "measure_step", lambda path: None)
    monkeypatch.setenv("FAKE_STEP_SRC", str(step))
    monkeypatch.setenv("FAKE_RESULT", '{"ok": true, "needs_measure": true}')
    rc, res = recad(capsys, "build123d", "export", "p.py", "--out", str(fake / "o.step"))
    assert rc == 0 and res["needs_measure"] is True and "measure_error" in res


def test_no_needs_measure_leaves_adapter_numbers(fake, capsys, monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(cli, "measure_step", lambda path: called.append(path))
    monkeypatch.setenv("FAKE_RESULT", '{"ok": true, "volume_mm3": 5.0, "needs_measure": false}')
    rc, res = recad(capsys, "build123d", "export", "p.py", "--out", str(tmp_path / "o.step"))
    assert rc == 0 and res["volume_mm3"] == 5.0 and not called


# ---------------------------------------------------------------- `recad backends`, console


def test_backends_listing(fake, capsys, monkeypatch):
    monkeypatch.delenv("RECAD_FREECAD_PYTHON", raising=False)
    monkeypatch.setattr(cli, "BACKENDS", {**cli.BACKENDS, "build123d": cli.Backend(
        "build123d", "RECAD_BUILD123D_PYTHON", False, "print('fake 1.0')", "py")})
    assert cli.main(["backends", "--json"]) == 0
    infos = {i["backend"]: i for i in json.loads(capsys.readouterr().out)}
    assert set(infos) == {"build123d", "freecad", "solidcraft", "nx"}
    assert infos["build123d"]["available"] and infos["build123d"]["version"] == "fake 1.0"
    assert not infos["freecad"]["available"] and "RECAD_FREECAD_PYTHON" in infos["freecad"][
        "reason"]
    assert not infos["nx"]["available"]


def test_module_entry_point_runs_in_subprocess(fake):
    env = dict(os.environ, FAKE_RESULT='{"ok": true}')
    proc = subprocess.run([sys.executable, "-m", "recad_cli", "build123d", "export", "p.py",
                           "--out", "o.step"], capture_output=True, text=True, env=env,
                          timeout=120)
    assert proc.returncode == 0 and json.loads(proc.stdout) == {"ok": True,
                                                                "backend": "build123d"}
    assert "fake adapter log line" in proc.stderr  # adapter stderr is passed through


# ---------------------------------------------------------------- real build123d adapter


def test_real_build123d_replay_through_dispatcher(tmp_path, capsys, monkeypatch):
    pytest.importorskip("build123d")
    monkeypatch.delenv("RECAD_BACKENDS_DIR", raising=False)
    monkeypatch.setenv("RECAD_BUILD123D_PYTHON", sys.executable)
    rc, res = recad(capsys, "build123d", "replay", "C01_block", "--out-dir", str(tmp_path))
    assert rc == 0 and res["features_valid"] is True and res["count"] == 1
    assert res["volume_mm3"] == pytest.approx(24000.0, rel=1e-6)


@pytest.mark.parametrize("argv", [[], ["--help"], ["build123d"], ["nope"]])
def test_console_output_is_ascii(argv):
    # review PR #8: `recad --help` crashed on a non-UTF-8 console (U+2026 in USAGE); console
    # output must be ASCII on Windows (#7 convention)
    env = dict(os.environ, PYTHONIOENCODING="cp437")
    proc = subprocess.run([sys.executable, "-m", "recad_cli", *argv], capture_output=True,
                          env=env, timeout=120)
    assert b"Traceback" not in proc.stderr, proc.stderr
    assert (proc.stdout + proc.stderr).isascii()
    assert proc.returncode in (0, 2)


def test_recadbench_entry_routes_nx_run(tmp_path, monkeypatch, capsys):
    from recad_cli import recadbench

    monkeypatch.chdir(tmp_path)
    (tmp_path / "j.py").write_text("pass\n")
    assert recadbench.main(["nx-run", "j.py"]) == 2  # not under cad/: usage error from nx_run
    assert "under" in json.loads(capsys.readouterr().out)["error"]
    assert recadbench.main(["compose"]) == 2
    assert recadbench.main([]) == 2
