"""Real worker/SQLite/extractor replay; synthetic HTTP and recorded model responses.

No live provider, market return or hosted-delivery claim is made here.
"""
from __future__ import annotations

import copy
import ast
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import json
import re
from pathlib import Path

import httpx
import pytest

from services.source_ingestion import hypothesis_card
from services.source_ingestion.demand_priority import DemandPriority, ResearchDemandReader, vocabulary_terms
from services.source_ingestion.distillation_worker import (
    DEFER_TTL_SECONDS, DistillationError, DistillationJobQueue, make_distillation_worker,
)
from services.source_ingestion.connectors.base import SourceRecord
from services.source_ingestion.hypothesis_card import ExtractionLedger, HypothesisExtractor
from services.source_ingestion.semantic_extraction_client import SemanticExtractionClient

START = datetime(2026, 11, 13, 10, tzinfo=timezone.utc)
TENANT = "tenant-a"


def demand(**updates):
    return {"schema_version": "research_demand.v1", "tenant_id": TENANT,
            "status": "open", "mandate": "momentum equity",
            "expires_at": "2026-12-10T00:00:00Z", **updates}


def response(rows, code=200):
    return httpx.Response(code, json={"demands": rows})


def credentials(monkeypatch):
    monkeypatch.delenv("DISTILLATION_RESEARCH_SERVICE_TOKEN_FILE", raising=False)
    monkeypatch.setenv("DISTILLATION_RESEARCH_SERVICE_TOKEN", "synthetic-reader-token")


def test_fixed_vocabulary_boundaries_and_no_free_text_terms():
    assert vocabulary_terms("momentum MOMENTUM mean-reversion mean_reversion") == {"momentum", "mean_reversion"}
    assert vocabulary_terms("bonus values cryptocurrency injected-shell-command") == set()
    assert "均值回歸" in vocabulary_terms("台股均值回歸")
    priority = DemandPriority(frozenset({"momentum", "equity"}))
    assert priority.score({"title": "momentum " * 100, "metadata": {"abstract": "equity"}}) == 2
    assert priority.score({"content_ref": "https://momentum/equity", "metadata": {"route": "momentum"}}) == 0


def test_authenticated_scoped_read_uses_actual_persona_mandate_contract(monkeypatch):
    credentials(monkeypatch)
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return response([demand(), demand(status="satisfied", mandate="carry"),
                         demand(expires_at="2026-11-01T00:00:00Z", mandate="value")])

    monkeypatch.setattr(httpx, "get", get)
    priority = ResearchDemandReader("http://research.test").read(TENANT, now=START.timestamp())
    assert priority == DemandPriority(frozenset({"momentum", "equity"}), "open_demand")
    url, args = calls[0]
    assert url == "http://research.test/api/research/persona-demands"
    assert args["params"] == {"status": "open"}
    assert args["headers"] == {"Authorization": "Bearer synthetic-reader-token", "X-Tenant-ID": TENANT}
    assert args["timeout"] == 5.0 and args["follow_redirects"] is False


@pytest.mark.parametrize("rows,code,reason", [
    ([], 200, "no_open_demand"), ([], 503, "demand_read_http_503"),
    ([], 302, "demand_read_http_302"), ([], 401, "demand_read_http_401"),
    ([demand(tenant_id="foreign")], 200, "demand_read_tenant_mismatch"),
    ([demand(expires_at="invalid")], 200, "demand_read_unavailable:ValueError"),
])
def test_reads_fail_closed_to_arrival_order(monkeypatch, rows, code, reason):
    credentials(monkeypatch)
    monkeypatch.setattr(httpx, "get", lambda *a, **k: response(rows, code))
    assert ResearchDemandReader().read(TENANT, now=START.timestamp()) == DemandPriority(reason=reason)


def test_unreachable_read_records_sanitized_reason(monkeypatch):
    credentials(monkeypatch)

    def unavailable(*a, **k):
        raise httpx.ConnectError("secret headers or token must not reach the queue")

    monkeypatch.setattr(httpx, "get", unavailable)
    assert ResearchDemandReader().read(TENANT, now=START.timestamp()) == DemandPriority(
        reason="demand_read_unavailable:ConnectError")


