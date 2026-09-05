from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

from services.control_plane.bff import main as bff_main
from services.source_ingestion.strategy_seed_store import StrategySpecSeedStore
from test_training_session_service_client import create_training_read_surface_double


OPERATOR_AUTH = "Bearer test-operator:operator"


@contextmanager
def _seeded_client():
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        tracked_env = {
            "STRATEGY_SEED_STORE_PATH": os.environ.get("STRATEGY_SEED_STORE_PATH"),
            "INTERACTION_SOURCE_STORE_PATH": os.environ.get("INTERACTION_SOURCE_STORE_PATH"),
        }
        os.environ["STRATEGY_SEED_STORE_PATH"] = os.path.join(td, "strategy_seeds.jsonl")
        os.environ["INTERACTION_SOURCE_STORE_PATH"] = os.path.join(td, "interaction_records.jsonl")
        bff_main.read_store = create_training_read_surface_double()
        client = TestClient(bff_main.app)
        try:
            yield client
        finally:
            bff_main.read_store = original_store
            for key, value in tracked_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def test_tw04_list_replay_returns_session_list():
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/trainer/replay",
            params={"persona_id": "persona-alpha"},
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert "data" in payload
        assert "page_info" in payload
        assert "meta" in payload
        assert payload["meta"]["surfaces"]["trainer_replay"] in {"ok", "stale"}
        assert isinstance(payload["data"], list)
        assert len(payload["data"]) >= 1


def test_tw04_list_replay_row_has_required_fields():
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/trainer/replay",
            params={"persona_id": "persona-alpha"},
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text
        rows = response.json()["data"]
        assert len(rows) >= 1
        row = rows[0]
        for field in (
            "session_id",
            "persona_id",
            "objective",
            "status",
            "started_at",
            "ended_at",
            "event_count",
            "latest_event_type",
            "latest_outcome_signal",
            "replay_resolution",
            "allowedActions",
            "links",
        ):
            assert field in row, f"Missing field: {field}"
        assert "state" in row["replay_resolution"]
        assert "canReplay" in row["allowedActions"]
        assert "canCommit" in row["allowedActions"]
        assert "canDiscard" in row["allowedActions"]
        assert "replay_detail" in row["links"]
        assert row["links"]["replay_detail"] == f"/trainer/replay/{row['session_id']}"


def test_tw04_list_replay_filters_by_status():
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/trainer/replay",
            params={"persona_id": "persona-alpha", "status": "completed"},
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text
        rows = response.json()["data"]
        for row in rows:
            assert row["status"] == "completed"


def test_tw04_list_replay_rejects_invalid_status():
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/trainer/replay",
            params={"persona_id": "persona-alpha", "status": "active"},
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 422, response.text


def test_tw04_list_replay_rejects_unknown_persona():
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/trainer/replay",
            params={"persona_id": "persona-does-not-exist"},
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 404, response.text


def test_tw04_detail_returns_full_replay_payload():
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/trainer/replay/trn-20260418-003",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["session_id"] == "trn-20260418-003"
        assert payload["persona_id"] == "persona-alpha"
        assert payload["status"] == "completed"
        assert "replay_resolution" in payload
        assert "artifacts" in payload
        assert "event_summary" in payload
        assert "events" in payload
        assert "allowedActions" in payload
        assert "links" in payload
        assert "meta" in payload
        assert payload["meta"]["surfaces"]["trainer_replay"] in {"ok", "stale"}
        assert payload["links"]["self"] == f"/trainer/replay/{payload['session_id']}"
        assert payload["links"]["session_detail"] == f"/trainer/sessions/{payload['session_id']}"


def test_tw04_detail_events_ordered_by_sequence_number():
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/trainer/replay/trn-20260418-003",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text
        events = response.json()["events"]
        assert len(events) >= 2
        seq_nums = [e["sequence_number"] for e in events]
        assert seq_nums == sorted(seq_nums)


def test_tw04_detail_events_have_replay_grade_fields():
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/trainer/replay/trn-20260418-003",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text
        for event in response.json()["events"]:
            for field in (
                "event_id",
                "session_id",
                "actor",
                "actor_label",
                "event_type",
                "message_body",
                "summary",
                "emitted_at",
                "sequence_number",
                "outcome_signal",
                "evidence_ref",
                "patch_delta",
                "eval_ref",
                "artifact_refs",
            ):
                assert field in event, f"Missing replay event field: {field}"


