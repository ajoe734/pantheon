"""Producer -> canonical projector -> BFF with verbatim hosted pending documents."""
from datetime import datetime, timezone, timedelta
from copy import deepcopy
import json
from pathlib import Path
import pytest

from services.execution.lean_runtime.test_paper_signal_producer import recorded_decision_producer
from services.trade_journey.lifecycle_projector import RelationalLifecycleProjector
from services.trade_journey.materializer import JourneyMaterializer
from services.control_plane.bff.tests.test_trade_journey_readable import client, Reader, HEADERS, LIST, sql_for
from services.control_plane.bff import trade_journeys as tj


def project_events(events):
    materializer = JourneyMaterializer()
    rows = []
    for offset, event in enumerate(events, 1):
        identity = RelationalLifecycleProjector._identity(event)
        rows.extend(RelationalLifecycleProjector._journey_events({
            "identity": identity, "event": event, "ingested_seq": offset,
            "sequence_no": event["sequence_no"], "source_mode": "live", "accepted_live": True,
        }))
    materializer.rebuild(rows)
    return materializer.get(events[0]["journey_id"], tenant_id="tenant-dev", environment="paper")


def parked_client(reader, now):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from services.control_plane.bff.models import OperatorIdentity
    app = FastAPI()
    app.include_router(tj.create_trade_journeys_router(
        extract_identity=lambda *a, **kw: OperatorIdentity(operator_id="op-1", roles=["operator"], claims={}),
        require_read_role=lambda identity: None, get_projection_reader=lambda: reader,
        utc_now=lambda: now.isoformat(),
    ))
    return TestClient(app)


def assert_bodies(projection, kind, code, now=None):
    c = parked_client(Reader([projection]), now) if now else client(Reader([projection]))
    response = c.get(LIST, headers=HEADERS)
    assert response.status_code == 200
    [row] = response.json()["data"]["items"]
    response = c.get(f"/bff/management/trade-journeys/{projection.journey_id}?tenant_id=tenant-dev&environment=paper", headers=HEADERS)
    assert response.status_code == 200
    detail = response.json()["data"]
    assert row["outcome"] == detail["outcome"]
    assert (row["outcome"]["kind"], row["outcome"]["reason_code"]) == (kind, code)
    return row, detail


def test_park_then_expire_list_and_detail_report_terminal_reason_and_next_step():
    producer, store, emitter, raw, binding = recorded_decision_producer("expired_2330_D20261125")
    producer.settle_pending_intraday(binding, "2026-11-27T10:00:00+08:00")
    parked = project_events(emitter.events[:1])
    row, detail = assert_bodies(parked, "open", "pending_settlement")
    assert row["outcome"]["reason_detail"]["target_day"] == "2026-11-26"
    assert row["outcome"]["reason_detail"]["expires_at"] == raw["#intraday-pending"]["plan"]["expires_at"]
    assert tj._attention_item(parked, now=datetime(2026, 12, 2, tzinfo=timezone.utc)) is None
    expired = project_events(emitter.events)
    assert expired.snapshot["status"] == "expired"
    row, detail = assert_bodies(expired, "expired", "target_session_not_reconciled_by_expiry")
    assert row["terminal_at"] is not None
    # AC4: the recorded decision is HOLD, so expiry missed no intended order.
    assert row["next_step"] is None
    assert row["side"] is None
    assert row["outcome"]["reason_detail"]["decision_action"] == "hold"
    assert row["outcome"]["reason_detail"]["expired_at"] == raw["#intraday-settlement"]["expired_at"]
    assert [e["decision_status"] for e in expired.timeline] == ["pending_settlement", "expired_no_price"]
    # Both paging and total queries include expiry, while parked rows stay open.
    calls = sql_for(attention="attention")
    for sql, _ in calls:
        if "COUNT(*)" in sql or "ORDER BY" in sql:
            assert "'expired'" in sql and "'open'" not in sql


