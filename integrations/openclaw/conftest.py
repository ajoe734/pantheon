"""Make integrations/openclaw modules importable by bare name in tests
(e.g. `import persona_agent_sync`) regardless of pytest's invocation cwd."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_registry_lock(tmp_path, monkeypatch):
    """The shared registry lock now fails closed (no private /tmp fallback), so
    tests must point it at a writable path unless they set their own."""
    if not os.getenv("PANTHEON_OPENCLAW_AGENT_REGISTRY_LOCK"):
        monkeypatch.setenv("PANTHEON_OPENCLAW_AGENT_REGISTRY_LOCK", str(tmp_path / "registry.lock"))