def test_missing_credential_and_rotating_file_fail_closed(tmp_path, monkeypatch):
    credentials(monkeypatch)
    monkeypatch.delenv("DISTILLATION_RESEARCH_SERVICE_TOKEN")
    monkeypatch.setattr(httpx, "get", lambda *a, **k: pytest.fail("anonymous read"))
    assert ResearchDemandReader().read(TENANT, now=START.timestamp()).reason == "demand_read_missing_credential"
    assert ResearchDemandReader().read(" ", now=START.timestamp()).reason == "demand_read_missing_tenant"
    path = tmp_path / "token"
    path.write_text("rotated-one")
    path.chmod(0o600)
    monkeypatch.setenv("DISTILLATION_RESEARCH_SERVICE_TOKEN_FILE", str(path))
    headers = []
    monkeypatch.setattr(httpx, "get", lambda *a, **k: headers.append(k["headers"]) or response([]))
    reader = ResearchDemandReader()
    reader.read(TENANT, now=START.timestamp())
    path.write_text("rotated-two")
    reader.read(TENANT, now=START.timestamp())
    assert [h["Authorization"] for h in headers] == ["Bearer rotated-one", "Bearer rotated-two"]
    path.unlink()
    assert reader.read(TENANT, now=START.timestamp()).reason == "demand_read_unavailable:RuntimeError"
    assert len(headers) == 2


def replay_worker(tmp_path, clock, monkeypatch, *, cap=2):
    """Use the controller's production factory, real extractor and durable queue."""
    recording = json.loads((Path(__file__).parent / "fixtures/hypothesis_screen_entry_recorded.json").read_text())
    model_calls = []

    def model(payload):
        # Hand-authored model fixture, with citations rebound to each real record.
        sid = ast.literal_eval(re.search(r"source_record_id=(.*?);", payload["prompt"]).group(1))
        model_calls.append({**payload, "source_id": sid})
        result = copy.deepcopy(recording["response"])
        card = result["output"]["structured_data"]["hypothesis_card"]
        for idea in (card, card["screen_idea"], card["entry_idea"]):
            for span in idea["cited_spans"]:
                span["source_record_id"] = sid
        return result

    monkeypatch.setattr(hypothesis_card, "_today", lambda: clock[0].strftime("%Y-%m-%d"))
    queue = DistillationJobQueue(tmp_path / "queue.sqlite", now=lambda: clock[0].timestamp())
    ledger = ExtractionLedger(tmp_path / "ledger.sqlite", daily_cap=cap)
    extractor = HypothesisExtractor(SemanticExtractionClient(transport_fn=model), ledger)
    worker = make_distillation_worker(job_queue=queue, seed_store_path=tmp_path / "seeds.jsonl",
                                      hypothesis_extractor=extractor)
    return worker, queue, ledger, model_calls, recording["stored_text"]


def source(sid, text, title="unrelated", tenant=TENANT):
    return SourceRecord(source_id=sid, connector_id="test-replay", source_type="paper", title=title,
                        content_ref="https://example.test/" + sid, status="normalized",
                        metadata={"tenant_id": tenant, "license_scope": "collector_public_web",
                                  "access_scope": ["research"], "body": text})