def test_tw04_detail_control_patch_event_has_patch_delta():
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/trainer/replay/trn-20260418-003",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text
        events = response.json()["events"]
        patch_events = [e for e in events if e["event_type"] == "control_patch"]
        assert len(patch_events) >= 1
        for ev in patch_events:
            assert isinstance(ev["patch_delta"], list)
            assert len(ev["patch_delta"]) >= 1
            for row in ev["patch_delta"]:
                assert "parameter_key" in row
                assert "previous_value" in row
                assert "new_value" in row


def test_tw04_detail_preview_trigger_event_has_eval_ref():
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/trainer/replay/trn-20260418-003",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text
        events = response.json()["events"]
        preview_events = [e for e in events if e["event_type"] == "preview_trigger"]
        assert len(preview_events) >= 1
        for ev in preview_events:
            assert isinstance(ev["eval_ref"], dict)
            assert "eval_id" in ev["eval_ref"]
            assert "baseline_snapshot_at" in ev["eval_ref"]
            assert "candidate_snapshot_at" in ev["eval_ref"]


def test_tw04_detail_evidence_ref_fully_resolved():
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/trainer/replay/trn-20260418-003",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text
        events = response.json()["events"]
        evidence_events = [e for e in events if e["evidence_ref"] is not None]
        assert len(evidence_events) >= 1
        for ev in evidence_events:
            ref = ev["evidence_ref"]
            assert "type" in ref
            assert "id" in ref
            assert "display_label" in ref
            assert "url_pattern" in ref


def test_tw04_detail_drawdown_evidence_points_to_deployed_owner_route():
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/trainer/replay/trn-20260418-003",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text
        events = response.json()["events"]
        drawdown_event = next(e for e in events if e["event_id"] == "tevt-20260418-002")
        evidence_ref = drawdown_event["evidence_ref"]
        assert evidence_ref is not None
        assert evidence_ref["url_pattern"] == "/operator/paper-live-drift/runtime-042"


def test_tw04_detail_event_summary_matches_events():
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/trainer/replay/trn-20260418-003",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        events = payload["events"]
        summary = payload["event_summary"]
        assert summary["event_count"] == len(events)
        if events:
            assert summary["first_sequence_number"] == events[0]["sequence_number"]
            assert summary["last_sequence_number"] == events[-1]["sequence_number"]


def test_tw04_detail_pending_decision_allows_commit_and_discard():
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/trainer/replay/trn-20260418-003",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["replay_resolution"]["state"] == "pending_decision"
        assert payload["allowedActions"]["canCommit"] is True
        assert payload["allowedActions"]["canDiscard"] is True


def test_tw04_detail_committed_session_suppresses_cta():
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/trainer/replay/trn-20260417-001",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["replay_resolution"]["state"] == "committed"
        assert payload["allowedActions"]["canCommit"] is False
        assert payload["allowedActions"]["canDiscard"] is False


def test_tw04_detail_404_for_unknown_session():
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/trainer/replay/trn-does-not-exist",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 404, response.text


