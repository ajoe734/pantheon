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


def _tick(tmp_path, monkeypatch):
    evidence = tmp_path / "evidence.jsonl"
    record = _source("src-1")
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
