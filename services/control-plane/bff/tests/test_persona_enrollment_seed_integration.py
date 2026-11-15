"""Enrolment through the real BFF path with the real Persona owner, Registry and Governance.

PERSONA-ENROLLMENT-SEED-LISTER-FIX-20261115.  The earlier enrolment tests
stood in for the Registry lister, which is how a defect in the lister's
paging passed.  Here the pool store uses its production HTTP lister and
reader against the real registry application holding hundreds of approved
artifacts with opaque keyset catalogue paging.
"""
from __future__ import annotations

import importlib.util
import io
import os
import sys
import urllib.error
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.registry import storage as registry_storage
from services.registry.models import (
    ArtifactState, ArtifactType, Lineage, RegistryEntry, StorageBackend, StorageRef,
)
from services.registry.service import app as registry_app
from services.runtime_auth_inbound import encode_jwt_hs256

_PATH = Path(__file__).with_name("test_persona_paper_enrollment.py")
_spec = importlib.util.spec_from_file_location("persona_paper_enrollment_world_fixtures", _PATH)
base = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = base
_spec.loader.exec_module(base)
world = base.world  # the real-owner fixture (Persona owner app, Governance app, coordinator)

REGISTRY_SECRET = "enrolment-registry-secret-0123456789"
CATALOGUE_SIZE = 600


def _entry(registry_id: str, n: int, persona: str | None, **over) -> RegistryEntry:
    meta = {"execution_context": "paper"}
    if persona:
        meta.update(persona_id=persona, persona_capital_binding_id="pcb-" + persona)
    meta.update(over.pop("metadata", {}))
    fields = dict(
        registry_id=registry_id, artifact_type=ArtifactType.EXECUTION_BUNDLE, strategy_id=f"strat-{n % 9}",
        version=f"1.0.{n}", artifact_state=ArtifactState.APPROVED,
        lineage=Lineage(source_run_ids=[f"run-{n}"]),
        storage_ref=StorageRef(backend=StorageBackend.OBJECT_STORE, path=f"s3://bucket/{registry_id}"),
        checksum=base.CHECKSUM, metadata=meta, owner_tenant=base.TENANT,
        created_at=f"2026-10-{1 + n % 28:02d}T{n % 24:02d}:{n % 60:02d}:00+00:00",
        approval_decision_id=f"gate-{registry_id}", approved_at="2026-11-01T00:00:00+00:00",
    )
    fields.update(over)
    return RegistryEntry(**fields)


@pytest.fixture
def real_registry(world, monkeypatch):
    for key, value in {
        "PANTHEON_REGISTRY_AUTH_MODE": "strict", "PANTHEON_REGISTRY_JWT_SECRET": REGISTRY_SECRET,
        "PANTHEON_REGISTRY_JWT_ISSUER": "", "PANTHEON_REGISTRY_JWT_AUDIENCE": "",
        "REGISTRY_STORE_BACKEND": "memory", "PERSONA_REGISTRY_BASE_URL": "http://registry.test",
        "PERSONA_REGISTRY_SERVICE_TOKEN": encode_jwt_hs256(
            {"sub": "persona-svc", "tenant": base.TENANT, "roles": ["operator"], "exp": 4102444800},
            secret=REGISTRY_SECRET),
    }.items():
        monkeypatch.setenv(key, value)
    registry_storage.reset_store()
    store = registry_storage.get_store()
    requests: list[str] = []
    with TestClient(registry_app) as client:
        def opener(request, timeout=None):
            path = request.full_url.replace("http://registry.test", "")
            requests.append(path)
            response = client.get(path, headers=dict(request.header_items()))
            if response.status_code >= 400:
                raise urllib.error.HTTPError(request.full_url, response.status_code, "registry",
                                             response.headers, io.BytesIO(response.content))
            return io.BytesIO(response.content)

        # The production lister and reader, as the persona service builds them.
        pool = world.module.POOL_STORE
        pool_module = sys.modules[type(pool).__module__]
        monkeypatch.setattr(pool_module, "urlopen", opener)
        world.module.POOL_STORE = type(pool)(pool._records)
        assert world.module.POOL_STORE._lister is pool_module.http_owner_artifact_lister
        store.put(_entry("artifact-a", 1, base.PERSONA, approval_decision_id="gate-baseline-1",
                         rollback_target="rollback-no-trade"))
        for i in range(CATALOGUE_SIZE):
            store.put(_entry(f"noise-{i:05d}", 100 + i, f"persona-other-{i % 30}" if i % 4 == 0 else None))
        yield type("Registry", (), {"store": store, "requests": requests})
    registry_storage.reset_store()


