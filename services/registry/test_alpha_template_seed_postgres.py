"""Startup alpha-template seeding against a real Postgres table holding the
production row shapes (REGISTRY-SEED-UNIQUE-SCAN-20261110).

Gated on TEST_DATABASE_URL.  Timing budgets come from the task acceptance:
seeding < 10 s fresh and on rerun, single register_if_absent p50 < 100 ms.
"""
from __future__ import annotations

import json
import os
import statistics
import time
from uuid import uuid4

import pytest

from .alpha_template import (
    build_alpha_template_registry_payload,
    ensure_builtin_alpha_templates,
    load_library_templates,
)
from .models import (
    ArtifactState,
    ArtifactType,
    Lineage,
    RegistryEntry,
    RegistryEntryCreate,
    StorageBackend,
    StorageRef,
)
from .pg_store import PostgresRegistryStore
from .service import RegistryService
from .test_strategy_summary_page_bounded import _realistic_strategy_spec_payload

STRATEGY_SPEC_ROWS = 5096
EXECUTION_BUNDLE_ROWS = 323
EVIDENCE_ENV = "REGISTRY_SEED_EVIDENCE_OUT"


@pytest.fixture
def seeded_registry():
    dsn = os.getenv("TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is required for real Postgres seeding proof")
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql

    schema = f"registry_seed_{uuid4().hex}"
    store = PostgresRegistryStore(
        dsn=dsn,
        entries_table=f"{schema}.entries",
        receipts_table=f"{schema}.command_receipts",
    )
    rows = []
    for i in range(STRATEGY_SPEC_ROWS):
        sid = f"strat-seedscan-{i:05d}"
        spec = _realistic_strategy_spec_payload(sid, persona_ids=[f"persona-{i % 12}"])
        entry = RegistryEntry(
            registry_id=f"reg-{sid}-v1",
            artifact_type=ArtifactType.STRATEGY_SPEC,
            strategy_id=sid,
            version="1.0.0",
            artifact_state=ArtifactState.DRAFT,
            lineage=Lineage(source_run_ids=["run-seed"]),
            storage_ref=StorageRef(
                backend=StorageBackend.INLINE, path="$.entry.metadata.strategy_spec"
            ),
            checksum=f"sha256:{sid}",
            metadata={"strategy_spec": spec},
            created_at="2026-10-01T00:00:00Z",
            updated_at="2026-10-01T00:00:00Z",
            owner_tenant="tenant-a",
        )
        rows.append((entry.registry_id, json.dumps(entry.to_dict(), sort_keys=True)))
    for i in range(EXECUTION_BUNDLE_ROWS):
        sid = f"strat-bundle-{i:05d}"
        entry = RegistryEntry(
            registry_id=f"reg-{sid}-v1",
            artifact_type=ArtifactType.EXECUTION_BUNDLE,
            strategy_id=sid,
            version="1.0.0",
            artifact_state=ArtifactState.DRAFT,
            lineage=Lineage(source_run_ids=["run-seed"]),
            storage_ref=StorageRef(backend=StorageBackend.OBJECT_STORE, path=f"s3://b/{sid}"),
            checksum=f"sha256:{sid}",
            metadata={"bundle": {"orders": [{"symbol": f"{2300 + k}.TW"} for k in range(20)]}},
            created_at="2026-10-01T00:00:00Z",
            updated_at="2026-10-01T00:00:00Z",
            owner_tenant="tenant-a",
        )
        rows.append((entry.registry_id, json.dumps(entry.to_dict(), sort_keys=True)))
    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    f'INSERT INTO "{schema}"."entries" (record_id, payload) VALUES (%s, %s::jsonb)',
                    rows,
                )
            conn.execute(f'ANALYZE "{schema}"."entries"')
        yield dsn, schema, store
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


def _record(label: str, data: dict) -> None:
    out = os.getenv(EVIDENCE_ENV, "").strip()
    if not out:
        return
    try:
        existing = json.loads(open(out).read())
    except (OSError, ValueError):
        existing = {}
    existing[label] = data
    with open(out, "w") as fh:
        json.dump(existing, fh, indent=2, sort_keys=True)


def test_fresh_and_rerun_seeding_under_ten_seconds(seeded_registry):
    _dsn, _schema, store = seeded_registry
    service = RegistryService(store)
    templates = load_library_templates()
    assert len(templates) >= 100

    started = time.perf_counter()
    views = ensure_builtin_alpha_templates(service)
    fresh = time.perf_counter() - started
    assert len(views) == len(templates)
    _record("seeding_fresh", {"seconds": round(fresh, 3)})
    assert fresh < 10.0, f"fresh seeding took {fresh:.2f}s"

    # Rerun with 140 templates present and the rest removed (the 11/10 state).
    ids = [build_alpha_template_registry_payload(t)[0] for t in templates]
    keep = set(ids[:140])
    for rid in ids[140:]:
        with store._entries._connect() as conn:
            conn.execute(
                f"DELETE FROM {store._entries.table} WHERE record_id = %s", (rid,)
            )
    started = time.perf_counter()
    ensure_builtin_alpha_templates(service)
    rerun = time.perf_counter() - started
    assert len(keep) == 140
    _record("seeding_rerun", {"seconds": round(rerun, 3)})
    assert rerun < 10.0, f"partial rerun took {rerun:.2f}s"
    _record(
        "seeding",
        {
            "templates": len(templates),
            "fresh_seconds": round(fresh, 3),
            "rerun_140_present_seconds": round(rerun, 3),
            "strategy_spec_rows": STRATEGY_SPEC_ROWS,
            "execution_bundle_rows": EXECUTION_BUNDLE_ROWS,
        },
    )


def test_single_register_if_absent_p50_under_100ms(seeded_registry):
    _dsn, _schema, store = seeded_registry
    service = RegistryService(store)
    samples = []
    for i in range(20):
        payload = RegistryEntryCreate(
            artifact_type=ArtifactType.STRATEGY_SPEC,
            strategy_id=f"strat-p50-{i}",
            version="1.0.0",
            artifact_state=ArtifactState.DRAFT,
            lineage=Lineage(),
            storage_ref=StorageRef(backend=StorageBackend.OBJECT_STORE, path="s3://b/p"),
            checksum=f"sha256:p50-{i}",
        )
        started = time.perf_counter()
        _view, created = service.register_if_absent(payload, f"reg-p50-{i}")
        samples.append((time.perf_counter() - started) * 1000)
        assert created
    p50 = statistics.median(samples)
    _record("single_register_if_absent", {"calls": 20, "p50_ms": round(p50, 2), "max_ms": round(max(samples), 2)})
    assert p50 < 100.0, f"p50 {p50:.1f}ms"
