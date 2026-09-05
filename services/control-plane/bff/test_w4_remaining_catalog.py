#!/usr/bin/env python3
"""APP-002-W4 catalog reads through explicit domain ports and doubles."""
from __future__ import annotations

import os
import sys


from services.control_plane.bff.ports import (
    PersonaRegistryReadsPort,
    TrainingSessionTrainerPort,
    create_in_memory_persona_capital_runtime_port,
)


class _PersonaRegistryDouble:
    def __init__(self) -> None:
        self.personas = {"persona-alpha": {"id": "persona-alpha", "persona_id": "persona-alpha"}}
        self.bindings = {"persona-alpha": [{"id": "binding-042"}]}
        self.sessions = {"persona-alpha": [{"session_id": "sess-001", "status": "active"}]}
        self.teaching = {"persona-alpha": [{"session_id": "teach-001", "status": "active"}]}
        self.capabilities = {"persona-alpha": {"snapshot_id": "cap-001"}}

    def list_personas(self, **_kwargs):
        return list(self.personas.values())

    def get_persona(self, persona_id):
        return self.personas.get(persona_id)

    def get_bindings_for_persona(self, persona_id):
        return self.bindings.get(persona_id, [])

    def list_sessions_for_persona(self, persona_id, **_kwargs):
        return self.sessions.get(persona_id, [])

    def list_teaching_sessions_for_persona(self, persona_id, **_kwargs):
        return self.teaching.get(persona_id, [])

    def get_capability_snapshot_for_persona(self, persona_id):
        return self.capabilities.get(persona_id)


class _TrainingSessionDouble:
    def get_trainer_session(self, session_id):
        if session_id == "sess-001":
            return {"session_id": session_id, "status": "active"}
        return None


def test_w4_remaining_catalog() -> None:
    catalog = create_in_memory_persona_capital_runtime_port(
        personas=[{"id": "persona-alpha", "persona_id": "persona-alpha"}],
        capital_pools=[{"id": "pool-main", "pool_id": "pool-main"}],
        bindings=[
            {
                "id": "binding-042",
                "binding_id": "binding-042",
                "persona_id": "persona-alpha",
                "capital_pool_id": "pool-main",
            }
        ],
        deployment_plans=[{"id": "plan-F-042", "plan_id": "plan-F-042"}],
        runtime_bindings=[
            {
                "id": "runtime-binding-042",
                "binding_id": "runtime-binding-042",
                "runtime_id": "runtime-042",
            }
        ],
    )
    persona = PersonaRegistryReadsPort(store=_PersonaRegistryDouble())
    training = TrainingSessionTrainerPort(training=_TrainingSessionDouble())

    assert catalog.list_personas()[0]["id"] == "persona-alpha"
    assert catalog.get_persona("persona-alpha") is not None
    assert persona.list_persona_sessions("persona-alpha")[0]["session_id"] == "sess-001"
    assert training.get_trainer_session("sess-001")["status"] == "active"
    assert persona.list_persona_teaching_sessions("persona-alpha")
    assert persona.get_persona_capabilities("persona-alpha")["snapshot_id"] == "cap-001"
    assert catalog.list_capital_pools()[0]["id"] == "pool-main"
    assert catalog.list_bindings()[0]["id"] == "binding-042"
    assert catalog.list_deployment_plans()[0]["id"] == "plan-F-042"
    assert catalog.list_runtime_bindings()[0]["runtime_id"] == "runtime-042"
    assert catalog.get_runtime_binding_by_runtime_id("runtime-042")["binding_id"] == (
        "runtime-binding-042"
    )
