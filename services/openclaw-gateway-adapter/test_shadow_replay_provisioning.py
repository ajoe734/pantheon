"""Replay agent ensure and live deny-all probe, through the adapter routes."""
import copy

import pytest
from fastapi.testclient import TestClient

import main
import shadow_replay_provisioning as replay
from test_consult_agent_provisioning import Gateway


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("PANTHEON_OPENCLAW_AGENT_REGISTRY_LOCK", str(tmp_path / "registry.lock"))
    monkeypatch.delenv("PANTHEON_SHADOW_REPLAY_AGENT_ID", raising=False)
    gateway = Gateway()
    monkeypatch.setattr(main, "_OPENCLAW_AGENT_PROVIDER", gateway)
    return gateway, TestClient(main.app)


def test_ensure_creates_deny_all_agent_probe_reports_it_and_other_agents_untouched(env):
    gateway, client = env
    before = copy.deepcopy(gateway.entries)
    first = client.post(replay.ENSURE_PATH)
    assert first.status_code == 200, first.text
    assert first.json()["agent_id"] == replay.SHADOW_REPLAY_AGENT_ID and first.json()["changed"] is True
    assert gateway.entries[:2] == before
    assert client.post(replay.ENSURE_PATH).json()["changed"] is False
    probe = client.get(replay.POLICY_PATH + "/" + replay.SHADOW_REPLAY_AGENT_ID)
    assert probe.json() == {"agent_id": replay.SHADOW_REPLAY_AGENT_ID, "tools": [], "side_effects": "none"}


def test_probe_fails_closed_when_agent_missing_or_policy_widened_and_repair_restores(env):
    gateway, client = env
    url = replay.POLICY_PATH + "/" + replay.SHADOW_REPLAY_AGENT_ID
    assert client.get(url).status_code == 503  # not provisioned: never reported as deny-all
    client.post(replay.ENSURE_PATH)
    gateway.entries[-1]["tools"] = {"allow": ["trade"]}
    assert client.get(url).status_code == 503
    assert client.post(replay.ENSURE_PATH).status_code == 200
    assert client.get(url).status_code == 200
    assert client.get(replay.POLICY_PATH + "/main").status_code == 404


def test_default_agent_identity_is_refused(env, monkeypatch):
    _, client = env
    monkeypatch.setenv("PANTHEON_SHADOW_REPLAY_AGENT_ID", "main")
    assert client.post(replay.ENSURE_PATH).status_code == 503
