from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


SERVICE_DIR = Path(__file__).resolve().parent
REPO_ROOT = SERVICE_DIR.parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SERVICE_DIR))

from store import TrainingSessionStore  # noqa: E402
from trace_export import TraceExportError, export_session  # noqa: E402
from services.research.imitation.dataset_builder import (  # noqa: E402
    DatasetBuildRequest,
    ImitationDatasetBuilderConfig,
    build_dataset,
)
from services.research.imitation.preference_models import (  # noqa: E402
    validate_correction_trace_payload,
    validate_preference_example_payload,
)


TARGET = {
    "registry_id": "reg-alpha-v2",
    "strategy_id": "alpha-mean-reversion",
    "artifact_version": "2.0.0",
    "artifact_type": "strategy_spec",
    "promotion_state": "candidate",
}


def _event(event_id: str, event_type: str, sequence_number: int, **overrides: object) -> dict:
    payload = {
        "event_id": event_id,
        "session_id": "trn-export-001",
        "event_type": event_type,
        "actor_type": "user",
        "actor": "operator",
        "actor_label": "Operator",
        "payload": {"target": TARGET, "actor_role": "operator"},
        "timestamp": f"2026-05-16T10:0{sequence_number}:00Z",
        "emitted_at": f"2026-05-16T10:0{sequence_number}:00Z",
        "correlation_id": f"trn-export-001:{event_id}",
        "sequence_number": sequence_number,
    }
    payload.update(overrides)
    return payload


def _seed_store(tmp_path: Path) -> TrainingSessionStore:
    store = TrainingSessionStore(tmp_path)
    events = [
        _event(
            "tevt-export-001",
            "message",
            1,
            message_body="Tighten the drawdown cap.",
        ),
        _event(
            "tevt-export-002",
            "control_patch",
            2,
            summary="Drawdown cap corrected.",
            patch_delta=[
                {
                    "parameter_key": "risk_limit_bps",
                    "previous_value": 120,
                    "new_value": 80,
                    "rationale": "Cap risk before behavior cloning.",
                }
            ],
            payload={
                "target": TARGET,
                "actor_role": "operator",
                "confidence": 0.82,
                "reward": 0.4,
            },
        ),
        _event(
            "tevt-export-003",
            "commit",
            3,
            summary="Replay candidate committed by operator.",
            artifact_refs={
                "before_artifact_ref": "artifact-alpha-before",
                "candidate_artifact_ref": "artifact-alpha-candidate",
                "after_artifact_ref": "artifact-alpha-after",
                "decision_record_ref": "trainer-replay-decision:trn-export-001:committed",
            },
            payload={
                "target": TARGET,
                "actor_role": "operator",
                "confidence": 0.95,
            },
        ),
    ]
    session = {
        "id": "trn-export-001",
        "session_id": "trn-export-001",
        "persona_id": "persona-alpha",
        "opened_by": "operator-01",
        "mode": "coaching",
        "session_type": "trainer",
        "objective": "Coach drawdown containment.",
        "topic": "Coach drawdown containment.",
        "status": "completed",
        "started_at": "2026-05-16T10:00:00Z",
        "ended_at": "2026-05-16T10:04:00Z",
        "trace_id": "trace-trn-export-001",
        "target": TARGET,
        "replay_resolution": {
            "state": "committed",
            "decision_by": "operator-01",
            "decision_at": "2026-05-16T10:04:00Z",
        },
        "artifacts": {
            "before_artifact_ref": "artifact-alpha-before",
            "candidate_artifact_ref": "artifact-alpha-candidate",
            "after_artifact_ref": "artifact-alpha-after",
            "decision_record_ref": "trainer-replay-decision:trn-export-001:committed",
        },
        "events": events,
    }
    store.put_session(session)
    for event in events:
        store.append_event(event)
    return store


def test_export_session_bc_matches_imitation_dataset_shape(tmp_path: Path) -> None:
    store = _seed_store(tmp_path)

    payload = export_session("trn-export-001", "bc", store=store)

    assert payload["target_format"] == "bc"
    request = DatasetBuildRequest.from_dict(payload)
    result = build_dataset(
        request,
        config=ImitationDatasetBuilderConfig(require_feedback_event_ids=True),
    )
    assert result.num_included == 1
    session = result.dataset_payload["sessions"][0]
    assert session["decision"] == "approve"
    assert session["target"]["strategy_id"] == "alpha-mean-reversion"
    assert [step["feedback_event_id"] for step in session["steps"]] == [
        "tevt-export-002",
        "tevt-export-003",
    ]


def test_export_session_preference_validates_imt002_shapes(tmp_path: Path) -> None:
    store = _seed_store(tmp_path)

    payload = export_session("trn-export-001", "preference", store=store)

    assert payload["target_format"] == "preference"
    assert len(payload["preference_examples"]) == 2
    assert len(payload["correction_traces"]) == 1
    for example in payload["preference_examples"]:
        validate_preference_example_payload(example)
    for trace in payload["correction_traces"]:
        validate_correction_trace_payload(trace)
    edit_example = next(item for item in payload["preference_examples"] if item["action"] == "edit")
    assert edit_example["chosen_artifact"]["parameters"]["risk_limit_bps"] == 80
    assert edit_example["rejected_artifact"]["parameters"]["risk_limit_bps"] == 120


def test_export_session_trl_uses_prompt_chosen_rejected_pair_shape(tmp_path: Path) -> None:
    store = _seed_store(tmp_path)

    payload = export_session("trn-export-001", "trl", store=store)

    assert payload["target_format"] == "trl"
    assert len(payload["pairs"]) == 2
    for row in payload["pairs"]:
        assert set(["prompt", "chosen", "rejected"]).issubset(row)
        assert isinstance(row["prompt"], str)
        assert isinstance(row["chosen"], str)
        assert isinstance(row["rejected"], str)
        json.loads(row["chosen"])
        json.loads(row["rejected"])
    edit_row = next(row for row in payload["pairs"] if row["source_feedback_event_id"] == "tevt-export-002")
    assert json.loads(edit_row["chosen"])["parameters"]["risk_limit_bps"] == 80
    assert json.loads(edit_row["rejected"])["parameters"]["risk_limit_bps"] == 120


def test_export_session_rejects_unknown_format(tmp_path: Path) -> None:
    store = _seed_store(tmp_path)

    with pytest.raises(TraceExportError, match="target_format"):
        export_session("trn-export-001", "csv", store=store)


def test_export_session_rejects_missing_session(tmp_path: Path) -> None:
    store = TrainingSessionStore(tmp_path)

    with pytest.raises(TraceExportError, match="not found"):
        export_session("trn-missing", "bc", store=store)
