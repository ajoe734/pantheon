"""Unit tests for pool publish guards that need no owner transport."""
from __future__ import annotations

import pytest

from services.control_plane.bff.persona_pool_publisher import (
    InMemoryPublishRecords,
    OwnerPoolReader,
    PersonaPoolPublisher,
    PoolPublishRefused,
)
from services.control_plane.persona.persona_strategy_pool import PoolNotFound


class _NoCoordinator:
    transport = None
    runtime_binding_reader = None

    def publish_pool_envelope(self, publication):  # pragma: no cover - must not run
        raise AssertionError("owner chain must not run")


class _Pool:
    def current(self, persona_id, tenant_id):
        raise PoolNotFound("no strategy pool for persona 'p'")

    version = lambda self, persona_id, tenant_id, number: self.current(persona_id, tenant_id)  # noqa: E731


def test_beyond_paper_refused_before_any_owner_read():
    publisher = PersonaPoolPublisher(coordinator=_NoCoordinator(), pool_store=_Pool())
    for stage in ("canary", "live", "none"):
        with pytest.raises(PoolPublishRefused) as exc:
            publisher.publish(tenant_id="t", persona_id="p", requested_by="op", stage=stage)
        assert exc.value.status_code == 403


def test_missing_pool_is_not_found_not_a_guess():
    publisher = PersonaPoolPublisher(coordinator=_NoCoordinator(), pool_store=_Pool())
    with pytest.raises(PoolPublishRefused) as exc:
        publisher.publish(tenant_id="t", persona_id="p", requested_by="op")
    assert exc.value.status_code == 404


def test_owner_pool_reader_maps_missing_to_not_found_and_reads_versions():
    seen = []

    def fetch(path, tenant):
        seen.append((path, tenant))
        return {"members": [], "pool_version": 3} if path.endswith("/versions/3") else None

    reader = OwnerPoolReader(fetch)
    assert reader.version("p", "t", 3)["pool_version"] == 3
    with pytest.raises(PoolNotFound):
        reader.current("p", "t")
    assert seen[0] == ("/api/personas/p/strategy-pool/versions/3", "t")


def test_publish_records_use_compare_and_set():
    records = InMemoryPublishRecords()
    ok, _ = records.compare_and_set("k", None, {"a": 1})
    stale, current = records.compare_and_set("k", None, {"a": 2})
    assert ok and not stale and current == {"a": 1}
