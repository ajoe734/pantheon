"""Unit tests for persona → OpenClaw agent sync (no live gateway)."""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List

import persona_agent_sync as sync
from integrations.openclaw.adapter.agora_servant import (
    AgoraServantAgentSyncError,
    ensure_agora_servant_agent,
)


def _cp(stdout: str = "", returncode: int = 0, stderr: str = "") -> "subprocess.CompletedProcess[str]":
    return subprocess.CompletedProcess(args=["openclaw"], returncode=returncode, stdout=stdout, stderr=stderr)


PERSONA_CRYPTO = {
    "id": "persona-crypto",
    "name": "Crypto Persona",
    "mandate": "systematic_crypto_trading",
    "strategy_family": "momentum",
    "lifecycle_state": "paper_running",
}


def test_soul_is_persona_specific_not_management():
    soul = sync.build_persona_soul(PERSONA_CRYPTO)
    assert "Crypto Persona" in soul and "persona-crypto" in soul
    assert "systematic_crypto_trading" in soul and "momentum" in soul
    # Must NOT be the management assistant; must keep guardrails + Chinese.
    assert "NOT the Management AI" in soul
    assert "Paper unless a live capital binding" in soul
    assert "繁體中文" in soul


def test_soul_includes_rich_traits_when_present():
    rich = {
        **PERSONA_CRYPTO,
        "traits": {
            "instruments": ["BTC", "ETH"],
            "risk_appetite": "moderate; max 2% per trade",
            "decision_style": "systematic, signal-driven",
            "time_horizon": "swing (days)",
            "hard_rules": "no leverage > 3x; flat on signal loss",
            "persona_voice": "terse, quantitative",
        },
    }
    soul = sync.build_persona_soul(rich)
    assert "Your trading character" in soul
    assert "BTC, ETH" in soul  # list flattened
    assert "max 2% per trade" in soul
    assert "systematic, signal-driven" in soul
    assert "no leverage > 3x" in soul
    assert "terse, quantitative" in soul


def test_soul_marks_missing_traits_honestly():
    soul = sync.build_persona_soul(PERSONA_CRYPTO)  # no traits
    assert "No detailed traits set yet" in soul
    assert "tell the operator" in soul


def test_traits_read_from_top_level_or_traits_dict():
    # top-level field also works (not only nested traits dict)
    soul = sync.build_persona_soul({**PERSONA_CRYPTO, "instruments": "gold futures (GC)"})
    assert "gold futures (GC)" in soul


def test_desired_spec_routes_and_models():
    spec = sync.desired_agent_spec(PERSONA_CRYPTO)
    assert spec.persona_id == "persona-crypto"
    assert spec.workspace.endswith("/persona-crypto")
    assert spec.model == sync.DEFAULT_PERSONA_MODEL  # no preferred_model => default
    assert spec.sync_generation == 1
    # preferred_model honored only if in the runtime profile provider pool
    spec2 = sync.desired_agent_spec({**PERSONA_CRYPTO, "preferred_model": "openai/gpt-5.5"})
    assert spec2.model == "openai/gpt-5.5"
    try:
        sync.desired_agent_spec({**PERSONA_CRYPTO, "preferred_model": "bogus/model"})
        assert False, "invalid model ref must fail closed"
    except ValueError as exc:
        assert "unknown_model_ref" in str(exc)


def test_desired_spec_accepts_route_policy_hard_pin():
    spec = sync.desired_agent_spec(
        PERSONA_CRYPTO,
        route_policy={"model_routing": {"mode": "hard_pin", "model": "openai/gpt-5.5"}},
    )
    assert spec.model == "openai/gpt-5.5"


