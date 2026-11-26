"""Authenticated ensure, id isolation, repair and unchanged live admission."""
import copy
import json

import pytest
from fastapi.testclient import TestClient

import consult_agent_provisioning as provisioning
from consultation_provider import create_app, admit_panel_agent, consult_agent_id, PanelAdmissionDenied
from main import _PERSONA_OPINION_RUNTIME_POLICY


class Gateway:
    _agent_id = "main"

    def __init__(self):
        self.entries = [{"id": "main", "default": True, "tools": {"allow": ["*"]}},
                        {"id": "persona-a", "tools": {"allow": ["trade"]}}]
        self.patches = []
        self.fail = False

    def _gateway_call(self, method, params=None):
        if self.fail:
            raise RuntimeError("gateway unavailable")
        if method == "config.get":
            return {"valid": True, "hash": str(len(self.patches)), "config": {"agents": {"list": copy.deepcopy(self.entries)}}}
        assert method == "config.patch"
        assert params["baseHash"] == str(len(self.patches))
        self.patches.append(params)
        for desired in json.loads(params["raw"])["agents"]["list"]:
            current = next((a for a in self.entries if a["id"] == desired["id"]), None)
            if current is None:
                self.entries.append(desired)
            else:
                current.update(desired)
        return {"ok": True}

    def gateway_agents_list(self):
        return copy.deepcopy(self.entries)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("PANTHEON_OPENCLAW_AGENT_REGISTRY_LOCK", str(tmp_path / "registry.lock"))
    monkeypatch.setattr(provisioning, "_persona_record", lambda tenant, persona: {"tenant_id": tenant, "persona_id": persona})
    gateway = Gateway()
    app = create_app(provider=gateway, provider_token="secret", replay_path=tmp_path / "replay.sqlite")
    return gateway, TestClient(app)


def headers(**overrides):
    return {"Authorization": "Bearer secret", "X-Pantheon-Tenant-Id": "tenant-a",
            "X-Pantheon-Service-Actor": "consultation-workflow-executor", **overrides}


def test_ensure_creates_repairs_and_preserves_other_agents(env):
    gateway, client = env
    before = copy.deepcopy(gateway.entries)
    body = {"tenant_id": "tenant-a", "persona_id": "persona-a"}
    first = client.post(provisioning.ENSURE_PATH, json=body, headers=headers())
    assert first.status_code == 200, first.text
    agent = first.json()["agent_id"]
    assert agent == consult_agent_id("tenant-a", "persona-a")
    assert first.json()["workspace_ref"].endswith("/" + agent)
    assert admit_panel_agent(gateway, tenant_id="tenant-a", persona_id="persona-a") == agent
    assert gateway.entries[:2] == before
    assert client.post(provisioning.ENSURE_PATH, json=body, headers=headers()).json()["changed"] is False
    assert len(gateway.patches) == 1
    gateway.entries[-1]["tools"] = {"allow": ["trade"]}
    with pytest.raises(PanelAdmissionDenied, match="deny-all"):
        admit_panel_agent(gateway, tenant_id="tenant-a", persona_id="persona-a")
    assert client.post(provisioning.ENSURE_PATH, json=body, headers=headers()).status_code == 200
    assert gateway.entries[-1]["tools"] == _PERSONA_OPINION_RUNTIME_POLICY["tools"]
    assert gateway.entries[:2] == before
    assert consult_agent_id("tenant-b", "persona-a") != agent


@pytest.mark.parametrize("override", [
    {"Authorization": "Bearer wrong"}, {"X-Pantheon-Service-Actor": "other"},
    {"X-Pantheon-Tenant-Id": "other"},
])
def test_authentication_and_tenant_match(env, override):
    gateway, client = env
    result = client.post(provisioning.ENSURE_PATH, json={"tenant_id": "tenant-a", "persona_id": "persona-a"}, headers=headers(**override))
    assert result.status_code in (400, 403) and not gateway.patches


@pytest.mark.parametrize("persona", ["main", "structured-extraction", "../x", "consult-forged"])
def test_reserved_or_invalid_identity(env, persona):
    gateway, client = env
    result = client.post(provisioning.ENSURE_PATH, json={"tenant_id": "tenant-a", "persona_id": persona}, headers=headers())
    assert result.status_code == 403 and not gateway.patches


def test_failure_is_retryable_and_not_cached(env):
    gateway, client = env
    body = {"tenant_id": "tenant-a", "persona_id": "persona-a"}
    gateway.fail = True
    failed = client.post(provisioning.ENSURE_PATH, json=body, headers=headers())
    assert failed.status_code == 503 and failed.json()["retryable"]
    gateway.fail = False
    assert client.post(provisioning.ENSURE_PATH, json=body, headers=headers()).status_code == 200


def _body():
    return {"tenant_id": "tenant-a", "persona_id": "persona-a"}


def test_matching_live_spec_is_one_read_and_does_not_wait_for_the_registry_lock(env):
    import threading
    import time
    from integrations.openclaw.persona_agent_sync import agent_registry_lock

    gateway, client = env
    assert client.post(provisioning.ENSURE_PATH, json=_body(), headers=headers()).status_code == 200
    calls = []
    inner = gateway._gateway_call
    gateway._gateway_call = lambda m, p=None: (calls.append(m), inner(m, p))[1]
    held, release = threading.Event(), threading.Event()

    def writer():  # an unrelated writer (persona agent ensure) holds the lock
        with agent_registry_lock():
            held.set()
            release.wait(10)

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    assert held.wait(5)
    started = time.monotonic()
    result = client.post(provisioning.ENSURE_PATH, json=_body(), headers=headers())
    elapsed = time.monotonic() - started
    release.set()
    thread.join(5)
    assert result.status_code == 200 and result.json()["changed"] is False
    assert calls == ["config.get"] and elapsed < 2
    assert len(gateway.patches) == 1  # never re-patched an agent that already matches


def test_slow_first_provisioning_answers_in_progress_then_ready(env, monkeypatch):
    import threading
    gateway, client = env
    monkeypatch.setenv(provisioning.ENSURE_WAIT_ENV, "0.2")
    gate = threading.Event()
    inner = gateway._gateway_call

    def slow(method, params=None):
        if method == "config.patch":
            gate.wait(10)
        return inner(method, params)

    gateway._gateway_call = slow
    first = client.post(provisioning.ENSURE_PATH, json=_body(), headers=headers())
    assert first.status_code == 202
    assert first.json()["status"] == "in_progress" and first.json()["retry_after_seconds"] > 0
    again = client.post(provisioning.ENSURE_PATH, json=_body(), headers=headers())
    assert again.status_code == 202  # single flight: no second writer started
    gate.set()
    monkeypatch.setenv(provisioning.ENSURE_WAIT_ENV, "10")
    ready = client.post(provisioning.ENSURE_PATH, json=_body(), headers=headers())
    assert ready.status_code == 200 and ready.json()["status"] == "ready"
    assert len(gateway.patches) == 1
