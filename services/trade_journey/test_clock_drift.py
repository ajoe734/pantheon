from __future__ import annotations

from datetime import datetime, timezone

from services.trade_journey.materializer import JourneyMaterializer
from services.trade_journey.slo_data_quality import compute_data_quality_metrics, evaluate_data_quality, load_slo_targets


def event(event_id: str, occurred_at: str, recorded_at: str, sequence: int) -> dict:
    return {
        "event_id": event_id, "journey_id": "tj-drift", "tenant_id": "tenant-a",
        "environment": "paper", "source": "remote-node", "stage": "signal_generation",
        "stage_status": "succeeded", "occurred_at": occurred_at,
        "recorded_at": recorded_at, "sequence": sequence, "signal_id": "sig-1",
    }


def test_clock_drift_uses_recorded_time_for_stable_rebuild_and_emits_incident() -> None:
    events = [
        event("later", "2026-07-13T00:00:30Z", "2026-07-13T00:00:02Z", 2),
        event("earlier", "2026-07-12T23:59:30Z", "2026-07-13T00:00:01Z", 1),
    ]
    first, second = JourneyMaterializer(), JourneyMaterializer()
    first.rebuild(events)
    second.rebuild(reversed(events))
    p1 = first.get("tj-drift", tenant_id="tenant-a", environment="paper")
    p2 = second.get("tj-drift", tenant_id="tenant-a", environment="paper")
    assert [e["event_id"] for e in p1.timeline] == ["earlier", "later"]
    assert p1.timeline == p2.timeline
    assert any(d["code"] == "clock_drift" for d in p1.diagnostics)
    now = datetime(2026, 7, 13, 0, 1, tzinfo=timezone.utc)
    metrics = compute_data_quality_metrics([p1], environment="paper", source_watermarks=first.source_watermarks, now=now)
    assert metrics.clock_drift_event_count == 2
    assert metrics.clock_drift_max_abs_seconds == 31
    incidents = evaluate_data_quality(metrics, load_slo_targets("paper"), [p1], now=now)
    assert any(i.code == "clock_drift" and i.journey_id == "tj-drift" for i in incidents)


def test_drifted_envelope_is_bounded_without_falsifying_causal_order() -> None:
    events = [
        event("first", "2026-07-13T00:00:30Z", "2026-07-13T00:00:01Z", 1),
        event("second", "2026-07-13T00:00:24Z", "2026-07-13T00:00:02Z", 2),
    ]
    m = JourneyMaterializer()
    m.rebuild(events)
    p = m.get("tj-drift", tenant_id="tenant-a", environment="paper")
    assert [e["event_id"] for e in p.timeline] == ["first", "second"]
    seconds = [datetime.fromisoformat(e["occurred_at"].replace("Z", "+00:00")).second for e in p.timeline]
    assert seconds == [30, 24]
    created = datetime.fromisoformat(p.snapshot["created_at"].replace("Z", "+00:00"))
    updated = datetime.fromisoformat(p.snapshot["updated_at"].replace("Z", "+00:00"))
    assert (created.second, updated.second) == (24, 30)
