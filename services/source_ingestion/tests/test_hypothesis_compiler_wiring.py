"""DISCOVERY-COMPILER-PRODUCTION-WIRING-20261111: compiler runs in the controller tick path."""

from __future__ import annotations

import copy
import json
import sqlite3
from typing import Any

import pytest

from services.source_ingestion import distillation_controller as dc
from services.source_ingestion import semantic_extraction_client as sec
from services.source_ingestion.controller_state import ControllerState, ControllerStateStore
from services.source_ingestion.distillation_controller import DistillationControllerConfig, run_controller_tick
from services.source_ingestion.hypothesis_compiler import compiler_from_env
from services.source_ingestion.tests.test_distillation_controller import DummyLoopWriter
from services.source_ingestion.tests.test_hypothesis_compiler import _proposal
from services.source_ingestion.tests.test_hypothesis_extraction import _card, _source

ENABLED = "PANTHEON_L2_EXTRACTION_ENABLED"


class _Calls:
    extract = 0
    compile = 0


def _install_fake_client(monkeypatch, proposal: dict[str, Any], testability: str) -> _Calls:
    calls = _Calls()
    card = _card(testability=testability)

    def transport(payload: dict[str, Any]) -> dict[str, Any]:
        if payload["prompt"].startswith("Propose a factor_rule_v1"):
            calls.compile += 1
            return {"output": {"structured_data": copy.deepcopy(proposal)}}
        calls.extract += 1
        s = {"is_abstained": False, "abstention_reason": None, "hypothesis_card": card}
        return {"output": {"structured_data": s, "usage": {"input_tokens": 1, "output_tokens": 1}}}

    real = sec.SemanticExtractionClient

    class Client(real):  # type: ignore[misc, valid-type]
        def __init__(self, **kw: Any) -> None:
            super().__init__(transport_fn=transport, **kw)

    monkeypatch.setattr(sec, "SemanticExtractionClient", Client)
    return calls


def _tick(tmp_path, monkeypatch, *, body=None):
    evidence = tmp_path / "evidence.jsonl"
    record = _source("src-1")
    if body is not None:
        record.metadata["body"] = body
    record.metadata["tenant_id"] = "test"
    evidence.write_text(json.dumps({"record_type": "source_record", "payload": record.to_dict()}) + "\n")
    config = DistillationControllerConfig(
        database_url="postgresql://x", registry_url="http://reg", interval_seconds=60, max_ticks=1,
        state_path=tmp_path / "state.json", alive_path=None, job_queue_path=tmp_path / "q.jsonl",
        seed_store_path=tmp_path / "seeds.jsonl", evidence_store_path=evidence, source_dirs=[tmp_path],
    )
    state = ControllerState(controller_id="c", controller_name="n", environment="test", tenant_id="test",
                            deployment={"git_sha": "s"})
    store = ControllerStateStore(config.state_path)
    store.save(state)
    registry: dict[str, dict] = {}
    monkeypatch.setattr(dc, "_get_registry_entry", lambda url, rid: registry.get(rid))

    def register(url: str, payload: dict) -> dict:
        md = dict(payload.get("metadata") or {})
        if isinstance(payload.get("strategy_spec"), dict):
            md.setdefault("strategy_spec", payload["strategy_spec"])
        entry = {**payload, "artifact_type": "strategy_spec", "artifact_state": "draft",
                 "lineage": {"parent_registry_ids": None, **dict(payload.get("lineage") or {}),
                             "source_strategy_spec_id": None}, "metadata": md}
        entry.pop("strategy_spec", None)
        entry.pop("source_digest", None)
        registry[payload["registry_id"]] = {"entry": entry}
        return {"entry": entry}

    monkeypatch.setattr(dc, "_register_strategy_spec_if_absent", register)
    res = run_controller_tick(config=config, state=state, store=store, writer=DummyLoopWriter())
    return res, config


