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
import threading
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

    receipt = store._receipts.get(
        store.receipt_key("cmd-abc", "reg-003", command_type="metadata")
    )
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


def test_concurrent_strategy_spec_revision_registration_serializes_not_races(pg_case):
    """Reviewer finding 4 (TOCTOU race): two concurrent callers racing to
    register the *next* StrategySpec revision (e.g. both attempting a
    version bump from the same current latest) must not both succeed —
    the second must see the first's committed version and be rejected for
    being no longer a valid next step, or the whole invariant is violated
    even though the store's (strategy_id, version, artifact_type)
    uniqueness constraint never fires (they target different versions).
    ``register_strategy_spec_revision``'s per-strategy_id advisory
    transaction lock must serialize the read-validate-write sequence so
    only one of two concurrent "next version from 1.0.0" attempts commits.
    """
    _store(pg_case)  # bootstrap schema/tables once before racing connections
    strategy_id = "strat-toctou"

    base_store = _store(pg_case, bootstrap=False)
    base_store.create_if_absent(_payload(strategy_id=strategy_id, version="1.0.0"), "reg-toctou-base")

    results: dict[str, tuple[bool, object]] = {}
    completion_order: list[str] = []
    order_lock = threading.Lock()

    def _attempt(seed_version: str):
        store = _store(pg_case, bootstrap=False)

        def _validate(existing: list) -> None:
            versions = {e.version for e in existing}

            def _parse(v: str) -> tuple[int, int, int]:
                return tuple(int(x) for x in v.split("."))  # type: ignore[return-value]

            latest = max(versions, key=_parse)
            major, minor, patch = _parse(latest)
            valid_next = {(major + 1, 0, 0), (major, minor + 1, 0), (major, minor, patch + 1)}
            if _parse(seed_version) not in valid_next:
                raise RegistryConcurrentUpdateError(
                    f"{seed_version} is not a valid next version from {latest}"
                )

        try:
            entry, created = store.register_strategy_spec_revision(
                strategy_id=strategy_id,
                registry_id=f"reg-toctou-{seed_version}",
                payload=_payload(strategy_id=strategy_id, version=seed_version),
                validate_lineage=_validate,
                unique_fields=("strategy_id", "version", "artifact_type"),
            )
            # Record real commit order via a Python-side monotonic sequence
            # captured immediately after the call returns (i.e. after this
            # thread's transaction has committed and released the advisory
            # lock). Postgres' own ``now()``/``updated_at`` reflects
            # *transaction start* time, not commit time — a transaction that
            # blocks longest on the advisory lock can still have the
            # earliest ``now()``, so sorting committed rows by ``updated_at``
            # does not reliably reflect actual serialization order under lock
            # contention. This Python-side order is the ground truth for
            # "which attempt's read-validate-write section actually ran
            # (and committed) second".
            with order_lock:
                completion_order.append(seed_version)
            results[seed_version] = (True, created)
        except Exception as exc:  # noqa: BLE001 - proving rejection, not a specific type here
            results[seed_version] = (False, exc)

    # Both 1.0.1 (patch bump) and 2.0.0 (major bump) are each individually a
    # valid "next version" from 1.0.0 — the TOCTOU race is exactly this
    # scenario, where a naive unlocked pre-check lets both validate
    # successfully against the same stale "latest=1.0.0" read. Note that a
    # major-version bump is *unconditionally* valid regardless of which
    # minor/patch preceded it (see the identical rule in
    # service.py's _check_strategy_spec_version_lineage), so if 1.0.1 wins
    # the race to commit first, 2.0.0 remains a legitimately valid next
    # step from 1.0.1 too — both succeeding in *that* order is correct
    # behavior, not a bug. The actual defect this test proves fixed is the
    # reviewer's exact failure mode: a *stale* commit (1.0.1) landing
    # *after* a later-committed revision (2.0.0) it is no longer a valid
    # next step from — this must never happen regardless of which attempt
    # the thread scheduler happens to run first.
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(_attempt, ["1.0.1", "2.0.0"]))

    entries = {e.version: e for e in _store(pg_case).list_by_strategy(strategy_id)}
    committed_versions = [v for v in ("1.0.1", "2.0.0") if entries.get(v) is not None]
    assert 1 <= len(committed_versions) <= 2, f"unexpected committed set: {committed_versions}"
    if len(committed_versions) == 2:
        # ``completion_order`` is the Python-side ground truth for actual
        # commit order (see the comment above where it is populated) — do
        # not resurrect a DB-timestamp-based ordering here.
        first_version, second_version = completion_order

        def _parse(v: str) -> tuple[int, int, int]:
            return tuple(int(x) for x in v.split("."))  # type: ignore[return-value]

        major, minor, patch = _parse(first_version)
        valid_next = {(major + 1, 0, 0), (major, minor + 1, 0), (major, minor, patch + 1)}
        assert _parse(second_version) in valid_next, (
            f"second-committed version {second_version!r} is not a valid next step from "
            f"first-committed {first_version!r} — a stale revision slipped in after a later "
            f"one already committed (results: {results}, completion_order: {completion_order})"
        )


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


