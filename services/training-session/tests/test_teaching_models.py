from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from jsonschema import Draft7Validator


SERVICE_DIR = Path(__file__).resolve().parents[1]


def _load_models_module():
    spec = importlib.util.spec_from_file_location("training_session_models_test", SERVICE_DIR / "models.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["training_session_models_test"] = module
    spec.loader.exec_module(module)
    return module


def _event_payload(**overrides: object) -> dict:
    payload = {
        "event_id": "tevt-20260516-001",
        "session_id": "trn-20260516-001",
        "tenant_id": "tenant-test",
        "event_type": "message",
        "actor_type": "user",
        "actor": "operator",
        "payload": {"message_body": "Tighten the drawdown cap."},
        "message_body": "Tighten the drawdown cap.",
        "timestamp": "2026-05-16T10:00:30Z",
        "emitted_at": "2026-05-16T10:00:30Z",
        "correlation_id": "trace-trn-001:message-001",
        "sequence_number": 1,
    }
    payload.update(overrides)
    return payload


def _session_payload(**overrides: object) -> dict:
    payload = {
        "id": "trn-20260516-001",
        "session_id": "trn-20260516-001",
        "persona_id": "persona-alpha",
        "tenant_id": "tenant-test",
        "opened_by": "operator-1",
        "mode": "coaching",
        "session_type": "trainer",
        "objective": "Coach macro-event reversal behavior.",
        "topic": "Coach macro-event reversal behavior.",
        "status": "active",
        "started_at": "2026-05-16T10:00:00Z",
        "ended_at": None,
        "current_control_state_ref": "controls://trainer/trn-20260516-001/current",
        "trace_id": "trace-trn-20260516-001",
        "context_refs": [{"type": "research_ticket", "id": "rt-1"}],
        "actor_context": {"persona_display_name": "Alpha Persona"},
        "events": [_event_payload()],
        "outcomes": [],
    }
    payload.update(overrides)
    return payload


def test_teaching_schemas_are_valid_draft7() -> None:
    module = _load_models_module()

    Draft7Validator.check_schema(module.load_teaching_session_schema())
    Draft7Validator.check_schema(module.load_teaching_event_schema())


def test_teaching_event_round_trips_through_model_and_schema() -> None:
    module = _load_models_module()
    payload = _event_payload()

    module.validate_teaching_event_payload(payload)
    event = module.TeachingEvent.from_dict(payload)

    assert event.event_type == "message"
    assert event.actor_type == "user"
    assert event.payload["message_body"] == "Tighten the drawdown cap."
    assert event.to_dict() == payload
    module.validate_teaching_event_payload(event.to_dict())


def test_teaching_session_round_trips_through_model_and_schema() -> None:
    module = _load_models_module()
    payload = _session_payload()

    module.validate_teaching_session_payload(payload)
    session = module.TeachingSession.from_dict(payload)

    assert session.session_id == "trn-20260516-001"
    assert session.persona_id == "persona-alpha"
    assert session.mode == "coaching"
    assert session.status == "active"
    assert session.to_dict() == payload
    module.validate_teaching_session_payload(session.to_dict())


def test_terminal_session_status_requires_ended_at() -> None:
    module = _load_models_module()
    payload = _session_payload(status="completed", ended_at=None)

    with pytest.raises(module.TeachingValidationError, match="ended_at"):
        module.TeachingSession.from_dict(payload)


def test_session_rejects_duplicate_event_ids() -> None:
    module = _load_models_module()
    payload = _session_payload(
        events=[
            _event_payload(sequence_number=1),
            _event_payload(sequence_number=2),
        ]
    )

    with pytest.raises(module.TeachingValidationError, match="duplicate event_id"):
        module.TeachingSession.from_dict(payload)


def test_event_rejects_timestamp_alias_drift() -> None:
    module = _load_models_module()
    payload = _event_payload(emitted_at="2026-05-16T10:01:00Z")

    with pytest.raises(module.TeachingValidationError, match="emitted_at"):
        module.TeachingEvent.from_dict(payload)


def test_event_session_cross_check() -> None:
    module = _load_models_module()
    session = module.TeachingSession.from_dict(_session_payload())
    event = module.TeachingEvent.from_dict(_event_payload())
    wrong_event = module.TeachingEvent.from_dict(_event_payload(session_id="trn-other"))
    wrong_tenant = module.TeachingEvent.from_dict(_event_payload(tenant_id="tenant-other"))

    assert module.validate_teaching_event_against_session(event, session) == []
    assert module.validate_teaching_event_against_session(wrong_event, session) == [
        "event.session_id must match session.session_id"
    ]
    assert module.validate_teaching_event_against_session(wrong_tenant, session) == [
        "event.tenant_id must match session.tenant_id"
    ]
