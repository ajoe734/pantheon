"""Contract tests for the mutation-owned Agora audit writer."""
from __future__ import annotations

from datetime import datetime, timezone

from services.control_plane.bff.agora_audit_store import AgoraAuditStore


def test_agora_audit_store_round_trips_after_reconstruction(tmp_path) -> None:
    path = tmp_path / "agora-audit.jsonl"
    first = AgoraAuditStore(str(path))
    record = first.record_agora_audit_event(
        {
            "action": "management.nl.ask.accepted",
            "targetType": "ManagementNLExchange",
            "targetId": "mnl-fresh",
            "actorId": "pantheon-dev-operator-a",
            "recordedAt": "2026-09-04T12:00:00Z",
        }
    )

    reloaded = AgoraAuditStore(str(path))
    events = reloaded.list_agora_audit_events(target_type="ManagementNLExchange")
    assert events == [record]
    assert events[0]["auditId"] == events[0]["entry_id"]
    assert events[0]["action_type"] == "management.nl.ask.accepted"


def test_agora_audit_store_applies_governance_filters(tmp_path) -> None:
    store = AgoraAuditStore(str(tmp_path / "agora-audit.jsonl"))
    store.record_agora_audit_event(
        {
            "action": "agora.signal.create",
            "targetType": "signal",
            "targetId": "sig-1",
            "actorId": "operator-a",
            "recordedAt": "2026-09-04T11:00:00Z",
        }
    )
    store.record_agora_audit_event(
        {
            "action": "agora.signal.create",
            "targetType": "signal",
            "targetId": "sig-2",
            "actorId": "operator-b",
            "recordedAt": "2026-09-04T12:00:00Z",
        }
    )
    events = store.list_agora_audit_events(
        actor="operator-b",
        action_types=["agora.signal.create"],
        from_ts=datetime(2026, 9, 4, 11, 30, tzinfo=timezone.utc),
    )
    assert [event["target_id"] for event in events] == ["sig-2"]