def test_tw04_commit_succeeds_and_appends_event():
    with _seeded_client() as client:
        response = client.post(
            "/api/v1/trainer/sessions/trn-20260418-003/commit",
            json={
                "expected_candidate_snapshot_at": "2026-04-18T08:18:00Z",
                "note": "Approved after stable eval.",
            },
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["session_id"] == "trn-20260418-003"
        assert payload["replay_resolution"]["state"] == "committed"
        assert payload["allowedActions"]["canCommit"] is False
        assert payload["allowedActions"]["canDiscard"] is False
        assert "committed_at" in payload
        assert "committed_by" in payload
        assert payload["event"]["event_type"] == "commit"
        artifact_refs = payload["event"]["artifact_refs"]
        assert artifact_refs is not None
        assert artifact_refs["before_artifact_ref"] is not None
        assert artifact_refs["after_artifact_ref"] is not None
        assert artifact_refs["after_artifact_ref"] is not None
        seed_extraction = payload["seed_extraction"]
        assert seed_extraction["status"] == "created"
        assert seed_extraction["seed_kind"] == "risk_constraint"
        assert seed_extraction["trainer_seed_extraction_ref"]["event_type"] == "trainer_commit"
        assert seed_extraction["review_inbox"]["route"] == "/bff/management/strategy-seeds"
        inbox = client.get(
            "/bff/management/strategy-seeds",
            params={"source_kind": "trainer", "status": "draft"},
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert inbox.status_code == 200, inbox.text
        assert any(
            item["seed_id"] == seed_extraction["seed_id"]
            for item in inbox.json()["data"]["items"]
        )
        stored = StrategySpecSeedStore().get(seed_extraction["seed_id"])
        assert stored is not None
        assert stored.lineage["trainer_seed_extraction_ref"]["event_id"] == payload["event"]["event_id"]
        assert payload["meta"]["surfaces"]["trainer_replay"] in {"ok", "stale"}


def test_tw04_commit_appended_event_visible_in_detail():
    with _seeded_client() as client:
        client.post(
            "/api/v1/trainer/sessions/trn-20260418-003/commit",
            json={"expected_candidate_snapshot_at": "2026-04-18T08:18:00Z"},
            headers={"Authorization": OPERATOR_AUTH},
        )
        detail = client.get(
            "/api/v1/trainer/replay/trn-20260418-003",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert detail.status_code == 200, detail.text
        events = detail.json()["events"]
        commit_events = [e for e in events if e["event_type"] == "commit"]
        assert len(commit_events) >= 1


def test_tw04_commit_rejected_on_snapshot_mismatch():
    with _seeded_client() as client:
        response = client.post(
            "/api/v1/trainer/sessions/trn-20260418-003/commit",
            json={"expected_candidate_snapshot_at": "wrong-snapshot-ref"},
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 409, response.text


def test_tw04_commit_rejected_when_already_committed():
    with _seeded_client() as client:
        response = client.post(
            "/api/v1/trainer/sessions/trn-20260417-001/commit",
            json={"expected_candidate_snapshot_at": "artifact-041-candidate"},
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 409, response.text


def test_tw04_commit_rejected_missing_expected_snapshot():
    with _seeded_client() as client:
        response = client.post(
            "/api/v1/trainer/sessions/trn-20260418-003/commit",
            json={"note": "missing the required field"},
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 422, response.text


def test_tw04_discard_succeeds_and_appends_event():
    with _seeded_client() as client:
        response = client.post(
            "/api/v1/trainer/sessions/trn-20260418-003/discard",
            json={
                "expected_candidate_snapshot_at": "2026-04-18T08:18:00Z",
                "note": "Discarding due to eval regression.",
            },
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["session_id"] == "trn-20260418-003"
        assert payload["replay_resolution"]["state"] == "discarded"
        assert payload["allowedActions"]["canCommit"] is False
        assert payload["allowedActions"]["canDiscard"] is False
        assert "discarded_at" in payload
        assert "discarded_by" in payload
        assert payload["event"]["event_type"] == "discard"
        artifact_refs = payload["event"]["artifact_refs"]
        assert artifact_refs is not None
        assert artifact_refs["before_artifact_ref"] is not None
        assert artifact_refs["after_artifact_ref"] is None
        assert payload["meta"]["surfaces"]["trainer_replay"] in {"ok", "stale"}


def test_tw04_discard_rejected_on_snapshot_mismatch():
    with _seeded_client() as client:
        response = client.post(
            "/api/v1/trainer/sessions/trn-20260418-003/discard",
            json={"expected_candidate_snapshot_at": "wrong-snapshot"},
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 409, response.text


def test_tw04_discard_rejected_when_already_committed():
    with _seeded_client() as client:
        response = client.post(
            "/api/v1/trainer/sessions/trn-20260417-001/discard",
            json={"expected_candidate_snapshot_at": "artifact-041-candidate"},
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 409, response.text


def test_tw04_list_requires_auth():
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/trainer/replay",
            params={"persona_id": "persona-alpha"},
        )
        assert response.status_code == 401, response.text


def test_tw04_detail_requires_auth():
    with _seeded_client() as client:
        response = client.get("/api/v1/trainer/replay/trn-20260418-003")
        assert response.status_code == 401, response.text


def test_tw04_commit_requires_auth():
    with _seeded_client() as client:
        response = client.post(
            "/api/v1/trainer/sessions/trn-20260418-003/commit",
            json={"expected_candidate_snapshot_at": "artifact-042-candidate"},
        )
        assert response.status_code == 401, response.text


def test_tw04_discard_requires_auth():
    with _seeded_client() as client:
        response = client.post(
            "/api/v1/trainer/sessions/trn-20260418-003/discard",
            json={"expected_candidate_snapshot_at": "artifact-042-candidate"},
        )
        assert response.status_code == 401, response.text


def test_tw04_list_row_allowed_actions_has_can_replay():
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/trainer/replay",
            params={"persona_id": "persona-alpha"},
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text
        rows = response.json()["data"]
        assert len(rows) >= 1
        for row in rows:
            assert "canReplay" in row["allowedActions"], "canReplay missing from allowedActions"
            assert isinstance(row["allowedActions"]["canReplay"], bool)


def test_tw04_list_surface_stale_with_local_snapshot():
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/trainer/replay",
            params={"persona_id": "persona-alpha"},
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text
        surface = response.json()["meta"]["surfaces"]["trainer_replay"]
        assert surface == "stale", f"Expected stale from local snapshot fallback, got {surface!r}"


def test_tw04_detail_surface_stale_with_local_snapshot():
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/trainer/replay/trn-20260418-003",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text
        surface = response.json()["meta"]["surfaces"]["trainer_replay"]
        assert surface == "stale", f"Expected stale from local snapshot fallback, got {surface!r}"


def test_tw04_existing_snapshot_backfills_drawdown_evidence_route():
    store = create_training_read_surface_double()
    replay = store.get_trainer_replay("trn-20260418-003")
    assert replay is not None
    drawdown_event = next(e for e in replay["events"] if e["event_id"] == "tevt-20260418-002")
    drawdown_event["evidence_ref"]["url_pattern"] = (
        "/telemetry/drawdown/tel-drawdown-2026-04-18"
    )
    store.add_replay(replay)

    normalized = store.get_trainer_replay("trn-20260418-003")
    normalized_event = next(
        e for e in normalized["events"] if e["event_id"] == "tevt-20260418-002"
    )
    assert normalized_event["evidence_ref"]["url_pattern"] == (
        "/operator/paper-live-drift/runtime-042"
    )


@contextmanager
def _seeded_client_with_degraded_session():
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        store = create_training_read_surface_double()
        degraded_session = {
            "session_id": "trn-degraded-001",
            "persona_id": "persona-alpha",
            "objective": "Degraded surface test session.",
            "status": "completed",
            "started_at": "2026-04-20T10:00:00Z",
            "ended_at": "2026-04-20T10:30:00Z",
            "replay_resolution": {
                "state": "pending_decision",
                "decision_at": None,
                "decision_by": None,
                "note": None,
            },
            "artifacts": {
                "before_artifact_ref": "artifact-degraded-before",
                "candidate_artifact_ref": "artifact-degraded-candidate",
                "after_artifact_ref": None,
            },
            "meta": {
                "surfaces": {
                    "trainer_replay": "degraded",
                }
            },
            "events": [
                {
                    "event_id": "tevt-degraded-001",
                    "session_id": "trn-degraded-001",
                    "actor": "operator",
                    "actor_label": "Operator",
                    "event_type": "message",
                    "message_body": "Test message.",
                    "summary": "Degraded session test event.",
                    "emitted_at": "2026-04-20T10:00:05Z",
                    "sequence_number": 1,
                    "outcome_signal": None,
                    "evidence_ref": None,
                    "patch_delta": None,
                    "eval_ref": None,
                    "artifact_refs": None,
                }
            ],
        }
        store.add_replay(degraded_session)
        bff_main.read_store = store
        client = TestClient(bff_main.app)
        try:
            yield client
        finally:
            bff_main.read_store = original_store


def test_tw04_detail_degraded_surface_suppresses_cta():
    with _seeded_client_with_degraded_session() as client:
        response = client.get(
            "/api/v1/trainer/replay/trn-degraded-001",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["meta"]["surfaces"]["trainer_replay"] == "degraded"
        assert payload["allowedActions"]["canCommit"] is False
        assert payload["allowedActions"]["canDiscard"] is False
        assert payload["allowedActions"]["canReplay"] is True


def test_tw04_list_can_replay_true_when_surface_not_unavailable():
    with _seeded_client_with_degraded_session() as client:
        response = client.get(
            "/api/v1/trainer/replay",
            params={"persona_id": "persona-alpha"},
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        surface = payload["meta"]["surfaces"]["trainer_replay"]
        assert surface in {"ok", "stale", "degraded"}, f"Unexpected surface: {surface!r}"
        for row in payload["data"]:
            assert row["allowedActions"]["canReplay"] is True
