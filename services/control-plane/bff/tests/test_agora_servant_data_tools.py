"""AGORA-SERVANT-DATA-TOOLS-20261127: servant read-only data tools that return cited facts.

SD_AGORA_SERVANT_CORE_2026-11-27 §4.3 and §10. These tests run the real BFF servant routes, the
real servant turn worker and, for broker-branch flow, the real source-ingest owner route over a
keyed ``tw_broker_top`` store. Only the language model is faked.
"""
from __future__ import annotations

import io
import json
import re
import time
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.agora.servant import data_tools
from services.control_plane.bff.agora.servant.data_tools import (
    FRESHNESS_STATES,
    MAX_TOOL_ROUNDS,
    TOOL_NAMES,
    ServantDataTools,
    SourceIngestToolClient,
    ToolContext,
)
from services.control_plane.bff.agora.servant.tool_turn import (
    PACKET_MARKER,
    REDACTED_NUMBER,
    SERVANT_TURN_SCHEMA,
    render_answer,
)
from services.control_plane.bff.agora.servant.turn_worker import ServantTurnWorker
from services.control_plane.bff.tests import test_agora_servant_sessions as sessions
from services.source_ingestion.routers.ingest_operations import create_ingest_operations_router
from services.source_ingestion.tests.test_broker_branch_flow_route import SECRET, _NoEvidence, _write_session

AUTH = sessions.AUTH
NOW = datetime(2026, 11, 27, 3, 0, tzinfo=timezone.utc)
SD_TOOL_SET = (
    "market_bars", "candidate_pool", "candidate_ranking", "broker_branch_flow", "disclosures", "news",
    "my_decisions", "my_journal", "my_shadow", "institutional_facts", "propose_research",
)


# --------------------------------------------------------------------------- helpers