def _enrollment(world, persona_id: str):
    return base.PersonaPaperEnrollment(
        tenant_id=base.TENANT, persona_id=persona_id, capital_pool_id=world.ids.capital_pool_id,
        persona_capital_binding_id=world.ids.persona_capital_binding_id,
        deployment_plan_id=world.ids.deployment_plan_id, persona_owner=world.port,
        transport=world.gate, runtime_binding_reader=lambda *_: deepcopy(world.runtime_rows),
        governance_actor_id=base.GATE, proposer=world.proposer, proposer_actor_id=base.PROPOSER,
    )


def test_trading_persona_among_hundreds_of_approved_artifacts_is_enrolled(world, real_registry):
    base._complete_paper_admission(world)
    world.activate_runtime()
    record = world.store.get(world.record.tenant_id, world.record.idempotency_key)

    outcome = world.coordinator("reconciler").reconcile_paper_enrollment(record)

    readback = outcome.references["paper_enrollment"]
    assert readback["status"] == "enrolled", readback
    assert base._persona(world)["lifecycle_state"] == "paper_owner"
    pool = world.module.POOL_STORE.current(base.PERSONA, base.TENANT)
    assert [m["artifact_ref"] for m in pool["members"]] == ["artifact-a"]
    assert pool["members"][0]["baseline_ref"] == "gate-baseline-1"
    # Bounded registry traffic: independent of the 600+ approved artifacts.
    assert len(real_registry.requests) <= 6, real_registry.requests
    assert not any("strategy-artifacts" in r for r in real_registry.requests)
    # The cycle observe step resolves through the same lister and sees the pool.
    bundle = base._observe(world)
    assert bundle["pool"]["members"] and "strategy_pool" not in bundle["unavailable"]


def test_release_probe_persona_is_not_enrolled_and_keeps_its_lifecycle(world, real_registry):
    base._complete_paper_admission(world)
    world.activate_runtime()
    probe = "persona-release-probe-1"
    world.make_persona(probe)
    before = world.owner.get(probe).model_dump(mode="json")
    real_registry.store.put(_entry("artifact-probe", 2, probe, approval_decision_id="gate-probe"))

    outcome = _enrollment(world, probe).enroll()

    assert (outcome.status, outcome.reason) == ("not_eligible", "release_probe_or_test_persona_not_enrolled")
    assert world.owner.get(probe).model_dump(mode="json")["lifecycle_state"] == before["lifecycle_state"]
    assert world.module.POOL_STORE._records.get(probe) is None
    assert real_registry.requests == []  # never reached the registry
    assert base._persona(world)["lifecycle_state"] == "research_only"  # the trading persona is unaffected


def test_two_approved_paper_artifacts_for_one_persona_refuse_with_a_readable_reason(world, real_registry):
    base._complete_paper_admission(world)
    world.activate_runtime()
    real_registry.store.put(_entry("artifact-b", 3, base.PERSONA, approval_decision_id="gate-b"))
    outcome = _enrollment(world, base.PERSONA).enroll()
    assert outcome.status == "refused"
    assert "found 2" in outcome.reason
    assert base._persona(world)["lifecycle_state"] == "research_only"  # pool seeds first; nothing advanced
