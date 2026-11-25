"""The broker reads memory with the host-issued principal file, not a stale env token (RC2)."""
from __future__ import annotations

import os
import socket
import threading
import time

import pytest

from persona_tool_broker import BrokerConfig, MemoryCallError, _auth_refusal_class, http_memory_get
from services.memory import main as memory_main
from services.memory.institutional_memory_store import InstitutionalMemoryStore
from services.memory.persona_memory_store import PersonaMemoryStore
from services.memory.test_main import JWT_SECRET, _make_jwt

TENANT = "tenant-alpha"


def _serve(app):
    import uvicorn

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started
    return server, thread, port


@pytest.fixture
def memory_url(monkeypatch):
    stores = (InstitutionalMemoryStore(), PersonaMemoryStore())
    monkeypatch.setattr(memory_main, "_store", lambda: stores[0])
    monkeypatch.setattr(memory_main, "_persona_store", lambda: stores[1])
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_RUNTIME_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("PANTHEON_MEMORY_AUTHZ_MODE", "local")
    server, thread, port = _serve(memory_main.app)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _query(scope: str):
    q = {"actor_id": "memory-operator-1", "actor_roles": ["operator"], "session_id": "run-1",
         "query": "momentum", "limit": 3, "scope": scope}
    if scope == "persona":
        q["persona_id"] = "persona-alpha"
    return q


def _env(monkeypatch, tmp_path, memory_url, *, file_token, env_token):
    token_file = tmp_path / "memory.token"
    token_file.write_text(file_token)
    token_file.chmod(0o600)
    monkeypatch.setenv("OPENCLAW_ADAPTER_MEMORY_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_TOKEN", env_token)
    monkeypatch.setenv("PANTHEON_MEMORY_API_URL", memory_url)
    return token_file


@pytest.mark.parametrize("bank,scope", [("own", "persona"), ("facts", "institutional")])
def test_principal_file_beats_stale_env_token_on_both_banks(memory_url, monkeypatch, tmp_path, bank, scope):
    good = _make_jwt(tenant_id=TENANT, roles=["operator"])
    stale = _make_jwt(tenant_id=TENANT, roles=["operator"], exp_offset=-100)
    _env(monkeypatch, tmp_path, memory_url, file_token=good, env_token=stale)
    get = http_memory_get(BrokerConfig.from_env())
    body = get("/api/memory/retrieve", _query(scope), actor_roles=["operator"])
    assert isinstance(body, dict)


def test_stale_env_without_file_is_refused_with_bounded_reason(memory_url, monkeypatch, tmp_path):
    stale = _make_jwt(tenant_id=TENANT, roles=["operator"], exp_offset=-100)
    monkeypatch.delenv("OPENCLAW_ADAPTER_MEMORY_TOKEN_FILE", raising=False)
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_TOKEN", stale)
    monkeypatch.setenv("PANTHEON_MEMORY_API_URL", memory_url)
    get = http_memory_get(BrokerConfig.from_env())
    with pytest.raises(MemoryCallError) as err:
        get("/api/memory/retrieve", _query("persona"), actor_roles=["operator"])
    assert err.value.status_code in (401, 403)
    text = str(err.value.args) + repr(err.value.__dict__)
    assert stale not in text and memory_url not in text
    assert "memory_http_" in text and len(text) < 400


def test_file_is_read_per_call_and_unreadable_file_never_falls_back(memory_url, monkeypatch, tmp_path):
    good = _make_jwt(tenant_id=TENANT, roles=["operator"])
    token_file = _env(monkeypatch, tmp_path, memory_url, file_token="x" * 10, env_token=good)
    get = http_memory_get(BrokerConfig.from_env())
    with pytest.raises(MemoryCallError):  # rotated-in garbage is refused, not masked by the env value
        get("/api/memory/retrieve", _query("persona"), actor_roles=["operator"])
    token_file.write_text(good)
    get("/api/memory/retrieve", _query("persona"), actor_roles=["operator"])  # same getter, new file content
    token_file.unlink()
    with pytest.raises(MemoryCallError) as err:
        get("/api/memory/retrieve", _query("persona"), actor_roles=["operator"])
    assert "memory_credential_unavailable" in str(err.value.args) + repr(err.value.__dict__)


def test_explicit_config_token_still_wins(memory_url, monkeypatch, tmp_path):
    good = _make_jwt(tenant_id=TENANT, roles=["operator"])
    _env(monkeypatch, tmp_path, memory_url, file_token="x" * 10, env_token="stale")
    config = BrokerConfig(enabled=True, token="t", memory_api_url=memory_url, memory_auth_token=good)
    http_memory_get(config)("/api/memory/retrieve", _query("persona"), actor_roles=["operator"])


def test_from_env_drops_env_token_only_when_file_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_TOKEN", "legacy")
    monkeypatch.delenv("OPENCLAW_ADAPTER_MEMORY_TOKEN_FILE", raising=False)
    assert BrokerConfig.from_env().memory_auth_token == "legacy"
    monkeypatch.setenv("OPENCLAW_ADAPTER_MEMORY_TOKEN_FILE", str(tmp_path / "f"))
    assert BrokerConfig.from_env().memory_auth_token == ""


def test_auth_refusal_class_is_fixed_and_never_echoes_detail():
    detail = "Verified JWT missing required identity claims: ['tenant'] secret-claim-value https://x"
    out = _auth_refusal_class(403, detail)
    assert out == "memory_http_403_identity_claims_missing"
    assert _auth_refusal_class(401, "totally unknown text abc123") == "memory_http_401_refused"