@pytest.mark.parametrize("mode", ["demand", "empty", "outage"])
def test_cap_two_five_records_across_windows_real_worker_queue_and_extractor(tmp_path, monkeypatch, mode):
    credentials(monkeypatch)

    def read(*a, **kwargs):
        if mode == "outage":
            raise httpx.ConnectError("offline")
        return response([demand()] if mode == "demand" else [])

    monkeypatch.setattr(httpx, "get", read)
    clock = [START]
    worker, queue, ledger, calls, text = replay_worker(tmp_path, clock, monkeypatch)
    titles = ["first", "second", "third", "momentum", "momentum equity"]
    for i, title in enumerate(titles):
        queue.enqueue_source_record(source(str(i), text, title), enqueued_at=START.timestamp() + i)
    clock[0] += timedelta(seconds=10)
    first = worker.catch_up([source(str(i), text, title) for i, title in enumerate(titles)], tenant_id=TENANT)
    assert (first.created, first.deferred, first.skipped) == (2, 3, 0)
    assert [c["source_id"] for c in calls] == ["0", "1"]  # New work always FIFO.
    since = queue.get("2").deferred_since
    assert queue.get("2").attempts == 0
    assert queue.get("2").next_attempt_at == "2026-11-14T00:00:00Z"
    assert worker.run_pending(tenant_id=TENANT).processed == 0  # no same-window spin
    assert ledger.calls_today() == 2

    # Reconstruct queue, ledger and production worker: deferral is not process state.
    clock[0] = START + timedelta(days=1)
    worker, queue, ledger, second_calls, _ = replay_worker(tmp_path, clock, monkeypatch)
    queue.enqueue_source_record(source("new", text, "momentum equity macro"), enqueued_at=clock[0])
    second = worker.run_pending(tenant_id=TENANT)
    expected = ["4", "3"] if mode == "demand" else ["2", "3"]
    assert [c["source_id"] for c in second_calls] == expected
    assert (second.created, second.deferred, second.skipped) == (2, 2, 0)
    assert ledger.calls_today() == 2 and queue.get("new").status == "retry_wait"
    reason = {"demand": "open_demand", "empty": "no_open_demand",
              "outage": "demand_read_unavailable:ConnectError"}[mode]
    assert queue.get(expected[0]).priority_reason == reason
    if mode == "demand":
        assert queue.get("4").demand_score > queue.get("3").demand_score
        assert queue.get("2").deferred_since == since  # Second cap hit doesn't reset TTL.
    assert not queue.list_dead_letters()

    # With no open demand the remaining deferred rows retain arrival order.
    monkeypatch.setattr(httpx, "get", lambda *a, **k: response([]))
    clock[0] += timedelta(days=1)
    third = worker.run_pending(tenant_id=TENANT)
    assert third.created == 2 and third.deferred == 0
    assert [c["source_id"] for c in second_calls][-2:] == (["2", "new"] if mode == "demand" else ["4", "new"])
    assert all(j.status == "done" for j in queue.list_all())


def test_expiry_after_fourteen_days_is_durable_and_costs_no_model_call(tmp_path, monkeypatch):
    credentials(monkeypatch)
    monkeypatch.setattr(httpx, "get", lambda *a, **k: response([]))
    clock = [START]
    worker, queue, _, calls, text = replay_worker(tmp_path, clock, monkeypatch, cap=0)
    queue.enqueue_source_record(source("expires", text))
    assert worker.run_pending(tenant_id=TENANT).deferred == 1
    since = queue.get("expires").deferred_since
    for day in range(1, 14):
        clock[0] = START + timedelta(days=day)
        assert worker.run_pending(tenant_id=TENANT).deferred == 1
        assert queue.get("expires").deferred_since == since and queue.get("expires").attempts == 0
    clock[0] = START + timedelta(seconds=DEFER_TTL_SECONDS)
    worker, queue, _, calls, _ = replay_worker(tmp_path, clock, monkeypatch, cap=0)
    result = worker.run_pending(tenant_id=TENANT)
    assert result.skipped == 1 and result.deferred == 0 and calls == []
    assert queue.get("expires").skip_reason == "budget_deferral_expired: 14 days since first cap deferral"
    assert worker.run_pending(tenant_id=TENANT).processed == 0


def test_scoped_run_never_reads_or_claims_foreign_records(tmp_path, monkeypatch):
    credentials(monkeypatch)
    tenants = []
    monkeypatch.setattr(httpx, "get", lambda *a, **k: tenants.append(k["headers"]["X-Tenant-ID"]) or response([]))
    clock = [START]
    worker, queue, _, _, text = replay_worker(tmp_path, clock, monkeypatch)
    for tenant in (TENANT, "foreign"):
        queue.enqueue_source_record(source(tenant, text, tenant=tenant))
    assert worker.run_pending(tenant_id=TENANT).processed == 1
    assert tenants == [TENANT] and queue.get("foreign").status == "pending"


