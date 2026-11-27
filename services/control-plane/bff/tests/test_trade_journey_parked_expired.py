"""Producer -> canonical projector -> BFF with verbatim hosted pending documents."""
from datetime import datetime, timezone

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


def assert_bodies(projection, kind, code):
    c = client(Reader([projection]))
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
    assert row["next_step"]["action_needed"] == "review_market_data_refresh"
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
    def fetch_for(events, outcome):
        row = {"journey_id": "tj-test", "created_at": "2026-11-27T01:00:00Z", "status": "expired" if outcome else "open", "outcome": outcome, "next_step": {"action_needed": "review_market_data_refresh"}}
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