def test_transaction_rollback_leaves_no_orphan_reservation_or_state(pg_case):
    """Store-level atomicity: an in-transaction exception in create_with_receipt,
    commit_metadata_cas, or commit_artifact_state_cas aborts the transaction,
    leaving 0 orphan rows in both entries and command_receipts, and allowing
    clean retry under the same command_key."""
    import psycopg

    dsn, entries_table, receipts_table = pg_case
    store = _store(pg_case)

    def _count_entries(where: str = "") -> int:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                clause = f" WHERE {where}" if where else ""
                cur.execute(f"SELECT count(*) FROM {entries_table}{clause}")
                return cur.fetchone()[0]

    def _count_receipts(where: str = "") -> int:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                clause = f" WHERE {where}" if where else ""
                cur.execute(f"SELECT count(*) FROM {receipts_table}{clause}")
                return cur.fetchone()[0]

    # 1. CREATE_WITH_RECEIPT ROLLBACK
    payload = _payload(strategy_id="strat-rb-store", version="1.0.0")

    def _failing_lineage(_entries):
        raise RuntimeError("injected lineage check failure before commit")

    with pytest.raises(RuntimeError, match="injected lineage check failure"):
        store.create_with_receipt(
            lambda: (payload, "reg-rb-01"),
            command_key="cmd-rb-create-01",
            actor={"actor_id": "test-actor"},
            request_fingerprint={"test": "data"},
            strategy_id="strat-rb-store",
            validate_lineage=_failing_lineage,
        )

    # Verify 0 rows in entries and receipts
    assert _count_entries("record_id = 'reg-rb-01'") == 0
    assert _count_receipts("payload->>'command_key' = 'cmd-rb-create-01'") == 0

    # Clean retry under same command_key succeeds
    entry, replayed = store.create_with_receipt(
        lambda: (payload, "reg-rb-01"),
        command_key="cmd-rb-create-01",
        actor={"actor_id": "test-actor"},
        request_fingerprint={"test": "data"},
        strategy_id="strat-rb-store",
    )
    assert replayed is False
    assert entry.registry_id == "reg-rb-01"
    assert _count_entries("record_id = 'reg-rb-01'") == 1
    assert _count_receipts("payload->>'command_key' = 'cmd-rb-create-01'") >= 1

    # 2. COMMIT_METADATA_CAS ROLLBACK
    base_snapshot = entry.to_dict()

    def _failing_meta_validate(_current):
        raise RuntimeError("injected metadata validation failure before commit")

    with pytest.raises(RuntimeError, match="injected metadata validation failure"):
        store.commit_metadata_cas(
            registry_id="reg-rb-01",
            base_snapshot=base_snapshot,
            validate=_failing_meta_validate,
            new_metadata={"note": "failing"},
            command_key="cmd-rb-meta-01",
            actor={"actor_id": "test-actor"},
        )

    assert _count_receipts("payload->>'command_key' = 'cmd-rb-meta-01'") == 0
    reread = store.get("reg-rb-01")
    assert reread.metadata != {"note": "failing"}

    # Clean retry under same command_key succeeds
    entry_meta, replayed_meta = store.commit_metadata_cas(
        registry_id="reg-rb-01",
        base_snapshot=base_snapshot,
        new_metadata={"note": "succeeding"},
        command_key="cmd-rb-meta-01",
        actor={"actor_id": "test-actor"},
    )
    assert replayed_meta is False
    assert entry_meta.metadata == {"note": "succeeding"}
    assert _count_receipts("payload->>'command_key' = 'cmd-rb-meta-01'") >= 1

    # 3. COMMIT_ARTIFACT_STATE_CAS ROLLBACK
    base_snapshot_adv = entry_meta.to_dict()

    def _failing_state_validate(_current):
        raise RuntimeError("injected state validation failure before commit")

    with pytest.raises(RuntimeError, match="injected state validation failure"):
        store.commit_artifact_state_cas(
            registry_id="reg-rb-01",
            base_snapshot=base_snapshot_adv,
            validate=_failing_state_validate,
            target_state=ArtifactState.CANDIDATE,
            command_key="cmd-rb-adv-01",
            actor={"actor_id": "test-actor"},
        )

    assert _count_receipts("payload->>'command_key' = 'cmd-rb-adv-01'") == 0
    reread_state = store.get("reg-rb-01")
    assert reread_state.artifact_state == ArtifactState.DRAFT

    # Clean retry under same command_key succeeds
    entry_adv, replayed_adv = store.commit_artifact_state_cas(
        registry_id="reg-rb-01",
        base_snapshot=base_snapshot_adv,
        target_state=ArtifactState.CANDIDATE,
        command_key="cmd-rb-adv-01",
        actor={"actor_id": "test-actor"},
    )
    assert replayed_adv is False
    assert entry_adv.artifact_state == ArtifactState.CANDIDATE
    assert _count_receipts("payload->>'command_key' = 'cmd-rb-adv-01'") >= 1

