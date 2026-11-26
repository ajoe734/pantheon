"""CAS mutations of legacy registry rows (REGISTRY-CAS-LEGACY-ROW-NORMALIZE-20261126).

Rows written before ``replication_success``/``replication_proof`` existed are
stored without those keys, while every caller snapshot is a full ``to_dict()``.
The Postgres CAS predicate is raw JSONB equality, so those rows used to fail
every guarded mutation as a "concurrent update". These tests pin the shared
rule (absent key == model default, present values must match exactly) on both
backends, plus the two hosted flows that hit it: draft->retired and the first
alpha-template evaluation write.

``_RawJsonOwnerStore`` reproduces ``PostgresJsonOwnerStore``'s contract for the
calls the registry CAS paths use: the stored payload is kept as raw JSON and
``compare_and_set`` matches on exact JSON equality (as ``payload = %s::jsonb``
does), returning the current raw row on a miss.
"""
from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from typing import Any, Callable, Optional

import pytest

from .alpha_template import (
    ALPHA_TEMPLATE_METADATA_KEY,
    build_alpha_template_registry_payload,
    load_library_templates,
    record_alpha_template_evaluation,
)
from .models import (
    ArtifactState,
    ArtifactType,
    Lineage,
    RegistryEntry,
    StorageBackend,
    StorageRef,
    entry_payloads_cas_equal,
    normalize_entry_payload_for_cas,
)
from .pg_store import PostgresRegistryStore
from .split_api import RegistryService
from .storage import RegistryConcurrentUpdateError, RegistryStore

LEGACY_MISSING = ("replication_success", "replication_proof")


def _jsonb(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True))


class _RawJsonOwnerStore:
    def __init__(self) -> None:
        self.rows: dict[str, Any] = {}
        self.cas_calls: list[tuple[str, Any]] = []
        self.before_cas: Optional[Callable[[int], None]] = None

    @contextmanager
    def transaction(self):
        yield object()

    def lock_table(self, *, conn: Any) -> None:
        return None

    def get(self, record_id: str):
        row = self.rows.get(record_id)
        return _jsonb(row) if row is not None else None

    def put(self, record_id: str, payload: dict) -> None:
        self.rows[record_id] = _jsonb(payload)

    def insert_if_absent(self, record_id, payload, *, unique_fields=(), conn=None):
        if record_id in self.rows:
            return False, _jsonb(self.rows[record_id])
        self.rows[record_id] = _jsonb(payload)
        return True, _jsonb(payload)

    def compare_and_set(self, record_id, expected_payload, payload, *, conn=None):
        if self.before_cas is not None:
            self.before_cas(len(self.cas_calls))
        self.cas_calls.append((record_id, copy.deepcopy(expected_payload)))
        current = self.rows.get(record_id)
        if expected_payload is None:
            if current is not None:
                return False, _jsonb(current)
            self.rows[record_id] = _jsonb(payload)
            return True, _jsonb(payload)
        if current is not None and current == _jsonb(expected_payload):
            self.rows[record_id] = _jsonb(payload)
            return True, _jsonb(payload)
        return False, _jsonb(current) if current is not None else None


def _pg_store() -> PostgresRegistryStore:
    store = object.__new__(PostgresRegistryStore)
    store._entries = _RawJsonOwnerStore()
    store._receipts = _RawJsonOwnerStore()
    store.full_summary_rows_extracted = 0
    store.full_summary_row_ids_extracted = []
    return store


def _entry(rid: str = "reg-legacy-1", **fields: Any) -> RegistryEntry:
    base = dict(
        registry_id=rid,
        artifact_type=ArtifactType.STRATEGY_SPEC,
        strategy_id=f"strat-{rid}",
        version="1.0.0",
        artifact_state=ArtifactState.DRAFT,
        lineage=Lineage(source_run_ids=[f"run-{rid}"]),
        storage_ref=StorageRef(backend=StorageBackend.INLINE, path="$.entry.metadata.strategy_spec"),
        checksum=f"sha256:{rid}",
        metadata={"tenant_id": "tenant-a", "note": "seed"},
        created_at="2026-10-01T00:00:00Z",
        updated_at="2026-10-01T00:00:00Z",
        owner_tenant="tenant-a",
    )
    base.update(fields)
    return RegistryEntry(**base)


def _legacy_payload(entry: RegistryEntry) -> dict:
    payload = entry.to_dict()
    for key in LEGACY_MISSING:
        payload.pop(key)
    return payload


def _seed_legacy(store: PostgresRegistryStore, entry: RegistryEntry) -> dict:
    payload = _legacy_payload(entry)
    store._entries.rows[entry.registry_id] = _jsonb(payload)
    return payload