class ScriptedModel(sessions.FakeOpenClawClient):
    """The only fake: a model that answers each structured round from a script."""

    def __init__(self, script: Callable[[dict[str, Any], int], dict[str, Any]]) -> None:
        super().__init__()
        self.script = script
        self.packets: list[dict[str, Any]] = []
        self.persona_calls: list[dict[str, Any]] = []

    def invoke_structured_extraction(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["extraction_schema"] == SERVANT_TURN_SCHEMA
        packet = json.loads(kwargs["prompt"].split(PACKET_MARKER, 1)[1])
        self.packets.append(packet)
        if self.fail_next_invoke:
            self.fail_next_invoke = False
            raise sessions.OpenClawOpsClientError("down", status_code=503, error_code="OPENCLAW_ADAPTER_UNREACHABLE")
        plan = {"action": "answer", "tool_calls": [], "answer_text": "", "cited_fact_ids": [], "missing_data": []}
        plan.update(self.script(packet, len(self.packets)))
        return {"status": "ok", "data": {"status": "completed", "output": {"structured_data": plan}}}

    def invoke_assistant_provider(self, **kwargs: Any) -> dict[str, Any]:
        # A persona consult or any free-form provider call would land here (SD §10: never by default).
        self.persona_calls.append(kwargs)
        raise AssertionError("the default servant turn must not call a persona or a free-form provider")


def _source_ingest_app(root: Path) -> TestClient:
    app = FastAPI()
    app.include_router(create_ingest_operations_router(
        SimpleNamespace(MARKET_DATA_STORAGE_ROOT=root, evidence_repository=_NoEvidence())
    ))
    return TestClient(app)


def _urlopen_into(app_client: TestClient, calls: list[str]):
    """Route the tool client's urllib GETs into an in-process owner app."""

    def urlopen(request, timeout=None):
        parts = urlsplit(request.full_url)
        calls.append(parts.path + ("?" + parts.query if parts.query else ""))
        response = app_client.get(parts.path + "?" + parts.query, headers=dict(request.header_items()))
        if response.status_code >= 400:
            raise urllib.error.HTTPError(request.full_url, response.status_code, "owner error", {}, io.BytesIO(response.content))
        return io.BytesIO(response.content)

    return urlopen


@pytest.fixture()
def broker_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PANTHEON_RUNTIME_JWT_SECRET", SECRET)
    root = tmp_path / "market"
    for day in ("2026-11-25", "2026-11-26"):
        _write_session(root, "2330", day)
    return root


def _tools(root: Path, owner_calls: list[str], **kw: Any) -> ServantDataTools:
    client = SourceIngestToolClient(
        base_url="http://source-ingest:8097", jwt_secret=SECRET, urlopen=_urlopen_into(_source_ingest_app(root), owner_calls)
    )
    return ServantDataTools(source_ingest=client, clock=lambda: NOW, **kw)


def _session(monkeypatch, tmp_path, model):
    client, _ = sessions._install_client(monkeypatch, tmp_path, fake=model)
    sessions._ensure_servant(client)
    return client, sessions._create_session(client)["data"]["session_id"]


def _send(client, session_id, content, key, context=None):
    body: dict[str, Any] = {"content": content}
    if context is not None:
        body["context"] = context
    return client.post(
        f"/bff/agora/servant/sessions/{session_id}/messages",
        json=body,
        headers={**AUTH, "Idempotency-Key": key, "X-Request-Id": f"req-{key}", "X-Trace-Id": f"trace-{key}"},
    )


def _run(client, model, tools):
    worker = ServantTurnWorker(owner=client.servant_owner, client_factory=lambda: model, tools=tools)
    worker.run_once()
    return worker


def _answer_event(client, session_id) -> dict[str, Any]:
    events = sessions._stream_events(client, session_id)
    done = [e for e in events if e["type"] in ("servant.message.completed", "servant.message.degraded")]
    assert done, events
    return done[-1]["data"]


def _stored_answer(client, session_id) -> dict[str, Any]:
    session = client.servant_owner.get_servant_session(session_id, tenant_id="pantheon-dev", user_id="agora-test-user")
    return [m for m in session["messages"] if m["role"] == "assistant"][-1]


def _numbers(text: str) -> list[str]:
    return re.findall(r"\d+(?:[.,:/\-]\d+)*", text)


# --------------------------------------------------------------------------- AC1: tool set + cited facts


def test_fixed_read_only_tool_set_matches_sd_4_3():
    assert TOOL_NAMES == SD_TOOL_SET
    assert not any("persona" in name or "order" in name or "meeting" in name for name in TOOL_NAMES)
    assert {t["name"] for t in ServantDataTools.catalog()} == set(SD_TOOL_SET)


def test_every_fact_is_cited_with_freshness(broker_store):
    calls: list[str] = []
    tools = _tools(broker_store, calls)
    result = tools.run("broker_branch_flow", {"symbols": ["2330"]}, ToolContext("pantheon-dev", "agora-test-user"))
    assert result.freshness == "fresh" and result.facts
    for fact in result.facts:
        assert {"value", "unit", "source_id", "as_of", "freshness", "query_hash"} <= set(fact)
        assert fact.get("connector_id") or fact.get("owner")
        assert fact["freshness"] in FRESHNESS_STATES
        assert fact["source_id"] and fact["query_hash"].startswith("sha256:")
    assert {f["as_of"] for f in result.facts} == {"2026-11-26"}


# --------------------------------------------------------------------------- AC3: bounded owner read


def test_broker_branch_flow_reads_keyed_dataset_through_bounded_owner_route(broker_store):
    calls: list[str] = []
    tools = _tools(broker_store, calls)
    ctx = ToolContext("pantheon-dev", "agora-test-user")
    result = tools.run(
        "broker_branch_flow",
        {"symbols": ["2330", "2317", "1101", "2454", "2603", "3008"], "side": "buy", "date_from": "2026-11-25"},
        ctx,
    )
    assert len(calls) == 1
    path, query = calls[0].split("?", 1)
    assert path == "/api/source-ingest/broker-branch-flow"  # never the tenant-wide source-records list
    assert "symbols=2330%2C2317%2C1101%2C2454%2C2603" in query and "side=buy" in query and "date_from=2026-11-25" in query
    assert {f["symbol"] for f in result.facts} == {"2330"}
    assert {f["as_of"] for f in result.facts} == {"2026-11-25", "2026-11-26"}
    assert all("買超" in f["label"] for f in result.facts)
    assert "3008" in result.missing and "2317" in result.missing  # skipped (cap 5) and without rows


def test_stale_and_unconfigured_sources_are_never_shown_as_fresh(broker_store):
    calls: list[str] = []
    later = ServantDataTools(source_ingest=_tools(broker_store, calls).source_ingest,
                             clock=lambda: datetime(2026, 12, 9, tzinfo=timezone.utc))
    stale = later.run("broker_branch_flow", {"symbols": ["2330"]}, ToolContext("t", "u"))
    assert stale.freshness == "stale" and {f["freshness"] for f in stale.facts} == {"stale"}

    unconfigured = ServantDataTools(source_ingest=SourceIngestToolClient(base_url="", token=""))
    for tool, args in (("broker_branch_flow", {"symbols": ["2330"]}), ("news", {"keyword": "台積電"}),
                       ("disclosures", {"symbol": "2330"}), ("market_bars", {"symbol": "2330"})):
        result = unconfigured.run(tool, args, ToolContext("t", "u"))
        assert result.freshness == "not_configured" and result.facts == [] and result.missing


def test_disclosure_and_news_reads_are_bounded_connector_pages():
    pages: list[dict[str, Any]] = []

    class Pages:
        configured = True

        def get(self, path, params, *, tenant_id):
            pages.append({"path": path, **params})
            newest = "2026-11-27T01:00:00Z"
            records = [
                {"source_id": f"src-{len(pages)}-{i}", "title": f"2330 重大訊息 {i}" if i % 2 == 0 else "其他公司",
                 "metadata": {"published_at": newest}, "created_at": newest}
                for i in range(200)
            ]
            return {"source_records": records, "next_page_token": "200"}

    tools = ServantDataTools(source_ingest=Pages(), clock=lambda: NOW)
    result = tools.run("disclosures", {"symbol": "2330"}, ToolContext("t", "u"))
    assert all(p["path"] == "/api/source-ingest/source-records" for p in pages)
    assert {p["connector_id"] for p in pages} == {"tw-mops-official-disclosures"}
    assert len(pages) == 1 and pages[0]["page_size"] == 200  # stops once 40 facts are kept
    assert result.freshness == "fresh" and len(result.facts) == 40
    assert all(f["connector_id"] == "tw-mops-official-disclosures" and f["unit"] == "text" for f in result.facts)

    pages.clear()
    news = tools.run("news", {"keyword": "沒有這個詞"}, ToolContext("t", "u"))
    assert len(pages) == 3  # bounded: at most 3 pages of 200, then report empty
    assert news.freshness == "empty_fresh" and news.facts == []


# --------------------------------------------------------------------------- AC5 + AC7 shape: 分析分點


def test_command_bar_analyze_broker_branches_runs_broker_flow_on_selected_symbols(monkeypatch, tmp_path, broker_store):
    owner_calls: list[str] = []

    def script(packet, n):
        # The model sees the facts the BFF already read and only writes prose with fact references.
        ids = [f["id"] for f in packet["facts"]]
        return {"answer_text": f"2330 最新交易日買超第一名淨買 {{{{{ids[0]}}}}}。", "cited_fact_ids": ids[:3]}

    model = ScriptedModel(script)
    client, session_id = _session(monkeypatch, tmp_path, model)
    context = {"page": "trading-room", "strategy_id": "strategy:alpha", "selected_symbols": ["2330.TW"], "quick_task": "分析分點"}
    accepted = _send(client, session_id, "分析分點", "cmd-001", context)
    assert accepted.status_code == 202, accepted.text
    _run(client, model, _tools(broker_store, owner_calls))

    # The command bar context reached the same route and the deterministic first round.
    assert owner_calls and owner_calls[0].startswith("/api/source-ingest/broker-branch-flow?symbols=2330")
    assert model.packets[0]["command_context"]["quick_task"] == "analyze_broker_branches"
    assert model.packets[0]["tool_rounds_used"] == 1 and model.packets[0]["facts"]

    event = _answer_event(client, session_id)
    assert event["provider_status"] == "completed" and event["answer_mode"] == "model"
    assert "60,000股" in event["answer"]  # BuyBroker-01: 70,000 - 10,000, rendered by the BFF
    citations = event["citations"]
    assert citations and {c["as_of"] for c in citations} == {"2026-11-26"}  # latest session only
    assert {c["connector_id"] for c in citations} == {"tw-finmind-broker-daily-report"}
    assert sum(1 for c in citations if c["cited"]) >= 1
    assert all(c["source_id"] and c["freshness"] == "fresh" for c in citations)
    accepted_event = [e for e in sessions._stream_events(client, session_id) if e["type"] == "servant.message.accepted"][-1]
    assert accepted_event["data"]["context"]["selected_symbols"] == ["2330"]
    assert model.persona_calls == []


def test_analyze_broker_branches_still_returns_rows_when_the_model_is_down(monkeypatch, tmp_path, broker_store):
    model = ScriptedModel(lambda packet, n: {})
    model.fail_next_invoke = True
    client, session_id = _session(monkeypatch, tmp_path, model)
    _send(client, session_id, "分析分點", "cmd-down", {"selected_symbols": ["2330"], "quick_task": "analyze_broker_branches"})
    _run(client, model, _tools(broker_store, []))
    event = _answer_event(client, session_id)
    assert event["answer_mode"] == "facts_only" and event["citations"]
    assert "tw-finmind-broker-daily-report" in event["answer"] and "2026-11-26" in event["answer"]


# --------------------------------------------------------------------------- AC2: rounds + numbers


def test_at_most_four_tool_rounds_then_the_model_must_answer(monkeypatch, tmp_path, broker_store):
    owner_calls: list[str] = []

    def script(packet, n):
        if packet["must_answer_now"]:
            return {"answer_text": "資料已整理。"}
        return {"action": "call_tools", "tool_calls": [{"tool": "broker_branch_flow", "arguments": {"symbols": ["2330"]}}]}

    model = ScriptedModel(script)
    client, session_id = _session(monkeypatch, tmp_path, model)
    _send(client, session_id, "一直查分點", "rounds-001")
    _run(client, model, _tools(broker_store, owner_calls))
    assert len(owner_calls) == MAX_TOOL_ROUNDS == 4
    assert len(model.packets) == MAX_TOOL_ROUNDS + 1 and model.packets[-1]["must_answer_now"] is True
    meta = _stored_answer(client, session_id)["metadata"]["tool_turn"]
    assert meta["tool_rounds"] == 4 and meta["provider_calls"] == 5


def test_the_model_never_supplies_a_number_that_is_not_a_returned_fact(monkeypatch, tmp_path, broker_store):
    def script(packet, n):
        if n == 1:
            return {"action": "call_tools", "tool_calls": [{"tool": "broker_branch_flow", "arguments": {"symbols": ["2330"]}}]}
        first = packet["facts"][0]["id"]
        # Hallucinated numbers (99,999 shares, 12.5%, 2025-01-01) around one real fact reference.
        return {"answer_text": f"2330 外資買超 99,999 張、占比 12.5%，2025-01-01 起連買；第一名淨買 {{{{{first}}}}}；{{{{f999}}}}。"}

    model = ScriptedModel(script)
    client, session_id = _session(monkeypatch, tmp_path, model)
    _send(client, session_id, "2330 分點怎麼樣", "num-001")
    _run(client, model, _tools(broker_store, []))
    stored = _stored_answer(client, session_id)
    answer = stored["content"]
    fact_values = {f"{c['value']:,}" for c in stored["citations"] if isinstance(c["value"], int)}
    allowed = {"2330"} | {c["as_of"][:10] for c in stored["citations"]} | fact_values
    for token in _numbers(answer):
        assert token in allowed, (token, answer)
    assert "99,999" not in answer and "12.5" not in answer and "2025-01-01" not in answer
    assert answer.count(REDACTED_NUMBER) == 4 and "60,000股" in answer
    guard = stored["metadata"]["tool_turn"]["number_guard"]
    assert guard["passed"] is False and guard["removed_count"] == 3 and guard["unknown_fact_refs"] == ["f999"]


def test_render_answer_keeps_only_fact_rendered_numbers():
    facts = {"f1": {"value": 1234, "unit": "shares", "as_of": "2026-11-26", "symbol": "2330"}}
    text, used, guard = render_answer("{{f1}} 與 5678", facts, allowed_tokens={"2330"})
    assert text == f"1,234股 與 {REDACTED_NUMBER}" and used == ["f1"] and guard["removed_count"] == 1


# --------------------------------------------------------------------------- AC4: missing data


def test_uncovered_request_names_the_missing_data_and_never_answers_from_memory(monkeypatch, tmp_path, broker_store):
    def script(packet, n):
        if n == 1:
            return {"action": "call_tools", "tool_calls": [{"tool": "insider_trades", "arguments": {"symbol": "2330"}}]}
        # The model tries to answer from memory anyway.
        return {"answer_text": "台積電本益比約 25 倍，董監持股 6.4%。", "missing_data": ["內部人申報持股"]}

    model = ScriptedModel(script)
    client, session_id = _session(monkeypatch, tmp_path, model)
    _send(client, session_id, "台積電內部人最近有沒有賣股", "missing-001")
    _run(client, model, _tools(broker_store, []))
    event = _answer_event(client, session_id)
    assert "缺少的資料" in event["answer"] and "insider_trades" in event["answer"] and "內部人申報持股" in event["answer"]
    assert "不會用模型記憶當作資料回答" in event["answer"]
    assert "25" not in event["answer"] and "6.4" not in event["answer"]
    assert event["citations"] == [] and set(event["missing_data"]) >= {"內部人申報持股"}


def test_no_tool_and_no_model_answer_still_reports_missing(broker_store):
    from services.control_plane.bff.agora.servant.tool_turn import run_tool_turn

    tools = ServantDataTools(source_ingest=SourceIngestToolClient(base_url="", token=""))
    outcome = run_tool_turn(
        tools=tools,
        ctx=ToolContext("t", "u", command_context={"quick_task": "analyze_broker_branches", "selected_symbols": ["2330"]}),
        history=[{"role": "user", "content": "分析分點"}],
        invoke=lambda prompt: {"action": "answer", "tool_calls": [], "answer_text": "", "cited_fact_ids": [], "missing_data": []},
    )
    assert outcome.answer_mode == "facts_only" and outcome.facts == []
    assert "缺少的資料" in outcome.answer and "source-ingest read is not configured" in outcome.answer


# --------------------------------------------------------------------------- AC6: no persona by default (SD §10)


def test_default_flow_ask_with_data_makes_no_persona_or_meeting_call(monkeypatch, tmp_path, broker_store):
    def script(packet, n):
        if n == 1:
            return {"action": "call_tools", "tool_calls": [
                {"tool": "broker_branch_flow", "arguments": {"symbols": ["2330"]}},
                {"tool": "my_decisions", "arguments": {"limit": 3}},
            ]}
        return {"answer_text": f"已查到分點 {{{{{packet['facts'][0]['id']}}}}}。"}

    model = ScriptedModel(script)
    client, session_id = _session(monkeypatch, tmp_path, model)
    meeting_calls: list[Any] = []
    import services.control_plane.bff.agora.interaction.router as interaction_router

    for name in dir(interaction_router):
        if "consult" in name.lower() or "meeting" in name.lower():
            attr = getattr(interaction_router, name)
            if callable(attr):
                monkeypatch.setattr(interaction_router, name, lambda *a, _n=name, **k: meeting_calls.append(_n))
    _send(client, session_id, "2330 分點和我的決定", "persona-001")
    tools = _tools(broker_store, [], agora_owner=client.servant_owner)
    _run(client, model, tools)
    event = _answer_event(client, session_id)
    assert event["provider_status"] == "completed" and event["citations"]
    assert model.persona_calls == [] and meeting_calls == []
    assert _stored_answer(client, session_id)["metadata"]["tool_turn"]["persona_calls"] == 0
    # The only provider calls were the structured, tool-less turn rounds.
    assert len(model.packets) == 2


def test_turn_schema_has_no_persona_capability():
    text = json.dumps(SERVANT_TURN_SCHEMA)
    assert "persona" not in text and "meeting" not in text
    assert data_tools.TOOL_CATALOG and all("persona" not in t["name"] for t in data_tools.TOOL_CATALOG)


# --------------------------------------------------------------------------- worker wiring


def test_interaction_worker_wires_the_tool_set_to_its_owners(monkeypatch):
    root = Path(__file__).resolve().parents[4]
    monkeypatch.syspath_prepend(str(root / "services" / "control-plane" / "bff"))
    monkeypatch.syspath_prepend(str(root / "scripts"))
    import run_agora_interaction_worker as launcher

    owner, research, journal = object(), object(), object()
    tools = launcher.build_servant_data_tools(owner, research, journal)
    assert tools.agora_owner is owner and tools.research_store is research and tools.journal_reader is journal
    assert tools.institutional_reader is not None and tools.research_drafter is not None
    assert type(tools.source_ingest).__name__ == "SourceIngestToolClient"


def test_v1_14_bundle_records_the_servant_data_tools_contract():
    import hashlib

    root = Path(__file__).resolve().parents[4]
    bundle = json.loads((root / "services/control-plane/specs/agora/bundle_index.v1_14.json").read_text(encoding="utf-8"))
    openapi_path = root / "services/control-plane/openapi/agora_v1_14.openapi.yaml"
    cap_path = root / "services/control-plane/specs/agora/v15/capability_manifest_v1_14.json"
    assert bundle["openapi"]["sha256"] == hashlib.sha256(openapi_path.read_bytes()).hexdigest()
    assert bundle["files"]["specs/agora/v15/capability_manifest_v1_14.json"] == hashlib.sha256(cap_path.read_bytes()).hexdigest()
    assert bundle["route_families"]["agora.servant.data-tools.v1"] == [
        "POST /bff/agora/servant/sessions/{session_id}/messages",
        "GET /bff/agora/servant/sessions/{session_id}/stream",
    ]
    openapi = json.loads(openapi_path.read_text(encoding="utf-8"))
    schemas = openapi["components"]["schemas"]
    assert set(schemas["ServantCommandContext"]["properties"]) == {"page", "strategy_id", "widget_id", "selected_symbols", "quick_task"}
    assert schemas["ServantCitedFact"]["properties"]["freshness"]["enum"] == list(FRESHNESS_STATES)
    assert schemas["ServantCitedFact"]["properties"]["tool"]["enum"] == list(TOOL_NAMES)
    cap = json.loads(cap_path.read_text(encoding="utf-8"))
    assert "/api/source-ingest/broker-branch-flow" in cap["owner_contracts"]["source-ingest"]
