"""End-to-end durability proof for the mounted Registry FastAPI app against a
real PostgreSQL owner store — architecture-resumption-sa-sd.md §3.4.

Gated on TEST_DATABASE_URL (skip cleanly without a live database, matching
services/incident/test_pg_store_integration.py). Where services/foundation/
tests/test_registry_owner_transaction.py proves the storage primitive's CAS
and atomicity directly, this module proves the same guarantees are reachable
through the actual mounted HTTP surface: register, metadata CAS update,
fresh-process restart (a second app/store instance against the same DSN),
and rejection of a stale CAS request over HTTP (409).
"""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

pytest.importorskip("psycopg")

from fastapi.testclient import TestClient

from services.runtime_auth_inbound import encode_jwt_hs256

from . import service as service_module
from .storage import reset_store

# Reviewer finding 2: once the durable Postgres backend is selected, the
# service now fails closed (500) unless auth is explicitly strict with a
# configured issuer/audience — a permissive-mode structured stub token
# ("actor_id:role1,role2") is no longer accepted against this backend. These
# HTTP-surface durability proofs therefore authenticate with a real,
# strictly-verified HS256 JWT (mirrors test_owner_durability_real_process.py),
# not the permissive stub used by the in-memory-backed unit tests elsewhere
# in this package.
_JWT_SECRET = "registry-owner-durability-secret"
_JWT_ISSUER = "registry-durability-tests"
_JWT_AUDIENCE = "registry-svc"


def _strict_jwt(*, subject: str = "durability-operator", tenant: str = "durability-tenant") -> str:
    return encode_jwt_hs256(
        {
            "sub": subject,
            "tenant": tenant,
            "roles": ["operator"],
            "iss": _JWT_ISSUER,
            "aud": _JWT_AUDIENCE,
            "exp": time.time() + 3600,
        },
        secret=_JWT_SECRET,
    )


_AUTH_HEADERS = {"Authorization": f"Bearer {_strict_jwt()}"}


@pytest.fixture
def pg_app():
    dsn = os.getenv("TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is required for real Postgres owner durability proof")
    import psycopg
    from psycopg import sql

    schema = f"registry_durability_{uuid4().hex}"
    prior_env = {
        key: os.environ.get(key)
        for key in (
            "REGISTRY_STORE_BACKEND",
            "REGISTRY_STORE_DSN",
            "REGISTRY_ENTRIES_TABLE",
            "REGISTRY_RECEIPTS_TABLE",
            "PANTHEON_REGISTRY_AUTH_MODE",
            "PANTHEON_REGISTRY_JWT_SECRET",
            "PANTHEON_REGISTRY_JWT_ISSUER",
            "PANTHEON_REGISTRY_JWT_AUDIENCE",
        )
    }
    os.environ["REGISTRY_STORE_BACKEND"] = "postgres"
    os.environ["REGISTRY_STORE_DSN"] = dsn
    os.environ["REGISTRY_ENTRIES_TABLE"] = f"{schema}.entries"
    os.environ["REGISTRY_RECEIPTS_TABLE"] = f"{schema}.command_receipts"
    os.environ["PANTHEON_REGISTRY_AUTH_MODE"] = "strict"
    os.environ["PANTHEON_REGISTRY_JWT_SECRET"] = _JWT_SECRET
    os.environ["PANTHEON_REGISTRY_JWT_ISSUER"] = _JWT_ISSUER
    os.environ["PANTHEON_REGISTRY_JWT_AUDIENCE"] = _JWT_AUDIENCE
    reset_store()
    try:
        yield TestClient(service_module.app, headers=_AUTH_HEADERS)
    finally:
        reset_store()
        for key, value in prior_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        with psycopg.connect(dsn) as conn:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


