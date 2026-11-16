"""Pool artifact lister against the REAL registry application.

PERSONA-ENROLLMENT-SEED-LISTER-FIX-20261115.  The defect passed tests that
faked the registry paging: the deployed catalogue returns opaque keyset page
tokens and holds hundreds of approved artifacts.  These tests serve the real
registry app (strict auth, real in-memory owner store) to the lister.
"""
from __future__ import annotations

import io
import os
import sys
import time
import urllib.error
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.registry import storage as registry_storage
from services.registry.models import (
    ArtifactState,
    ArtifactType,
    Lineage,
    RegistryEntry,
    StorageBackend,
    StorageRef,
)
from services.registry.service import app as registry_app
from services.runtime_auth_inbound import encode_jwt_hs256

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import persona_strategy_pool as psp  # noqa: E402

TENANT = "tenant-a"
SECRET = "lister-real-registry-secret"
CHECKSUM = "sha256:" + "b" * 64
CATALOGUE_SIZE = 1000


def make_entry(registry_id: str, n: int, *, persona: str | None = None, **over) -> RegistryEntry:
    metadata = {"execution_context": "paper"}
    if persona:
        metadata.update(persona_id=persona, persona_capital_binding_id="pcb-" + persona)
    metadata.update(over.pop("metadata", {}))
    fields = dict(
        registry_id=registry_id, artifact_type=ArtifactType.EXECUTION_BUNDLE, strategy_id=f"strat-{n % 9}",
        version=f"1.0.{n}", artifact_state=ArtifactState.APPROVED,
        lineage=Lineage(source_run_ids=[f"run-{n}"]),
        storage_ref=StorageRef(backend=StorageBackend.OBJECT_STORE, path=f"s3://bucket/{registry_id}"),
        checksum=CHECKSUM, metadata=metadata, owner_tenant=TENANT,
        created_at=f"2026-10-{1 + n % 28:02d}T{n % 24:02d}:{n % 60:02d}:00+00:00",
        approval_decision_id=f"gate-{registry_id}", approved_at="2026-11-01T00:00:00+00:00",
    )
    fields.update(over)
    return RegistryEntry(**fields)


class RealRegistry:
    """The real registry app; counts every request the lister makes."""

    def __init__(self, monkeypatch) -> None:
        for key, value in {
            "PANTHEON_REGISTRY_AUTH_MODE": "strict", "PANTHEON_REGISTRY_JWT_SECRET": SECRET,
            "PANTHEON_REGISTRY_JWT_ISSUER": "", "PANTHEON_REGISTRY_JWT_AUDIENCE": "",
            "PERSONA_REGISTRY_BASE_URL": "http://registry.test", "REGISTRY_STORE_BACKEND": "memory",
        }.items():
            monkeypatch.setenv(key, value)
        token = encode_jwt_hs256(
            {"sub": "persona-svc", "tenant": TENANT, "roles": ["operator"], "exp": 4102444800}, secret=SECRET
        )
        monkeypatch.setenv("PERSONA_REGISTRY_SERVICE_TOKEN", token)
        registry_storage.reset_store()
        self.store = registry_storage.get_store()
        self.requests: list[str] = []
        self._client_cm = TestClient(registry_app)
        self.client = self._client_cm.__enter__()

        def opener(request, timeout=None):
            url = request.full_url
            self.requests.append(url.replace("http://registry.test", ""))
            response = self.client.get(url.replace("http://registry.test", ""), headers=dict(request.header_items()))
            if response.status_code >= 400:
                raise urllib.error.HTTPError(url, response.status_code, "registry", response.headers,
                                             io.BytesIO(response.content))
            return io.BytesIO(response.content)

        monkeypatch.setattr(psp, "urlopen", opener)

    def close(self) -> None:
        self._client_cm.__exit__(None, None, None)
        registry_storage.reset_store()

    def load(self, persona: str, *, noise: int = CATALOGUE_SIZE - 2) -> None:
        self.store.put(make_entry("art-baseline", 1, persona=persona,
                                  metadata={"fail_closed_baseline": True}, approval_decision_id=None))
        self.store.put(make_entry("art-run", 2, persona=persona, rollback_target="art-baseline",
                                  metadata={"rollback_target_registry_id": "art-baseline"}))
        for i in range(noise):
            other = f"persona-other-{i % 40}" if i % 3 == 0 else None
            self.store.put(make_entry(f"noise-{i:05d}", 100 + i, persona=other))


