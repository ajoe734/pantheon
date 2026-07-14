from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from services.foundation import (
    EnvironmentName,
    EnvironmentScope,
    EventEnvelope,
    OutboxRecord,
    TraceContext,
)
from services.foundation.reliable_delivery import (
    AtomicJsonRecordStore,
    ReliableInboxStore,
    ReliableOutboxStore,
    reconcile_prepared,
)


def _event(*, event_id: str = "evt-1", idempotency_key: str = "idmp-1") -> EventEnvelope:
    trace = TraceContext.new(
        environment=EnvironmentScope(name=EnvironmentName.SANDBOX),
        source_system="delivery-test",
        idempotency_key=idempotency_key,
    )
    return EventEnvelope(
        event_id=event_id,
        event_type="incident.resolved",
        aggregate_type="incident",
        aggregate_id="inc-1",
        sequence_no=1,
        trace=trace,
        payload={"incident_id": "inc-1"},
        idempotency_key=idempotency_key,
        producer_service="incident-svc",
    )


def _outbox(tmp_path):
    return ReliableOutboxStore(
        backend="json",
        dsn=None,
        table_name="test.outbox",
        json_path=tmp_path / "outbox.json",
        owner_service="test-svc",
    )


def _inbox(tmp_path):
    return ReliableInboxStore(
        backend="json",
        dsn=None,
        table_name="test.inbox",
        json_path=tmp_path / "inbox.json",
        owner_service="consumer-svc",
        consumer_name="consumer",
    )


def test_atomic_json_record_store_preserves_concurrent_writes(tmp_path):
    store = AtomicJsonRecordStore(tmp_path / "records.json")

    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(lambda index: store.put(f"record-{index}", {"index": index}), range(40)))

    records = store.list_all()
    assert len(records) == 40
    assert {record["index"] for record in records} == set(range(40))


def test_prepared_record_survives_restart_and_reconciles(tmp_path):
    store = _outbox(tmp_path)
    event = _event()
    prepared = store.prepare(
        record=OutboxRecord(outbox_id="outbox-1", owner_service="incident-svc", event=event),
        transition={"aggregate_type": "incident", "aggregate_id": "inc-1"},
    )
    assert prepared.delivery_ready is False
    assert store.list_pending_and_failed() == []

    restarted = _outbox(tmp_path)
    assert len(restarted.list_prepared()) == 1
    assert reconcile_prepared(restarted, transition_applied=lambda transition: False) == 0
    assert reconcile_prepared(restarted, transition_applied=lambda transition: True) == 1
    assert restarted.list_pending_and_failed()[0].event.event_id == event.event_id


def test_prepare_reuses_canonical_envelope_when_retry_only_changes_trace_and_timestamps(tmp_path):
    store = _outbox(tmp_path)
    event = _event()
    transition = {"aggregate_type": "incident", "aggregate_id": "inc-1"}
    prepared = store.prepare(
        record=OutboxRecord(outbox_id="outbox-1", owner_service="incident-svc", event=event),
        transition=transition,
    )
    retry_event = replace(
        _event(),
        event_time=datetime(2026, 7, 14, 1, tzinfo=timezone.utc),
        emitted_at=datetime(2026, 7, 14, 1, 0, 1, tzinfo=timezone.utc),
    )

    retried = store.prepare(
        record=OutboxRecord(
            outbox_id="outbox-1",
            owner_service="incident-svc",
            event=retry_event,
        ),
        transition=transition,
    )

    assert retry_event.trace_id != event.trace_id
    assert retried.event.to_dict() == prepared.event.to_dict()


def test_prepare_rejects_same_outbox_id_with_divergent_payload(tmp_path):
    store = _outbox(tmp_path)
    event = _event()
    transition = {"aggregate_type": "incident", "aggregate_id": "inc-1"}
    store.prepare(
        record=OutboxRecord(outbox_id="outbox-1", owner_service="incident-svc", event=event),
        transition=transition,
    )
    divergent = replace(event, payload={"incident_id": "inc-other"})

    with pytest.raises(ValueError, match=r"event\.payload"):
        store.prepare(
            record=OutboxRecord(
                outbox_id="outbox-1",
                owner_service="incident-svc",
                event=divergent,
            ),
            transition=transition,
        )


@pytest.mark.parametrize(
    ("record_change", "transition", "expected_mismatch"),
    [
        ({"event": replace(_event(), event_id="evt-other")}, None, r"event\.event_id"),
        ({"owner_service": "other-svc"}, None, "owner_service"),
        ({}, {"aggregate_type": "incident", "aggregate_id": "inc-other"}, "transition"),
    ],
)
def test_prepare_rejects_other_divergent_stable_semantics(
    tmp_path,
    record_change,
    transition,
    expected_mismatch,
):
    store = _outbox(tmp_path)
    event = _event()
    canonical_transition = {"aggregate_type": "incident", "aggregate_id": "inc-1"}
    store.prepare(
        record=OutboxRecord(outbox_id="outbox-1", owner_service="incident-svc", event=event),
        transition=canonical_transition,
    )
    incoming = OutboxRecord(
        outbox_id="outbox-1",
        owner_service="incident-svc",
        event=event,
    )

    with pytest.raises(ValueError, match=expected_mismatch):
        store.prepare(
            record=replace(incoming, **record_change),
            transition=transition or canonical_transition,
        )


def test_backoff_dead_letter_and_governed_redrive_are_durable(tmp_path):
    store = _outbox(tmp_path)
    prepared = store.prepare(
        record=OutboxRecord(outbox_id="outbox-1", owner_service="incident-svc", event=_event()),
        transition={"aggregate_type": "incident", "aggregate_id": "inc-1"},
    )
    active = store.activate(prepared)
    now = datetime(2026, 7, 14, tzinfo=timezone.utc)

    first = active.mark_failed(
        "offline",
        max_attempts=2,
        base_delay_seconds=4,
        now=now,
    )
    store.put(first)
    assert first.next_attempt_at == now + timedelta(seconds=4)
    assert first.is_due(now + timedelta(seconds=3)) is False
    assert first.is_due(now + timedelta(seconds=4)) is True

    dead = first.mark_failed(
        "still offline",
        max_attempts=2,
        base_delay_seconds=4,
        now=now + timedelta(seconds=4),
    )
    store.put(dead)
    assert dead.status == "dead_lettered"
    assert store.list_pending_and_failed() == []

    redriven = store.redrive(
        "outbox-1",
        actor="risk_owner:risk-1",
        note="approval_ref=APR-1; dependency recovered",
    )
    assert redriven.status == "pending"
    assert redriven.redrive_count == 1
    assert redriven.last_redrive_actor == "risk_owner:risk-1"
    assert _outbox(tmp_path).get("outbox-1").redrive_count == 1  # type: ignore[union-attr]


def test_inbox_accepts_exact_retry_and_rejects_divergent_replay(tmp_path):
    inbox = _inbox(tmp_path)
    event = _event()
    inbox.record_applied(event, result_ref="pm-inc-1")

    state, receipt = _inbox(tmp_path).classify(event)
    assert state == "duplicate"
    assert receipt["result_ref"] == "pm-inc-1"  # type: ignore[index]

    divergent = _event(event_id=event.event_id, idempotency_key=event.idempotency_key)
    divergent = EventEnvelope.from_dict(
        {**divergent.to_dict(), "payload": {"incident_id": "inc-other"}}
    )
    state, _ = inbox.classify(divergent)
    assert state == "conflict"
    with pytest.raises(ValueError, match="divergent replay"):
        inbox.record_applied(divergent, result_ref="pm-other")