# -- the shared rule ----------------------------------------------------------

def test_absent_defaulted_keys_compare_equal_and_present_values_do_not():
    full = _entry().to_dict()
    legacy = _legacy_payload(_entry())
    assert legacy != full
    assert entry_payloads_cas_equal(legacy, full)
    assert normalize_entry_payload_for_cas(legacy) == full

    # A present value is never overridden by the model default.
    assert not entry_payloads_cas_equal({**legacy, "replication_success": True}, full)
    # Any real field difference still differs.
    assert not entry_payloads_cas_equal({**legacy, "version": "1.0.1"}, full)
    assert not entry_payloads_cas_equal({**legacy, "metadata": {"note": "other"}}, full)
    assert entry_payloads_cas_equal(None, None)
    assert not entry_payloads_cas_equal(None, full)


# -- Postgres store: advance (commit_artifact_state_cas) ----------------------

def test_pg_draft_to_retired_on_legacy_row_succeeds_and_writes_full_shape():
    store = _pg_store()
    _seed_legacy(store, _entry())
    service = RegistryService(store)

    view = service.advance_artifact_state(
        "reg-legacy-1",
        ArtifactState.RETIRED,
        command_key="retire-1",
        actor={"id": "ops", "role": "service", "tenant": "tenant-a"},
        expected_artifact_state=ArtifactState.DRAFT,
        expected_version="1.0.0",
        expected_updated_at="2026-10-01T00:00:00Z",
    )

    assert view.entry.artifact_state == ArtifactState.RETIRED
    stored = store._entries.rows["reg-legacy-1"]
    assert stored["artifact_state"] == "retired"
    assert set(stored) == set(_entry().to_dict())
    assert stored["replication_success"] is None and stored["replication_proof"] is None


def test_pg_advance_replay_returns_original_committed_entry():
    store = _pg_store()
    _seed_legacy(store, _entry())
    service = RegistryService(store)
    kwargs = dict(
        command_key="retire-1",
        actor={"id": "ops", "role": "service", "tenant": "tenant-a"},
        expected_artifact_state=ArtifactState.DRAFT,
        expected_version="1.0.0",
        expected_updated_at="2026-10-01T00:00:00Z",
    )
    first = service.advance_artifact_state("reg-legacy-1", ArtifactState.RETIRED, **kwargs)
    cas_calls = len(store._entries.cas_calls)
    second = service.advance_artifact_state("reg-legacy-1", ArtifactState.RETIRED, **kwargs)

    assert second.entry.to_dict() == first.entry.to_dict()
    assert len(store._entries.cas_calls) == cas_calls  # replay never re-CASes the entry


def test_pg_advance_refuses_legacy_row_changed_by_another_writer():
    store = _pg_store()
    _seed_legacy(store, _entry())
    base = store.get("reg-legacy-1").to_dict()
    # Another writer changes a real field after our read.
    store._entries.rows["reg-legacy-1"]["metadata"] = {"tenant_id": "tenant-a", "note": "theirs"}
    before = copy.deepcopy(store._entries.rows["reg-legacy-1"])

    with pytest.raises(RegistryConcurrentUpdateError):
        store.commit_artifact_state_cas(
            registry_id="reg-legacy-1", base_snapshot=base, target_state=ArtifactState.RETIRED,
            command_key="retire-2",
        )
    assert store._entries.rows["reg-legacy-1"] == before


def test_pg_retry_still_refuses_a_write_that_lands_between_miss_and_retry():
    store = _pg_store()
    _seed_legacy(store, _entry())
    base = store.get("reg-legacy-1").to_dict()

    def interleave(call_index: int) -> None:
        if call_index == 1:  # right before the normalized retry
            store._entries.rows["reg-legacy-1"]["checksum"] = "sha256:concurrent"

    store._entries.before_cas = interleave
    with pytest.raises(RegistryConcurrentUpdateError):
        store.commit_artifact_state_cas(
            registry_id="reg-legacy-1", base_snapshot=base, target_state=ArtifactState.RETIRED,
        )
    assert store._entries.rows["reg-legacy-1"]["artifact_state"] == "draft"
    assert store._entries.rows["reg-legacy-1"]["checksum"] == "sha256:concurrent"


def test_pg_present_replication_value_is_a_real_difference():
    store = _pg_store()
    payload = _legacy_payload(_entry())
    payload["replication_success"] = True
    store._entries.rows["reg-legacy-1"] = _jsonb(payload)
    stale = _entry().to_dict()  # caller believes replication_success is None

    with pytest.raises(RegistryConcurrentUpdateError):
        store.commit_artifact_state_cas(
            registry_id="reg-legacy-1", base_snapshot=stale, target_state=ArtifactState.RETIRED,
        )


