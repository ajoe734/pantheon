"""Tests for the Persona/Training-Session domain ports.

Validates:
1. PersonaRegistryReadsPort delegates persona/session/teaching/capability
   reads to an injected Persona Registry-backed store and merges bindings
2. TrainingSessionTrainerPort delegates every trainer/replay method to an
   injected Training Session-backed store
3. RapidEvaluationPort exposes a fixed Training Session owner (ACG-02-017)
   and only executes once a caller binds a backend
4. PersonaTrainingDomainPort composes all three ports
"""

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

BFF_DIR = Path(__file__).resolve().parent.parent

from services.control_plane.bff.ports.persona_training import (
    PersonaRegistryReadsPort,
    PersonaTrainingDomainPort,
    RapidEvaluationOwnership,
    RapidEvaluationPort,
    TrainingSessionTrainerPort,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakePersonaRegistryStore:
    def __init__(self) -> None:
        self.personas = {
            "persona-alpha": {"persona_id": "persona-alpha", "lifecycle_state": "active"},
        }
        self.bindings = {"persona-alpha": [{"binding_id": "bind-1"}]}
        self.sessions = {"persona-alpha": [{"session_id": "sess-1"}]}
        self.teaching_sessions = {"persona-alpha": [{"session_id": "teach-1"}]}
        self.capabilities = {"persona-alpha": {"snapshot_id": "cap-1"}}
        self.list_calls: List[Dict[str, Any]] = []

    def list_personas(self, *, lifecycle_state=None, mandate=None, strategy_family=None):
        self.list_calls.append(
            {
                "lifecycle_state": lifecycle_state,
                "mandate": mandate,
                "strategy_family": strategy_family,
            }
        )
        return list(self.personas.values())

    def get_persona(self, persona_id):
        return self.personas.get(persona_id)

    def get_bindings_for_persona(self, persona_id):
        return self.bindings.get(persona_id, [])

    def list_sessions_for_persona(self, persona_id, *, status=None):
        return self.sessions.get(persona_id, [])

    def list_teaching_sessions_for_persona(self, persona_id, *, status=None):
        return self.teaching_sessions.get(persona_id, [])

    def get_capability_snapshot_for_persona(self, persona_id):
        return self.capabilities.get(persona_id)


class _FakeTrainingSessionStore:
    def __init__(self) -> None:
        self.calls: List[Any] = []

    def create_trainer_session(self, **kwargs):
        self.calls.append(("create_trainer_session", kwargs))
        return {"session_id": "trn-1", **kwargs}

    def list_trainer_sessions(self, **kwargs):
        self.calls.append(("list_trainer_sessions", kwargs))
        return [{"session_id": "trn-1"}]

    def get_trainer_session(self, session_id):
        self.calls.append(("get_trainer_session", session_id))
        return {"session_id": session_id}

    def get_trainer_controls(self, session_id):
        self.calls.append(("get_trainer_controls", session_id))
        return {"session_id": session_id, "controls": []}

    def patch_trainer_controls(self, session_id, **kwargs):
        self.calls.append(("patch_trainer_controls", session_id, kwargs))
        return {"session_id": session_id, **kwargs}

    def append_trainer_message(self, session_id, **kwargs):
        self.calls.append(("append_trainer_message", session_id, kwargs))
        return {"session_id": session_id, **kwargs}

    def get_trainer_preview(self, session_id, **kwargs):
        self.calls.append(("get_trainer_preview", session_id, kwargs))
        return {"session_id": session_id}

    def refresh_trainer_preview(self, session_id, **kwargs):
        self.calls.append(("refresh_trainer_preview", session_id, kwargs))
        return {"session_id": session_id}

    def list_trainer_replays(self, **kwargs):
        self.calls.append(("list_trainer_replays", kwargs))
        return [{"session_id": "trn-1"}]

    def get_trainer_replay(self, session_id):
        self.calls.append(("get_trainer_replay", session_id))
        return {"session_id": session_id}

    def commit_trainer_replay(self, session_id, **kwargs):
        self.calls.append(("commit_trainer_replay", session_id, kwargs))
        return {"session_id": session_id, "status": "committed"}

    def discard_trainer_replay(self, session_id, **kwargs):
        self.calls.append(("discard_trainer_replay", session_id, kwargs))
        return {"session_id": session_id, "status": "discarded"}


# ---------------------------------------------------------------------------
# 1. PersonaRegistryReadsPort
# ---------------------------------------------------------------------------

class TestPersonaRegistryReadsPort:
    def test_requires_store(self):
        port = PersonaRegistryReadsPort()
        with pytest.raises(RuntimeError):
            port.list_personas()

    def test_list_personas_delegates(self):
        store = _FakePersonaRegistryStore()
        port = PersonaRegistryReadsPort(store=store)
        result = port.list_personas(lifecycle_state="active")
        assert result == [{"persona_id": "persona-alpha", "lifecycle_state": "active"}]
        assert store.list_calls == [
            {"lifecycle_state": "active", "mandate": None, "strategy_family": None}
        ]

    def test_get_persona_merges_bindings(self):
        store = _FakePersonaRegistryStore()
        port = PersonaRegistryReadsPort(store=store)
        result = port.get_persona("persona-alpha")
        assert result["persona_id"] == "persona-alpha"
        assert result["bindings"] == [{"binding_id": "bind-1"}]

    def test_get_persona_missing_returns_none(self):
        store = _FakePersonaRegistryStore()
        port = PersonaRegistryReadsPort(store=store)
        assert port.get_persona("persona-missing") is None

    def test_list_persona_sessions_delegates(self):
        store = _FakePersonaRegistryStore()
        port = PersonaRegistryReadsPort(store=store)
        assert port.list_persona_sessions("persona-alpha") == [{"session_id": "sess-1"}]

    def test_list_persona_teaching_sessions_delegates(self):
        store = _FakePersonaRegistryStore()
        port = PersonaRegistryReadsPort(store=store)
        assert port.list_persona_teaching_sessions("persona-alpha") == [
            {"session_id": "teach-1"}
        ]

    def test_get_persona_capabilities_delegates(self):
        store = _FakePersonaRegistryStore()
        port = PersonaRegistryReadsPort(store=store)
        assert port.get_persona_capabilities("persona-alpha") == {"snapshot_id": "cap-1"}


# ---------------------------------------------------------------------------
# 2. TrainingSessionTrainerPort
# ---------------------------------------------------------------------------

class TestTrainingSessionTrainerPort:
    def test_requires_training_store(self):
        port = TrainingSessionTrainerPort()
        with pytest.raises(RuntimeError):
            port.list_trainer_sessions()

    def test_create_trainer_session_delegates(self):
        training = _FakeTrainingSessionStore()
        port = TrainingSessionTrainerPort(training=training)
        result = port.create_trainer_session(persona_id="persona-alpha", objective="obj")
        assert result["session_id"] == "trn-1"
        assert training.calls[0][0] == "create_trainer_session"

    def test_replay_methods_delegate(self):
        training = _FakeTrainingSessionStore()
        port = TrainingSessionTrainerPort(training=training)

        assert port.list_trainer_replays() == [{"session_id": "trn-1"}]
        assert port.get_trainer_replay("trn-1") == {"session_id": "trn-1"}
        assert port.commit_trainer_replay("trn-1", actor_id="op-1")["status"] == "committed"
        assert port.discard_trainer_replay("trn-1", actor_id="op-1")["status"] == "discarded"

        called_methods = [call[0] for call in training.calls]
        assert called_methods == [
            "list_trainer_replays",
            "get_trainer_replay",
            "commit_trainer_replay",
            "discard_trainer_replay",
        ]

    def test_control_and_preview_methods_delegate(self):
        training = _FakeTrainingSessionStore()
        port = TrainingSessionTrainerPort(training=training)

        assert port.get_trainer_controls("trn-1")["session_id"] == "trn-1"
        assert port.patch_trainer_controls("trn-1", patch={})["session_id"] == "trn-1"
        assert port.append_trainer_message("trn-1", text="hi")["session_id"] == "trn-1"
        assert port.get_trainer_preview("trn-1")["session_id"] == "trn-1"
        assert port.refresh_trainer_preview("trn-1")["session_id"] == "trn-1"


# ---------------------------------------------------------------------------
# 3. RapidEvaluationPort
# ---------------------------------------------------------------------------

class TestRapidEvaluationPort:
    def test_owner_is_training_session(self):
        port = RapidEvaluationPort()
        assert port.owner == "training-session"
        assert isinstance(port.ownership, RapidEvaluationOwnership)
        assert port.ownership.implementation_symbol == "run_rapid_eval"
        assert (
            port.ownership.implementation_module
            == "services/training-session/rapid_eval_integration.py"
        )

    def test_create_without_backend_raises(self):
        port = RapidEvaluationPort()
        with pytest.raises(RuntimeError, match="training-session"):
            port.create_rapid_eval("trn-1", eval_scope="full")

    def test_get_without_backend_raises(self):
        port = RapidEvaluationPort()
        with pytest.raises(RuntimeError, match="training-session"):
            port.get_rapid_eval("reval-1")

    def test_create_and_get_delegate_to_bound_backend(self):
        created: List[Any] = []
        fetched: List[Any] = []

        def fake_create(session_id, **kwargs):
            created.append((session_id, kwargs))
            return {"rapid_eval_id": "reval-1", "session_id": session_id}

        def fake_get(eval_id, **kwargs):
            fetched.append((eval_id, kwargs))
            return {"rapid_eval_id": eval_id}

        port = RapidEvaluationPort(create=fake_create, get=fake_get)
        result = port.create_rapid_eval("trn-1", eval_scope="full")
        assert result == {"rapid_eval_id": "reval-1", "session_id": "trn-1"}
        assert created == [("trn-1", {"eval_scope": "full"})]

        result = port.get_rapid_eval("reval-1")
        assert result == {"rapid_eval_id": "reval-1"}
        assert fetched == [("reval-1", {})]


# ---------------------------------------------------------------------------
# 4. PersonaTrainingDomainPort
# ---------------------------------------------------------------------------

class TestPersonaTrainingDomainPort:
    def test_defaults_to_unbound_component_ports(self):
        domain_port = PersonaTrainingDomainPort()
        assert domain_port.rapid_eval_owner == "training-session"
        with pytest.raises(RuntimeError):
            domain_port.list_personas()

    def test_composes_injected_ports(self):
        persona_store = _FakePersonaRegistryStore()
        training_store = _FakeTrainingSessionStore()
        rapid_eval_calls: List[Any] = []

        domain_port = PersonaTrainingDomainPort(
            persona_port=PersonaRegistryReadsPort(store=persona_store),
            trainer_port=TrainingSessionTrainerPort(training=training_store),
            rapid_eval_port=RapidEvaluationPort(
                create=lambda session_id, **kwargs: rapid_eval_calls.append(
                    (session_id, kwargs)
                )
                or {"rapid_eval_id": "reval-1"},
            ),
        )

        assert domain_port.list_personas() == [
            {"persona_id": "persona-alpha", "lifecycle_state": "active"}
        ]
        assert domain_port.get_persona("persona-alpha")["bindings"] == [
            {"binding_id": "bind-1"}
        ]
        assert domain_port.list_trainer_sessions() == [{"session_id": "trn-1"}]
        assert domain_port.get_trainer_session("trn-1") == {"session_id": "trn-1"}
        assert domain_port.create_rapid_eval("trn-1", eval_scope="full") == {
            "rapid_eval_id": "reval-1"
        }
        assert rapid_eval_calls == [("trn-1", {"eval_scope": "full"})]
        assert domain_port.rapid_eval_owner == "training-session"