def test_park_then_runtime_release_preserves_park_in_same_journey(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from services.execution.lean_runtime.paper_runtime import PaperRuntimeService, OrderEvent
    from services.execution.lean_runtime.runtime_identity import RuntimeIdentity
    from services.execution.lean_runtime.pending_signal_store import InMemoryPendingSignalStore
    producer, store, emitter, raw, binding = recorded_decision_producer()
    pending = raw["#intraday-pending"]
    producer._daily_bar_reader = lambda *_: [{"symbol": pending["symbol"], "bar_frequency": "1d", "bar_start": "2026-11-27T09:00:00+08:00", "bar_availability_ts": "2026-11-27T16:00:00+08:00", "open": 220.0}]
    assert producer.settle_pending_intraday(binding, "2026-11-27T16:30:00+08:00") == 1
    [signal] = store.get_pending()
    monkeypatch.setenv("PANTHEON_LIFECYCLE_OUTBOX_PATH", str(tmp_path / "outbox.json"))
    runtime = PaperRuntimeService(
        store=InMemoryPendingSignalStore(),
        identity=RuntimeIdentity.from_env({"PANTHEON_RUNTIME_ID": binding["runtime_id"]}),
        runtime_manager_client=SimpleNamespace(list_all=lambda: [binding]),
        telemetry_emitter=emitter,
    )
    runtime._handle_order_event(OrderEvent(
        event_type="signal_generation", symbol=signal["symbol"], quantity=signal["quantity"],
        action=signal["action"], fill_price=220.0, metadata=signal,
        created_at=signal["timestamp"],
    ))
    # The runtime reuses the exact upstream payload; ingest deduplicates it.
    unique = {e["event_id"]: e for e in emitter.events if "sequence_no" in e}
    events = list(unique.values())
    assert [e["sequence_no"] for e in events] == [1, 2, 3]
    assert [e["event_type"] for e in events] == ["trade_decision", "signal_generation", "trade_decision"]
    assert len({e["journey_id"] for e in events}) == 1
    assert events[1]["causal_parent_id"] == events[0]["event_id"]
    projection = project_events(events)
    row, detail = assert_bodies(projection, "open", None)
    assert projection.timeline[0]["decision_status"] == "pending_settlement"
    assert row["journey_id"] == signal["journey_id"]
    assert detail["stages"]["trade_decision"]["status"] == "succeeded"


def test_release_probe_reports_skip_pass_and_wrong_expiry():
    import runpy
    from pathlib import Path
    probe = runpy.run_path(str(Path(__file__).resolve().parents[4] / "acceptance/probes/trade-journey-parked-expired"))
    manifest = {"deployedAt": "2026-11-27T00:00:00Z"}
    def fetch_for(events, outcome, **fields):
        row = {"journey_id": "tj-test", "created_at": "2026-11-27T01:00:00Z", "status": "expired" if outcome else "open", "outcome": outcome, "next_step": {"action_needed": "review_market_data_refresh"}}
        row.update(fields)
        def fetch(path):
            if "/timeline?" in path:
                return {"data": {"items": events}, "page_info": {"next_page_token": None}}
            if "/tj-test?" in path:
                return {"data": row}
            return {"data": {"items": [row]}, "page_info": {"next_page_token": None}}
        return fetch
    check = probe["check"]
    now = datetime(2026, 11, 27, 2, tzinfo=timezone.utc)
    assert check(fetch_for([], {}), manifest, now=now)["exit_code"] == 77
    park = {"decision_status": "pending_settlement"}
    assert check(fetch_for([park], {}), manifest, now=now)["exit_code"] == 0
    expire = {"decision_status": "expired_no_price", "reason_code": "target_session_not_reconciled_by_expiry"}
    outcome = {"kind": "expired", "reason_code": expire["reason_code"]}
    assert check(fetch_for([park, expire], outcome), manifest, now=now)["exit_code"] == 0
    assert check(fetch_for([park, expire], {"kind": "normal"}), manifest, now=now)["exit_code"] == 1
    hold_outcome = {**outcome, "reason_detail": {"decision_action": "hold"}}
    assert check(fetch_for([park, expire], hold_outcome, side=None, next_step=None), manifest, now=now)["exit_code"] == 0
    assert check(fetch_for([park, expire], hold_outcome), manifest, now=now)["exit_code"] == 1


@pytest.mark.parametrize("action", ["hold", "buy", "sell"])
def test_release_probe_uses_rendered_producer_list_detail_and_timeline(action):
    import runpy
    check = runpy.run_path(str(Path(__file__).resolve().parents[4] / "acceptance/probes/trade-journey-parked-expired"))["check"]
    producer, store, emitter, raw, binding = recorded_decision_producer("expired_2330_D20261125")
    producer.settle_pending_intraday(binding, "2026-11-27T10:00:00+08:00")
    # HOLD is the unmodified recording; BUY/SELL are derived order-intent cases.
    events = deepcopy(emitter.events)
    if action != "hold":
        for event in events:
            event["metadata"]["side"] = action
    projection = project_events(events)
    original = deepcopy(projection.timeline)
    now = datetime(2026, 11, 27, 10, tzinfo=timezone.utc)
    from services.control_plane.bff.trade_journey_projection_store import TimelinePage
    class TimelineReader(Reader):
        def page_timeline(self, **kwargs):
            return TimelinePage(items=projection.timeline, next_page_token=None, total=len(projection.timeline))
    c = parked_client(TimelineReader([projection]), now)
    manifest = {"deployedAt": "2026-11-25T00:00:00Z"}
    mutation = None
    def fetch(path):
        response = c.get(path, headers=HEADERS)
        assert response.status_code == 200, response.text
        payload = response.json()
        if "/timeline?" in path:
            for event in payload["data"]["items"]:
                assert "side" not in event
                assert "decision_action" not in (event.get("reason_detail") or {})
        if mutation:
            mutation(path, payload)
        return payload
    report = check(fetch, manifest, now=now)
    assert report["failed"] == []
    assert report["exit_code"] == 0
    assert report["expired"] == [projection.journey_id]
    # Both list and detail must retain the action-appropriate next step.
    for surface in ("list", "detail"):
        def mutation(path, payload):
            if "/timeline?" in path:
                return
            is_detail = "/" + projection.journey_id + "?" in path
            if is_detail != (surface == "detail"):
                return
            body = payload["data"] if is_detail else payload["data"]["items"][0]
            body["next_step"] = {"action_needed": "review_market_data_refresh"} if action == "hold" else None
        report = check(fetch, manifest, now=now)
        expected = "expire_hold_no_order:" if action == "hold" else "expire_next_step:"
        assert report["failed"] == [expected + projection.journey_id]
        assert report["exit_code"] == 1
    # Timeline reason validation remains mandatory, including for HOLD.
    def mutation(path, payload):
        if "/timeline?" in path:
            payload["data"]["items"][-1]["reason_code"] = "wrong_reason"
    assert check(fetch, manifest, now=now)["failed"] == ["expire_event_reason:" + projection.journey_id]
    assert projection.timeline == original


EVIDENCE = Path(__file__).resolve().parents[4] / "docs/deployment/evidence/TRADE-JOURNEY-PARKED-ROWS-NOT-STALLED-OR-PARTIAL-20261127/evidence.json"
HOSTED = json.loads(EVIDENCE.read_text())["recorded_hosted_bodies"]
CAPTURE_NOW = datetime(2026, 11, 27, 23, 50, tzinfo=timezone.utc)


def hosted_projection(prefix):
    from services.trade_journey.materializer import JourneyProjection
    jid = next(k for k in HOSTED["details"] if k.startswith(prefix))
    detail = deepcopy(HOSTED["details"][jid]["data"])
    return JourneyProjection(jid, detail["tenant_id"], detail["environment"],
                             list(detail["stage_events"].values()), detail,
                             detail["graph_edges"], detail["diagnostics"])


@pytest.mark.parametrize("prefix,side,quantity,kind", [
    ("tj_e6eb7d82", "buy", 46, "open"),
    ("tj_45cb6e82", None, 1, "expired"),
    ("tj_9eeb8979", None, 1, "open"),
])
def test_verbatim_hosted_list_and_detail(prefix, side, quantity, kind):
    p = hosted_projection(prefix)
    recorded = HOSTED["list_rows"][p.journey_id]
    assert recorded["read_state"] == "partial"  # captured regression, unchanged fixture
    assert recorded["flags"]["stalled"] is (kind == "open")
    row, detail = assert_bodies(p, kind, "pending_settlement" if kind == "open" else "target_session_not_reconciled_by_expiry", now=CAPTURE_NOW)
    for body in (row, detail):
        assert body["flags"]["stalled"] is False
        assert body["read_state"] == "formal"
        assert body["side"] == side
        assert body["quantity"] == quantity
        assert body["next_step"] is None
        assert "signal_generation" not in body["completeness"]["missing_stages"]
    assert not any(d["code"] == "missing_stages" for d in detail["diagnostics"])
    assert next(d for d in detail["diagnostics"] if d["code"] == "clock_drift") == p.diagnostics[0]
    assert tj._metrics([p], now=CAPTURE_NOW)["stalled_count"] == 0
    if kind == "expired":
        assert detail["outcome"]["reason_detail"]["decision_action"] == "hold"


def test_pending_expiry_boundary_and_release_missing_signal():
    p = hosted_projection("tj_e6eb7d82")
    expiry = tj._parse_iso(p.timeline[0]["reason_detail"]["expires_at"])
    for delta, stalled in [(0, False), (900, False), (901, True)]:
        now = expiry + timedelta(seconds=delta)
        assert tj._list_row(p, now=now)["flags"]["stalled"] is stalled
        assert tj._detail(p, now=now)["flags"]["stalled"] is stalled
        assert tj._metrics([p], now=now)["stalled_count"] == int(stalled)
    p.snapshot["stages"]["trade_decision"]["status"] = "succeeded"
    p.timeline[0]["stage_status"] = "succeeded"
    assert tj._detail(p, now=CAPTURE_NOW)["read_state"] == "partial"
    assert any(d["code"] == "missing_stages" and "signal_generation" in d["stages"] for d in tj._detail(p, now=CAPTURE_NOW)["diagnostics"])


def test_release_probe_checks_predeployment_nonprobe_pending_list_and_detail():
    import runpy
    check = runpy.run_path(str(EVIDENCE.parents[4] / "acceptance/probes/trade-journey-parked-expired"))["check"]
    p = hosted_projection("tj_e6eb7d82")
    row = tj._list_row(p, now=CAPTURE_NOW)
    detail = tj._detail(p, now=CAPTURE_NOW)
    seen = []
    def fetch(path):
        from urllib.parse import parse_qs, urlsplit
        seen.append(path)
        query = parse_qs(urlsplit(path).query)
        if p.journey_id in path:
            return {"data": detail}
        return {"data": {"items": [] if "from" in query else [row]}, "page_info": {"next_page_token": None}}
    manifest = {"deployedAt": "2026-11-27T23:40:00Z"}
    assert check(fetch, manifest, now=CAPTURE_NOW)["exit_code"] == 0
    assert any("include_probe=false" in path and "status=open" in path for path in seen)
    for body in (row, detail):
        body["flags"]["stalled"] = True
        assert check(fetch, manifest, now=CAPTURE_NOW)["exit_code"] == 1
        body["flags"]["stalled"] = False
        body["read_state"] = "partial"
        assert check(fetch, manifest, now=CAPTURE_NOW)["exit_code"] == 1
        body["read_state"] = "formal"


def test_postgres_routes_filter_before_paging_and_count_stalls():
    from uuid import uuid4
    import psycopg
    from services.trade_journey.projection_store import ProjectionStore
    from services.control_plane.bff.trade_journey_projection_store import TradeJourneyProjectionStore
    from services.control_plane.bff.tests.test_trade_journey_readable_residuals import _ephemeral_postgres_dsn

    gen = _ephemeral_postgres_dsn()
    dsn = next(gen)
    schema = "test_parked_" + uuid4().hex[:8]
    now = datetime.now(timezone.utc)
    try:
        ProjectionStore(dsn, schema=schema, bootstrap=True)
        projections = [hosted_projection(prefix) for prefix in ("tj_e6eb7d82", "tj_45cb6e82", "tj_9eeb8979")]
        # Derived expiry cases use the database's current day; originals above
        # are tested verbatim at capture time by test_verbatim_hosted_list_and_detail.
        overdue = deepcopy(projections[0])
        overdue.journey_id = "tj-overdue"
        projections.append(overdue)
        for action in ("buy", "sell"):
            expired = deepcopy(projections[1])
            expired.journey_id = "tj-expired-" + action
            expired.timeline[0]["side"] = action
            projections.append(expired)
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            for index, p in enumerate(projections, 1):
                event = p.timeline[0]
                if p.snapshot["status"] == "open":
                    event["reason_detail"]["expires_at"] = (now + timedelta(days=1) if p is not overdue else now - timedelta(seconds=901)).isoformat()
                cur.execute(
                    f"INSERT INTO {schema}.journeys "
                    "(tenant_id, environment, journey_id, status, stage_coverage, is_terminal, first_occurred_at, last_occurred_at, first_ingested_seq, last_ingested_seq, current_identity_summary, diagnostic_summary, projection_revision) "
                    "VALUES ('tenant-dev','paper',%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,1)",
                    (p.journey_id, p.snapshot["status"], json.dumps(p.snapshot["stages"]), p.snapshot["status"] == "expired", now - timedelta(days=2), now - timedelta(days=1), index, index,
                     json.dumps(p.snapshot["identifiers"]), json.dumps({"diagnostics": p.diagnostics})),
                )
                cur.execute(
                    f"INSERT INTO {schema}.journey_stages "
                    "(tenant_id, environment, journey_id, source_event_id, stage_name, stage_status, stage_ordinal, source_ingested_seq, event_sequence, occurred_at, recorded_at, contract_fields) "
                    "VALUES ('tenant-dev','paper',%s,%s,'trade_decision',%s,8,%s,1,%s,%s,%s::jsonb)",
                    (p.journey_id, "event-" + str(index), p.snapshot["stages"]["trade_decision"]["status"], index, now - timedelta(days=1), now, json.dumps(event)),
                )
        reader = TradeJourneyProjectionStore(dsn, schema=schema, token_secret="s" * 16)
        c = parked_client(reader, now)
        def get(suffix):
            response = c.get(LIST + suffix, headers=HEADERS)
            assert response.status_code == 200, response.text
            return response.json()
        stalled = get("&stalled=true&page_size=1")
        assert stalled["page_info"]["total"] == 1
        assert stalled["data"]["items"][0]["journey_id"] == "tj-overdue"
        assert stalled["data"]["items"][0]["flags"]["stalled"] is True
        assert get("&stalled=false&page_size=1")["page_info"]["total"] == 5
        attention = get("&attention=attention&page_size=1")
        assert attention["page_info"]["total"] == 2
        page = reader.page_journeys(tenant_id="tenant-dev", environment="paper", filters={"attention": "attention"})
        assert {p.journey_id for p in page.items} == {"tj-expired-buy", "tj-expired-sell"}
        for p in page.items:
            assert tj._detail(p, now=now)["next_step"]["action_needed"] == "review_market_data_refresh"
        metrics = c.get("/bff/management/trade-journeys/metrics?tenant_id=tenant-dev&environment=paper", headers=HEADERS)
        assert metrics.status_code == 200
        assert metrics.json()["data"]["stalled_count"] == 1
        assert metrics.json()["data"]["total_journeys"] == 6
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        gen.close()
