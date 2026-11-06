"""Runner test fixtures (helpers: runner_helpers.py, on the pytest pythonpath)."""

from __future__ import annotations

import pytest


@pytest.fixture()
def no_gt_env(monkeypatch):
    monkeypatch.delenv("RECADBENCH_GT_DIR", raising=False)


@pytest.fixture(autouse=True)
def gate_key_dir(tmp_path_factory, monkeypatch):
    """Every test gets its own runner-private gate key store (never ~/.recadbench-private)."""
    d = tmp_path_factory.mktemp("gate-keys")
    monkeypatch.setenv("RECADBENCH_GATE_KEY_DIR", str(d))
    return d
