"""L12-IMIT-001 proofs: inbound authority, concurrency, and failure recovery.

These are the negative and adversarial halves of the task's proof set.  The
happy-path loop lives in ``test_l12_imit_001_real_dataset_scheduling.py``; this
module proves the boundaries hold when the caller is hostile, two tenants run
at once, a tick fires twice, the backlog file is damaged, or a worker is
SIGKILLed mid-lease.

Covered:

  * auth-negative        — unauthenticated, forged, wrong-tenant, and
                           tenant-smuggling requests on every direct route
  * concurrent           — two tenants ticking and claiming simultaneously
    cross-tenant           never see or take each other's work
  * duplicate / replay   — concurrent duplicate ticks converge on one
                           candidate; replay is fenced against a live lease
  * corruption / restart — a damaged backlog is an outage, never an empty
                           backlog, and the service recovers when it is fixed
  * real two-process     — with ``PANTHEON_TEST_POSTGRES_DSN``, two OS
    Postgres crash /       processes share a Postgres candidate authority, one
    restart / reclaim      is SIGKILLed mid-lease, and the survivor reclaims
                           the orphaned work without double-settling it
"""

from __future__ import annotations

import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import uuid
from datetime import timedelta
from pathlib import Path
from unittest import mock

import pytest

from conftest import TEST_SERVICE_TOKEN, auth_headers, authorized_client


SERVICE_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = SERVICE_DIR.parents[1]

POSTGRES_DSN = os.getenv("PANTHEON_TEST_POSTGRES_DSN", "").strip()

# Every direct imitation-loop route, with a request that would succeed for an
# authorized caller.  Auth-negative assertions sweep the whole surface so a
# newly added route cannot quietly stay open.
PROTECTED_ROUTES = (
    ("POST", "/api/policy-learning/shadow-eval-tick", {"tick_id": "tick-authz"}),
    ("GET", "/api/policy-learning/candidates", None),
    ("GET", "/api/policy-learning/candidates/sic-anything", None),
    ("GET", "/api/policy-learning/candidates/sic-anything/governance", None),
    ("POST", "/api/policy-learning/candidates/sic-anything/promote", None),
    ("GET", "/api/policy-learning/worker/backlog", None),
    ("GET", "/api/policy-learning/worker/claims", None),
    ("GET", "/api/policy-learning/worker/degraded", None),
    ("GET", "/api/policy-learning/worker/dlq", None),
    ("GET", "/api/policy-learning/worker/readback/sic-anything", None),
    ("POST", "/api/policy-learning/worker/claim", {}),
    ("POST", "/api/policy-learning/worker/process", {}),
    ("POST", "/api/policy-learning/worker/restart", {}),
    ("POST", "/api/policy-learning/worker/retry/sic-anything", {}),
    ("POST", "/api/policy-learning/worker/dlq/sic-anything/replay", {}),
)


