"""The shared CAD code policy (backends/recad_cli/code_policy.py): allow-list behaviour, what the
repo's own build123d replays and the NX smoke journal need, and the escape gadgets of the H8
verifier round (N2 / N3 / N5)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from recad_cli import code_policy as cp

BACKENDS = Path(__file__).resolve().parents[1]
SKILLS = os.environ.get("RECADBENCH_SKILLS_DIR")


@pytest.mark.parametrize("name,expect", [
    ("import_step", {"import", "step"}), ("DexManager", {"dex", "manager"}),
    ("CreateStep214Importer", {"create", "step214", "importer"}), ("Read_s", {"read", "s"}),
    ("NXOpen", {"nx", "open"}), ("ListingWindow", {"listing", "window"}),
])
def test_words(name, expect):
    assert cp.words(name) == expect


@pytest.mark.parametrize("path", sorted((BACKENDS / "build123d" / "replays").glob("C*.py")),
                         ids=lambda p: p.stem)
def test_build123d_replays_pass_the_native_profile(path):
    assert cp.check_source(path.read_text(encoding="utf-8"), "build123d_native") is None


def test_nx_smoke_journal_passes():
    from recadbench_run.agents.replay_scripts import NX_SMOKE_JOURNAL

    assert cp.check_source(NX_SMOKE_JOURNAL, "nx") is None


@pytest.mark.skipif(not SKILLS, reason="needs RECADBENCH_SKILLS_DIR (host): skillset examples")
def test_skillset_examples_pass():
    root = Path(SKILLS)
    for p in sorted(root.glob("build123d/examples/*/part.py")):
        assert cp.check_source(p.read_text(encoding="utf-8"), "build123d_native") is None, p
    for p in sorted(root.glob("nx/examples/*.py")):
        assert cp.check_source(p.read_text(encoding="utf-8"), "nx") is None, p


@pytest.mark.parametrize("code", [
    "import operator\nf = operator.attrgetter('x')\n",
    "import zipfile\n", "import sys\nsys.path.insert(0, 'x')\n", "import os\n",
    "import NXOpen.UF\n", "import NXOpen.Facet\n",
    "import NXOpen\nNXOpen.Session.GetSession().Execute('a', 'b', 'c', [])\n",
    "import NXOpen\nNXOpen.Session.GetSession().DexManager\n",
    "import NXOpen\nNXOpen.Session.GetSession().Parts.Open('x.prt')\n",
    "x = (lambda: 1).__code__\n", "k = '__clas' + 's__'\n", "g = (i for i in []).gi_frame\n",
    "from nxhelpers import nxh\nnxh.os\n", "from nxhelpers import nxh\nnxh.UF\n",
    "from . import j\n",
])
def test_nx_profile_denies(code):
    assert cp.check_source(code, "nx"), code


def test_nx_profile_local_modules():
    code = "import helper\nfrom helper import make\n"
    assert cp.check_source(code, "nx")
    assert cp.check_source(code, "nx", local_modules={"helper"}) is None
    # the native build123d file is self-contained: no local modules at all
    assert cp.check_source(code, "build123d_native", local_modules={"helper"})


def test_parse_errors_are_refused():
    assert "does not parse" in cp.check_source("def (:\n", "build123d")


def test_pins(tmp_path):
    run = tmp_path
    (run / "lib").mkdir()
    (run / "lib" / "h.py").write_bytes(b"import os\n")
    (run / ".recadbench").mkdir()
    pins = {"lib/h.py": cp.sha256_bytes(b"import os\n"), "cad/x.py": "0" * 64}
    (run / ".recadbench" / "compose_files.json").write_text(
        json.dumps({"files": pins}), encoding="utf-8")
    got = cp.pinned(run)
    assert cp.is_pinned(run, run / "lib" / "h.py", got)
    assert cp.check_file(run, run / "lib" / "h.py", "nx", "import os\n", got) is None
    (run / "lib" / "h.py").write_bytes(b"import os  # changed\n")
    assert not cp.is_pinned(run, run / "lib" / "h.py", got)
    assert cp.check_file(run, run / "lib" / "h.py", "nx", "import os\n", got)
    (run / "cad").mkdir()
    (run / "cad" / "x.py").write_text("", encoding="utf-8")
    assert not cp.is_pinned(run, run / "cad" / "x.py", got)  # only lib/ and .claude/ are pinned
