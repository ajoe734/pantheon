"""The materialise route takes the cycle's own query; it has no default query."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

ADAPTER_DIR = Path(__file__).resolve().parent
ROOT = ADAPTER_DIR.parents[1]
for entry in (str(ROOT), str(ADAPTER_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import main as adapter_main  # noqa: E402

HEADERS = {"X-Pantheon-Service-Token": "adapter-secret"}
ROUTE = "/api/openclaw-adapter/agents/persona/memory/materialize"


class _Memory:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.payload


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = (tmp_path / "workspaces").resolve()
    (root / "persona-a").mkdir(parents=True)
    monkeypatch.setenv("PANTHEON_MEMORY_API_URL", "http://memory.test")
    with patch.multiple(adapter_main, _OPENCLAW_AGENT_WORKSPACE_ROOT=root,
                        _ASSISTANT_SERVICE_TOKEN="adapter-secret", _ASSISTANT_SERVICE_AUTH_REQUIRED=True):
        yield root, TestClient(adapter_main.app)


def test_query_and_tags_reach_the_memory_plane_and_the_workspace_cache(env):
    root, client = env
    seen = {}

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        return _Memory({"hits": []})

    with patch("integrations.openclaw.persona_memory_bridge.urllib.request.urlopen", fake_urlopen):
        response = client.post(ROUTE, headers=HEADERS, json={
            "persona_id": "persona-a", "workspace_ref": str(root / "persona-a"),
            "query": "momentum 2330 gross utilisation", "tags": ["family:momentum", "shortfall:gross_utilisation"]})
    assert response.status_code == 200, response.text
    assert "query=momentum+2330+gross+utilisation" in seen["url"]
    assert "tags=family%3Amomentum" in seen["url"] and "tags=shortfall%3Agross_utilisation" in seen["url"]
    assert response.json()["memory"]["retrieval"]["query"] == "momentum 2330 gross utilisation"
    context = json.loads((root / "persona-a" / "memory" / "context.json").read_text(encoding="utf-8"))
    assert context["retrieval"]["tags"] == ["family:momentum", "shortfall:gross_utilisation"]


@pytest.mark.parametrize("body", [{}, {"query": ""}, {"query": "   "}, {"query": "x", "extra": 1}])
def test_missing_blank_or_unknown_fields_are_rejected_without_a_memory_call(env, body):
    root, client = env
    with patch("integrations.openclaw.persona_memory_bridge.urllib.request.urlopen") as urlopen:
        response = client.post(ROUTE, headers=HEADERS, json={
            "persona_id": "persona-a", "workspace_ref": str(root / "persona-a"), **body})
    assert response.status_code == 422
    urlopen.assert_not_called()
