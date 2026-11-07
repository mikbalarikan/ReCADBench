"""NX STEP read-back tolerance (P11): Parasolid vs OCCT volumes of a curved part differ by up to
~1e-4 relative; that is a valid export, a broken one is off by far more (CI, no NX needed)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ADAPTER = Path(__file__).resolve().parents[1] / "adapter" / "adapter.py"
spec = importlib.util.spec_from_file_location("nx_adapter_under_test", ADAPTER)
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)

NX_RES = {"ok": True, "bodies": 1, "volume": 609.824023, "faces": 88, "unit": "mm"}


@pytest.mark.parametrize(
    ("rb_volume", "rb_solids", "ok"),
    [
        (609.805707, 1, True),  # P11 dr02, NX nx@v1 Opus: 3.0e-5 relative
        (609.76099, 1, True),  # P11 dr02, NX nx@v2 Sonnet: 1.0e-4 relative
        (608.9, 1, False),  # 1.5e-3: not a kernel difference
        (70.73, 1, False),  # inconsistent solid
        (609.824023, 2, False),  # two solids
    ],
)
def test_readback_tolerance(monkeypatch, tmp_path, rb_volume, rb_solids, ok):
    monkeypatch.setattr(
        adapter,
        "_readback",
        lambda step: {"solids": rb_solids, "volume_mm3": rb_volume, "faces": 88, "by": "test"},
    )
    out = adapter._step_result(dict(NX_RES), tmp_path / "output.step")
    assert out["ok"] is ok
    if not ok:
        assert "STEP read-back mismatch" in out["error"]
