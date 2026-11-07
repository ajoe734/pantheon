"""Paper advisory admission: snapshot + ceiling make eligible Personas pass Agora eligibility."""
from __future__ import annotations

import pytest

from services.control_plane.bff.agora.interaction.router import _environment_allowed
from services.control_plane.bff.personas.reconciliation import (
    PersonaAdvisoryAdmissionError,
    PersonaAdvisoryAdmissionPort,
    paper_advisory_ineligibility,
    paper_advisory_snapshot_id,
)


class FakeOwner:
    def __init__(self, persona):
        self.persona = persona
        self.snapshots = {}
        self.updates = 0

    def upsert_persona_capability_snapshot(self, **kw):
        sem = {k: v for k, v in kw.items() if k != "generated_at"}
        cur = self.snapshots.get(kw["snapshot_id"])
        if cur is not None and cur["sem"] != sem:
            raise RuntimeError("conflict")
        self.snapshots.setdefault(kw["snapshot_id"], {"sem": sem, "capabilities": kw["capabilities"]})
        return {"snapshot_id": kw["snapshot_id"], "capabilities": kw["capabilities"]}

    def update_persona(self, pid, *, metadata=None, **_):
        self.updates += 1
        self.persona["metadata"] = {**self.persona.get("metadata", {}), **metadata}
        return self.persona


def _paper():
    return {"persona_id": "p1", "tenant_id": "t", "lifecycle_state": "research_only", "status": "active",
            "metadata": {"provisioning_reconciliation_state": "paper_running"}}


def test_missing_ceiling_reproduces_then_admission_fixes_and_is_stable():
    p = _paper()
    assert not _environment_allowed(p, "paper")  # reproduction: no ceiling
    owner = FakeOwner(p)
    port = PersonaAdvisoryAdmissionPort(persona_mutation_port=owner)
    first = port.ensure("p1", p, generated_at="t1")
    assert first["capabilities"] == ["persona_opinion"]
    assert first["capability_snapshot_id"] == paper_advisory_snapshot_id("p1")
    assert _environment_allowed(p, "paper") and not _environment_allowed(p, "canary")
    assert not _environment_allowed(p, "live")
    port.ensure("p1", p, generated_at="t2")  # repeat: stable, no extra write
    assert owner.updates == 1 and len(owner.snapshots) == 1


@pytest.mark.parametrize("patch", [
    {"lifecycle_state": "retired"},
    {"status": "suspended"},
    {"lifecycle_state": "draft"},
    {"lifecycle_state": "research_only", "metadata": {}},
])
def test_ineligible_personas_refused_without_writes(patch):
    p = {**_paper(), **patch}
    owner = FakeOwner(p)
    with pytest.raises(PersonaAdvisoryAdmissionError):
        PersonaAdvisoryAdmissionPort(persona_mutation_port=owner).ensure("p1", p, generated_at="t")
    assert not owner.snapshots and owner.updates == 0
    assert paper_advisory_ineligibility(p)


def test_higher_existing_ceiling_is_not_touched():
    p = _paper()
    p["metadata"]["environment_ceiling"] = "live"
    owner = FakeOwner(p)
    with pytest.raises(PersonaAdvisoryAdmissionError):
        PersonaAdvisoryAdmissionPort(persona_mutation_port=owner).ensure("p1", p, generated_at="t")
    assert owner.updates == 0