# -- Postgres store: metadata CAS and plain update -----------------------------

def test_pg_metadata_cas_on_legacy_row_and_replay():
    store = _pg_store()
    _seed_legacy(store, _entry())
    service = RegistryService(store)
    actor = {"id": "ops", "role": "service", "tenant": "tenant-a"}
    new_meta = {"tenant_id": "tenant-a", "note": "updated"}

    view, replay = service.update_metadata(
        "reg-legacy-1", expected_metadata={"tenant_id": "tenant-a", "note": "seed"},
        new_metadata=new_meta, command_key="meta-1", actor=actor,
    )
    assert replay is False
    assert view.entry.metadata == new_meta
    stored = store._entries.rows["reg-legacy-1"]
    assert stored["metadata"] == new_meta
    assert all(key in stored for key in LEGACY_MISSING)

    again, replay = service.update_metadata(
        "reg-legacy-1", expected_metadata={"tenant_id": "tenant-a", "note": "seed"},
        new_metadata=new_meta, command_key="meta-1", actor=actor,
    )
    assert replay is True
    assert again.entry.to_dict() == view.entry.to_dict()


def test_pg_update_on_legacy_row_and_stale_refusal():
    store = _pg_store()
    _seed_legacy(store, _entry())
    current = store.get("reg-legacy-1")
    expected = current.to_dict()
    current.checksum = "sha256:new"
    committed = store.update(current, expected=expected)
    assert committed.checksum == "sha256:new"
    assert all(key in store._entries.rows["reg-legacy-1"] for key in LEGACY_MISSING)

    store._entries.rows["reg-legacy-2"] = _jsonb(_legacy_payload(_entry("reg-legacy-2")))
    stale = store.get("reg-legacy-2")
    stale_expected = stale.to_dict()
    store._entries.rows["reg-legacy-2"]["version"] = "9.9.9"
    stale.checksum = "sha256:mine"
    with pytest.raises(RegistryConcurrentUpdateError):
        store.update(stale, expected=stale_expected)
    assert store._entries.rows["reg-legacy-2"]["checksum"] == "sha256:reg-legacy-2"


# -- alpha-template evaluation on a legacy row ---------------------------------

def test_first_alpha_template_evaluation_writes_to_legacy_row():
    store = _pg_store()
    template = load_library_templates()[0]
    registry_id, create = build_alpha_template_registry_payload(template)
    entry = RegistryEntry(
        registry_id=registry_id,
        artifact_type=create.artifact_type,
        strategy_id=create.strategy_id,
        version=create.version,
        artifact_state=create.artifact_state,
        lineage=create.lineage,
        storage_ref=create.storage_ref,
        checksum=create.checksum,
        evaluation_summary=create.evaluation_summary,
        metadata=create.metadata,
        created_at="2026-10-01T00:00:00Z",
        updated_at="2026-10-01T00:00:00Z",
        owner_tenant="__builtin__",
    )
    _seed_legacy(store, entry)

    view = record_alpha_template_evaluation(
        RegistryService(store), template["template_id"], evaluation_ref="eval-run-1",
    )

    stored = store._entries.rows[registry_id]
    assert stored["metadata"][ALPHA_TEMPLATE_METADATA_KEY]["evaluation_ref"] == "eval-run-1"
    assert view.entry.metadata[ALPHA_TEMPLATE_METADATA_KEY]["evaluation_ref"] == "eval-run-1"
    assert all(key in stored for key in LEGACY_MISSING)


# -- in-memory store keeps the same semantics ----------------------------------

def test_memory_store_accepts_legacy_shaped_snapshot_and_refuses_real_change():
    store = RegistryStore()
    store.put(_entry())
    legacy_expected = _legacy_payload(_entry())

    entry, replay = store.commit_artifact_state_cas(
        registry_id="reg-legacy-1", base_snapshot=legacy_expected,
        target_state=ArtifactState.RETIRED,
    )
    assert replay is False and entry.artifact_state == ArtifactState.RETIRED

    store.put(_entry("reg-legacy-2"))
    with pytest.raises(RegistryConcurrentUpdateError):
        store.commit_metadata_cas(
            registry_id="reg-legacy-2",
            base_snapshot={**_legacy_payload(_entry("reg-legacy-2")), "version": "2.0.0"},
            new_metadata={"note": "x"},
        )

    current = store.get("reg-legacy-2")
    current.checksum = "sha256:new"
    assert store.update(current, expected=_legacy_payload(_entry("reg-legacy-2"))).checksum == "sha256:new"
    with pytest.raises(RegistryConcurrentUpdateError):
        store.update(current, expected={**_legacy_payload(_entry("reg-legacy-2")), "checksum": "x"})