def test_stale_deferral_cannot_release_a_new_workers_lease(tmp_path):
    queue = DistillationJobQueue(tmp_path / "queue.sqlite", now=lambda: START.timestamp())
    queue.enqueue_source_record(source("one", "text"))
    first = queue.claim_due(worker_id="first", now=START, lease_seconds=1)[0]
    later = START + timedelta(seconds=2)
    second = queue.claim_due(worker_id="second", now=later)[0]
    with pytest.raises(DistillationError, match="stale or expired"):
        queue.defer_budget(first, now=later.timestamp())
    assert queue.get("one").lease_token == second.lease_token


def test_shared_ledger_reservations_cannot_overspend_cap(tmp_path):
    assert hypothesis_card.DEFAULT_DAILY_CALL_CAP == 400
    ledgers = [ExtractionLedger(tmp_path / "shared.sqlite", daily_cap=2) for _ in range(8)]
    with ThreadPoolExecutor(max_workers=8) as executor:
        reserved = list(executor.map(lambda i: ledgers[i % 8].reserve_call(), range(40)))
    assert sum(reserved) == 2 and ledgers[0].calls_today() == 2


def test_extracted_card_ends_deferral_before_registry_retry(tmp_path):
    queue = DistillationJobQueue(tmp_path / "queue.sqlite", now=lambda: START.timestamp())
    queue.enqueue_source_record(source("one", "text"))
    first = queue.claim_due(worker_id="first", now=START)[0]
    queue.defer_budget(first, now=START.timestamp())
    later = START + timedelta(days=1)
    second = queue.claim_due(worker_id="second", now=later)[0]
    queue.clear_budget_deferral(second, now=later.timestamp())
    assert queue.get("one").deferred_since is None
    assert queue.mark_retry_or_dead_letter(second, error="registry timeout", now=later, base_delay_seconds=1) == "retry_wait"


def test_environment_controller_tick_drives_demand_deferral_across_two_windows(tmp_path, monkeypatch):
    from services.source_ingestion import distillation_controller as dc
    from services.source_ingestion import semantic_extraction_client as sec
    from services.source_ingestion.controller_state import ControllerState, ControllerStateStore
    from services.source_ingestion.tests.test_distillation_controller import DummyLoopWriter

    credentials(monkeypatch)
    monkeypatch.setattr(httpx, "get", lambda *a, **k: response([demand()]))
    clock = [START]
    monkeypatch.setattr(hypothesis_card, "_today", lambda: clock[0].strftime("%Y-%m-%d"))
    recording = json.loads((Path(__file__).parent / "fixtures/hypothesis_screen_entry_recorded.json").read_text())
    extracted = []

    def transport(payload):
        if payload["prompt"].startswith("Propose a factor_rule_v1"):
            return {"output": {"structured_data": {"outcome": "not_expressible", "reason": "test refusal"}}}
        sid = ast.literal_eval(re.search(r"source_record_id=(.*?);", payload["prompt"]).group(1))
        extracted.append(sid)
        result = copy.deepcopy(recording["response"])
        card = result["output"]["structured_data"]["hypothesis_card"]
        for idea in (card, card["screen_idea"], card["entry_idea"]):
            for span in idea["cited_spans"]:
                span["source_record_id"] = sid
        return result

    real_client = sec.SemanticExtractionClient

    class Client(real_client):
        def __init__(self, **kwargs):
            super().__init__(transport_fn=transport, **kwargs)

    monkeypatch.setattr(sec, "SemanticExtractionClient", Client)
    monkeypatch.setenv("PANTHEON_L2_EXTRACTION_ENABLED", "1")
    monkeypatch.setenv("PANTHEON_L2_EXTRACTION_DAILY_CALL_CAP", "2")
    monkeypatch.setenv("PANTHEON_L2_EXTRACTION_LEDGER_PATH", str(tmp_path / "ledger.sqlite"))
    monkeypatch.setenv("SOURCE_INGEST_EVIDENCE_BACKEND", "jsonl")
    queue_path = tmp_path / "queue.sqlite"
    records = [source(str(i), recording["stored_text"], title) for i, title in enumerate(
        ["first", "second", "third", "momentum", "momentum equity"])]
    queue = DistillationJobQueue(queue_path, now=lambda: clock[0].timestamp())
    for i, record in enumerate(records):
        queue.enqueue_source_record(record, enqueued_at=START.timestamp() + i)
    evidence = tmp_path / "evidence.jsonl"
    evidence.write_text("".join(json.dumps({"record_type": "source_record", "payload": s.to_dict()}) + "\n"
                                for s in records))
    monkeypatch.setattr(dc, "DistillationJobQueue", lambda *a, **k: DistillationJobQueue(
        *a, **k, now=lambda: clock[0].timestamp()))
    config = dc.DistillationControllerConfig(
        database_url="postgresql://unused", registry_url="http://unused", interval_seconds=60, max_ticks=1,
        state_path=tmp_path / "state.json", alive_path=None, job_queue_path=queue_path,
        seed_store_path=tmp_path / "seeds.jsonl", evidence_store_path=evidence, source_dirs=[tmp_path],
    )
    state = ControllerState(controller_id="demand-test", controller_name="distillation", environment="test",
                            tenant_id=TENANT, deployment={"git_sha": "test"})
    store = ControllerStateStore(config.state_path)
    store.save(state)
    clock[0] += timedelta(seconds=10)
    first = dc.run_controller_tick(config=config, state=state, store=store, writer=DummyLoopWriter())
    assert first["reconcile"]["deferred"] == 3 and extracted == ["0", "1"]
    assert all(queue.get(sid).status == "retry_wait" for sid in ("2", "3", "4"))
    clock[0] = START + timedelta(days=1)
    second = dc.run_controller_tick(config=config, state=state, store=store, writer=DummyLoopWriter())
    assert second["reconcile"]["deferred"] == 1 and extracted == ["0", "1", "4", "3"]
    assert queue.get("4").priority_reason == "open_demand"