@pytest.mark.parametrize("sector", [False, True])
def test_screen_entry_recording_through_environment_controller(tmp_path, monkeypatch, sector):
    from services.source_ingestion.hypothesis_compiler import CompilerStore
    from services.source_ingestion.tests.test_hypothesis_extraction import _screen_entry_recording
    from services.registry.candidate_pool import evaluate_screened_views
    from services.registry.test_candidate_pool import recorded_market

    recording = _screen_entry_recording()
    seen = []

    def transport(payload):
        seen.append(payload)
        if payload["prompt"].startswith("Propose a factor_rule_v1"):
            assert '"screen_idea"' in payload["prompt"] and '"entry_idea"' in payload["prompt"]
            return {"output": {"structured_data": copy.deepcopy(recording["proposal"])}}
        response = copy.deepcopy(recording["response"])
        if sector:
            response["output"]["structured_data"]["hypothesis_card"] = recording["sector_card"]
        return response

    real = sec.SemanticExtractionClient

    class Client(real):
        def __init__(self, **kwargs):
            super().__init__(transport_fn=transport, **kwargs)

    monkeypatch.setattr(sec, "SemanticExtractionClient", Client)
    monkeypatch.setenv(ENABLED, "1")
    res, config = _tick(tmp_path, monkeypatch, body=recording["stored_text"])
    store = CompilerStore(config.seed_store_path.with_name("hypothesis_compiler.sqlite"))
    assert all(p["agent_id"] == "structured-extraction" and "tools" not in p for p in seen)
    if sector:
        assert len(seen) == 1 and res["reconcile"]["created"] == 0
        assert [s for s, _ in _outcomes(config)] == ["needs_data"]
        assert store.drafts() == []
        # Industry is catalogued; the recorded net_inflow field is still unsupported.
        assert store.needs_data_report() == [
            {"dataset": "tw_sector_flows", "count": 1}]
    else:
        assert len(seen) == 2 and res["reconcile"]["created"] == 1
        artifact = store.drafts()[0]["artifact"]
        seeds = config.seed_store_path.read_text()
        assert artifact["screen_fingerprint"] in seeds and artifact["entry_fingerprint"] in seeds
        assert [s["kind"] for s in artifact["strategy_logic"]["screen"]["steps"]] == ["filter", "rank"]
        views = evaluate_screened_views(artifact, recorded_market(), "2026-09-02")
        assert views["2317.TW"]["candidate_pool_snapshot"]["members"] == ["2317.TW"]
        assert views["2317.TW"]["action"] == "BUY"
        assert views["2330.TW"]["hold_reason"] == "outside_candidate_pool"


def _outcomes(config) -> list[tuple[str, str]]:
    conn = sqlite3.connect(str(config.seed_store_path.with_name("hypothesis_compiler.sqlite")))
    return conn.execute("SELECT status, reason FROM compile_outcomes").fetchall()


def test_tick_compiles_testable_card_into_factor_rule(tmp_path, monkeypatch):
    monkeypatch.setenv(ENABLED, "1")
    monkeypatch.setenv("PANTHEON_L2_EXTRACTION_BUDGET_PATH", str(tmp_path / "ledger.sqlite"))
    calls = _install_fake_client(monkeypatch, _proposal(), "testable")
    res, config = _tick(tmp_path, monkeypatch)
    assert res["status"] == "success" and res["reconcile"]["created"] == 1
    assert calls.extract == 1 and calls.compile == 1
    seeds = [json.loads(l) for l in config.seed_store_path.read_text().splitlines() if l.strip()]
    text = json.dumps(seeds)
    assert "factor_rule_v1" in text and "rule_fingerprint" in text
    assert [s for s, _ in _outcomes(config)] == ["compiled"]


def test_tick_records_not_expressible_outcome(tmp_path, monkeypatch):
    monkeypatch.setenv(ENABLED, "1")
    monkeypatch.setenv("PANTHEON_L2_EXTRACTION_BUDGET_PATH", str(tmp_path / "ledger.sqlite"))
    calls = _install_fake_client(monkeypatch, _proposal(op="exec_python"), "testable")
    res, config = _tick(tmp_path, monkeypatch)
    assert res["reconcile"]["created"] == 0 and res["reconcile"]["skipped"] == 1
    assert calls.compile == 1
    assert [s for s, _ in _outcomes(config)] == ["not_expressible"]


def test_tick_records_needs_data_outcome(tmp_path, monkeypatch):
    monkeypatch.setenv(ENABLED, "1")
    monkeypatch.setenv("PANTHEON_L2_EXTRACTION_BUDGET_PATH", str(tmp_path / "ledger.sqlite"))
    _install_fake_client(monkeypatch, _proposal(), "needs_data:monthly_revenue")
    res, config = _tick(tmp_path, monkeypatch)
    assert res["reconcile"]["skipped"] == 1
    assert [s for s, _ in _outcomes(config)] == ["needs_data"]


def test_extraction_disabled_runs_neither_extraction_nor_compilation(tmp_path, monkeypatch):
    monkeypatch.delenv(ENABLED, raising=False)
    calls = _install_fake_client(monkeypatch, _proposal(), "testable")
    assert compiler_from_env() is None
    _tick(tmp_path, monkeypatch)
    assert calls.extract == 0 and calls.compile == 0
    assert not (tmp_path / "hypothesis_compiler.sqlite").exists()
    assert not (tmp_path / "ledger.sqlite").exists()


