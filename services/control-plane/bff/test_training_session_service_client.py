from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from ports import TrainingSessionTrainerPort


class _TrainingSessionDouble:
    def __init__(self) -> None:
        self.calls = []

    def create_trainer_session(self, **kwargs):
        self.calls.append(("create", kwargs))
        return {"session_id": "trn-service-001", **kwargs, "allowedActions": {"canSendMessage": True}}

    def append_trainer_message(self, session_id, **kwargs):
        self.calls.append(("message", session_id, kwargs))
        return {"event": {"session_id": session_id, "message_body": kwargs["message_body"]}}


def test_trainer_session_port_delegates_to_training_service_double() -> None:
    training = _TrainingSessionDouble()
    port = TrainingSessionTrainerPort(training=training)

    session = port.create_trainer_session(
        persona_id="persona-alpha", objective="Service-backed trainer session", context_refs=[], actor_id="operator-1"
    )
    message = port.append_trainer_message("trn-service-001", message_body="Adjust max drawdown.", actor_id="operator-1")

    assert session["session_id"] == "trn-service-001"
    assert session["allowedActions"]["canSendMessage"] is True
    assert message["event"]["message_body"] == "Adjust max drawdown."
    assert [call[0] for call in training.calls] == ["create", "message"]