def _register(client: TestClient, **overrides) -> dict:
    payload = {
        "artifact_type": "strategy_spec",
        "strategy_id": "durability-strat",
        "version": "1.0.0",
        "artifact_state": "draft",
        "checksum": "sha256:cafef00d",
    }
    payload.update(overrides)
    resp = client.post("/api/registry/entries", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_register_and_readback_survive_fresh_process_restart(pg_app):
    created = _register(pg_app)
    registry_id = created["entry"]["registry_id"]

    # Simulate a fresh process: drop the in-process singleton and reconnect.
    reset_store()
    fresh_client = TestClient(service_module.app, headers=_AUTH_HEADERS)
    resp = fresh_client.get(f"/api/registry/entries/{registry_id}")
    assert resp.status_code == 200
    assert resp.json()["entry"]["strategy_id"] == "durability-strat"


def test_metadata_cas_update_over_http_succeeds_and_verifies_readback(pg_app):
    created = _register(pg_app)
    registry_id = created["entry"]["registry_id"]
    assert created["entry"]["metadata"] is None

    resp = pg_app.patch(
        f"/api/registry/entries/{registry_id}/metadata",
        json={"expected_metadata": None, "metadata": {"note": "operator draft"}},
        headers=_AUTH_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["X-Idempotent-Replay"] == "false"
    assert resp.json()["entry"]["metadata"] == {"note": "operator draft"}

    # Real owner GET/readback proof, not POST/body-accepted-as-truth.
    readback = pg_app.get(f"/api/registry/entries/{registry_id}")
    assert readback.json()["entry"]["metadata"] == {"note": "operator draft"}


def test_metadata_cas_update_over_http_rejects_stale_expected(pg_app):
    created = _register(pg_app)
    registry_id = created["entry"]["registry_id"]

    ok = pg_app.patch(
        f"/api/registry/entries/{registry_id}/metadata",
        json={"expected_metadata": None, "metadata": {"note": "first"}},
        headers=_AUTH_HEADERS,
    )
    assert ok.status_code == 200

    stale = pg_app.patch(
        f"/api/registry/entries/{registry_id}/metadata",
        json={"expected_metadata": None, "metadata": {"note": "based on stale read"}},
        headers=_AUTH_HEADERS,
    )
    assert stale.status_code == 409

    current = pg_app.get(f"/api/registry/entries/{registry_id}")
    assert current.json()["entry"]["metadata"] == {"note": "first"}


def test_metadata_cas_update_idempotent_replay_over_http(pg_app):
    created = _register(pg_app)
    registry_id = created["entry"]["registry_id"]

    first = pg_app.patch(
        f"/api/registry/entries/{registry_id}/metadata",
        json={"expected_metadata": None, "metadata": {"note": "v1"}, "command_key": "cmd-http-1"},
        headers=_AUTH_HEADERS,
    )
    assert first.status_code == 200
    assert first.headers["X-Idempotent-Replay"] == "false"

    replay = pg_app.patch(
        f"/api/registry/entries/{registry_id}/metadata",
        json={"expected_metadata": None, "metadata": {"note": "v1"}, "command_key": "cmd-http-1"},
        headers=_AUTH_HEADERS,
    )
    assert replay.status_code == 200
    assert replay.headers["X-Idempotent-Replay"] == "true"
    assert replay.json()["entry"]["metadata"] == {"note": "v1"}


def test_metadata_cas_update_missing_entry_is_404(pg_app):
    resp = pg_app.patch(
        "/api/registry/entries/reg-does-not-exist/metadata",
        json={"expected_metadata": None, "metadata": {"note": "x"}},
        headers=_AUTH_HEADERS,
    )
    assert resp.status_code == 404


def test_metadata_cas_update_rejects_unauthenticated_caller(pg_app):
    """No Authorization header at all must fail closed (401), not fall
    through to an anonymous-compatibility path."""
    created = _register(pg_app)
    registry_id = created["entry"]["registry_id"]

    resp = pg_app.patch(
        f"/api/registry/entries/{registry_id}/metadata",
        json={"expected_metadata": None, "metadata": {"note": "no auth"}},
        headers={"Authorization": ""},
    )
    assert resp.status_code == 401

    unchanged = pg_app.get(f"/api/registry/entries/{registry_id}")
    assert unchanged.json()["entry"]["metadata"] is None


def test_register_entry_rejects_unauthenticated_caller(pg_app):
    """POST /api/registry/entries must also fail closed without a verified
    caller — not just the metadata PATCH route."""
    resp = pg_app.post(
        "/api/registry/entries",
        json={
            "artifact_type": "strategy_spec",
            "strategy_id": "durability-strat",
            "version": "1.0.0",
            "artifact_state": "draft",
            "checksum": "sha256:cafef00d",
        },
        headers={"Authorization": ""},
    )
    assert resp.status_code == 401


def test_advance_state_conflict_is_409_not_silent_overwrite(pg_app):
    created = _register(
        pg_app,
        lineage={"source_run_ids": ["run-1"]},
    )
    registry_id = created["entry"]["registry_id"]

    advance_1 = pg_app.post(
        f"/api/registry/entries/{registry_id}/advance",
        json={"target_state": "candidate", "expected_artifact_state": "draft"},
    )
    assert advance_1.status_code == 200

    # advance() re-reads the current entry each call, so a legitimate second
    # advance from the now-current state must succeed (not a stale-CAS proof
    # by itself) — the real proof is that a forbidden transition from the
    # *new* current state still fails explicitly rather than silently no-op.
    forbidden = pg_app.post(
        f"/api/registry/entries/{registry_id}/advance",
        json={"target_state": "draft", "expected_artifact_state": "candidate"},
    )
    assert forbidden.status_code == 400


def test_readyz_fails_closed_when_owner_schema_is_missing(monkeypatch):
    """Reviewer finding 8: with bootstrap disabled and no pre-created
    schema/table, /readyz must report ready=false (not 200/ready=true) —
    a bare "SELECT 1" previously succeeded regardless of whether the actual
    owner schema/table existed, so this reproduced live as /readyz 200
    ready=true while every real entry GET raised psycopg's
    InvalidSchemaName."""
    dsn = os.getenv("TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is required for real Postgres owner durability proof")

    schema = f"registry_missing_schema_{uuid4().hex}"
    monkeypatch.setenv("REGISTRY_STORE_BACKEND", "postgres")
    monkeypatch.setenv("REGISTRY_STORE_DSN", dsn)
    monkeypatch.setenv("REGISTRY_ENTRIES_TABLE", f"{schema}.entries")
    monkeypatch.setenv("REGISTRY_RECEIPTS_TABLE", f"{schema}.command_receipts")
    # Bootstrap disabled: the schema/table are never created, reproducing
    # the exact "bootstrap=0/missing schema" scenario from the live probe.
    monkeypatch.setenv("REGISTRY_STORE_BOOTSTRAP", "0")
    monkeypatch.setenv("PANTHEON_REGISTRY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_REGISTRY_JWT_SECRET", _JWT_SECRET)
    monkeypatch.setenv("PANTHEON_REGISTRY_JWT_ISSUER", _JWT_ISSUER)
    monkeypatch.setenv("PANTHEON_REGISTRY_JWT_AUDIENCE", _JWT_AUDIENCE)
    reset_store()
    try:
        from . import main as main_module

        client = TestClient(main_module.app)
        resp = client.get("/readyz")
        body = resp.json()
        assert body.get("ready") is False, body
        assert resp.status_code != 200 or body.get("ready") is False
    finally:
        reset_store()


# ===========================================================================
# Gen-8 independent Codex rejection of PR #5620 — findings 2 and 8, which
# require real concurrency / a live Postgres owner and so cannot be proven
# against the in-memory backend (see test_service.py for the gen-8 findings
# that can be).
# ===========================================================================


def _valid_spec(strategy_id: str, **variant_metadata) -> dict:
    """Minimal schema-valid StrategySpec — see test_registry_positives._valid_spec."""
    spec = {
        "spec_version": "1.0",
        "strategy_id": strategy_id,
        "title": "Owner durability probe strategy",
        "hypothesis": "Deterministic probe hypothesis for registry owner durability tests.",
        "objective": "Prove real registry write-owner capability, not just route existence.",
        "market_scope": {"symbols": ["TEST"], "frequency": "1d"},
        "data_dependencies": [{"ref": "test-fixture", "kind": "note"}],
        "execution_profile": {"signal_schema_version": "1.0", "quantity_type": "SHARES"},
        "evaluation_plan": {"metrics": ["sharpe"]},
        "governance": {"approval_required": True},
        "provenance": {"source_kind": "manual", "created_at": "2026-01-01T00:00:00Z"},
    }
    if variant_metadata:
        spec["metadata"] = dict(variant_metadata)
    return spec


def test_generic_route_embedded_spec_serializes_concurrent_revision_race(pg_app):
    """Reviewer finding 2 (gen-8 review): a full StrategySpec submitted
    through the generic POST /api/registry/entries route must commit through
    the same per-strategy_id serialized invariant as the dedicated
    POST /api/registry/strategy-specs facade — not a separate, unlocked
    pre-check followed by a plain unconditional insert.

    Proof: version "1.1.0" is allowed to fully commit (acquire the
    per-strategy_id advisory lock, re-validate, insert, and release the
    lock) while a second request for version "1.0.1" is paused *before* it
    enters the lock (patched at the unlocked pre-check, never while holding
    the lock — pausing inside the lock would deadlock the "1.1.0" request
    behind it). Once "1.0.1" resumes and enters the locked path, it
    re-reads the *true* current latest (now "1.1.0", not the stale "1.0.0"
    it originally pre-checked against) and must be rejected: "1.0.1" is not
    a valid next revision from "1.1.0". The pre-fix code validated only
    against the stale pre-check read and would have let both commit.
    """
    strategy_id = "owner-durability-generic-race"
    base = pg_app.post(
        "/api/registry/entries",
        json={
            "artifact_type": "strategy_spec",
            "strategy_id": strategy_id,
            "version": "1.0.0",
            "lineage": {"source_run_ids": ["run-base"]},
            "metadata": {"strategy_spec": _valid_spec(strategy_id)},
        },
    )
    assert base.status_code == 200, base.text
    parent_reg_id = base.json()["entry"]["registry_id"]
    parent_checksum = base.json()["entry"]["checksum"]

    from . import service as service_module

    real_validate = service_module._validate_strategy_spec_version_lineage
    paused_started = threading.Event()
    release_paused = threading.Event()

    def _pausing_validate(registry_service, strategy_id_arg, version, lineage, *, ctx, **kwargs):
        if version == "1.0.1":
            paused_started.set()
            assert release_paused.wait(15), "1.1.0 request did not signal completion in time"
        return real_validate(registry_service, strategy_id_arg, version, lineage, ctx=ctx, **kwargs)

    def _submit(version: str):
        return pg_app.post(
            "/api/registry/entries",
            json={
                "artifact_type": "strategy_spec",
                "strategy_id": strategy_id,
                "version": version,
                "lineage": {"source_run_ids": ["run-base"], "parent_registry_ids": [parent_reg_id]},
                "base_checksum": parent_checksum,
                "metadata": {"strategy_spec": _valid_spec(strategy_id, variant=version)},
            },
        )

    service_module._validate_strategy_spec_version_lineage = _pausing_validate
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            stale = pool.submit(_submit, "1.0.1")
            assert paused_started.wait(15)
            fresh = pool.submit(_submit, "1.1.0")
            fresh_result = fresh.result(timeout=15)
            assert fresh_result.status_code == 200, fresh_result.text
            release_paused.set()
            stale_result = stale.result(timeout=15)
    finally:
        service_module._validate_strategy_spec_version_lineage = real_validate

    assert stale_result.status_code in (400, 409), stale_result.text

    listed = pg_app.get(f"/api/registry/strategies/{strategy_id}/strategy-specs")
    versions = sorted(item["entry"]["version"] for item in listed.json())
    assert versions == ["1.0.0", "1.1.0"]


def test_readyz_fails_closed_when_receipts_table_is_missing(pg_app):
    """Reviewer finding 8 (gen-8 review): readiness previously probed only
    the entries table. Drop the command-receipts table (leaving entries
    intact) and /readyz must report not-ready — a bare entries-only probe
    previously reported ready=true here while any idempotency-keyed
    create/metadata/advance commit would raise a 500 against the missing
    receipts table."""
    import psycopg

    # Trigger bootstrap (schema/table creation) first via a real register
    # call, then drop only the receipts table out from under the app.
    _register(pg_app)

    receipts_table = os.environ["REGISTRY_RECEIPTS_TABLE"]
    with psycopg.connect(os.environ["REGISTRY_STORE_DSN"]) as conn:
        conn.execute(f"DROP TABLE {receipts_table}")

    from . import main as main_module

    client = TestClient(main_module.app, headers=_AUTH_HEADERS)
    resp = client.get("/readyz")
    body = resp.json()
    assert body.get("ready") is False, body


# ===========================================================================
# Gen-10 independent Codex rejection of PR #5620 (findings 1, 2, 4, 5): a
# caller-supplied registry_id collision on the allocation-policy-artifact
# route bypassed authorization/content validation; create_with_receipt's
# receipts-before-entries lock order (opposite of every other create path)
# could deadlock a mixed keyed/unkeyed concurrent registration; a typed
# create replay returned the later mutated row instead of the original
# creation receipt; and /advance accepted an entirely unbound (no caller
# claimed base at all) request.
# ===========================================================================


def _alloc_payload(registry_id: str, *, capital_pool_id: str = "pool-durability", version: str = "1.0.0") -> dict:
    return {
        "version": version,
        "registry_id": registry_id,
        "allocation_policy_artifact": {
            "artifact_id": f"artifact-{registry_id}",
            "capital_pool_id": capital_pool_id,
            "scope_ref": "paper",
            "sponsor_persona_id": "persona-momentum",
            "synthesis_method": "weighted_fusion",
            "target_weights": {"SPY": 0.6, "QQQ": 0.4},
            "created_at": "2026-06-01T12:00:00Z",
            "provenance_refs": ["prop-001"],
            "conflict_resolution_log_id": "log-001",
        },
    }


def test_allocation_policy_cross_tenant_registry_id_collision_is_denied(pg_app):
    """Reviewer finding 1 (gen-10 review): a caller-supplied registry_id that
    already names another tenant's private AllocationPolicyArtifact must be
    denied (403), not silently returned as if the POST succeeded."""
    tenant_a = {"Authorization": f"Bearer {_strict_jwt(subject='alice', tenant='tenant-a')}"}
    tenant_b = {"Authorization": f"Bearer {_strict_jwt(subject='bob', tenant='tenant-b')}"}

    payload = _alloc_payload("reg-alloc-durability-cross")
    created = pg_app.post("/api/registry/allocation-policy-artifacts", json=payload, headers=tenant_a)
    assert created.status_code == 200, created.text

    denied_read = pg_app.get(
        f"/api/registry/allocation-policy-artifacts/reg-alloc-durability-cross", headers=tenant_b,
    )
    assert denied_read.status_code == 403

    collision = pg_app.post("/api/registry/allocation-policy-artifacts", json=payload, headers=tenant_b)
    assert collision.status_code == 403, collision.text

    # A same-tenant, identical-content replay must still succeed (idempotent
    # collision, not a regression of legitimate re-registration).
    replay = pg_app.post("/api/registry/allocation-policy-artifacts", json=payload, headers=tenant_a)
    assert replay.status_code == 200, replay.text


def test_allocation_policy_registry_id_collision_with_different_kind_is_denied(pg_app):
    """Reviewer finding 1 (gen-10 review): a registry_id already owned by a
    different artifact kind (e.g. a StrategySpec) must not be returned
    through the allocation-policy-artifacts POST just because the id string
    matches — even for the same tenant."""
    tenant_a = {"Authorization": f"Bearer {_strict_jwt(subject='alice', tenant='tenant-a')}"}

    spec_payload = {
        "registry_id": "reg-durability-cross-kind",
        "strategy_id": "durability-cross-kind-strat",
        "version": "1.0.0",
        "strategy_spec": _valid_spec("durability-cross-kind-strat"),
        "lineage": {"source_run_ids": ["source"]},
    }
    created = pg_app.post("/api/registry/strategy-specs", json=spec_payload, headers=tenant_a)
    assert created.status_code == 200, created.text

    leak = pg_app.post(
        "/api/registry/allocation-policy-artifacts",
        json=_alloc_payload("reg-durability-cross-kind"),
        headers=tenant_a,
    )
    assert leak.status_code == 400, leak.text
    assert "strategy_spec" not in leak.text or "AllocationPolicyArtifact registry entry not found" in leak.text


def test_strategy_spec_create_replay_returns_original_snapshot_not_live_mutated_row(pg_app):
    """Reviewer finding 4 (gen-10 review): an exact-content create replay of
    a StrategySpec must return the entry exactly as it was at its original
    creation, not whatever it has since become via an unrelated later
    metadata edit. The ordinary GET route remains the way to observe the
    live, mutated state."""
    tenant_a = {"Authorization": f"Bearer {_strict_jwt(subject='alice', tenant='tenant-a')}"}

    spec_payload = {
        "registry_id": "reg-durability-replay-snapshot",
        "strategy_id": "durability-replay-snapshot-strat",
        "version": "1.0.0",
        "strategy_spec": _valid_spec("durability-replay-snapshot-strat"),
        "lineage": {"source_run_ids": ["source"]},
    }
    created = pg_app.post("/api/registry/strategy-specs", json=spec_payload, headers=tenant_a)
    assert created.status_code == 200, created.text
    original_metadata = created.json()["entry"]["metadata"]

    edit = pg_app.patch(
        "/api/registry/entries/reg-durability-replay-snapshot/metadata",
        json={
            "expected_metadata": original_metadata,
            "metadata": dict(original_metadata, operator_note="edited-after-create"),
            "command_key": "edit-after-create",
        },
        headers=tenant_a,
    )
    assert edit.status_code == 200, edit.text

    replay = pg_app.post("/api/registry/strategy-specs", json=spec_payload, headers=tenant_a)
    assert replay.status_code == 200, replay.text
    assert replay.json()["entry"]["metadata"] == original_metadata, (
        "create replay must return the original creation snapshot, not the live mutated row"
    )

    live = pg_app.get("/api/registry/entries/reg-durability-replay-snapshot", headers=tenant_a)
    assert live.json()["entry"]["metadata"].get("operator_note") == "edited-after-create", (
        "the ordinary GET route must still show the live, mutated state"
    )


def test_advance_without_any_caller_bound_base_is_rejected(pg_app):
    """Reviewer finding 5 (gen-10 review): every public advance facade now
    requires expected_artifact_state — an advance request that omits every
    caller-claimed base field entirely must be rejected (422) rather than
    silently falling back to a fresh server re-read as its CAS base."""
    created = _register(pg_app, lineage={"source_run_ids": ["run-1"]})
    registry_id = created["entry"]["registry_id"]

    bound = pg_app.post(
        f"/api/registry/entries/{registry_id}/advance",
        json={"target_state": "candidate", "expected_artifact_state": "draft"},
    )
    assert bound.status_code == 200, bound.text

    unbound = pg_app.post(
        f"/api/registry/entries/{registry_id}/advance",
        json={"target_state": "retired"},
    )
    assert unbound.status_code == 422, unbound.text

    unchanged = pg_app.get(f"/api/registry/entries/{registry_id}")
    assert unchanged.json()["entry"]["artifact_state"] == "candidate", (
        "a rejected unbound advance must not mutate the entry"
    )


def test_mixed_keyed_and_unkeyed_create_paths_do_not_deadlock(pg_app):
    """Reviewer finding 2 (gen-10 review): create_with_receipt (the
    Idempotency-Key'd POST /api/registry/entries path) previously locked its
    receipts table before ever touching entries — the opposite order from
    create_if_absent/register_strategy_spec_revision, which always lock
    entries first. Two unrelated, lawful concurrent HTTP requests — one
    landing on each path — could deadlock in Postgres as a result. Fire many
    concurrent mixed requests and assert none raises a database error (in
    particular, never a raw 500 from an uncaught DeadlockDetected); both
    kinds of request must complete with an ordinary HTTP status."""

    def _keyed_draft(i: int):
        return pg_app.post(
            "/api/registry/entries",
            json={"name": f"deadlock-probe-draft-{i}"},
            headers={**_AUTH_HEADERS, "Idempotency-Key": f"deadlock-probe-key-{i}"},
        )

    def _unkeyed_spec(i: int):
        strategy_id = f"deadlock-probe-spec-{i}"
        return pg_app.post(
            "/api/registry/strategy-specs",
            json={
                "strategy_id": strategy_id,
                "version": "1.0.0",
                "strategy_spec": _valid_spec(strategy_id),
                "lineage": {"source_run_ids": ["source"]},
            },
        )

    tasks = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for i in range(8):
            tasks.append(pool.submit(_keyed_draft, i))
            tasks.append(pool.submit(_unkeyed_spec, i))
        results = [task.result(timeout=30) for task in tasks]

    statuses = [r.status_code for r in results]
    assert all(status in (200, 409) for status in statuses), statuses
    assert all(status != 500 for status in statuses), statuses
