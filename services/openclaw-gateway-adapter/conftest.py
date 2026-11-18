"""Isolate the shared agent-registry lock for adapter tests.

The lock fails closed (no private /tmp fallback) and defaults to
/home/node/.openclaw, which unprivileged CI cannot create. Use the existing
configurable lock path, pointed at a per-test tmp file, unless a test sets its own.
"""
import os

import pytest


@pytest.fixture(autouse=True)
def _isolated_registry_lock(tmp_path, monkeypatch):
    if not os.getenv("PANTHEON_OPENCLAW_AGENT_REGISTRY_LOCK"):
        monkeypatch.setenv("PANTHEON_OPENCLAW_AGENT_REGISTRY_LOCK", str(tmp_path / "registry.lock"))


@pytest.fixture(autouse=True)
def _fixture_model_chain(monkeypatch):
    """The model chain is a deployment variable; tests only set fixture values."""
    if not os.getenv("OPENCLAW_MODEL_CHAIN"):
        monkeypatch.setenv("OPENCLAW_MODEL_CHAIN", "fx-a/model-1,fx-b/model-2,fx-b/model-3")
