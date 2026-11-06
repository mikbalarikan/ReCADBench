"""Runner test fixtures (helpers: runner_helpers.py, on the pytest pythonpath)."""

from __future__ import annotations

import pytest


@pytest.fixture()
def no_gt_env(monkeypatch):
    monkeypatch.delenv("RECADBENCH_GT_DIR", raising=False)
