"""The materialise route honours the cycle's own query and tags; a blank query is refused."""
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


@pytest.mark.parametrize("body", [{"query": ""}, {"query": "   "}, {"query": "x", "extra": 1}])
def test_blank_or_unknown_fields_are_rejected_without_a_memory_call(env, body):
    root, client = env
    with patch("integrations.openclaw.persona_memory_bridge.urllib.request.urlopen") as urlopen:
        response = client.post(ROUTE, headers=HEADERS, json={
            "persona_id": "persona-a", "workspace_ref": str(root / "persona-a"), **body})
    assert response.status_code == 422
    urlopen.assert_not_called()


def test_a_supplied_query_replaces_the_bootstrap_default(env):
    from integrations.openclaw.persona_memory_bridge import DEFAULT_MEMORY_QUERY
    root, client = env
    seen = []

    def fake_urlopen(request, timeout):
        seen.append(request.full_url)
        return _Memory({"hits": []})

    with patch("integrations.openclaw.persona_memory_bridge.urllib.request.urlopen", fake_urlopen):
        body = {"persona_id": "persona-a", "workspace_ref": str(root / "persona-a")}
        client.post(ROUTE, headers=HEADERS, json={**body, "query": "carry 2882"})
        client.post(ROUTE, headers=HEADERS, json=body)
    assert "query=carry+2882" in seen[0] and DEFAULT_MEMORY_QUERY.split()[0] not in seen[0]
    assert "query=recent+lessons" in seen[1]


def _strict_memory_app(tmp_path, monkeypatch):
    from services.memory import main as memory_main
    secret = "m" * 40
    for key, value in {
        "PANTHEON_MEMORY_STORE_PATH": str(tmp_path / "m.json"), "PANTHEON_MEMORY_STORE": str(tmp_path / "m.json"),
        "PANTHEON_PERSONA_MEMORY_STORE_PATH": str(tmp_path / "p.json"),
        "PANTHEON_PERSONA_MEMORY_STORE": str(tmp_path / "p.json"),
        "PANTHEON_MEMORY_AUTHZ_MODE": "local", "PANTHEON_MEMORY_AUTH_MODE": "strict",
        "PANTHEON_MEMORY_JWT_SECRET": secret,
    }.items():
        monkeypatch.setenv(key, value)
    memory_main._STORE = None
    memory_main._PERSONA_STORE = None
    return memory_main, TestClient(memory_main.app), secret


def _persona_entry():
    return {"memory_id": "pmem-1", "persona_id": "persona-a", "memory_type": "strategy_lesson",
            "content": {"summary": "x", "tags": []}, "source_event_type": "postmortem_published",
            "source_event_id": "PM-1", "written_at": "2026-06-09T01:00:00Z", "write_authority": "incident-svc",
            "relevance_scope": "persona_private", "reuse_count": 0}


def _jwt(secret, roles, sub="openclaw-persona-sync"):
    import base64, hashlib, hmac, time
    enc = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=")
    now = int(time.time())
    body = enc(b'{"alg":"HS256","typ":"JWT"}') + b"." + enc(json.dumps({
        "sub": sub, "tenant_id": "tenant-dev", "roles": roles, "iat": now, "exp": now + 600}).encode())
    return (body + b"." + enc(hmac.new(secret.encode(), body, hashlib.sha256).digest())).decode()


def test_materialize_ok_with_file_credential_against_strict_memory(env, tmp_path, monkeypatch):
    root, client = env
    _memory_main, memory_client, secret = _strict_memory_app(tmp_path, monkeypatch)
    token_file = tmp_path / "OPENCLAW_ADAPTER_MEMORY_TOKEN"
    token_file.write_text(_jwt(secret, ["persona_memory_reader"]))
    token_file.chmod(0o600)
    monkeypatch.setenv("OPENCLAW_ADAPTER_MEMORY_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("PANTHEON_OPENCLAW_MEMORY_ACTOR_ID", "openclaw-persona-sync")
    monkeypatch.setenv("PANTHEON_OPENCLAW_MEMORY_ACTOR_ROLES", "persona_memory_reader")
    monkeypatch.delenv("PANTHEON_MEMORY_AUTH_TOKEN", raising=False)

    def via_memory(request, timeout):
        path = request.full_url.split("memory.test", 1)[1]
        reply = memory_client.get(path, headers={"Authorization": request.headers["Authorization"]})
        assert reply.status_code == 200, reply.text
        return _Memory(reply.json())

    with patch("integrations.openclaw.persona_memory_bridge.urllib.request.urlopen", via_memory):
        response = client.post(ROUTE, headers=HEADERS, json={
            "persona_id": "persona-a", "workspace_ref": str(root / "persona-a")})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ok"


def test_adapter_principal_is_refused_by_memory_writes_and_a_bad_file_fails_closed(env, tmp_path, monkeypatch):
    root, client = env
    _memory_main, memory_client, secret = _strict_memory_app(tmp_path, monkeypatch)
    denied = memory_client.post("/api/memory/persona-entries", json=_persona_entry(),
                                headers={"Authorization": "Bearer " + _jwt(secret, ["persona_memory_reader"])})
    assert denied.status_code in {401, 403}
    monkeypatch.setenv("OPENCLAW_ADAPTER_MEMORY_TOKEN_FILE", str(tmp_path / "missing"))
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_TOKEN", "stale-env-token")
    with pytest.raises(RuntimeError):
        adapter_main.adapter_memory_credential()
