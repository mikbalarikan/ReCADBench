"""Tests for space/deploy_hf.py (staging, audit, push with a fake Hub API; no network)."""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SPACE = Path(__file__).resolve().parents[1]
REPO = SPACE.parent
if str(SPACE) not in sys.path:
    sys.path.insert(0, str(SPACE))

import deploy_hf as dh  # noqa: E402
import jobs  # noqa: E402
import runtime  # noqa: E402

GRADIO = "6.32.0"
OWNER = "mikbalarikan"
# fake values built at run time so no token-like or user-path literal sits in the source
FAKE_TOKEN = "hf_" + "Q" * 34
FAKE_WIN_PATH = "C:" + "\\Users\\someone\\rcb\\gt"


# ----------------------------------------------------------------------------- fixtures


def _w(path: Path, text: str = "x\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    """A minimal ReCADBench checkout: real card / Dockerfiles / requirements, tiny packages."""
    repo = tmp_path / "repo"
    sp = repo / "space"
    for name in ("app.py", "jobs.py", "store.py", "theme.py", "runtime.py"):
        _w(sp / name, "import os\n")
    _w(sp / "deploy_hf.py")
    _w(sp / "tests" / "test_x.py")
    _w(sp / "__pycache__" / "app.cpython-313.pyc")
    _w(sp / "space_data" / "store" / "results.jsonl", "{}\n")
    for name in ("README.md", "requirements.txt", "Dockerfile.eval"):
        shutil.copy2(SPACE / name, sp / name)
    shutil.copytree(SPACE / "docker", sp / "docker")
    ev = repo / "evaluator"
    _w(ev / "pyproject.toml", "[project]\nname='x'\n")
    _w(ev / "recadbench_eval" / "__init__.py", '__version__ = "0.1.0"\n')
    _w(ev / "recadbench_eval" / "submission.py", "import json\nimport zipfile\n")
    _w(ev / "recadbench_eval" / "cli.py", "import numpy\n")
    _w(ev / "golden" / "synthetic_box.step", "ISO-10303-21;\n")
    _w(ev / "tests" / "test_y.py")
    _w(ev / "recadbench_eval.egg-info" / "SOURCES.txt")
    _w(repo / "spec" / "pyproject.toml", "[project]\nname='s'\n")
    _w(repo / "spec" / "recadbench_spec" / "__init__.py")
    _w(repo / "spec" / "tests" / "examples" / "gt_meta" / "meta.yaml")
    assets = repo / "docs" / "assets"
    for name in (*dh.ASSET_FILES, "banner-light.webp"):
        _w(assets / name, "RIFF")
    _w(assets / "tasks" / "r385_hs01_motor_mount.webp", "RIFF")
    row = {"id": "r385_hs01_motor_mount", "no": 1, "title": "Motor mount cup"}
    _w(repo / dh.TASKS_PUBLIC, json.dumps([{**row, "family": "R385", "difficulty": "medium"}]))
    return repo


def _stage(fake_repo: Path, tmp_path: Path, **kw) -> Path:
    out = tmp_path / "staging"
    dh.stage(out, kw.pop("owner", OWNER), repo=fake_repo, gradio_version=GRADIO, **kw)
    return out


def _files(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


# ----------------------------------------------------------------------------- names / cards


def test_space_variables_are_public_values_only():
    v = dh.space_variables(OWNER)
    assert v == {
        "RCB_MODE": "space",
        "RCB_SUBMISSIONS_REPO": "mikbalarikan/recadbench-submissions",
        "RCB_GT_REPO": "mikbalarikan/recadbench-gt",
        "RCB_INPUTS_REPO": "mikbalarikan/recadbench-inputs",
        "RCB_ADMINS": "mikbalarikan",
        "RCB_MAX_PARALLEL": "1",
        "RCB_TASK_TIMEOUT": "420",
        "RCB_MAX_RUN": "2700",
    }
    assert not any("TOKEN" in k or "SECRET" in k for k in v)
    assert dh.DEFAULT_OWNER == OWNER


def test_space_variables_bound_the_scorer_slot_per_upload():
    """One upload must not hold the single scorer slot for hours (the 900 s evaluator default
    gives a 10 x 2 x 900 + 300 s = 5 h watchdog), but the slowest dev task (~160 s on a fast
    desktop CPU) must still fit one child."""
    v = dh.space_variables(OWNER)
    t = runtime.task_timeout_s(v)
    assert 300 <= t < runtime.DEFAULT_TASK_TIMEOUT_S
    assert runtime.max_run_s(v, t) <= 3600
    assert runtime.max_run_s({}, runtime.DEFAULT_TASK_TIMEOUT_S) <= 3600  # capped by default


def test_hub_mode_variables_are_kept_for_later():
    v = dh.hub_mode_variables(OWNER)
    assert v["RCB_MODE"] == "hub" and not set(v) & dh.SPACE_ONLY_VARIABLES
    assert v["RCB_EVAL_IMAGE"] == "hf.co/spaces/mikbalarikan/recadbench-eval"
    assert v["RCB_JOB_FLAVOR"] == "cpu-basic" and v["RCB_JOB_NAMESPACE"] == OWNER
    assert not any("TOKEN" in k or "SECRET" in k for k in v)


def test_hub_mode_job_timeout_leaves_enough_per_scoring_child():
    """The slowest dev task needs ~160 s on a fast desktop CPU (over 75 s = the 30m split)."""
    v = dh.hub_mode_variables(OWNER)
    budget = jobs.parse_duration_s(v["RCB_JOB_TIMEOUT"])
    t = jobs.per_task_timeout_s(v["RCB_JOB_TIMEOUT"], jobs.DEFAULT_N_TASKS)
    assert t >= dh.MIN_CHILD_TIMEOUT_S
    assert jobs.DEFAULT_N_TASKS * jobs.CHILDREN_PER_TASK * t + jobs.JOB_RESERVE_S <= budget


def test_space_variables_are_read_by_the_app():
    sources = "\n".join(p.read_text(encoding="utf-8") for p in dh.space_files(SPACE))
    for key in dh.space_variables(OWNER):
        assert re.search(rf"[\"']{key}[\"']", sources), key


@pytest.mark.parametrize("bad", ["", "a/b", "../x", "-x", "x-", "a b", "x" * 97])
def test_owner_is_validated(bad):
    with pytest.raises(dh.DeployError):
        dh.repo_ids(bad)


def test_real_app_card_is_valid():
    text = (SPACE / "README.md").read_text(encoding="utf-8")
    assert dh.check_app_card(text) == []
    meta, body = dh.split_front_matter(text)
    assert meta["sdk"] == "docker" and meta["app_port"] == 7860
    assert meta["hf_oauth"] is True and meta["hf_oauth_expiration_minutes"] == 480
    assert meta["pinned"] is True and meta["license"] == "mit" and meta["emoji"] == "\U0001f4d0"
    assert len(meta["short_description"]) <= 60 and meta["colorFrom"] == "red"
    assert meta["colorTo"] == "gray" and meta["tags"]
    assert not set(dh.GRADIO_ONLY_KEYS) & set(meta)
    assert "## Deploy" in body and "HF_TOKEN" in body and "RCB_MAX_PARALLEL=1" in body


@pytest.mark.parametrize(
    ("old", "new", "why"),
    [
        ("sdk: docker", "sdk: gradio", "sdk must be 'docker'"),
        ("app_port: 7860", "app_port: 8080", "app_port"),
        ("hf_oauth: true", "hf_oauth: false", "hf_oauth must be"),
        ("pinned: true", "pinned: true\nsdk_version: 6.32.0", "gradio-sdk key"),
        ("short_description: Scan", "short_description: " + "x" * 60 + " Scan", "short_desc"),
    ],
)
def test_app_card_problems(old, new, why):
    text = (SPACE / "README.md").read_text(encoding="utf-8")
    assert old in text
    problems = dh.check_app_card(text.replace(old, new, 1))
    assert any(why in p for p in problems), problems


def test_pin_requirements():
    req = "# c\ngradio[oauth]>=6.0,<7\nhuggingface_hub>=1.0  # hub\nmatplotlib\nnot-here>=1\n"
    got = dh.pin_requirements(req, {"gradio": "6.32.0", "huggingface-hub": "2.4.0",
                                    "matplotlib": "3.11.2"})  # fmt: skip
    assert got == (
        "# c\ngradio[oauth]==6.32.0\nhuggingface_hub==2.4.0\nmatplotlib==3.11.2\nnot-here>=1\n"
    )
    assert dh.pin_requirements("filelock\n", {"gradio": "6.32.0"}).endswith(
        "gradio[oauth]==6.32.0\n"
    )
    with pytest.raises(dh.DeployError):
        dh.pin_requirements("filelock\n", {})


def test_installed_versions_cover_the_real_requirements():
    req = (SPACE / "requirements.txt").read_text(encoding="utf-8")
    got = dh.installed_versions(req)
    assert {"gradio", "huggingface-hub", "filelock", "fastapi", "uvicorn"} <= set(got)
    pinned = dh.pin_requirements(req, {**got, "gradio": GRADIO})
    lines = [x for x in pinned.splitlines() if x and not x.startswith("#")]
    assert all("==" in x for x in lines if dh._req_name(x) in got), lines
    assert f"gradio[oauth]=={GRADIO}" in lines


def test_render_refuses_unknown_placeholder():
    assert dh.render("{{OWNER}}/{{APP_SPACE}}", "o") == "o/recadbench-open"
    with pytest.raises(dh.DeployError):
        dh.render("{{NOPE}}", "o")


# ----------------------------------------------------------------------------- Dockerfiles


def _real_dockerfiles() -> tuple[str, str]:
    return (
        (SPACE / "docker" / "Dockerfile").read_text(encoding="utf-8"),
        (SPACE / "Dockerfile.eval").read_text(encoding="utf-8"),
    )


def test_space_dockerfile_is_valid():
    text, ev = _real_dockerfiles()
    assert dh.check_space_dockerfile(text, ev) == []
    assert dh.dockerfile_drift(ev, text) == []
    ins = dh.docker_instructions(text)
    assert ins[0] == "FROM python:3.11-slim" and ins[-1] == 'CMD ["python", "app.py"]'
    assert "COPY spec ./spec" in ins and "COPY evaluator ./evaluator" in ins
    assert any("pip install ./spec ./evaluator -r requirements.txt" in i for i in ins)
    # the app folder is root-owned (created and filled before USER, no --chown): the scorer
    # (uid 1000) cannot plant files the site serves; the GT ($HOME/.rcb-gt) is not under it
    assert ins.index("WORKDIR /opt/rcb/app") < ins.index("USER user")
    assert ins.index("COPY assets ./assets") < ins.index("USER user")
    assert not any(i.startswith("COPY") and "--chown" in i for i in ins)
    assert not any(i.startswith("COPY") and ".rcb-gt" in i for i in ins)
    env = " ".join(i for i in ins if i.startswith("ENV "))
    assert "SYSTEM=spaces" in env.split() and "PYTHONNOUSERSITE=1" in env.split()
    # the app itself is PID 1 (it is the hardened, non-dumpable process): no ENTRYPOINT, no
    # init wrapper whose /proc/1/environ would expose HF_TOKEN to the scorer (same uid)
    assert not any(i.upper().startswith("ENTRYPOINT") for i in ins)
    assert [i for i in ins if i.upper().startswith("CMD")] == ['CMD ["python", "app.py"]']
    assert "tini" not in " ".join(ins)


@pytest.mark.parametrize(
    ("old", "new", "why"),
    [
        ("libegl1", "", "drifted"),
        ('CMD ["python", "app.py"]', 'CMD ["python", "other.py"]', "last instruction"),
        ("RUN useradd -m -u 1000 user", "RUN useradd -m user", "uid 1000"),
        ("GRADIO_SERVER_NAME=0.0.0.0", "GRADIO_SERVER_NAME=127.0.0.1", "GRADIO_SERVER_NAME"),
        ("import recadbench_eval, build123d, OCP, gradio,", "import gradio,", "not cover OCP"),
        ("WORKDIR /opt/rcb/app", "WORKDIR /app", "WORKDIR /opt/rcb/app missing"),
        ("    SYSTEM=spaces \\\n", "", "SYSTEM=spaces missing"),
        ("    PYTHONNOUSERSITE=1 \\\n", "", "PYTHONNOUSERSITE=1 missing"),
        (
            "ENV HOME=/home/user \\",
            "ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH \\",
            "PATH must not",
        ),
        ("COPY assets ./assets", "COPY --chown=1000:1000 assets ./assets", "COPY --chown"),
        (
            'CMD ["python", "app.py"]',
            'ENTRYPOINT ["/usr/bin/tini", "--"]\nCMD ["python", "app.py"]',
            "no ENTRYPOINT",
        ),
        (
            'CMD ["python", "app.py"]',
            'ENTRYPOINT ["python"]\nCMD ["python", "app.py"]',
            "no ENTRYPOINT",
        ),
        ("USER user\n", "RUN apt-get install -y tini\nUSER user\n", "no init wrapper"),
        (
            'CMD ["python", "app.py"]',
            'CMD ["dumb-init", "x"]\nCMD ["python", "app.py"]',
            "no init wrapper",
        ),
        ('CMD ["python", "app.py"]', 'CMD ["sh", "-c", "python app.py"]', "last instruction"),
        ('CMD ["python", "app.py"]', "CMD python app.py", "last instruction"),
    ],
)
def test_space_dockerfile_problems(old, new, why):
    text, ev = _real_dockerfiles()
    assert old in text
    problems = dh.check_space_dockerfile(text.replace(old, new), ev)
    assert any(why in p for p in problems), problems


def test_space_dockerfile_app_folder_must_be_root_owned():
    text, ev = _real_dockerfiles()
    late = text.replace("WORKDIR /opt/rcb/app\n", "").replace(
        "USER user\n", "USER user\nWORKDIR /opt/rcb/app\n"
    )
    assert any("must come before USER" in p for p in dh.check_space_dockerfile(late, ev))


def test_dockerfile_drift_compares_the_ocp_base_only():
    ev = 'FROM a\nENV X=1\nRUN apt-get \\\n  libgl1\nWORKDIR /opt\nRUN pip install x\nCMD ["run"]\n'
    assert dh.dockerfile_drift(ev, "FROM a\nENV X=1\nRUN apt-get libgl1\nRUN other\n") == []
    assert dh.dockerfile_drift(ev, "FROM a\nRUN apt-get libgl1\n") == ["ENV X=1"]
    assert dh.dockerfile_drift(ev, "FROM a\nRUN apt-get libgl1\nENV X=1\n") == [
        "RUN apt-get libgl1"
    ]


def test_copy_sources_and_uncopied_files():
    text, _ = _real_dockerfiles()
    assert {"*.py", "*.json", "assets", "requirements.txt", "spec", "evaluator"} <= set(
        dh.copy_sources(text)
    )
    staged = ["app.py", "tasks_public.json", "assets/tasks/a.webp", "spec/x.py", "README.md",
              "Dockerfile", "requirements.txt", "theme.css"]  # fmt: skip
    assert dh.uncopied_files(staged, text) == ["theme.css"]


# ----------------------------------------------------------------------------- imports


def test_non_stdlib_imports():
    src = "import os\nimport numpy as np\nfrom yaml import x\ndef f():\n    import OCP\n"
    assert dh.non_stdlib_imports(src) == {"numpy", "yaml"}
    assert dh.non_stdlib_imports(src, module_level_only=False) == {"numpy", "yaml", "OCP"}


def test_unknown_space_imports():
    got = dh.unknown_space_imports({"app.py": "import gradio\nimport jobs\nimport torch\n",
                                    "jobs.py": "import recadbench_eval\n"})  # fmt: skip
    assert got == {"app.py": {"torch"}}


# ----------------------------------------------------------------------------- stage


def test_stage_layout(fake_repo, tmp_path):
    out = _stage(fake_repo, tmp_path)
    space = _files(out / "space")
    assert {"Dockerfile", "README.md", "requirements.txt", "tasks_public.json"} <= space
    assert {"app.py", "jobs.py", "store.py", "theme.py", "runtime.py"} <= space
    assert "deploy_hf.py" not in space and "Dockerfile.eval" not in space
    assert not any(p.startswith(("tests/", "__pycache__/", "space_data/")) for p in space)
    assert not any(p.startswith(("recadbench_eval/", "docker/")) for p in space)
    assert {"assets/banner-dark.webp", "assets/banner-light.webp", "assets/flow-dark.webp",
            "assets/tasks/r385_hs01_motor_mount.webp"} <= space  # fmt: skip
    assert {"spec/pyproject.toml", "evaluator/recadbench_eval/cli.py",
            "evaluator/golden/synthetic_box.step"} <= space  # fmt: skip
    assert not any("/tests/" in f"/{p}" or ".egg-info" in p for p in space)
    assert "spec/tests/examples/gt_meta/meta.yaml" not in space
    assert (out / "space" / "Dockerfile").read_text(encoding="utf-8") == (
        SPACE / "docker" / "Dockerfile"
    ).read_text(encoding="utf-8")
    meta, _ = dh.split_front_matter((out / "space" / "README.md").read_text(encoding="utf-8"))
    assert meta["sdk"] == "docker" and "sdk_version" not in meta
    req = (out / "space" / "requirements.txt").read_text(encoding="utf-8")
    assert f"gradio[oauth]=={GRADIO}" in req
    titles = json.loads((out / "space" / "tasks_public.json").read_text(encoding="utf-8"))
    assert titles[0]["title"] == "Motor mount cup"

    card = (out / "submissions_card" / "README.md").read_text(encoding="utf-8")
    assert "PRIVATE" in card and "mikbalarikan/recadbench-open" in card
    assert not (out / "eval_space").exists()
    assert dh.audit(out) == []
    # a second stage rebuilds the folders this tool wrote
    _stage(fake_repo, tmp_path)


def test_stage_other_owner_rewrites_links(fake_repo, tmp_path):
    out = _stage(fake_repo, tmp_path, owner="acme")
    readme = (out / "space" / "README.md").read_text(encoding="utf-8")
    assert "mikbalarikan" not in readme and "acme/recadbench-inputs" in readme
    assert dh.marker_owner(out / "space") == "acme"


def test_stage_refusals(fake_repo, tmp_path):
    with pytest.raises(dh.DeployError, match="ReCADBench repo"):
        dh.stage(fake_repo / "staging", OWNER, repo=fake_repo, gradio_version=GRADIO)
    foreign = tmp_path / "foreign"
    _w(foreign / "space" / "mine.txt")
    with pytest.raises(dh.DeployError, match="not written by deploy_hf"):
        dh.stage(foreign, OWNER, repo=fake_repo, gradio_version=GRADIO)
    remote = tmp_path / "remote_repo"
    _w(remote / ".git" / "config", '[remote "origin"]\n\turl = x\n')
    with pytest.raises(dh.DeployError, match="git checkout"):
        dh.stage(remote / "staging", OWNER, repo=fake_repo, gradio_version=GRADIO)


def test_stage_refuses_drifted_dockerfile(fake_repo, tmp_path):
    p = fake_repo / "space" / "docker" / "Dockerfile"
    p.write_text(p.read_text(encoding="utf-8").replace("libegl1", ""), encoding="utf-8")
    with pytest.raises(dh.DeployError, match="drifted"):
        _stage(fake_repo, tmp_path)
    assert not (tmp_path / "staging").exists()  # checked before anything is written


def test_stage_refuses_uncopied_app_file(fake_repo, tmp_path):
    _w(fake_repo / "space" / "extra.css", "body{}\n")
    with pytest.raises(dh.DeployError, match=r"does not COPY.*extra\.css"):
        _stage(fake_repo, tmp_path)


def test_stage_refuses_gradio_card(fake_repo, tmp_path):
    p = fake_repo / "space" / "README.md"
    p.write_text(p.read_text(encoding="utf-8").replace("sdk: docker", "sdk: gradio"), "utf-8")
    with pytest.raises(dh.DeployError, match="sdk must be 'docker'"):
        _stage(fake_repo, tmp_path)


def test_stage_refuses_tasks_public_with_extra_keys(fake_repo, tmp_path):
    _w(fake_repo / dh.TASKS_PUBLIC, json.dumps([{"id": "x", "no": 1, "key_dims": [1.0]}]))
    with pytest.raises(dh.DeployError, match="allowlist"):
        _stage(fake_repo, tmp_path)


def test_stage_real_repo(tmp_path):
    """The actual checkout stages and audits clean (every space/*.py, no tests, no GT)."""
    out = tmp_path / "staging"
    warnings = dh.stage(out, OWNER, repo=REPO, gradio_version=GRADIO)
    staged = {p.name for p in (out / "space").glob("*.py")}
    expected = {p.name for p in SPACE.glob("*.py")} - {"deploy_hf.py"}
    assert staged == expected
    assert (out / "space" / "tasks_public.json").is_file()
    assert len(list((out / "space" / "assets" / "tasks").glob("*.webp"))) == 10
    assert not any(p.suffix.lower() in dh.CAD_SUFFIXES for p in (out / "space").rglob("*")
                   if "golden" not in p.parts)  # fmt: skip
    assert not [w for w in warnings if "imports" in w], warnings
    assert dh.audit(out) == []


# ----------------------------------------------------------------------------- audit


@pytest.mark.parametrize(
    ("rel", "content", "why"),
    [
        ("space/assets/x.stl", "solid", "outside evaluator/golden"),
        ("space/spec/part.STEP", "x", "outside evaluator/golden"),
        ("space/evaluator/golden/gt_plan.json", "{}", "forbidden"),
        ("space/key_dims.yaml", "x", "forbidden"),
        ("space/gt_native.prt", "x", "forbidden"),
        ("space/meta.yaml", "x", "forbidden"),
        ("space/notes.md", f"token {FAKE_TOKEN}", "token"),
        ("space/notes.md", f"path {FAKE_WIN_PATH}", "Windows user path"),
        ("space/notes.md", "path /c/Users/someone/x", "Windows user path"),
        ("space/__pycache__/a.pyc", "x", "excluded folder"),
        ("space/evaluator/tests/test_a.py", "x", "excluded folder"),
        ("space/deploy_hf.py", "x", "forbidden"),
        ("space/Dockerfile.eval", "x", "forbidden"),
        ("space/recadbench_eval/__init__.py", "", "shadow"),
        ("submissions_card/results.jsonl", "{}", "forbidden"),
    ],
)
def test_audit_blocks(fake_repo, tmp_path, rel, content, why):
    out = _stage(fake_repo, tmp_path)
    _w(out / rel, content)
    problems = dh.audit(out)
    assert any(why in p for p in problems), problems
    assert FAKE_TOKEN not in "\n".join(problems) and "someone" not in "\n".join(problems)


def test_audit_reports_missing_required(fake_repo, tmp_path):
    out = _stage(fake_repo, tmp_path)
    (out / "space" / "Dockerfile").unlink()
    assert "space/Dockerfile is missing" in dh.audit(out)


# ----------------------------------------------------------------------------- push


class RepositoryNotFoundError(Exception):
    pass


class FakeApi:
    def __init__(self, private=None, missing=()):
        self.calls: list[tuple[str, tuple, dict]] = []
        self.private = dict(private or {})
        self.missing = set(missing)

    def __getattr__(self, name):  # any other Hub call (add_space_secret, ...) is a failure
        raise AssertionError(f"unexpected Hub call {name}")

    def _rec(self, name, *a, **kw):
        self.calls.append((name, a, kw))

    def names(self):
        return [c[0] for c in self.calls]

    def whoami(self):
        self._rec("whoami")
        return {"name": OWNER}

    def create_repo(self, repo_id, **kw):
        self._rec("create_repo", repo_id, **kw)
        self.missing.discard(repo_id)
        self.private.setdefault(repo_id, kw.get("private", False))

    def repo_info(self, repo_id, repo_type=None):
        self._rec("repo_info", repo_id, repo_type=repo_type)
        if repo_id in self.missing:
            raise RepositoryNotFoundError(repo_id)
        return SimpleNamespace(private=self.private.get(repo_id, False))

    def upload_folder(self, **kw):
        self._rec("upload_folder", **kw)

    def add_space_variable(self, repo_id, key, value):
        self._rec("add_space_variable", repo_id, key, value)


def _lines():
    seen: list[str] = []
    return seen, seen.append


PRIVATE_DATASETS = {"mikbalarikan/recadbench-submissions": True, "mikbalarikan/recadbench-gt": True}


def test_push_dry_run_makes_no_hub_call(fake_repo, tmp_path):
    out = _stage(fake_repo, tmp_path)
    seen, emit = _lines()
    for what in (*dh.FOLDERS, dh.ALL):
        dh.push(out, what, api=FakeApi(), dry_run=True, out=emit)
    assert any("variable RCB_MODE=space" in s for s in seen)
    assert any("variable RCB_MAX_PARALLEL=1" in s for s in seen)
    assert any("(PUBLIC, docker)" in s for s in seen)
    assert "dry run: nothing uploaded" in seen
    assert not any("eval" in s.lower() and "recadbench-eval" in s for s in seen)


def test_push_rejects_the_dropped_eval_target(fake_repo, tmp_path):
    out = _stage(fake_repo, tmp_path)
    with pytest.raises(dh.DeployError, match="--what"):
        dh.push(out, "eval", api=FakeApi(), out=lambda s: None)
    with pytest.raises(SystemExit):
        dh.main(["push", "--staging", str(out), "--what", "eval"], api=FakeApi(), out=print)


def test_push_submissions_creates_private_dataset_card_only(fake_repo, tmp_path):
    out = _stage(fake_repo, tmp_path)
    api = FakeApi(missing={"mikbalarikan/recadbench-submissions"})
    dh.push(out, "submissions", api=api, out=lambda s: None)
    name, args, kw = api.calls[1]
    assert (name, args) == ("create_repo", ("mikbalarikan/recadbench-submissions",))
    assert kw == {"repo_type": "dataset", "private": True, "exist_ok": True}
    up = [c for c in api.calls if c[0] == "upload_folder"][0][2]
    assert up["repo_id"] == "mikbalarikan/recadbench-submissions" and up["repo_type"] == "dataset"
    assert up["allow_patterns"] == ["README.md"] and up["ignore_patterns"] == [dh.MARKER]
    assert Path(up["folder_path"]) == out.resolve() / "submissions_card"
    assert "delete_patterns" not in up
    assert api.names().index("repo_info") < api.names().index("upload_folder")


def test_push_submissions_refuses_when_hub_says_public(fake_repo, tmp_path):
    out = _stage(fake_repo, tmp_path)
    # an existing public repo: exist_ok keeps it public, so the Hub reports private=False
    api = FakeApi(private={"mikbalarikan/recadbench-submissions": False})
    with pytest.raises(dh.DeployError, match="not private"):
        dh.push(out, "submissions", api=api, out=lambda s: None)
    assert "upload_folder" not in api.names()


def test_push_space_public_docker_with_variables_never_secrets(fake_repo, tmp_path):
    out = _stage(fake_repo, tmp_path)
    api = FakeApi(private=PRIVATE_DATASETS)
    seen, emit = _lines()
    dh.push(out, "space", api=api, out=emit)
    create = [c for c in api.calls if c[0] == "create_repo"][0]
    assert create[1] == ("mikbalarikan/recadbench-open",)
    assert create[2]["private"] is False and create[2]["space_sdk"] == "docker"
    assert create[2]["repo_type"] == "space" and "space_secrets" not in create[2]
    created = {d["key"]: d["value"] for d in create[2]["space_variables"]}
    assert created == dh.space_variables(OWNER)
    added = {c[1][1]: c[1][2] for c in api.calls if c[0] == "add_space_variable"}
    assert added == dh.space_variables(OWNER)
    assert added["RCB_MODE"] == "space" and added["RCB_ADMINS"] == OWNER
    up = [c for c in api.calls if c[0] == "upload_folder"][0][2]
    assert up["repo_id"] == "mikbalarikan/recadbench-open" and up["repo_type"] == "space"
    assert up["delete_patterns"] == ["*.py"] and up["ignore_patterns"] == [dh.MARKER]
    assert Path(up["folder_path"]) == out.resolve() / "space"
    # variables are set before the upload so the first build already sees them
    assert api.names().index("add_space_variable") < api.names().index("upload_folder")
    text = "\n".join(seen)
    assert "New secret" in text and "HF_TOKEN" in text and "FINE-GRAINED" in text
    assert "Jobs" not in text


def test_push_space_refuses_public_submissions_dataset(fake_repo, tmp_path):
    out = _stage(fake_repo, tmp_path)
    api = FakeApi(private={"mikbalarikan/recadbench-submissions": False})
    with pytest.raises(dh.DeployError, match="PUBLIC"):
        dh.push(out, "space", api=api, out=lambda s: None)
    assert "upload_folder" not in api.names() and "create_repo" not in api.names()


def test_push_space_warns_when_datasets_missing(fake_repo, tmp_path):
    out = _stage(fake_repo, tmp_path)
    api = FakeApi(missing={"mikbalarikan/recadbench-submissions", "mikbalarikan/recadbench-gt"})
    seen, emit = _lines()
    dh.push(out, "space", api=api, out=emit)
    assert sum("does not exist yet" in s for s in seen) == 2


def test_push_all_but_secrets_submissions_then_space(fake_repo, tmp_path):
    out = _stage(fake_repo, tmp_path)
    api = FakeApi(private={"mikbalarikan/recadbench-gt": True},
                  missing={"mikbalarikan/recadbench-submissions"})  # fmt: skip
    seen, emit = _lines()
    dh.push(out, dh.ALL, api=api, out=emit)
    ups = [c[2]["repo_id"] for c in api.calls if c[0] == "upload_folder"]
    assert ups == ["mikbalarikan/recadbench-submissions", "mikbalarikan/recadbench-open"]
    assert not any("does not exist yet" in s for s in seen)  # created by the first step
    assert sum(s.startswith("pushed ") for s in seen) == 2


def test_push_all_audits_every_folder_before_uploading(fake_repo, tmp_path):
    out = _stage(fake_repo, tmp_path)
    _w(out / "space" / "parts" / "x" / "gt.step", "x")  # planted after staging
    api = FakeApi(private=PRIVATE_DATASETS)
    with pytest.raises(dh.DeployError, match="audit failed"):
        dh.push(out, dh.ALL, api=api, out=lambda s: None)
    assert api.calls == []


def test_push_refusals(fake_repo, tmp_path):
    out = _stage(fake_repo, tmp_path)
    with pytest.raises(dh.DeployError, match="staged for owner"):
        dh.push(out, "space", "someone-else", api=FakeApi(), out=lambda s: None)
    with pytest.raises(dh.DeployError, match="not written by deploy_hf"):
        dh.push(tmp_path / "nowhere", "space", api=FakeApi(), out=lambda s: None)
    _w(out / "space" / "evaluator" / "parts" / "x" / "gt.step", "x")  # planted after staging
    api = FakeApi()
    with pytest.raises(dh.DeployError, match="audit failed"):
        dh.push(out, "space", api=api, out=lambda s: None)
    assert api.calls == []


def test_push_refuses_folder_that_was_run_in_place(fake_repo, tmp_path):
    """Running the staged app writes __pycache__ (absolute paths): push says to re-stage."""
    out = _stage(fake_repo, tmp_path)
    _w(out / "space" / "__pycache__" / "app.cpython-313.pyc", FAKE_WIN_PATH)
    api = FakeApi()
    with pytest.raises(dh.DeployError, match="re-run `deploy_hf.py stage`") as e:
        dh.push(out, "space", api=api, dry_run=True, out=lambda s: None)
    assert "bytecode staged" in str(e.value) and FAKE_WIN_PATH not in str(e.value)
    assert api.calls == []
    dh.push(out, "submissions", api=FakeApi(), dry_run=True, out=lambda s: None)  # other ok
    _stage(fake_repo, tmp_path)  # re-staging rebuilds the folder clean
    dh.push(out, "space", api=FakeApi(), dry_run=True, out=lambda s: None)


def test_main_stage_prints_summary(fake_repo, tmp_path):
    seen, emit = _lines()
    out = tmp_path / "staging"
    rc = dh.main(["stage", "--out", str(out), "--gradio-version", GRADIO], repo=fake_repo, out=emit)
    assert rc == 0 and any(s.startswith("audit: clean") for s in seen)
    assert any(s.startswith("space/") for s in seen) and any("evaluator/" in s for s in seen)
    assert any("RCB_MODE=space" in s for s in seen)
    assert all(s.isascii() for s in seen)


# ----------------------------------------------------------------------------- HF Jobs (later)


def test_hf_jobs_dispatcher_with_the_installed_huggingface_hub(monkeypatch):
    """Hub mode is not deployed, but HFJobsDispatcher must keep working for the later switch:
    with the real HfApi.run_job / inspect_job (HTTP faked) and the hub-mode variables, the Space
    image reference becomes a spaceId job spec and the job stages map to dispatcher states."""
    hf_api = pytest.importorskip("huggingface_hub.hf_api")
    sent: list[tuple[str, str, dict | None]] = []
    stage = {"value": "SCHEDULING"}
    job = {"id": "J1", "owner": {"id": "o1", "name": OWNER, "type": "user"}}

    class Resp:
        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    class Session:
        def post(self, url, json=None, headers=None):
            sent.append(("POST", url, json))
            return Resp({**job, "status": {"stage": "SCHEDULING"}})

        def get(self, url, headers=None, **kw):
            sent.append(("GET", url, None))
            return Resp({**job, "status": {"stage": stage["value"]}})

    monkeypatch.setattr(hf_api, "get_session", lambda: Session())
    monkeypatch.setattr(hf_api, "hf_raise_for_status", lambda r, *a, **k: None)
    api = hf_api.HfApi(endpoint="https://hub.invalid", token="not-a-real-token")
    v = dh.hub_mode_variables(OWNER)
    for key, value in v.items():
        monkeypatch.setenv(key, value)
    d = jobs.HFJobsDispatcher(
        submissions_repo=v["RCB_SUBMISSIONS_REPO"],
        gt_repo=v["RCB_GT_REPO"],
        image=v["RCB_EVAL_IMAGE"],
        token="not-a-real-token",
        flavor=v["RCB_JOB_FLAVOR"],
        namespace=v["RCB_JOB_NAMESPACE"],
        api=api,
    )
    assert d.dispatch("sub-0123abcd", "a" * 64, "submissions/x.zip") == "J1"
    method, url, spec = sent[0]
    assert method == "POST" and url == "https://hub.invalid/api/jobs/mikbalarikan"
    assert spec["spaceId"] == "mikbalarikan/recadbench-eval" and "dockerImage" not in spec
    assert spec["flavor"] == "cpu-basic" and spec["timeoutSeconds"] == 3 * 3600
    assert spec["secrets"] == {"HF_TOKEN": "not-a-real-token"}
    assert spec["command"][:2] == ["python", "-c"] and "score-submission" in spec["command"][2]
    child = int(spec["command"][2].split('"--timeout", ')[1].split("]")[0].strip("'\""))
    assert child == d.task_timeout_s == (3 * 3600 - jobs.JOB_RESERVE_S) // 20
    assert child >= dh.MIN_CHILD_TIMEOUT_S
    assert "not-a-real-token" not in json.dumps(spec["command"])
    expected = {
        "SCHEDULING": jobs.QUEUED,
        "RUNNING": jobs.RUNNING,
        "COMPLETED": jobs.COMPLETED,
        "ERROR": jobs.ERROR,
        "CANCELED": jobs.ERROR,
    }
    for value, state in expected.items():
        stage["value"] = value
        assert d.status("J1") == state, value
    assert sent[-1][1] == "https://hub.invalid/api/jobs/mikbalarikan/J1"


def test_hf_jobs_timeout_from_argument_env_or_default(monkeypatch):
    class Api:
        def run_job(self, **kw):
            self.kw = kw
            return SimpleNamespace(id="J1")

    def timeout(**kw):
        api = Api()
        d = jobs.HFJobsDispatcher("org/subs", "org/gt", "img:1", "tok", api=api, **kw)
        d.dispatch("sub-0123abcd", "b" * 64, "submissions/x.zip")
        return api.kw["timeout"], d.task_timeout_s

    monkeypatch.delenv(jobs.JOB_TIMEOUT_ENV, raising=False)
    assert timeout() == (jobs.DEFAULT_JOB_TIMEOUT, (1800 - jobs.JOB_RESERVE_S) // 20)
    monkeypatch.setenv(jobs.JOB_TIMEOUT_ENV, " 2h ")
    assert timeout() == ("2h", (7200 - jobs.JOB_RESERVE_S) // 20)
    assert timeout(timeout="90m")[0] == "90m"  # an explicit argument wins over the env
    monkeypatch.setenv(jobs.JOB_TIMEOUT_ENV, "")
    assert timeout()[0] == jobs.DEFAULT_JOB_TIMEOUT
    for bad in ("soon", "5m"):  # unparsable / too short for 10 tasks x 2 children
        monkeypatch.setenv(jobs.JOB_TIMEOUT_ENV, bad)
        with pytest.raises(ValueError):
            timeout()
