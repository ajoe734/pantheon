"""Bounded owner read of admissible card drafts (CARD-ADMISSION-DRAFT-READ-BOUNDED-20261125)."""

from __future__ import annotations

import contextlib
import json
import tracemalloc

from .models import (
    ArtifactState, ArtifactType, Lineage, RegistryEntryCreate, StorageBackend, StorageRef,
)
from .pg_store import PostgresRegistryStore
from .service import RegistryService
from .storage import RegistryStore
from .test_service import _bearer, _jwt, strict_client  # noqa: F401


def _create(store, rid, *, tenant="tenant-a", card=True, state=ArtifactState.DRAFT, bulk=5000, meta_tenant=None):
    wiring = {"status": "gate_ready"} if card else {"status": "blocked"}
    spec = {"strategy_id": rid, "bulk": "x" * bulk, "metadata": {"card_wiring": wiring}}
    store.create(
        RegistryEntryCreate(
            artifact_type=ArtifactType.STRATEGY_SPEC, strategy_id=rid, version="1.0.0",
            artifact_state=state, lineage=Lineage(source_run_ids=["run"]),
            storage_ref=StorageRef(backend=StorageBackend.INLINE, path="$.entry.metadata.strategy_spec"),
            checksum=f"sha256:{rid}",
            metadata={"strategy_spec": spec, "tenant_id": meta_tenant or tenant},
        ),
        rid, actor={"tenant": tenant},
    )


def test_owner_read_does_not_materialise_non_matching_drafts_hosted_shape():
    store = RegistryStore()
    for i in range(7441):
        _create(store, f"reg-plain-{i:05d}", card=False, bulk=5600)
    for i in range(3):
        _create(store, f"reg-card-{i}")
    _create(store, "reg-card-other", tenant="tenant-b")
    _create(store, "reg-card-approved", state=ArtifactState.APPROVED)
    service = RegistryService(store=store)
    tracemalloc.start()
    tracemalloc.reset_peak()
    base = tracemalloc.get_traced_memory()[0]
    page = service.card_admission_draft_page(
        visible_tenants=frozenset({"tenant-a"}), tenant_id="tenant-a", page_size=20)
    peak = tracemalloc.get_traced_memory()[1] - base
    tracemalloc.stop()
    body = json.dumps(page, default=str)
    assert [i["entry"]["registry_id"] for i in page["items"]] == ["reg-card-0", "reg-card-1", "reg-card-2"]
    assert page["next_page_token"] is None
    assert len(body) < 100_000  # vs ~39 MB for the unpaged draft list
    assert peak < 8 * 1024 * 1024, peak  # registry limit is 256 MiB; non-matching drafts are never copied
    print(f"CARD_DRAFT_PEAK_BYTES drafts=7441 peak={peak} response_bytes={len(body)}")


def test_keyset_paging_walks_every_match_once():
    store = RegistryStore()
    for i in range(5):
        _create(store, f"reg-card-{i}", bulk=10)
    service = RegistryService(store=store)
    seen, token = [], None
    for _ in range(5):
        page = service.card_admission_draft_page(
            visible_tenants=None, tenant_id="tenant-a", page_size=2, after_registry_id=token)
        seen += [i["entry"]["registry_id"] for i in page["items"]]
        token = page["next_page_token"]
        if token is None:
            break
    assert seen == [f"reg-card-{i}" for i in range(5)]


def test_postgres_owner_filters_in_sql_with_limit_and_bound_tenant():
    captured = []

    class _Cursor:
        def fetchall(self):
            return []

    class _Conn:
        def execute(self, sql, args=()):
            captured.append((sql, args))
            return _Cursor()

    class _Entries:
        table = "registry_entries"

        @contextlib.contextmanager
        def _use_conn(self, conn):
            yield _Conn()

    pg = PostgresRegistryStore.__new__(PostgresRegistryStore)
    pg._entries = _Entries()
    pg.list_card_admission_drafts(
        visible_tenants=frozenset({"tenant-a"}), tenant_id="tenant-a", limit=21, after_registry_id="reg-9")
    sql, args = captured[0]
    assert "artifact_state' = 'draft'" in sql and "card_wiring,status}' = 'gate_ready'" in sql
    assert "metadata,tenant_id}' = %s" in sql and "LIMIT 21" in sql and "ORDER BY" in sql
    assert "tenant-a" in args and "reg-9" in args


def test_endpoint_requires_tenant_scopes_and_pages(strict_client):  # noqa: F811
    from .storage import get_store

    token = _jwt(subject="card-ep", tenant="tenant-a")
    store = get_store()
    for i in range(3):
        _create(store, f"reg-ep-card-{i}", bulk=10)
    _create(store, "reg-ep-plain", card=False, bulk=10)
    base = "/api/registry/strategy-specs?view=card_admission_drafts"
    assert strict_client.get(base, headers=_bearer(token)).status_code == 400
    first = strict_client.get(base + "&tenant_id=tenant-a&page_size=2", headers=_bearer(token)).json()
    assert [i["entry"]["registry_id"] for i in first["items"]] == ["reg-ep-card-0", "reg-ep-card-1"]
    second = strict_client.get(
        base + f"&tenant_id=tenant-a&page_size=2&page_token={first['next_page_token']}",
        headers=_bearer(token)).json()
    assert [i["entry"]["registry_id"] for i in second["items"]] == ["reg-ep-card-2"]
    other = strict_client.get(base + "&tenant_id=tenant-b", headers=_bearer(token)).json()
    assert other["items"] == []
