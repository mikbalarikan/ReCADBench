"""Orchestrator test fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def gate_key_dir(tmp_path_factory, monkeypatch):
    """Every test gets its own runner-private gate key store (never ~/.recadbench-private)."""
    d = tmp_path_factory.mktemp("gate-keys")
    monkeypatch.setenv("RECADBENCH_GATE_KEY_DIR", str(d))
    return d