@pytest.fixture
def registry(monkeypatch):
    real = RealRegistry(monkeypatch)
    yield real
    real.close()


def legacy_lister(persona_id: str, tenant_id: str):
    """The pre-fix lister, kept verbatim to reproduce the hosted failure."""
    offset = 0
    while True:
        payload = psp._http_json(
            f"/api/registry/strategy-artifacts?artifact_state=approved&limit=200&offset={offset}", tenant_id)
        for row in payload["items"]:
            psp.http_registry_entry_reader(row["registry_id"], tenant_id)
        nxt = payload.get("next_page_token")
        if not nxt:
            return []
        offset = int(nxt)


def test_reproduces_hosted_failure_then_fix_resolves_the_persona(registry):
    registry.load("persona-1")
    with pytest.raises(ValueError, match="invalid literal for int"):
        legacy_lister("persona-1", TENANT)
    legacy_calls = len(registry.requests)
    assert legacy_calls >= 200  # one entry read per catalogue row before the token blew up

    registry.requests.clear()
    found = psp.http_owner_artifact_lister("persona-1", TENANT)
    assert [e["registry_id"] for e in found] == ["art-run"]
    # bounded: one lineage read plus one owner-record read per artifact the persona owns
    assert len(registry.requests) == 3
    assert registry.requests[0].startswith("/api/registry/lineage?")


def test_registry_calls_do_not_depend_on_catalogue_size(registry):
    registry.load("persona-1", noise=50)
    psp.http_owner_artifact_lister("persona-1", TENANT)
    small = len(registry.requests)
    registry.load("persona-2", noise=CATALOGUE_SIZE)
    registry.requests.clear()
    started = time.monotonic()
    found = psp.http_owner_artifact_lister("persona-2", TENANT)
    elapsed = time.monotonic() - started
    assert [e["registry_id"] for e in found] == ["art-run"]
    assert len(registry.requests) == small == 3
    assert elapsed < 5.0


def test_lister_does_not_page_the_catalogue_so_keyset_tokens_are_never_parsed(registry):
    registry.load("persona-1")
    page = registry.client.get(
        "/api/registry/strategy-artifacts?artifact_state=approved&limit=200",
        headers={"Authorization": "Bearer " + os.environ["PERSONA_REGISTRY_SERVICE_TOKEN"], "X-Tenant-Id": TENANT},
    )
    # The registry really does answer with an opaque keyset token at this size.
    assert page.status_code == 200, page.text
    assert str(page.json()["next_page_token"]).startswith("k1:")
    registry.requests.clear()
    psp.http_owner_artifact_lister("persona-1", TENANT)
    assert not any("strategy-artifacts" in r for r in registry.requests)


def test_zero_candidates_fail_closed_through_the_seed(registry):
    registry.load("persona-1", noise=20)
    assert psp.http_owner_artifact_lister("persona-unknown", TENANT) == []


def test_several_candidates_are_returned_so_the_seed_refuses(registry):
    registry.load("persona-1", noise=20)
    registry.store.put(make_entry("art-run-2", 3, persona="persona-1"))
    found = psp.http_owner_artifact_lister("persona-1", TENANT)
    assert sorted(e["registry_id"] for e in found) == ["art-run", "art-run-2"]
    store = psp.PersonaStrategyPoolStore(_NoRecords(), owner_lister=psp.http_owner_artifact_lister)
    with pytest.raises(psp.PoolMemberRefused, match="found 2"):
        store.seed_from_owner_records("persona-1", TENANT, actor_id="op")


def test_another_tenants_artifact_is_not_listed(registry):
    registry.load("persona-1", noise=5)
    registry.store.put(make_entry("art-foreign", 4, persona="persona-1", owner_tenant="tenant-b"))
    found = psp.http_owner_artifact_lister("persona-1", TENANT)
    assert [e["registry_id"] for e in found] == ["art-run"]


class _NoRecords:
    def get(self, _pid):
        return None