def test_real_research_owner_route_enforces_issued_reader_tenant(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from services.research.research_demand import ResearchDemandStore
    from services.research.tests.test_research_orchestrator_http_service import _load_service_module
    from scripts.issue_dev_paper_principals import issue_environment

    key = "synthetic-demand-reader-verifier-key-" * 2
    dev_tenant = "tenant-dev"
    values = issue_environment({
        "PANTHEON_ENV": "dev", "PANTHEON_DEV_BFF_TENANT_ID": dev_tenant,
        "PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED": "true",
        "PANTHEON_DEV_BFF_JWT_SECRET": key,
        "PANTHEON_DEV_BFF_JWT_ISSUER": "test-issuer", "PANTHEON_DEV_BFF_JWT_AUDIENCE": "test-audience",
    })
    for suffix, value in {"AUTH_MODE": "strict", "JWT_SECRET": key, "JWT_ISSUER": "test-issuer",
                          "JWT_AUDIENCE": "test-audience"}.items():
        monkeypatch.setenv("PANTHEON_RESEARCH_" + suffix, value)
    module = _load_service_module(data_dir=str(tmp_path / "research"))
    module.research_demand_store = ResearchDemandStore(tmp_path / "demands.json", now=lambda: START)
    for tenant in (dev_tenant, "foreign"):
        module.research_demand_store.open(tenant, {
            "persona_id": "p1", "mandate": "Taiwan momentum", "pool_version": 1,
            "short_measure": "effective_bets",
        })
    client = TestClient(module.app)
    assert client.get("/api/research/persona-demands").status_code in (401, 403)
    monkeypatch.delenv("DISTILLATION_RESEARCH_SERVICE_TOKEN_FILE", raising=False)
    monkeypatch.setenv("DISTILLATION_RESEARCH_SERVICE_TOKEN", values["DISTILLATION_RESEARCH_SERVICE_TOKEN"])
    monkeypatch.setattr(httpx, "get", lambda url, **k: client.get(
        "/api/research/persona-demands", params=k["params"], headers=k["headers"],
    ))
    reader = ResearchDemandReader("http://research.test")
    assert reader.read(dev_tenant, now=START.timestamp()) == DemandPriority(frozenset({"taiwan", "momentum"}), "open_demand")
    assert reader.read("foreign", now=START.timestamp()).reason == "demand_read_http_403"
