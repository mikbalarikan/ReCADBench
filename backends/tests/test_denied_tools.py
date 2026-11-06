"""backend.yaml denied_tools (ADR 0004): every backend.yaml validates against spec 0.3.0, and the
deny entries catch representative scan-import / scan-analysis calls but not ordinary CAD calls.

The matcher below is the rule the schema documents (`tool` = tool-name prefix, `args_regex`
searched in the JSON-serialised arguments); P10's gate_hook must implement the same rule.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml
from recadbench_spec import validate_file

BACKENDS = Path(__file__).resolve().parents[1]
MANIFESTS = sorted(BACKENDS.glob("*/backend.yaml"))


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
]


@pytest.mark.parametrize("backend,tool,args,expect", CASES)
def test_denied_tools_match(backend, tool, args, expect) -> None:
    assert denied(load(backend), tool, args) is expect