def test_compiler_transport_uses_restricted_structured_agent():
    from services.source_ingestion.hypothesis_compiler import client_proposer
    from services.source_ingestion.semantic_extraction_client import SemanticExtractionClient

    seen = []

    def transport(payload):
        seen.append(payload)
        return {"output": {"structured_data": {"outcome": "not_expressible", "reason": "x"}}}

    proposer = client_proposer(SemanticExtractionClient(transport_fn=transport))
    proposer({"claim": "c", "cited_spans": [{}]}, {})
    assert seen and seen[0]["agent_id"] == "structured-extraction"


def test_proposer_failure_is_retried_not_materialized():
    from services.source_ingestion.hypothesis_compiler import CompilerStore, HypothesisCompiler

    def boom(card, cat):
        raise RuntimeError("422")

    out = HypothesisCompiler(boom, CompilerStore(":memory:")).compile({"testability": "testable", "claim": "c"})
    assert out.status == "skipped" and out.reason.startswith("proposer_failed")


def test_authenticated_adapter_contract_controller_tick_compiles(tmp_path, monkeypatch):
    """Real client HTTP dispatch -> real adapter app with service auth enforced (no transport injection)."""
    import sys
    from pathlib import Path
    from unittest.mock import patch

    adapter_dir = Path(__file__).resolve().parents[2] / "openclaw-gateway-adapter"
    monkeypatch.syspath_prepend(str(adapter_dir))
    for stale in ("main",):
        if stale in sys.modules and not hasattr(sys.modules[stale], "_ASSISTANT_SERVICE_TOKEN"):
            monkeypatch.delitem(sys.modules, stale)
    import main as adapter_main
    from fastapi.testclient import TestClient

    tc = TestClient(adapter_main.app, raise_server_exceptions=True)
    token = "adapter-test-secret"
    monkeypatch.setenv(ENABLED, "1")
    monkeypatch.setenv("PANTHEON_L2_EXTRACTION_BUDGET_PATH", str(tmp_path / "ledger.sqlite"))
    monkeypatch.setenv("PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL", "http://adapter.test")
    monkeypatch.setenv("PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN", token)

    calls = _Calls()
    card = _card(testability="testable")
    proposal = _proposal()

    class _Result:
        def __init__(self, data):
            self._data = data

        def to_dict(self):
            return {"provider": "openclaw", "mode": "user", "status": "completed",
                    "output": {"structured_data": self._data, "usage": {"input_tokens": 1, "output_tokens": 1}}}

    def fake_invoke(prompt, **kw):
        assert kw["agent_id"] == adapter_main.OPENCLAW_STRUCTURED_AGENT_ID
        if prompt.startswith("Propose a factor_rule_v1"):
            calls.compile += 1
            return _Result(copy.deepcopy(proposal))
        calls.extract += 1
        return _Result({"is_abstained": False, "abstention_reason": None, "hypothesis_card": card})

    seen_status: list[int] = []

    def bridge(endpoint, *, body_bytes, headers, deadline_at, max_body_bytes=10_000_000):
        path = "/" + endpoint.split("://", 1)[1].split("/", 1)[1]
        resp = tc.post(path, content=body_bytes, headers=headers)
        seen_status.append(resp.status_code)
        return resp.status_code, resp.content, {}

    snapshot = {"valid": True, "config": {"agents": {
        "defaults": {"cliBackends": {"claude-cli": {
            "args": adapter_main._CLAUDE_CLI_TOOLSEARCH_ARGS,
            "resumeArgs": adapter_main._CLAUDE_CLI_TOOLSEARCH_RESUME_ARGS}}},
        "list": [{"id": adapter_main.OPENCLAW_STRUCTURED_AGENT_ID,
                  "tools": {"deny": ["*"], "exec": {"security": "deny", "ask": "always"}}}]}}}
    provider = adapter_main._OPENCLAW_AGENT_PROVIDER
    monkeypatch.setattr(sec, "_perform_http_request", bridge)
    with (
        patch.object(adapter_main, "_ASSISTANT_SERVICE_TOKEN", token),
        patch.object(adapter_main, "_ASSISTANT_SERVICE_AUTH_REQUIRED", True),
        patch.object(provider, "_gateway_call", return_value=snapshot),
        patch.object(provider, "invoke_structured", side_effect=fake_invoke),
    ):
        res, config = _tick(tmp_path, monkeypatch)
    assert 401 not in seen_status and seen_status
    assert res["reconcile"]["created"] == 1
    assert calls.extract == 1 and calls.compile == 1
    assert [s for s, _ in _outcomes(config)] == ["compiled"]
