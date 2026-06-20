"""Unit tests for persona → OpenClaw agent sync (no live gateway)."""
from __future__ import annotations

import json
import subprocess
from typing import Any, Dict, List

import persona_agent_sync as sync


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
    # preferred_model honored only if in the known pool
    spec2 = sync.desired_agent_spec({**PERSONA_CRYPTO, "preferred_model": "openai/gpt-5.5"})
    assert spec2.model == "openai/gpt-5.5"
    spec3 = sync.desired_agent_spec({**PERSONA_CRYPTO, "preferred_model": "bogus/model"})
    assert spec3.model == sync.DEFAULT_PERSONA_MODEL


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
