"""Real-Postgres durability proofs for PostgresRegistryStore.

Gated on TEST_DATABASE_URL per the established pattern in
services/incident/test_pg_store_integration.py: skip cleanly when no live
database is configured, otherwise prove CAS/atomicity/restart/replay against
an actual PostgreSQL instance (not the SQL-emulating fake used by
test_control_plane_postgres_owner_stores.py).

architecture-resumption-sa-sd.md §3.3/§3.4 requires: fresh-process restart
and two-process shared-backend visibility, stale-CAS rejection, and a
same-key retry recovering the original committed version even after newer
versions exist. Each is proven here against the real database.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from services.registry.models import (
    ArtifactState,
    ArtifactType,
    Lineage,
    RegistryEntryCreate,
    StorageBackend,
    StorageRef,
)
from services.registry.pg_store import (
    DivergentCommandReplayError,
    PostgresRegistryStore,
    RegistryConcurrentUpdateError,
)


@pytest.fixture
def pg_case():
    dsn = os.getenv("TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is required for real Postgres CAS/atomicity proof")
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql

    schema = f"registry_owner_{uuid4().hex}"
    entries_table = f"{schema}.entries"
    receipts_table = f"{schema}.command_receipts"
    try:
        yield dsn, entries_table, receipts_table
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


def _store(case, *, bootstrap: bool = True) -> PostgresRegistryStore:
    dsn, entries_table, receipts_table = case
    return PostgresRegistryStore(
        dsn=dsn, entries_table=entries_table, receipts_table=receipts_table, bootstrap=bootstrap,
    )


def _payload(**overrides) -> RegistryEntryCreate:
    defaults = dict(
        artifact_type=ArtifactType.STRATEGY_SPEC,
        strategy_id="strat-durability",
        version="1.0.0",
        artifact_state=ArtifactState.DRAFT,
        lineage=Lineage(),
        storage_ref=StorageRef(backend=StorageBackend.OBJECT_STORE, path="s3://bucket/path"),
        checksum="sha256:deadbeef",
    )
    defaults.update(overrides)
    return RegistryEntryCreate(**defaults)


def test_create_if_absent_survives_fresh_process_restart(pg_case):
    """A second store instance against the same DSN is the fresh-process-restart
    proof: nothing lives in Python process memory, so a brand new
    PostgresRegistryStore object must still see the entry the first one wrote."""
    store_a = _store(pg_case)
    entry, created = store_a.create_if_absent(_payload(), "reg-001")
    assert created is True

    store_b = _store(pg_case)  # simulates a fresh process reconnecting
    reread = store_b.get("reg-001")
    assert reread is not None
    assert reread.strategy_id == "strat-durability"
    assert reread.version == "1.0.0"
    assert reread.artifact_state == ArtifactState.DRAFT


def test_two_concurrent_stores_race_to_exactly_one_create(pg_case):
    """Two processes racing create_if_absent on the same registry_id must
    commit exactly one row; the loser gets the winner's durable entry back,
    not a silently overwritten duplicate."""
    _store(pg_case)  # bootstrap schema/tables once before racing connections
    results = []

    def _attempt(seed: str):
        store = _store(pg_case, bootstrap=False)
        entry, created = store.create_if_absent(_payload(checksum=f"sha256:{seed}"), "reg-race")
        results.append((created, entry.checksum))

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(_attempt, ["aaa", "bbb"]))

    created_flags = [r[0] for r in results]
    assert created_flags.count(True) == 1
    assert created_flags.count(False) == 1
    winning_checksum = [c for created, c in results if created][0]
    losing_checksum = [c for created, c in results if not created][0]
    assert winning_checksum == losing_checksum

    canonical = _store(pg_case).get("reg-race")
    assert canonical.checksum == winning_checksum


def test_cas_update_binds_callers_base_snapshot_not_latest(pg_case):
    """update() must reject a write whose `expected` snapshot is stale, even
    though a naive re-fetch-then-write would have silently overwritten the
    intervening change."""
    store = _store(pg_case)
    entry, _ = store.create_if_absent(_payload(), "reg-002")
    base_snapshot = entry.to_dict()

    # A concurrent writer advances the entry first.
    concurrent_view = store.get("reg-002")
    concurrent_view.artifact_state = ArtifactState.CANDIDATE
    store.update(concurrent_view, expected=base_snapshot)

    # The original caller's stale base_snapshot must now be rejected.
    stale_entry = entry
    stale_entry.artifact_state = ArtifactState.CANDIDATE
    stale_entry.metadata = {"note": "based on stale read"}
    with pytest.raises(RegistryConcurrentUpdateError):
        store.update(stale_entry, expected=base_snapshot)

    # Durable state reflects only the winning writer's commit.
    canonical = store.get("reg-002")
    assert canonical.artifact_state == ArtifactState.CANDIDATE
    assert canonical.metadata != {"note": "based on stale read"}


def test_metadata_cas_commit_and_receipt_are_one_atomic_transaction(pg_case):
    """commit_metadata_cas must durably persist both the entry mutation and
    its command receipt, or neither — proven here by observing both rows
    after commit, then proving a same-key replay does not mutate again."""
    store = _store(pg_case)
    entry, _ = store.create_if_absent(_payload(), "reg-003")
    base_snapshot = entry.to_dict()

    updated, replayed = store.commit_metadata_cas(
        registry_id="reg-003",
        base_snapshot=base_snapshot,
        new_metadata={"note": "first commit"},
        command_key="cmd-abc",
    )
    assert replayed is False
    assert updated.metadata == {"note": "first commit"}

    receipt = store._receipts.get(store.receipt_key("cmd-abc", "reg-003"))
    assert receipt is not None
    assert receipt["registry_id"] == "reg-003"
    assert receipt["committed_entry"]["metadata"] == {"note": "first commit"}

    # A same-key identical replay must not re-run the mutation, AND must
    # return the entry exactly as it was ORIGINALLY committed under this
    # command_key — not whatever the row has become since under a different
    # key (proven by seeding a second, unrelated mutation first). Returning
    # the *current* row here would leak an unrelated later write into a
    # caller's idempotent retry of an earlier command.
    stale_snapshot = updated.to_dict()
    store.commit_metadata_cas(
        registry_id="reg-003",
        base_snapshot=stale_snapshot,
        new_metadata={"note": "second commit, different key"},
        command_key="cmd-def",
    )

    replayed_result, was_replay = store.commit_metadata_cas(
        registry_id="reg-003",
        base_snapshot=base_snapshot,  # deliberately stale — replay must skip the CAS check entirely
        new_metadata={"note": "first commit"},
        command_key="cmd-abc",
    )
    assert was_replay is True
    assert replayed_result.metadata == {"note": "first commit"}

    # The durable row itself reflects the later, unrelated command — the
    # replay above must not have re-mutated it back to "first commit".
    assert store.get("reg-003").metadata == {"note": "second commit, different key"}


def test_metadata_cas_divergent_replay_under_same_key_fails_closed(pg_case):
    """Reusing a command_key with a *different* requested mutation must be
    rejected rather than silently accepted as a second version under one key."""
    store = _store(pg_case)
    entry, _ = store.create_if_absent(_payload(), "reg-004")
    base_snapshot = entry.to_dict()

    store.commit_metadata_cas(
        registry_id="reg-004",
        base_snapshot=base_snapshot,
        new_metadata={"note": "original"},
        command_key="cmd-shared",
    )

    with pytest.raises(DivergentCommandReplayError):
        store.commit_metadata_cas(
            registry_id="reg-004",
            base_snapshot=base_snapshot,
            new_metadata={"note": "a different request entirely"},
            command_key="cmd-shared",
        )


def test_metadata_cas_receipt_is_scoped_by_tenant_and_actor(pg_case):
    """The same client-chosen command_key from two different tenants/actors
    against the same aggregate must not collide on one receipt row — each
    must independently commit its own mutation instead of the second call
    being misread as a replay of the first."""
    store = _store(pg_case)
    entry, _ = store.create_if_absent(_payload(), "reg-006")
    base_snapshot = entry.to_dict()

    first, first_replay = store.commit_metadata_cas(
        registry_id="reg-006",
        base_snapshot=base_snapshot,
        new_metadata={"note": "tenant-a wrote this"},
        command_key="cmd-shared-across-tenants",
        actor={"actor_id": "alice", "tenant": "tenant-a"},
    )
    assert first_replay is False
    assert first.metadata == {"note": "tenant-a wrote this"}

    second, second_replay = store.commit_metadata_cas(
        registry_id="reg-006",
        base_snapshot=first.to_dict(),
        new_metadata={"note": "tenant-b wrote this"},
        command_key="cmd-shared-across-tenants",
        actor={"actor_id": "bob", "tenant": "tenant-b"},
    )
    assert second_replay is False
    assert second.metadata == {"note": "tenant-b wrote this"}


def test_metadata_cas_rejects_stale_base_snapshot(pg_case):
    """A metadata CAS call whose base_snapshot no longer matches the durable
    row must fail closed rather than fabricate success or clobber the
    intervening write."""
    store = _store(pg_case)
    entry, _ = store.create_if_absent(_payload(), "reg-005")
    base_snapshot = entry.to_dict()

    store.commit_metadata_cas(
        registry_id="reg-005",
        base_snapshot=base_snapshot,
        new_metadata={"note": "winner"},
    )

    with pytest.raises(RegistryConcurrentUpdateError):
        store.commit_metadata_cas(
            registry_id="reg-005",
            base_snapshot=base_snapshot,  # stale: the row already moved past this snapshot
            new_metadata={"note": "loser, based on stale read"},
        )

    assert store.get("reg-005").metadata == {"note": "winner"}


def test_concurrent_first_bootstrap_does_not_raise(pg_case):
    """Two brand-new store instances racing their very first CREATE SCHEMA IF
    NOT EXISTS must not raise a unique-violation; ensure_postgres_schema must
    treat a concurrent winner the same as an already-existing schema."""

    def _bootstrap():
        _store(pg_case, bootstrap=True)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: _bootstrap(), range(4)))


def test_missing_config_fails_closed_not_memory_fallback():
    """Selecting the postgres backend with no DSN configured must raise, not
    silently construct an in-memory store."""
    from services.registry.pg_store import build_postgres_registry_store

    prior = os.environ.pop("REGISTRY_STORE_DSN", None)
    prior_db_url = os.environ.pop("DATABASE_URL", None)
    try:
        with pytest.raises(ValueError):
            build_postgres_registry_store()
    finally:
        if prior is not None:
            os.environ["REGISTRY_STORE_DSN"] = prior
        if prior_db_url is not None:
            os.environ["DATABASE_URL"] = prior_db_url
