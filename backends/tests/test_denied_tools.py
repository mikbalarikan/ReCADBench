"""backend.yaml denied_tools (ADR 0004): every backend.yaml validates against spec 0.3.0, and the
deny entries catch representative scan-import / scan-analysis calls but not ordinary CAD calls.

The matcher below is the rule the schema documents (`tool` = tool-name prefix, `args_regex`
searched in the JSON-serialised arguments); P10's gate_hook must implement the same rule.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest
import yaml
from recadbench_spec import validate_file

BACKENDS = Path(__file__).resolve().parents[1]
MANIFESTS = sorted(BACKENDS.glob("*/backend.yaml"))

_spec = importlib.util.spec_from_file_location("nx_run_deny", BACKENDS / "nx/runner/nx_run.py")
nx_run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nx_run)


def nx_args(code: str) -> dict:
    """nx (kind cli) matcher arguments: the journal as nx_run.journal_source gives it (decoded as
    Python decodes it, # comments removed; backends/nx/backend.yaml)."""
    return {
        "command": "recadbench nx-run cad/j.py",
        "journal": nx_run.journal_source(code.encode("utf-8")),
    }


def denied(manifest: dict, tool: str, args: dict) -> bool:
    blob = json.dumps(args, ensure_ascii=False)
    for d in manifest.get("denied_tools") or []:
        if tool.startswith(d["tool"]) and (
            "args_regex" not in d or re.search(d["args_regex"], blob)
        ):
            return True
    return False


def load(backend: str) -> dict:
    return yaml.safe_load((BACKENDS / backend / "backend.yaml").read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", MANIFESTS, ids=lambda p: p.parent.name)
def test_backend_yaml_valid_without_warnings(path: Path) -> None:
    report = validate_file(path)
    assert report.ok, [str(i) for i in report.errors]
    assert not report.warnings, [str(i) for i in report.warnings]
    assert yaml.safe_load(path.read_text(encoding="utf-8")).get("denied_tools")


CASES = [
    # (backend, tool, args, denied?)
    ("build123d", "mcp__build123d__import_cad_file", {"path": "input/input.stl"}, True),
    ("build123d", "mcp__build123d__mesh_section", {"axis": "Z"}, True),
    ("build123d", "mcp__build123d__find_holes", {}, True),
    ("build123d", "mcp__build123d__recognise_features", {}, True),
    ("build123d", "mcp__build123d__compare", {"a": "part", "b": "scan"}, True),
    ("build123d", "mcp__build123d__execute", {"code": "s = import_stl('input/input.stl')"}, True),
    ("build123d", "mcp__build123d__execute", {"code": "m = Mesher().read('x.3mf')"}, True),
    ("build123d", "mcp__build123d__execute",
     {"code": "with BuildPart() as bp:\n    Box(10, 20, 5)"}, False),
    ("build123d", "mcp__build123d__measure", {}, False),
    ("build123d", "mcp__build123d__export", {"format": "step"}, False),
    ("freecad", "mcp__freecad__import_stl", {"path": "input/input.stl"}, True),
    ("freecad", "mcp__freecad__execute_python", {"code": "import Mesh\nMesh.open('in.stl')"},
     True),
    ("freecad", "mcp__freecad__execute_python",
     {"code": "body = doc.addObject('PartDesign::Body', 'Body')"}, False),
    ("freecad", "mcp__freecad__open_document", {"path": "input/input.stl"}, True),
    ("freecad", "mcp__freecad__pad_sketch", {"length": 10}, False),
    ("solidcraft", "mcp__solidcraft__execute",
     {"commands": [{"command": "pointcloud.import", "params": {"path": "in.stl"}}]}, True),
    ("solidcraft", "mcp__solidcraft__batch",
     {"commands": [{"command": "file.insert_mesh", "params": {}}]}, True),
    ("solidcraft", "mcp__solidcraft__execute",
     {"commands": [{"command": "sketch.fit_curves_to_section", "params": {}}]}, True),
    ("solidcraft", "mcp__solidcraft__execute",
     {"commands": [{"command": "feature.extrude", "params": {"distance": 5}}]}, False),
    ("solidcraft", "mcp__solidcraft__open", {"path": "scan.stl"}, True),
    # H3 fixer regressions: macro route, import-shaped module matching, export allowed.
    ("freecad", "mcp__freecad__create_macro",
     {"name": "m", "code": "import Mesh\nMesh.open('input/input.stl')", "description": ""}, True),
    ("freecad", "mcp__freecad__create_macro",
     {"name": "m", "code": "import Mesh\nMesh.insert('scan.stl', doc.Name)"}, True),
    ("freecad", "mcp__freecad__create_macro_from_template", {"name": "m", "template": "basic"},
     False),
    ("freecad", "mcp__freecad__create_macro",
     {"name": "m", "code": "body = doc.addObject('PartDesign::Body', 'Body')"}, False),
    ("freecad", "mcp__freecad__run_macro", {"macro_name": "m"}, True),
    ("freecad", "mcp__freecad__execute_python", {"code": "import FreeCAD as App, Mesh"}, True),
    ("freecad", "mcp__freecad__execute_python",
     {"code": "x = 1\nfrom MeshPart import meshFromShape"}, True),
    ("freecad", "mcp__freecad__execute_python", {"code": "m = load()\nMesh.show(m)"}, True),
    ("freecad", "mcp__freecad__execute_python", {"code": "doc.addObject('Mesh::Feature', 'S')"},
     True),
    ("freecad", "mcp__freecad__execute_python", {"code": 'App.openDocument("scan.STL")'}, True),
    ("freecad", "mcp__freecad__execute_python",
     {"code": "import os\nf = os.listdir('input\\')"}, True),
    ("freecad", "mcp__freecad__execute_python",
     {"code": "points = [App.Vector(0,0,0), App.Vector(10,0,0), App.Vector(10,5,0)]\n"
              "w = Part.makePolygon(points + [points[0]])"}, False),
    ("freecad", "mcp__freecad__execute_python",
     {"code": "# mesh-free sketch\nsk = body.newObject('Sketcher::SketchObject', 'S')"}, False),
    ("freecad", "mcp__freecad__execute_python",
     {"code": "# control points of the profile\nspline.interpolate(points)"}, False),
    ("freecad", "mcp__freecad__execute_python",
     {"code": "Part.export([body], 'out/output.step')"}, False),
    ("build123d", "mcp__build123d__execute",
     {"code": "export_stl(bp.part, 'work/self_check.stl')"}, False),
    ("build123d", "mcp__build123d__execute",
     {"code": "pts = [(0, 0), (10, 0), (5, 8)]\nwith BuildSketch():\n    Polygon(*pts)"}, False),
    ("build123d", "mcp__build123d__execute",
     {"code": "import glob, build123d as b\nf = glob.glob('input/*')[0]\n"
              "s = getattr(b, 'import_' + 'st' + 'l')(f)"}, True),
    ("build123d", "mcp__build123d__execute", {"code": "x = 1\nimport_stl(path)"}, True),
    ("build123d", "mcp__build123d__execute", {"code": "import trimesh\nm = trimesh.load(p)"},
     True),
    ("build123d", "mcp__build123d__execute", {"code": 's = import_step("scan.stl")'}, True),
    # nx (kind cli): tool = the command line, arguments = {command, journal source without
    # comments} (backends/nx/backend.yaml; recadbench nx-run enforces the same rule before NX
    # starts).
    *[
        ("nx", "recadbench nx-run cad/j.py", nx_args(code), expect)
        for code, expect in [
            # H4 fixer 3: the words in COMMENTS no longer refuse a journal ...
            ("# no Convergent bodies here; solid features only\nimport NXOpen\n", False),
            ("# read params from the IR, never from input/ (the scan)\nx = 1\n", False),
            ("# do not use FitSurface\nnxf.edge_blend(part, chains)\n", False),
            ("x = 1  # not NXOpen.Facet, not import trimesh\n", False),
            # ... but code, strings and docstrings still do, and a comment cannot hide code
            ('"""import trimesh"""\nexec(__doc__)\n', True),
            ("s = '# NXOpen.Facet'\n", True),
            ("# harmless\rimport trimesh\n", True),  # CPython ends the line at a lone CR
            ("x = 1 # c\nb = part.Features.CreateFitSurfaceBuilder(NF)\n", True),
            ("def f(:\n    # FitSurface\n", True),  # does not tokenize: matched as is
            ("import NXOpen, NXOpen.Facet\nb = part.FacetedBodies.CreateSTLImportBuilder()", True),
            ("from NXOpen import Features, Facet", True),
            ("x = 1\nb2.File = 'input/input.stl'", True),
            ('b2.File = "scan.STL"', True),
            ("cb = part.Features.CreateConvergentBodyBuilder()", True),
            ("imp = S.DexManager.CreateObjImporter()", True),
            ("b = part.Features.CreateFitSurfaceBuilder(NF)", True),
            ("x = 1\nimport trimesh", True),
            ("import os\nfiles = os.listdir('input/')", True),
            ("from nxhelpers import nxh, nxf\npart = nxh.work_part()\n"
             "blk = nxh.block(part, (0, 0, 0), (40, 30, 10))", False),
            ("# facet count of the display body is irrelevant\nnxf.edge_blend(part, chains)",
             False),
            ("import NXOpen.Features\nimport NXOpen.GeometricUtilities as GU", False),
            ("input_w = 5\nnxh.expr(part, 'input_w', input_w)", False),
            ("f = part.Features.CreateFitCurveBuilder(NF)", False),
        ]
    ],
]


@pytest.mark.parametrize("backend,tool,args,expect", CASES)
def test_denied_tools_match(backend, tool, args, expect) -> None:
    assert denied(load(backend), tool, args) is expect