def _load_service_module(data_dir: str, env: dict[str, str] | None = None):
    for key in list(sys.modules):
        if "policy_learning_authz_test" in key:
            del sys.modules[key]
    sys.modules.pop("store", None)
    sys.modules.pop("agora_dataset_authority", None)
    sys.modules.pop("inbound_authority", None)
    if str(SERVICE_DIR) not in sys.path:
        sys.path.insert(0, str(SERVICE_DIR))
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    try:
        spec = importlib.util.spec_from_file_location(
            "policy_learning_authz_test_main", SERVICE_DIR / "main.py"
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules["policy_learning_authz_test_main"] = module
        environ = {
            "POLICY_LEARNING_DATA_DIR": data_dir,
            "POLICY_LEARNING_STORE_BACKEND": "json",
            "PERSISTENCE_POSTURE": "lenient",
        }
        environ.update(env or {})
        with mock.patch.dict("os.environ", environ):
            spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop("store", None)


def _load_store_module():
    sys.modules.pop("store", None)
    if str(SERVICE_DIR) not in sys.path:
        sys.path.insert(0, str(SERVICE_DIR))
    spec = importlib.util.spec_from_file_location(
        "policy_learning_authz_store", SERVICE_DIR / "store.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _agora_record(dataset_version_id: str, *, tenant_id: str = "tenant-a", order: int = 0) -> dict:
    evidence_id = f"ev-{tenant_id}-{dataset_version_id}"
    return {
        "evidence_id": evidence_id,
        "dataset_version_id": dataset_version_id,
        "dataset_kind": "learn",
        "interaction_kind": "feedback",
        "persona_id": "persona-1",
        "session_id": "session-1",
        "tenant_id": tenant_id,
        "user_id": "",
        "content": {
            "steps": [
                {
                    "observation": [0.9, 0.1, -0.2],
                    "action": "buy_small",
                    "reward": 0.3,
                    "feedback_event_id": f"{evidence_id}-a",
                },
                {
                    "observation": [-0.8, 0.2, 0.55],
                    "action": "reduce_risk",
                    "reward": 0.1,
                    "feedback_event_id": f"{evidence_id}-b",
                },
            ]
        },
        "source_refs": ["artifact://source-1"],
        "learning_eligible": True,
        "captured_at": "2026-07-26T00:00:00Z",
        "extracted_at": f"2026-07-26T00:00:{order:02d}Z",
        "version": 1,
    }


def _install_authority(svc, records: list[dict]):
    authority_module = sys.modules["agora_dataset_authority"]
    svc.DATASET_AUTHORITY = authority_module.AgoraDatasetAuthority(records=records)
    return svc.DATASET_AUTHORITY


def _raw_client(svc):
    from fastapi.testclient import TestClient

    return TestClient(svc.app)


def _call(client, method: str, path: str, body, headers: dict[str, str] | None = None):
    if method == "GET":
        return client.get(path, headers=headers)
    return client.post(path, json=body if body is not None else {}, headers=headers)


# ---------------------------------------------------------------------------
# Auth-negative
# ---------------------------------------------------------------------------


def test_every_direct_route_rejects_an_unauthenticated_caller() -> None:
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        _install_authority(svc, [_agora_record("dsv-1", order=1)])
        client = _raw_client(svc)

        for method, path, body in PROTECTED_ROUTES:
            response = _call(client, method, path, body, headers={"X-Tenant-Id": "tenant-a"})
            assert response.status_code == 401, f"{method} {path} answered {response.status_code}"

        assert svc.store.list_candidates() == []


def test_direct_routes_reject_a_forged_service_token() -> None:
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        _install_authority(svc, [_agora_record("dsv-1", order=1)])
        client = _raw_client(svc)
        forged = auth_headers("tenant-a", token="not-the-service-token")

        for method, path, body in PROTECTED_ROUTES:
            response = _call(client, method, path, body, headers=forged)
            # A non-JWT string in strict mode is refused by the shared
            # validator; it never falls back to structured-token parsing.
            assert response.status_code == 401, f"{method} {path} answered {response.status_code}"

        assert svc.store.list_candidates() == []


def test_authenticated_caller_outside_its_tenant_authority_is_refused() -> None:
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        _install_authority(svc, [_agora_record("dsv-1", tenant_id="tenant-outside", order=1)])
        client = _raw_client(svc)

        response = _call(
            client,
            "POST",
            "/api/policy-learning/shadow-eval-tick",
            {"tick_id": "tick-outside"},
            headers=auth_headers("tenant-outside"),
        )
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "TENANT_FORBIDDEN"
        assert svc.store.list_candidates() == []


def test_body_tenant_cannot_smuggle_a_second_scope_past_the_header() -> None:
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        _install_authority(
            svc,
            [_agora_record("dsv-b", tenant_id="tenant-b", order=1)],
        )
        client = _raw_client(svc)

        smuggled = _call(
            client,
            "POST",
            "/api/policy-learning/shadow-eval-tick",
            {"tick_id": "tick-smuggle", "tenant_id": "tenant-b"},
            headers=auth_headers("tenant-a"),
        )
        assert smuggled.status_code == 403
        assert smuggled.json()["detail"]["code"] == "TENANT_PAYLOAD_MISMATCH"

        claimed = _call(
            client,
            "POST",
            "/api/policy-learning/worker/claim",
            {"worker_id": "w", "tenant_id": "tenant-b"},
            headers=auth_headers("tenant-a"),
        )
        assert claimed.status_code == 403
        assert svc.store.list_candidates() == []


def test_conflicting_tenant_headers_are_refused() -> None:
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        _install_authority(svc, [_agora_record("dsv-1", order=1)])
        client = _raw_client(svc)

        response = client.post(
            "/api/policy-learning/shadow-eval-tick",
            json={"tick_id": "tick-conflict"},
            headers={
                "Authorization": f"Bearer {TEST_SERVICE_TOKEN}",
                "X-Tenant-Id": "tenant-a",
                "X-Pantheon-Tenant": "tenant-b",
            },
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "TENANT_HEADER_CONFLICT"


def test_explicit_dataset_ref_for_another_tenant_is_rejected_not_restamped() -> None:
    """A ref naming another tenant must not be re-bound to the caller's scope."""

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        _install_authority(svc, [_agora_record("dsv-b", tenant_id="tenant-b", order=1)])
        client = authorized_client(svc.app, "tenant-a")

        response = client.post(
            "/api/policy-learning/shadow-eval-tick",
            json={
                "tick_id": "tick-refs",
                "dataset_refs": [{"dataset_version_id": "dsv-b", "tenant_id": "tenant-b"}],
            },
        )
        assert response.status_code == 201
        body = response.json()
        assert body["candidate_count"] == 0
        assert body["rejected_refs"][0]["reason"] == "dataset_ref_tenant_mismatch"
        assert svc.store.list_candidates() == []


def test_candidate_reads_are_invisible_across_tenants() -> None:
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        _install_authority(
            svc,
            [
                _agora_record("dsv-a", tenant_id="tenant-a", order=1),
                _agora_record("dsv-b", tenant_id="tenant-b", order=2),
            ],
        )
        client_a = authorized_client(svc.app, "tenant-a")
        client_b = authorized_client(svc.app, "tenant-b")

        created_a = client_a.post(
            "/api/policy-learning/shadow-eval-tick", json={"tick_id": "tick-a"}
        ).json()
        client_a.post("/api/policy-learning/worker/process", json={"worker_id": "worker-a"})
        candidate_id = created_a["candidate_ids"][0]

        # Tenant B knows the id but must not be able to use it anywhere.
        for method, path in (
            ("GET", f"/api/policy-learning/candidates/{candidate_id}"),
            ("GET", f"/api/policy-learning/worker/readback/{candidate_id}"),
            ("GET", f"/api/policy-learning/candidates/{candidate_id}/governance"),
            ("POST", f"/api/policy-learning/candidates/{candidate_id}/promote"),
            ("POST", f"/api/policy-learning/worker/retry/{candidate_id}"),
            ("POST", f"/api/policy-learning/worker/dlq/{candidate_id}/replay"),
        ):
            response = _call(client_b, method, path, {})
            assert response.status_code == 404, f"{method} {path} answered {response.status_code}"
            assert candidate_id not in response.text

        assert client_b.get("/api/policy-learning/candidates").json() == []
        assert client_b.get("/api/policy-learning/worker/claims").json() == []
        # Tenant A's record is untouched by tenant B's attempts.
        assert client_a.get(f"/api/policy-learning/candidates/{candidate_id}").json()["status"] == "processed"


def test_a_verified_jwt_without_tenant_authority_is_refused() -> None:
    """A signature is not authority: the token must carry tenant claims."""

    from services.runtime_auth_inbound import encode_jwt_hs256

    secret = "l12-imit-001-jwt-secret"
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        _install_authority(svc, [_agora_record("dsv-1", order=1)])
        client = _raw_client(svc)

        scoped = encode_jwt_hs256(
            {
                "sub": "imitation-scheduler",
                "roles": ["policy-learning-service"],
                "allowed_tenants": ["tenant-a"],
            },
            secret=secret,
        )
        unscoped = encode_jwt_hs256(
            {"sub": "imitation-scheduler", "roles": ["policy-learning-service"]},
            secret=secret,
        )
        unprivileged = encode_jwt_hs256(
            {"sub": "someone", "roles": ["viewer"], "allowed_tenants": ["tenant-a"]},
            secret=secret,
        )

        with mock.patch.dict(
            os.environ,
            {"POLICY_LEARNING_JWT_SECRET": secret, "POLICY_LEARNING_SERVICE_TOKEN": ""},
        ):
            ok = client.post(
                "/api/policy-learning/shadow-eval-tick",
                json={"tick_id": "tick-jwt"},
                headers={"Authorization": f"Bearer {scoped}", "X-Tenant-Id": "tenant-a"},
            )
            assert ok.status_code == 201

            no_tenant = client.post(
                "/api/policy-learning/shadow-eval-tick",
                json={"tick_id": "tick-jwt-2"},
                headers={"Authorization": f"Bearer {unscoped}", "X-Tenant-Id": "tenant-a"},
            )
            assert no_tenant.status_code == 403
            assert no_tenant.json()["detail"]["code"] == "TENANT_SCOPE_UNCONFIGURED"

            wrong_role = client.post(
                "/api/policy-learning/shadow-eval-tick",
                json={"tick_id": "tick-jwt-3"},
                headers={"Authorization": f"Bearer {unprivileged}", "X-Tenant-Id": "tenant-a"},
            )
            assert wrong_role.status_code == 403

            forged_signature = client.post(
                "/api/policy-learning/shadow-eval-tick",
                json={"tick_id": "tick-jwt-4"},
                headers={
                    "Authorization": f"Bearer {scoped[:-4]}beef",
                    "X-Tenant-Id": "tenant-a",
                },
            )
            assert forged_signature.status_code == 401

        # Only the one authorized tick created work.
        assert {c["tick_id"] for c in svc.store.list_candidates()} == {"tick-jwt"}


def test_inbound_authority_does_not_inherit_the_runtime_manager_secret() -> None:
    """policy-learning is its own authority boundary."""

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        _install_authority(svc, [_agora_record("dsv-1", order=1)])
        client = _raw_client(svc)
        runtime_secret = "runtime-manager-secret"

        from services.runtime_auth_inbound import encode_jwt_hs256

        runtime_token = encode_jwt_hs256(
            {
                "sub": "runtime-manager",
                "roles": ["policy-learning-service"],
                "allowed_tenants": ["tenant-a"],
            },
            secret=runtime_secret,
        )

        with mock.patch.dict(
            os.environ,
            {
                "PANTHEON_RUNTIME_JWT_SECRET": runtime_secret,
                "POLICY_LEARNING_SERVICE_TOKEN": "",
            },
        ):
            os.environ.pop("POLICY_LEARNING_JWT_SECRET", None)
            configuration = svc.authority_configuration()
            assert configuration["jwt_verifier_configured"] is False
            assert configuration["inherits_runtime_manager_secret"] is False
            assert configuration["configured"] is False
            assert configuration["mode"] == "strict"

            # A token another service's secret would verify is not accepted here.
            response = client.post(
                "/api/policy-learning/shadow-eval-tick",
                json={"tick_id": "tick-runtime-secret"},
                headers={"Authorization": f"Bearer {runtime_token}", "X-Tenant-Id": "tenant-a"},
            )
            assert response.status_code in (401, 500)
            assert svc.store.list_candidates() == []


def _load_agora_authority_module():
    if str(SERVICE_DIR) not in sys.path:
        sys.path.insert(0, str(SERVICE_DIR))
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    spec = importlib.util.spec_from_file_location(
        "policy_learning_authz_agora", SERVICE_DIR / "agora_dataset_authority.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["policy_learning_authz_agora"] = module
    spec.loader.exec_module(module)
    return module


def test_agora_resolution_requires_an_authenticated_connection() -> None:
    """A DSN with no identity is refused, not attempted anonymously."""

    authority_module = _load_agora_authority_module()
    anonymous = authority_module.AgoraDatasetAuthority(
        backend="postgres", dsn="postgresql://127.0.0.1:5432/pantheon"
    )
    described = anonymous.describe()
    assert described["backend"] == "postgres"
    assert described["authenticated"] is False
    assert described["session_access"] == "read_only"
    assert described["seed_fallback"] == "none"

    with mock.patch.dict(os.environ, {}, clear=True):
        with pytest.raises(authority_module.AgoraAuthorityUnavailable) as excinfo:
            anonymous.list_dataset_versions(tenant_id="tenant-a")
    assert excinfo.value.reason == "agora_authority_unauthenticated"


def test_agora_authority_reports_whether_a_dedicated_read_role_is_configured() -> None:
    authority_module = _load_agora_authority_module()
    with mock.patch.dict(
        os.environ,
        {
            "AGORA_DATASET_STORE_DSN": "postgresql://agora_reader:secret@db:5432/pantheon",
            "DATABASE_URL": "postgresql://pantheon_app:pantheon_app@db:5432/pantheon",
        },
    ):
        os.environ.pop("AGORA_DATASET_STORE_BACKEND", None)
        dedicated = authority_module.AgoraDatasetAuthority().describe()
    assert dedicated["dedicated_read_role_dsn"] is True
    assert dedicated["authenticated"] is True
    assert "secret" not in json.dumps(dedicated)

    with mock.patch.dict(
        os.environ,
        {"DATABASE_URL": "postgresql://pantheon_app:pantheon_app@db:5432/pantheon"},
    ):
        os.environ.pop("AGORA_DATASET_STORE_BACKEND", None)
        os.environ.pop("AGORA_DATASET_STORE_DSN", None)
        shared = authority_module.AgoraDatasetAuthority().describe()
    assert shared["dedicated_read_role_dsn"] is False
    assert shared["authenticated"] is True


@pytest.mark.skipif(not POSTGRES_DSN, reason="PANTHEON_TEST_POSTGRES_DSN is not configured")
def test_real_postgres_agora_resolution_is_tenant_scoped_and_read_only() -> None:
    """Against real Agora rows: one tenant sees only its own, and cannot write."""

    import psycopg

    authority_module = _load_agora_authority_module()
    schema = f"agora_imit_it_{uuid.uuid4().hex[:16]}"
    tenant_a = f"tenant-{uuid.uuid4().hex[:8]}"
    tenant_b = f"tenant-{uuid.uuid4().hex[:8]}"

    try:
        with psycopg.connect(POSTGRES_DSN) as conn:
            conn.execute(f'CREATE SCHEMA "{schema}"')
            conn.execute(
                f"""
                CREATE TABLE "{schema}"."{authority_module.RECORDS_TABLE}" (
                    evidence_id TEXT PRIMARY KEY,
                    dataset_version_id TEXT,
                    dataset_kind TEXT,
                    interaction_kind TEXT,
                    persona_id TEXT,
                    session_id TEXT,
                    tenant_id TEXT,
                    user_id TEXT,
                    content JSONB,
                    source_refs JSONB,
                    learning_eligible BOOLEAN,
                    captured_at TEXT,
                    extracted_at TEXT,
                    version INTEGER
                )
                """
            )
            for tenant_id in (tenant_a, tenant_b):
                for index in range(2):
                    record = _agora_record(f"dsv-{index}", tenant_id=tenant_id, order=index)
                    conn.execute(
                        f'INSERT INTO "{schema}"."{authority_module.RECORDS_TABLE}" '
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s)",
                        (
                            record["evidence_id"],
                            record["dataset_version_id"],
                            record["dataset_kind"],
                            record["interaction_kind"],
                            record["persona_id"],
                            record["session_id"],
                            record["tenant_id"],
                            record["user_id"],
                            json.dumps(record["content"]),
                            json.dumps(record["source_refs"]),
                            record["learning_eligible"],
                            record["captured_at"],
                            record["extracted_at"],
                            record["version"],
                        ),
                    )

        authority = authority_module.AgoraDatasetAuthority(
            backend="postgres", dsn=POSTGRES_DSN, schema=schema
        )
        assert authority.describe()["authenticated"] is True

        versions_a = authority.list_dataset_versions(tenant_id=tenant_a)
        assert {version.tenant_id for version in versions_a} == {tenant_a}
        assert len(versions_a) == 2

        # A version that exists, but in another tenant, is simply not visible.
        with pytest.raises(authority_module.AgoraAuthorityUnavailable) as excinfo:
            authority.get_dataset_versions("dsv-0", tenant_id=f"tenant-{uuid.uuid4().hex[:8]}")
        assert excinfo.value.reason == "dataset_version_not_found"

        # The session policy-learning opens on another owner's table is
        # read-only, so a stray write is refused by Postgres itself.
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            with authority._connect() as conn:
                conn.execute(
                    f'DELETE FROM "{schema}"."{authority_module.RECORDS_TABLE}" WHERE tenant_id = %s',
                    (tenant_a,),
                )
        with psycopg.connect(POSTGRES_DSN) as conn:
            surviving = conn.execute(
                f'SELECT count(*) FROM "{schema}"."{authority_module.RECORDS_TABLE}"'
            ).fetchone()[0]
        assert surviving == 4
    finally:
        with psycopg.connect(POSTGRES_DSN) as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


# ---------------------------------------------------------------------------
# Concurrent cross-tenant
# ---------------------------------------------------------------------------


def test_concurrent_cross_tenant_ticks_and_claims_stay_isolated() -> None:
    """Two tenants ticking and claiming at once never touch each other."""

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        _install_authority(
            svc,
            [_agora_record(f"dsv-a{n}", tenant_id="tenant-a", order=n) for n in range(6)]
            + [_agora_record(f"dsv-b{n}", tenant_id="tenant-b", order=10 + n) for n in range(6)],
        )
        clients = {
            "tenant-a": authorized_client(svc.app, "tenant-a"),
            "tenant-b": authorized_client(svc.app, "tenant-b"),
        }
        results: dict[str, dict] = {}
        barrier = threading.Barrier(2)

        def run(tenant: str) -> None:
            client = clients[tenant]
            barrier.wait()
            tick = client.post(
                "/api/policy-learning/shadow-eval-tick", json={"tick_id": f"tick-{tenant}"}
            ).json()
            claimed = client.post(
                "/api/policy-learning/worker/claim",
                json={"worker_id": f"worker-{tenant}", "batch_size": 50, "lease_seconds": 300},
            ).json()
            results[tenant] = {"tick": tick, "claim": claimed}

        threads = [threading.Thread(target=run, args=(tenant,)) for tenant in clients]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        for tenant in clients:
            assert results[tenant]["tick"]["candidate_count"] == 6
            assert results[tenant]["claim"]["claimed_count"] == 6

        claimed_a = {c["candidate_id"] for c in results["tenant-a"]["claim"]["claims"]}
        claimed_b = {c["candidate_id"] for c in results["tenant-b"]["claim"]["claims"]}
        assert not claimed_a & claimed_b

        stored = {c["candidate_id"]: c for c in svc.store.list_candidates()}
        assert len(stored) == 12
        for candidate_id in claimed_a:
            assert stored[candidate_id]["tenant_id"] == "tenant-a"
            assert stored[candidate_id]["lease_owner"] == "worker-tenant-a"
        for candidate_id in claimed_b:
            assert stored[candidate_id]["tenant_id"] == "tenant-b"
            assert stored[candidate_id]["lease_owner"] == "worker-tenant-b"


def test_one_tenants_restart_does_not_recover_another_tenants_orphans() -> None:
    """Recovery is a per-tenant sweep, not a global one."""

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        _install_authority(
            svc,
            [
                _agora_record("dsv-a", tenant_id="tenant-a", order=1),
                _agora_record("dsv-b", tenant_id="tenant-b", order=2),
            ],
        )
        client_a = authorized_client(svc.app, "tenant-a")
        client_b = authorized_client(svc.app, "tenant-b")
        client_a.post("/api/policy-learning/shadow-eval-tick", json={"tick_id": "tick-a"})
        client_b.post("/api/policy-learning/shadow-eval-tick", json={"tick_id": "tick-b"})

        # Both tenants' workers claim, then both die, leaving expired leases.
        for client, tenant in ((client_a, "tenant-a"), (client_b, "tenant-b")):
            client.post(
                "/api/policy-learning/worker/claim",
                json={"worker_id": f"worker-{tenant}", "batch_size": 10, "lease_seconds": 900},
            )
        for candidate in svc.store.list_candidates():
            candidate["lease_expires_at"] = "2020-01-01T00:00:00Z"
            svc.store.put_candidate(candidate)

        recovered = client_a.post(
            "/api/policy-learning/worker/restart", json={"worker_id": "worker-a2"}
        ).json()
        assert recovered["released_count"] == 1
        assert recovered["tenant_id"] == "tenant-a"

        by_tenant = {
            candidate["tenant_id"]: candidate for candidate in svc.store.list_candidates()
        }
        assert by_tenant["tenant-a"]["status"] == "processed"
        # Tenant B's orphan was left exactly as it was, for tenant B to recover.
        assert by_tenant["tenant-b"]["status"] == "claimed"
        assert by_tenant["tenant-b"]["lease_owner"] == "worker-tenant-b"

        recovered_b = client_b.post(
            "/api/policy-learning/worker/restart", json={"worker_id": "worker-b2"}
        ).json()
        assert recovered_b["released_count"] == 1
        assert recovered_b["processed_count"] == 1


# ---------------------------------------------------------------------------
# Duplicate ticks and replay
# ---------------------------------------------------------------------------


def test_concurrent_duplicate_ticks_create_exactly_one_candidate_each() -> None:
    """Duplicate suppression survives a race, not just a repeat."""

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        _install_authority(svc, [_agora_record(f"dsv-{n}", order=n) for n in range(4)])
        worker_count = 6
        barrier = threading.Barrier(worker_count)
        created: list[list[str]] = []
        lock = threading.Lock()

        def tick() -> None:
            client = authorized_client(svc.app, "tenant-a")
            barrier.wait()
            body = client.post(
                "/api/policy-learning/shadow-eval-tick", json={"tick_id": "tick-race"}
            ).json()
            with lock:
                created.append(body["candidate_ids"])

        threads = [threading.Thread(target=tick) for _ in range(worker_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        flattened = [candidate_id for batch in created for candidate_id in batch]
        assert len(flattened) == len(set(flattened)), "the same candidate was created twice"
        assert len(flattened) == 4
        assert len(svc.store.list_candidates()) == 4


def test_replay_is_refused_while_a_worker_still_holds_the_lease() -> None:
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        _install_authority(svc, [_agora_record("dsv-1", order=1)])
        client = authorized_client(svc.app, "tenant-a")
        tick = client.post(
            "/api/policy-learning/shadow-eval-tick", json={"tick_id": "tick-lease"}
        ).json()
        candidate_id = tick["candidate_ids"][0]

        # Put the candidate in the DLQ, then let a worker take a live lease on it.
        failed = svc.store.get_candidate(candidate_id)
        failed["status"] = "failed"
        failed["error_message"] = "injected trainer failure"
        svc.store.put_candidate(failed)
        svc.store.requeue_candidate(candidate_id)
        leased = svc.store.claim_candidates(
            worker_id="worker-live", batch_size=1, lease_seconds=600
        )
        assert leased[0]["candidate_id"] == candidate_id

        refused = client.post(f"/api/policy-learning/worker/retry/{candidate_id}")
        assert refused.status_code == 409
        assert refused.json()["detail"]["code"] == "CANDIDATE_LEASE_HELD"

        # The live owner keeps its lease and can still settle.
        held = svc.store.get_candidate(candidate_id)
        assert held["status"] == "claimed"
        assert held["lease_owner"] == "worker-live"
        settled = svc.store.settle_candidate(
            {**held, "status": "processed"}, lease_token=leased[0]["lease_token"]
        )
        assert settled["status"] == "processed"


def test_replay_of_a_degraded_candidate_reruns_it_after_the_authority_returns() -> None:
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        authority_module = sys.modules["agora_dataset_authority"]
        records = [_agora_record("dsv-1", order=1)]
        _install_authority(svc, records)
        client = authorized_client(svc.app, "tenant-a")
        candidate_id = client.post(
            "/api/policy-learning/shadow-eval-tick", json={"tick_id": "tick-degrade"}
        ).json()["candidate_ids"][0]

        svc.DATASET_AUTHORITY = authority_module.AgoraDatasetAuthority(backend="", dsn="")
        client.post("/api/policy-learning/worker/process", json={"worker_id": "worker-1"})
        degraded = client.get(f"/api/policy-learning/candidates/{candidate_id}").json()
        assert degraded["status"] == "degraded"
        assert degraded["degradation"]["seed_fallback_used"] is False

        _install_authority(svc, records)
        replayed = client.post(f"/api/policy-learning/worker/dlq/{candidate_id}/replay").json()
        assert replayed["status"] == "processed"
        assert replayed["dataset_lineage"]["seed_fallback_used"] is False
        assert "degradation" not in replayed


# ---------------------------------------------------------------------------
# Corruption and restart
# ---------------------------------------------------------------------------


def test_a_corrupt_backlog_is_an_outage_not_an_empty_backlog() -> None:
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        _install_authority(svc, [_agora_record("dsv-1", order=1), _agora_record("dsv-2", order=2)])
        client = authorized_client(svc.app, "tenant-a")
        client.post("/api/policy-learning/shadow-eval-tick", json={"tick_id": "tick-corrupt"})

        backlog_path = Path(svc.store.candidates_path)
        healthy = backlog_path.read_text(encoding="utf-8")
        assert len(json.loads(healthy)) == 2

        # A crash during a non-atomic write leaves a truncated file.
        backlog_path.write_text(healthy[: len(healthy) // 2], encoding="utf-8")

        for path in (
            "/api/policy-learning/candidates",
            "/api/policy-learning/worker/backlog",
            "/api/policy-learning/worker/dlq",
        ):
            response = client.get(path)
            assert response.status_code == 503, f"{path} answered {response.status_code}"
            detail = response.json()["detail"]
            assert detail["code"] == "CANDIDATE_STORE_CORRUPT"
            assert detail["reason"]

        claimed = client.post("/api/policy-learning/worker/claim", json={"worker_id": "w"})
        assert claimed.status_code == 503

        # A tick must not overwrite the damaged file with a fresh backlog.
        ticked = client.post(
            "/api/policy-learning/shadow-eval-tick", json={"tick_id": "tick-after-corrupt"}
        )
        assert ticked.status_code == 503
        assert backlog_path.read_text(encoding="utf-8") == healthy[: len(healthy) // 2]

        # Restoring the file restores the loop, with the original work intact.
        backlog_path.write_text(healthy, encoding="utf-8")
        recovered = client.post("/api/policy-learning/worker/process", json={"worker_id": "w"}).json()
        assert recovered["processed_count"] == 2
        assert {c["status"] for c in client.get("/api/policy-learning/candidates").json()} == {
            "processed"
        }


def test_a_backlog_of_the_wrong_shape_is_also_refused() -> None:
    store_module = _load_store_module()
    with tempfile.TemporaryDirectory() as data_dir:
        store = store_module.PolicyLearningStore(data_dir)
        store.put_candidate({"candidate_id": "sic-1", "status": "proposed"})

        for damaged in ("[]", '{"sic-1": "not-an-object"}', "not json at all"):
            Path(store.candidates_path).write_text(damaged, encoding="utf-8")
            with pytest.raises(store_module.CandidateStoreCorrupt):
                store.list_candidates()

        # A missing or empty file is a genuine empty backlog, not corruption.
        Path(store.candidates_path).write_text("", encoding="utf-8")
        assert store.list_candidates() == []
        Path(store.candidates_path).unlink()
        assert store.list_candidates() == []


def test_backlog_writes_are_atomic_under_a_concurrent_reader() -> None:
    """A reader never observes a half-written backlog."""

    store_module = _load_store_module()
    with tempfile.TemporaryDirectory() as data_dir:
        writer = store_module.PolicyLearningStore(data_dir)
        reader = store_module.PolicyLearningStore(data_dir)
        for index in range(50):
            writer.put_candidate(
                {
                    "candidate_id": f"sic-{index:03d}",
                    "status": "proposed",
                    "dataset_ref": {"dataset_version_id": f"dsv-{index}", "tenant_id": "tenant-a"},
                    "padding": "x" * 512,
                }
            )

        stop = threading.Event()
        failures: list[str] = []

        def read_forever() -> None:
            while not stop.is_set():
                try:
                    records = reader.list_candidates()
                except Exception as exc:  # corruption or partial read
                    failures.append(repr(exc))
                    return
                if len(records) < 50:
                    failures.append(f"observed {len(records)} of 50 candidates")
                    return

        thread = threading.Thread(target=read_forever)
        thread.start()
        try:
            for index in range(50, 120):
                writer.put_candidate(
                    {
                        "candidate_id": f"sic-{index:03d}",
                        "status": "proposed",
                        "dataset_ref": {"dataset_version_id": f"dsv-{index}", "tenant_id": "tenant-a"},
                        "padding": "x" * 512,
                    }
                )
        finally:
            stop.set()
            thread.join()

        assert not failures, failures
        assert len(writer.list_candidates()) == 120


# ---------------------------------------------------------------------------
# Real two-process Postgres crash / restart / reclaim
# ---------------------------------------------------------------------------


_CHILD_WORKER = textwrap.dedent(
    """
    import json, os, sys, time
    sys.path.insert(0, os.environ["L12_SERVICE_DIR"])
    import store as store_module

    candidate_store = store_module.PostgresPolicyLearningCandidateStore(
        dsn=os.environ["L12_DSN"], table=os.environ["L12_TABLE"], bootstrap=False
    )
    mode = sys.argv[1]
    if mode == "claim-and-hang":
        claimed = candidate_store.claim_candidates(
            worker_id=os.environ["L12_WORKER_ID"],
            lease_seconds=int(os.environ["L12_LEASE_SECONDS"]),
            batch_size=int(os.environ["L12_BATCH"]),
        )
        print(json.dumps([record["candidate_id"] for record in claimed]), flush=True)
        # Hold the lease and wait to be killed, exactly like a worker that dies
        # after claiming but before settling.
        while True:
            time.sleep(0.5)
    elif mode == "claim-once":
        claimed = candidate_store.claim_candidates(
            worker_id=os.environ["L12_WORKER_ID"],
            lease_seconds=int(os.environ["L12_LEASE_SECONDS"]),
            batch_size=int(os.environ["L12_BATCH"]),
        )
        print(json.dumps([record["candidate_id"] for record in claimed]), flush=True)
    """
)


def _spawn_child(mode: str, *, dsn: str, table: str, worker_id: str, lease_seconds: int, batch: int):
    env = dict(os.environ)
    env.update(
        {
            "L12_SERVICE_DIR": str(SERVICE_DIR),
            "L12_DSN": dsn,
            "L12_TABLE": table,
            "L12_WORKER_ID": worker_id,
            "L12_LEASE_SECONDS": str(lease_seconds),
            "L12_BATCH": str(batch),
        }
    )
    return subprocess.Popen(
        [sys.executable, "-c", _CHILD_WORKER, mode],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


@pytest.mark.skipif(not POSTGRES_DSN, reason="PANTHEON_TEST_POSTGRES_DSN is not configured")
def test_real_postgres_two_process_crash_restart_and_reclaim() -> None:
    """A SIGKILLed worker's backlog is recovered exactly once, by its peer.

    This runs against a real Postgres with two real OS processes.  The killed
    worker never gets to settle, and once its lease expires the survivor
    reclaims the work; the dead worker's lease token is fenced out even if the
    process were to come back holding it.
    """

    import psycopg

    store_module = _load_store_module()
    schema = f"pl_imit_it_{uuid.uuid4().hex[:16]}"
    table = f"{schema}.candidates"
    tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
    lease_seconds = 2
    candidate_count = 8

    try:
        candidate_store = store_module.PostgresPolicyLearningCandidateStore(
            dsn=POSTGRES_DSN, table=table
        )
        for index in range(candidate_count):
            version_id = f"dsv-{index}"
            candidate_id = store_module.candidate_id_for(tenant_id, "tick-crash", version_id)
            record, created = candidate_store.create_candidate_if_absent(
                {
                    "candidate_id": candidate_id,
                    "id": candidate_id,
                    "dedupe_key": store_module.candidate_dedupe_key(
                        tenant_id, "tick-crash", version_id
                    ),
                    "tick_id": "tick-crash",
                    "status": "proposed",
                    "dataset_ref": {"dataset_version_id": version_id, "tenant_id": tenant_id},
                }
            )
            assert created

        # Duplicate scheduling from a second process is absorbed by the unique
        # (tenant, tick, dataset version) authority, not by a read-then-write.
        _, created_again = candidate_store.create_candidate_if_absent(
            {
                "candidate_id": store_module.candidate_id_for(tenant_id, "tick-crash", "dsv-0"),
                "dedupe_key": store_module.candidate_dedupe_key(tenant_id, "tick-crash", "dsv-0"),
                "tick_id": "tick-crash",
                "status": "proposed",
                "dataset_ref": {"dataset_version_id": "dsv-0", "tenant_id": tenant_id},
            }
        )
        assert created_again is False
        assert len(candidate_store.list_candidates()) == candidate_count

        # Two real processes claim concurrently; the sets must be disjoint.
        children = [
            _spawn_child(
                "claim-once",
                dsn=POSTGRES_DSN,
                table=table,
                worker_id=f"proc-{index}",
                lease_seconds=600,
                batch=candidate_count // 2,
            )
            for index in range(2)
        ]
        claimed_sets = []
        for child in children:
            stdout, stderr = child.communicate(timeout=60)
            assert child.returncode == 0, stderr
            claimed_sets.append(set(json.loads(stdout.strip().splitlines()[-1])))
        assert not claimed_sets[0] & claimed_sets[1]
        assert len(claimed_sets[0] | claimed_sets[1]) == candidate_count

        # Reset for the crash scenario.
        for record in candidate_store.list_candidates():
            store_module.clear_lease(record)
            record["status"] = "proposed"
            record["attempt_count"] = 0
            candidate_store.put_candidate(record)

        victim = _spawn_child(
            "claim-and-hang",
            dsn=POSTGRES_DSN,
            table=table,
            worker_id="worker-victim",
            lease_seconds=lease_seconds,
            batch=candidate_count,
        )
        victim_claims = set(json.loads(victim.stdout.readline().strip()))
        assert len(victim_claims) == candidate_count
        stale_tokens = {
            record["candidate_id"]: record.get("lease_token")
            for record in candidate_store.list_candidates()
        }

        # SIGKILL: no shutdown hook, no settle, no lease release.
        victim.send_signal(signal.SIGKILL)
        victim.wait(timeout=30)
        assert victim.returncode in (-signal.SIGKILL, 137)

        # While the lease is live the survivor takes nothing.
        assert (
            candidate_store.claim_candidates(
                worker_id="worker-survivor", lease_seconds=60, batch_size=candidate_count
            )
            == []
        )

        time.sleep(lease_seconds + 1)
        released = candidate_store.release_expired_leases()
        assert set(released) == victim_claims

        reclaimed = candidate_store.claim_candidates(
            worker_id="worker-survivor", lease_seconds=600, batch_size=candidate_count
        )
        assert {record["candidate_id"] for record in reclaimed} == victim_claims
        # The reclaim is a second attempt on work that was never settled once.
        assert all(record["attempt_count"] == 2 for record in reclaimed), [
            record["attempt_count"] for record in reclaimed
        ]

        # The dead worker's token is fenced: a revived process cannot settle.
        for record in reclaimed:
            with pytest.raises(store_module.LeaseLostError):
                candidate_store.settle_candidate(
                    {**record, "status": "failed"},
                    lease_token=stale_tokens[record["candidate_id"]],
                )

        for record in reclaimed:
            candidate_store.settle_candidate(
                {**record, "status": "processed"}, lease_token=record["lease_token"]
            )
        settled = candidate_store.list_candidates()
        assert {record["status"] for record in settled} == {"processed"}
        assert len(settled) == candidate_count

        # Durability: a brand new store handle (a restarted container) sees the
        # same authoritative rows.
        restarted = store_module.PostgresPolicyLearningCandidateStore(
            dsn=POSTGRES_DSN, table=table, bootstrap=False
        )
        assert {record["candidate_id"] for record in restarted.list_candidates()} == victim_claims
    finally:
        if "victim" in dir() and victim.poll() is None:  # pragma: no cover - defensive
            victim.kill()
        with psycopg.connect(POSTGRES_DSN) as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


@pytest.mark.skipif(not POSTGRES_DSN, reason="PANTHEON_TEST_POSTGRES_DSN is not configured")
def test_real_postgres_concurrent_cross_tenant_claims_never_overlap() -> None:
    """Tenant-scoped claiming holds under real row locks."""

    import psycopg
    from concurrent.futures import ThreadPoolExecutor

    store_module = _load_store_module()
    schema = f"pl_imit_it_{uuid.uuid4().hex[:16]}"
    table = f"{schema}.candidates"
    tenants = [f"tenant-{uuid.uuid4().hex[:8]}" for _ in range(3)]

    try:
        candidate_store = store_module.PostgresPolicyLearningCandidateStore(
            dsn=POSTGRES_DSN, table=table
        )
        for tenant_id in tenants:
            for index in range(5):
                version_id = f"dsv-{index}"
                candidate_id = store_module.candidate_id_for(tenant_id, "tick-x", version_id)
                candidate_store.create_candidate_if_absent(
                    {
                        "candidate_id": candidate_id,
                        "dedupe_key": store_module.candidate_dedupe_key(
                            tenant_id, "tick-x", version_id
                        ),
                        "tick_id": "tick-x",
                        "status": "proposed",
                        "dataset_ref": {"dataset_version_id": version_id, "tenant_id": tenant_id},
                    }
                )

        def claim(tenant_id: str):
            worker_store = store_module.PostgresPolicyLearningCandidateStore(
                dsn=POSTGRES_DSN, table=table, bootstrap=False
            )
            return tenant_id, worker_store.claim_candidates(
                worker_id=f"worker-{tenant_id}",
                lease_seconds=300,
                batch_size=50,
                tenant_id=tenant_id,
            )

        with ThreadPoolExecutor(max_workers=len(tenants)) as executor:
            results = list(executor.map(claim, tenants))

        seen: set[str] = set()
        for tenant_id, claimed in results:
            assert len(claimed) == 5
            for record in claimed:
                assert record["dataset_ref"]["tenant_id"] == tenant_id
                assert record["candidate_id"] not in seen
                seen.add(record["candidate_id"])
        assert len(seen) == 15
    finally:
        with psycopg.connect(POSTGRES_DSN) as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


@pytest.mark.skipif(not POSTGRES_DSN, reason="PANTHEON_TEST_POSTGRES_DSN is not configured")
def test_real_postgres_is_the_default_candidate_authority_in_product_mode() -> None:
    """Compose hands DATABASE_URL and POLICY_LEARNING_STORE_BACKEND=json.

    The candidate backlog is the loop's authoritative record, so that
    combination must still resolve to the durable Postgres authority.
    """

    store_module = _load_store_module()
    schema = f"pl_imit_it_{uuid.uuid4().hex[:16]}"

    import psycopg

    try:
        with mock.patch.dict(
            os.environ,
            {
                "POLICY_LEARNING_STORE_BACKEND": "json",
                "DATABASE_URL": POSTGRES_DSN,
                "POLICY_LEARNING_CANDIDATE_STORE_TABLE": f"{schema}.candidates",
            },
        ):
            os.environ.pop("POLICY_LEARNING_STORE_DSN", None)
            os.environ.pop("POLICY_LEARNING_CANDIDATE_AUTHORITY", None)
            with tempfile.TemporaryDirectory() as data_dir:
                store = store_module.build_policy_learning_store(data_dir, product_mode=True)
                described = store.describe()
                assert described["candidate_authority"] == "postgres"
                assert described["durable"] is True
                assert described["reason"] == "product_mode_durable_default"
                assert "dsn" not in described

                candidate_id = store_module.candidate_id_for("tenant-a", "tick-1", "dsv-1")
                store.create_candidate_if_absent(
                    {
                        "candidate_id": candidate_id,
                        "dedupe_key": store_module.candidate_dedupe_key(
                            "tenant-a", "tick-1", "dsv-1"
                        ),
                        "tick_id": "tick-1",
                        "status": "proposed",
                        "dataset_ref": {"dataset_version_id": "dsv-1", "tenant_id": "tenant-a"},
                    }
                )
                # Nothing was written to the non-durable JSON path.
                assert not Path(store.candidates_path).exists()

                # A restarted process with an empty data dir still sees the row.
                with tempfile.TemporaryDirectory() as fresh_dir:
                    restarted = store_module.build_policy_learning_store(
                        fresh_dir, product_mode=True
                    )
                    assert [c["candidate_id"] for c in restarted.list_candidates()] == [candidate_id]
    finally:
        with psycopg.connect(POSTGRES_DSN) as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