def _load_deploy_script():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "openclaw-sync-persona-agents.py"
    spec = importlib.util.spec_from_file_location("openclaw_sync_persona_agents_script", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_deploy_script_soul_matches_shared_renderer():
    deploy_script = _load_deploy_script()
    assert deploy_script.build_soul(PERSONA_CRYPTO) == sync.build_persona_soul(PERSONA_CRYPTO)
    assert "## Memory" in deploy_script.build_soul(PERSONA_CRYPTO)


def test_deploy_script_materializes_memory_when_configured(monkeypatch, tmp_path):
    deploy_script = _load_deploy_script()
    called: Dict[str, Any] = {}

    class Result:
        def to_dict(self):
            return {"hit_count": 1}

    def fake_materialize(**kwargs):
        called.update(kwargs)
        return Result()

    deploy_script._shared_materialize_memory = fake_materialize
    monkeypatch.setenv("PANTHEON_MEMORY_API_URL", "http://memory-service")
    report = {"created": [], "updated": [], "memory_materialized": [], "failed": []}

    deploy_script.record_memory_materialization(report, "persona-crypto", str(tmp_path / "workspace"))

    assert report["memory_materialized"] == ["persona-crypto"]
    assert report["failed"] == []
    assert called["memory_api_url"] == "http://memory-service"
    assert called["persona_id"] == "persona-crypto"
    assert called["workspace"].endswith("workspace")


def test_sync_creates_missing_agent_and_writes_soul():
    calls: List[List[str]] = []
    souls: Dict[str, str] = {}

    def runner(args: List[str]):
        calls.append(args)
        if args[:3] == ["openclaw", "agents", "list"]:
            return _cp(json.dumps({"agents": [{"id": "main"}]}))  # persona-crypto absent
        return _cp("{}")

    def soul_writer(ws: str, soul: str):
        souls[ws] = soul

    report = sync.sync_persona_agents([PERSONA_CRYPTO], runner=runner, soul_writer=soul_writer)
    assert report.created == ["persona-crypto"]
    assert report.updated == [] and report.failed == []
    # A real `agents add` was issued with the persona id + its own workspace/model.
    add = next(c for c in calls if c[:3] == ["openclaw", "agents", "add"])
    assert "persona-crypto" in add and "--workspace" in add and "--model" in add
    # SOUL written to the agent's workspace.
    assert any(ws.endswith("/persona-crypto") for ws in souls)
    assert "Crypto Persona" in next(iter(souls.values()))


def test_sync_uses_route_policy_resolver_for_new_agent_model():
    calls: List[List[str]] = []

    def runner(args: List[str]):
        calls.append(args)
        if args[:3] == ["openclaw", "agents", "list"]:
            return _cp(json.dumps({"agents": [{"id": "main"}]}))
        return _cp("{}")

    report = sync.sync_persona_agents(
        [PERSONA_CRYPTO],
        runner=runner,
        soul_writer=lambda ws, s: None,
        route_policy_resolver=lambda persona: {
            "model_routing": {"mode": "hard_pin", "model": "openai/gpt-5.5"}
        },
    )

    assert report.created == ["persona-crypto"]
    add = next(c for c in calls if c[:3] == ["openclaw", "agents", "add"])
    assert add[add.index("--model") + 1] == "openai/gpt-5.5"


def test_sync_materializes_memory_after_agent_create():
    calls: List[List[str]] = []
    materialized: List[tuple[str, str, str]] = []

    def runner(args: List[str]):
        calls.append(args)
        if args[:3] == ["openclaw", "agents", "list"]:
            return _cp(json.dumps({"agents": [{"id": "main"}]}))
        return _cp("{}")

    def materializer(workspace: str, persona: Dict[str, Any], spec: sync.PersonaAgentSpec):
        materialized.append((workspace, persona["id"], spec.persona_id))

    report = sync.sync_persona_agents(
        [PERSONA_CRYPTO],
        runner=runner,
        soul_writer=lambda ws, s: None,
        memory_materializer=materializer,
    )

    assert report.created == ["persona-crypto"]
    assert report.memory_materialized == ["persona-crypto"]
    assert materialized == [(sync.PERSONA_WORKSPACE_ROOT + "/persona-crypto", "persona-crypto", "persona-crypto")]


def test_sync_blocks_existing_agent_model_drift_without_set_model_support():
    calls: List[List[str]] = []
    souls: Dict[str, str] = {}

    def runner(args: List[str]):
        calls.append(args)
        if args[:3] == ["openclaw", "agents", "list"]:
            return _cp(json.dumps({"agents": [{"id": "persona-crypto", "model": "openai/gpt-5.5"}]}))
        return _cp("{}")

    report = sync.sync_persona_agents([PERSONA_CRYPTO], runner=runner, soul_writer=lambda ws, s: souls.setdefault(ws, s))

    assert report.failed and report.failed[0]["persona_id"] == "persona-crypto"
    assert report.failed[0]["error"] == "model_drift_update_unavailable"
    assert report.failed[0]["desired_model"] == sync.DEFAULT_PERSONA_MODEL
    assert not any(c[:3] == ["openclaw", "agents", "set-identity"] for c in calls)
    assert souls == {}


def test_sync_records_existing_agent_set_identity_failure():
    def runner(args: List[str]):
        if args[:3] == ["openclaw", "agents", "list"]:
            return _cp(json.dumps({"agents": [{"id": "persona-crypto", "model": sync.DEFAULT_PERSONA_MODEL}]}))
        return _cp("", returncode=1, stderr="cannot set identity")

    report = sync.sync_persona_agents([PERSONA_CRYPTO], runner=runner, soul_writer=lambda ws, s: None)

    assert report.updated == []
    assert report.failed and report.failed[0]["persona_id"] == "persona-crypto"
    assert "cannot set identity" in report.failed[0]["error"]


def test_sync_updates_existing_agent_idempotently():
    calls: List[List[str]] = []
    souls: Dict[str, str] = {}

    def runner(args: List[str]):
        calls.append(args)
        if args[:3] == ["openclaw", "agents", "list"]:
            return _cp(json.dumps({"agents": [{"id": "main"}, {"id": "persona-crypto"}]}))
        return _cp("{}")

    report = sync.sync_persona_agents([PERSONA_CRYPTO], runner=runner, soul_writer=lambda ws, s: souls.setdefault(ws, s))
    assert report.updated == ["persona-crypto"] and report.created == []
    # Existing agent => no `agents add`, but a set-identity + SOUL refresh.
    assert not any(c[:3] == ["openclaw", "agents", "add"] for c in calls)
    assert any(c[:3] == ["openclaw", "agents", "set-identity"] for c in calls)
    assert souls  # SOUL refreshed so registry edits propagate


def test_sync_records_failure_without_raising():
    def runner(args: List[str]):
        if args[:3] == ["openclaw", "agents", "list"]:
            return _cp(json.dumps({"agents": [{"id": "main"}]}))
        return _cp("", returncode=1, stderr="boom")

    report = sync.sync_persona_agents([PERSONA_CRYPTO], runner=runner, soul_writer=lambda ws, s: None)
    assert report.created == []
    assert report.failed and report.failed[0]["persona_id"] == "persona-crypto"
    assert "boom" in report.failed[0]["error"]


def test_human_list_fallback_parsing():
    def runner(args: List[str]):
        if args[:3] == ["openclaw", "agents", "list"]:
            return _cp("- main (default)\n- persona-crypto\n")  # non-JSON human output
        return _cp("{}")

    report = sync.sync_persona_agents([PERSONA_CRYPTO], runner=runner, soul_writer=lambda ws, s: None)
    assert report.updated == ["persona-crypto"]  # parsed from human lines => treated as existing


def test_agora_servant_adapter_returns_agent_projection():
    def runner(args: List[str]):
        if args[:3] == ["openclaw", "agents", "list"]:
            return _cp(json.dumps({"agents": [{"id": "main"}]}))
        return _cp("{}")

    result = ensure_agora_servant_agent(PERSONA_CRYPTO, runner=runner, soul_writer=lambda ws, s: None)
    assert result["status"] == "created"
    assert result["agent_id"] == "persona-crypto"
    assert result["model_id"] == "openclaw/persona-crypto"
    assert result["workspace_ref"].endswith("/persona-crypto")


def test_agora_servant_adapter_raises_on_failed_sync():
    def runner(args: List[str]):
        if args[:3] == ["openclaw", "agents", "list"]:
            return _cp(json.dumps({"agents": [{"id": "main"}]}))
        return _cp("", returncode=1, stderr="cannot add")

    try:
        ensure_agora_servant_agent(PERSONA_CRYPTO, runner=runner, soul_writer=lambda ws, s: None)
        assert False, "expected servant sync failure"
    except AgoraServantAgentSyncError as exc:
        assert "cannot add" in str(exc)
