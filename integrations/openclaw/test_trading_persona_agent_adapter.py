"""Adapter route: a trading persona gets its own agent, SOUL, tool profile, memory.

Faked: the `openclaw` CLI runner (agent registry) and the Memory Plane HTTP read.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

ADAPTER_DIR = Path(__file__).resolve().parents[2] / "services" / "openclaw-gateway-adapter"
ROOT = Path(__file__).resolve().parents[2]
for entry in (str(ROOT), str(ADAPTER_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import main as adapter_main  # noqa: E402
from services.persona.harness import HarnessVersion, digest  # noqa: E402

SURFACE = {
    "identity_inputs": ["Paper research persona."],
    "guardrails": {"allowed_tools": ["research.read"], "forbidden_solo_actions": ["trial_admission"]},
    "consult_policy": [{"action": "trial_admission", "required_roles": ["risk_persona"]}],
    "tool_guidance": {"research.read": "Read governed research."},
    "skills": [],
    "workflow": {"cadence_seconds": 3600, "steps": ["observe", "orient", "propose"]},
    "cycle_thresholds": {"minimum_evidence_count": 3, "minimum_confidence": 0.8},
    "memory_retrieval_policy": {"scopes": ["own_private"], "max_items": 10, "minimum_confidence": 0.8},
}
HEADERS = {"X-Pantheon-Service-Token": "adapter-secret", "Idempotency-Key": "k-1", "X-Request-Id": "r-1"}


def _persona(pid: str, *, harness: bool = True) -> dict:
    persona = {"persona_id": pid, "name": "青禾", "mandate": "TW equity momentum",
               "strategy_family": "momentum", "lifecycle_state": "paper_owner", "metadata": {}}
    if harness:
        version = HarnessVersion(
            persona_id=pid, version=1, parent_version=None, patch_id=None,
            created_at="2026-11-12T00:00:00Z", created_by="owner",
            content_digest=digest(SURFACE), surface=SURFACE,
        ).model_dump(mode="json")
        persona["metadata"]["persona_harness"] = {"active_version": 1, "versions": [version]}
    return persona


class FakeCli:
    def __init__(self):
        self.agents: list[dict] = []
        self.calls: list[list[str]] = []

    def __call__(self, args):
        self.calls.append(list(args))
        out = ""
        if args[:3] == ["openclaw", "agents", "list"]:
            out = json.dumps([{"id": a["id"]} for a in self.agents])
        elif args[:3] == ["openclaw", "agents", "add"]:
            self.agents.append({"id": args[3]})
        elif args[:3] == ["openclaw", "config", "get"]:
            out = json.dumps(self.agents)
        elif args[:3] == ["openclaw", "config", "set"]:
            self.agents = json.loads(args[4])
        return subprocess.CompletedProcess(args, 0, out, "")


class _Memory:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.payload


def _hit(pid, memory_id, summary):
    return {"type": "persona", "relevance_score": 0.9,
            "entry": {"memory_id": memory_id, "persona_id": pid, "memory_type": "episodic",
                      "content": {"summary": summary}, "written_at": "2026-11-12T00:00:00Z"}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = (tmp_path / "workspaces").resolve()
    cli = FakeCli()

    def soul_writer(workspace, soul):
        Path(workspace).mkdir(parents=True, exist_ok=True)
        (Path(workspace) / "SOUL.md").write_text(soul, encoding="utf-8")

    monkeypatch.setenv("PANTHEON_MEMORY_API_URL", "http://memory.test")
    with patch.multiple(adapter_main, _OPENCLAW_AGENT_WORKSPACE_ROOT=root,
                        _OPENCLAW_AGENT_IDEMPOTENCY_DB=tmp_path / "state" / "agents.sqlite3",
                        _ASSISTANT_SERVICE_TOKEN="adapter-secret", _ASSISTANT_SERVICE_AUTH_REQUIRED=True,
                        _gateway_state_agent_runner=cli, _gateway_state_soul_writer=soul_writer):
        yield root, cli, TestClient(adapter_main.app)


def _post(client, root, persona):
    return client.post(
        "/api/openclaw-adapter/agents/persona/ensure", headers=HEADERS,
        json={"persona": persona, "workspace_ref": str(root / persona["persona_id"])},
    )


def test_trading_persona_agent_soul_tools_and_private_memory(env):
    root, cli, client = env
    memory = {"hits": [_hit("persona-a", "mem-own", "own lesson"),
                       _hit("persona-b", "mem-other", "OTHER PRIVATE SECRET")]}
    with patch("integrations.openclaw.persona_memory_bridge.urllib.request.urlopen",
               return_value=_Memory(memory)):
        response = _post(client, root, _persona("persona-a"))

    assert response.status_code == 201, response.text
    agent = response.json()["agent"]
    assert agent["agent_id"] == "persona-a" and agent["memory"] == {"status": "materialized"}
    workspace = root / "persona-a"
    soul = (workspace / "SOUL.md").read_text(encoding="utf-8")
    assert "Version: **1**" in soul and "observe" in soul          # active harness rendered
    assert "stage a memory candidate" in soul and "update MEMORY.md" not in soul
    memory_md = (workspace / "MEMORY.md").read_text(encoding="utf-8")
    assert "persona_memory:mem-own" in memory_md                      # source memory id
    assert "OTHER PRIVATE SECRET" not in memory_md and "mem-other" not in memory_md
    # Tool profile persisted into agents.list: read-only + proposal submission only.
    tools = next(a for a in cli.agents if a["id"] == "persona-a")["tools"]
    assert tools["allow"] == ["read", "persona_proposal_submit"]
    assert tools["exec"]["security"] == "deny"
    assert not any(m in t for t in tools["allow"] for m in ("order", "broker", "capital", "lifecycle"))


def test_missing_harness_is_explicit_not_synthesized_and_memory_failure_degrades(env):
    root, cli, client = env
    with patch("integrations.openclaw.persona_memory_bridge.urllib.request.urlopen",
               side_effect=OSError("memory down")):
        response = _post(client, root, _persona("persona-a", harness=False))
    assert response.status_code == 201
    agent = response.json()["agent"]
    assert agent["memory"]["status"] == "degraded" and "memory down" in agent["memory"]["reason"]
    soul = (root / "persona-a" / "SOUL.md").read_text(encoding="utf-8")
    assert "harness_not_initialized" in soul and "Version:" not in soul


def test_rejects_reserved_ids_and_foreign_workspace(env):
    root, _, client = env
    for pid in ("main", "agora-servant-" + "0" * 20, "persona-opinion-x"):
        assert _post(client, root, _persona(pid)).status_code == 422
    foreign = client.post(
        "/api/openclaw-adapter/agents/persona/ensure", headers=HEADERS,
        json={"persona": _persona("persona-a"), "workspace_ref": str(root / "persona-b")},
    )
    assert foreign.status_code == 422


def test_route_requires_service_token(env):
    root, _, client = env
    headers = {k: v for k, v in HEADERS.items() if k != "X-Pantheon-Service-Token"}
    response = client.post("/api/openclaw-adapter/agents/persona/ensure", headers=headers,
                           json={"persona": _persona("persona-a"), "workspace_ref": str(root / "persona-a")})
    assert response.status_code in (401, 403)


def test_on_request_memory_refresh_before_a_cycle_turn_is_private_and_bound(env):
    root, _, client = env
    (root / "persona-a").mkdir(parents=True)
    memory = {"hits": [_hit("persona-a", "mem-new", "fresh lesson"), _hit("persona-b", "mem-x", "LEAK")]}
    with patch("integrations.openclaw.persona_memory_bridge.urllib.request.urlopen",
               return_value=_Memory(memory)):
        ok = client.post("/api/openclaw-adapter/agents/persona/memory/materialize", headers=HEADERS,
                         json={"persona_id": "persona-a", "workspace_ref": str(root / "persona-a")})
        foreign = client.post("/api/openclaw-adapter/agents/persona/memory/materialize", headers=HEADERS,
                              json={"persona_id": "persona-a", "workspace_ref": str(root / "persona-b")})
    assert ok.status_code == 200 and ok.json()["memory"]["hit_count"] == 1
    text = (root / "persona-a" / "MEMORY.md").read_text(encoding="utf-8")
    assert "persona_memory:mem-new" in text and "LEAK" not in text
    assert foreign.status_code == 400


def test_concurrent_persona_syncs_do_not_lose_each_others_tool_policy(env):
    """Registry read-modify-write is serialized: both profiles keep read+proposal, exec denied."""
    import threading
    import time

    root, cli, client = env
    def slow(args):
        proc = FakeCli.__call__(cli, args)  # snapshot is taken before the delay
        if args[:3] == ["openclaw", "config", "get"]:
            time.sleep(0.3)
        return proc

    results: dict = {}

    def run(pid):
        with patch("integrations.openclaw.persona_memory_bridge.urllib.request.urlopen",
                   return_value=_Memory({"hits": []})):
            results[pid] = _post(TestClient(adapter_main.app), root, _persona(pid)).status_code

    with patch.object(adapter_main, "_gateway_state_agent_runner", slow):
        threads = [threading.Thread(target=run, args=(p,)) for p in ("persona-a", "persona-b")]
        [t.start() for t in threads]
        [t.join(30) for t in threads]

    assert results == {"persona-a": 201, "persona-b": 201}
    by_id = {a["id"]: a for a in cli.agents}
    assert set(by_id) == {"persona-a", "persona-b"}
    for pid in by_id:
        tools = by_id[pid]["tools"]
        assert tools["allow"] == ["read", "persona_proposal_submit"]
        assert tools["exec"]["security"] == "deny"


def test_standalone_cli_reconcile_and_http_ensure_share_one_registry_lock(env, tmp_path, monkeypatch):
    """CLI (scripts/openclaw-sync-persona-agents.py) vs adapter HTTP: neither restores a stale snapshot."""
    import importlib.util
    import threading
    import time

    monkeypatch.setenv("PANTHEON_OPENCLAW_AGENT_REGISTRY_LOCK", str(tmp_path / "shared" / "registry.lock"))
    root, cli, client = env
    spec = importlib.util.spec_from_file_location(
        "cli_sync", ROOT / "scripts" / "openclaw-sync-persona-agents.py")
    cli_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli_mod)

    from integrations.openclaw import persona_agent_sync as lib

    def slow(args):
        proc = FakeCli.__call__(cli, args)  # snapshot taken before the delay
        if args[:3] == ["openclaw", "config", "get"]:
            time.sleep(0.3)
        return proc

    def cli_runner(personas, **kw):
        return lib.sync_persona_agents(
            personas, runner=slow,
            soul_writer=lambda w, s: None,  # default workspace root is gateway-only
            runtime_policy=lib.persona_agent_runtime_policy(),
        ).to_dict()

    results: dict = {}

    def run_cli():
        results["cli"] = cli_runner([_persona("persona-a")])

    def run_http():
        time.sleep(0.1)  # CLI is mid read-modify-write
        with patch("integrations.openclaw.persona_memory_bridge.urllib.request.urlopen",
                   return_value=_Memory({"hits": []})):
            results["http"] = _post(TestClient(adapter_main.app), root, _persona("persona-b")).status_code

    with patch.object(adapter_main, "_gateway_state_agent_runner", slow):
        threads = [threading.Thread(target=run_cli), threading.Thread(target=run_http)]
        [t.start() for t in threads]
        [t.join(30) for t in threads]

    assert results["cli"]["failed"] == [] and results["http"] == 201
    assert callable(cli_mod.reconcile)
    by_id = {a["id"]: a for a in cli.agents}
    assert set(by_id) == {"persona-a", "persona-b"}
    for agent in by_id.values():
        assert agent["tools"]["allow"] == ["read", "persona_proposal_submit"]
        assert agent["tools"]["exec"]["security"] == "deny"


def test_servant_ensure_initializes_fresh_nested_ledger_dir_and_replays(env, tmp_path):
    """The shared registry lock must not replace creating the idempotency DB parent."""
    root, cli, client = env
    nested = tmp_path / "fresh" / "nested" / "ledger" / "agents.sqlite3"
    pid = "agora-servant-0123456789abcdefabcd"
    payload = {
        "persona_registry_ref": f"persona:{pid}",
        "workspace_ref": str(root / pid),
        "capability_snapshot": {"allowed_capabilities": ["persona_opinion"],
                                "persona_class": "agora_servant"},
    }
    agent = {"status": "created", "agent_id": pid, "workspace_ref": payload["workspace_ref"]}
    assert not nested.parent.exists()
    with patch.object(adapter_main, "_OPENCLAW_AGENT_IDEMPOTENCY_DB", nested), patch.object(
        adapter_main, "ensure_agora_servant_agent", return_value=agent
    ) as ensure:
        first = client.post("/api/openclaw-adapter/agents/ensure", headers=HEADERS, json=payload)
        replay = client.post("/api/openclaw-adapter/agents/ensure", headers=HEADERS, json=payload)

    assert first.status_code == 201, first.text
    assert replay.status_code == 201, replay.text
    assert replay.json() == first.json()
    assert nested.exists()
    ensure.assert_called_once()
