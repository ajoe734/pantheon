from services.trade_journey.replay_backfill import backfill_legacy, replay_as_of


def event(event_id, occurred, recorded, status="succeeded", **extra):
    return {"event_id": event_id, "journey_id": "tj-1", "tenant_id": "t1", "environment": "paper",
            "occurred_at": occurred, "recorded_at": recorded, "stage": "risk_evaluation",
            "stage_status": status, "policy_version": "p1", "model_version": "m1", **extra}


def test_as_of_replay_preserves_recording_time_and_applies_late_correction_deterministically():
    original = event("e1", "2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z", "rejected")
    correction = event("c1", "2026-01-01T00:00:00Z", "2026-01-03T00:00:00Z",
                       corrects_event_id="e1", overlay={"stage_status": "succeeded", "policy_version": "p2"})
    before = replay_as_of([correction, original], occurred_as_of="2026-01-02T00:00:00Z", recorded_as_of="2026-01-02T00:00:00Z")
    after = replay_as_of([original, correction], occurred_as_of="2026-01-02T00:00:00Z", recorded_as_of="2026-01-04T00:00:00Z")
    repeated = replay_as_of([correction, original], occurred_as_of="2026-01-02T00:00:00Z", recorded_as_of="2026-01-04T00:00:00Z")
    assert before.projections[0]["stages"]["risk_evaluation"]["status"] == "rejected"
    assert after.projections[0]["stages"]["risk_evaluation"]["status"] == "succeeded"
    assert after.evidence_hash == repeated.evidence_hash
    assert after.projections[0]["revision"] == 1


def test_legacy_backfill_labels_inference_and_queues_conflicts_and_low_confidence():
    result = backfill_legacy([
        {"legacy_id": "a", "journey_id": "tj-a"},
        {"legacy_id": "b", "candidate_journey_ids": ["tj-b"], "confidence": .91},
        {"legacy_id": "c", "candidate_journey_ids": ["tj-c"], "confidence": .4},
        {"legacy_id": "d", "candidate_journey_ids": ["tj-d2", "tj-d1"], "confidence": .99},
        {"legacy_id": "e"},
    ])
    assert result["mappings"] == [
        {"legacy_id": "a", "journey_id": "tj-a", "confidence": 1.0, "basis": "explicit"},
        {"legacy_id": "b", "journey_id": "tj-b", "confidence": .91, "basis": "inferred"},
    ]
    assert [item["reason"] for item in result["orphan_queue"]] == ["low_confidence", "conflict", "unmapped"]
    assert result["evidence"] == {"total": 5, "before_mapped": 1, "after_mapped": 2,
                                  "conflicts": 1, "orphans": 3, "confidence_threshold": .8}
