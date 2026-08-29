"""L12-IMIT-001 proofs: real-dataset imitation and shadow evaluation.

Covers the four proofs the task requires:

  1. default-compose real dataset      — discovery works with the compose
                                          default ``POLICY_LEARNING_STORE_BACKEND=json``
  2. tenant isolation and no-seed      — cross-tenant reads fail, and no failure
                                          path substitutes the seed fixture
  3. two-worker restart and replay     — leases keep concurrent workers disjoint
                                          and recover a crashed worker's claims
  4. candidate governance gate         — a candidate cannot reach runtime
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import threading
from datetime import timedelta
from pathlib import Path
from unittest import mock

import pytest

from conftest import TEST_SERVICE_TOKEN, auth_headers, authorized_client


SERVICE_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = SERVICE_DIR.parents[1]

# Marker strings from the non-product seed fixture.  Product-path assertions
# search serialized candidates for these so a seed leak fails loudly.
SEED_MARKERS = ("alpha-mean-reversion", "traj-smoke-2026-05-16", "trader-01")


def _load_service_module(data_dir: str, env: dict[str, str] | None = None):
    for key in list(sys.modules):
        if "policy_learning_imit_test" in key:
            del sys.modules[key]
    sys.modules.pop("store", None)
    sys.modules.pop("agora_dataset_authority", None)
    if str(SERVICE_DIR) not in sys.path:
        sys.path.insert(0, str(SERVICE_DIR))
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    try:
        spec = importlib.util.spec_from_file_location(
            "policy_learning_imit_test_main",
            SERVICE_DIR / "main.py",
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules["policy_learning_imit_test_main"] = module
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
    spec = importlib.util.spec_from_file_location("policy_learning_imit_store", SERVICE_DIR / "store.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_authority_module():
    if str(SERVICE_DIR) not in sys.path:
        sys.path.insert(0, str(SERVICE_DIR))
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    spec = importlib.util.spec_from_file_location(
        "policy_learning_imit_authority", SERVICE_DIR / "agora_dataset_authority.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_scheduler_module():
    sys.modules.pop("policy_learning_imit_scheduler", None)
    spec = importlib.util.spec_from_file_location(
        "policy_learning_imit_scheduler", SERVICE_DIR / "scheduler_worker.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["policy_learning_imit_scheduler"] = module
    spec.loader.exec_module(module)
    return module


def _agora_record(
    dataset_version_id: str,
    *,
    tenant_id: str = "tenant-a",
    user_id: str = "user-a",
    evidence_id: str | None = None,
    content: dict | None = None,
    learning_eligible: bool = True,
    dataset_kind: str = "learn",
    order: int = 0,
) -> dict:
    """One row shaped like ``agora.agora_dataset_records``."""

    resolved_evidence = evidence_id or f"ev-{dataset_version_id}"
    return {
        "evidence_id": resolved_evidence,
        "dataset_version_id": dataset_version_id,
        "dataset_kind": dataset_kind,
        "interaction_kind": "feedback",
        "persona_id": "persona-1",
        "session_id": "session-1",
        "tenant_id": tenant_id,
        "user_id": user_id,
        "content": content
        or {
            "steps": [
                {
                    "observation": [0.9, 0.1, -0.2],
                    "action": "buy_small",
                    "reward": 0.3,
                    "feedback_event_id": f"{resolved_evidence}-a",
                },
                {
                    "observation": [-0.8, 0.2, 0.55],
                    "action": "reduce_risk",
                    "reward": 0.1,
                    "feedback_event_id": f"{resolved_evidence}-b",
                },
            ]
        },
        "source_refs": ["artifact://source-1"],
        "learning_eligible": learning_eligible,
        "captured_at": "2026-07-26T00:00:00Z",
        "extracted_at": f"2026-07-26T00:00:{order:02d}Z",
        "version": 1,
    }


def _service_authority_module():
    """The authority module instance the loaded service actually imported.

    Loading a second copy would create distinct exception classes, so the
    service's ``except AgoraAuthorityUnavailable`` would stop matching.
    """

    return sys.modules["agora_dataset_authority"]


def _install_authority(svc, records: list[dict], *, tenant_id: str = "tenant-a"):
    authority_module = _service_authority_module()
    svc.DATASET_AUTHORITY = authority_module.AgoraDatasetAuthority(records=records)
    os.environ["POLICY_LEARNING_AGORA_TENANT_ID"] = tenant_id
    return svc.DATASET_AUTHORITY


def _client(svc, tenant_id: str = "tenant-a"):
    """An authenticated client bound to one tenant.

    The imitation-loop routes take their tenant from the verified request, so a
    test that wants a second tenant asks for a second client rather than
    putting a tenant id in the body.
    """

    return authorized_client(svc.app, tenant_id)


def _assert_no_seed_leak(payload) -> None:
    serialized = json.dumps(payload, default=str)
    for marker in SEED_MARKERS:
        assert marker not in serialized, f"seed fixture leaked into product output: {marker}"


# ---------------------------------------------------------------------------
# Proof 1 — default-compose real dataset
# ---------------------------------------------------------------------------


def test_dataset_authority_resolves_independently_of_policy_store_backend() -> None:
    """Discovery must not depend on POLICY_LEARNING_STORE_BACKEND.

    Compose runs policy-learning with the JSON candidate store and only hands
    it DATABASE_URL; before L12-IMIT-001 that combination discovered nothing.
    """

    authority_module = _load_authority_module()
    for policy_store_backend in ("json", "postgres"):
        with mock.patch.dict(
            "os.environ",
            {
                "POLICY_LEARNING_STORE_BACKEND": policy_store_backend,
                "DATABASE_URL": "postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon",
            },
            clear=False,
        ):
            os.environ.pop("AGORA_DATASET_STORE_BACKEND", None)
            os.environ.pop("AGORA_DATASET_STORE_DSN", None)
            authority = authority_module.AgoraDatasetAuthority()
        described = authority.describe()
        assert described["backend"] == "postgres"
        assert described["records_table"] == "agora.agora_dataset_records"
        assert described["independent_of_policy_store_backend"] is True


def test_default_product_path_trains_on_real_tenant_dataset() -> None:
    """End-to-end: propose -> claim -> train -> evaluate."""

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        _install_authority(
            svc,
            [_agora_record("dsv-real-1", order=1), _agora_record("dsv-real-2", order=2)],
        )
        client = _client(svc)

        tick = client.post(
            "/api/policy-learning/shadow-eval-tick",
            json={
                "tick_id": "tick-real",
                "eval_type": "imitation",
                "dataset_refs": [
                    {"id": "dsv-real-1", "dataset_version_id": "dsv-real-1"},
                    {"id": "dsv-real-2", "dataset_version_id": "dsv-real-2"},
                ],
            },
        )
        assert tick.status_code == 201
        body = tick.json()
        assert body["dataset_source"] == "explicit_refs"
        assert body["dataset_mode"] == "product"
        assert body["seed_fallback_used"] is False
        assert body["candidate_count"] == 2

        processed = client.post(
            "/api/policy-learning/worker/process", json={"worker_id": "worker-1"}
        ).json()
        assert processed["processed_count"] == 2
        assert processed["degraded_count"] == 0
        assert processed["failed_count"] == 0

        candidates = client.get("/api/policy-learning/candidates").json()
        assert {c["status"] for c in candidates} == {"processed"}
        for candidate in candidates:
            lineage = candidate["dataset_lineage"]
            assert lineage["authority"] == "agora.dataset.v1"
            assert lineage["tenant_id"] == "tenant-a"
            assert lineage["seed_fallback_used"] is False
            assert lineage["authoritative"] is True
            assert lineage["dataset_version_ids"] == [candidate["dataset_ref"]["dataset_version_id"]]
            assert lineage["content_sha256"]
            assert lineage["payload_sha256"]
            # Lineage is retained on the evaluation and the trained artifact,
            # not only on the candidate row.
            assert candidate["training_evaluation"]["dataset_lineage"] == lineage
            assert candidate["lineage"]["dataset_lineage"] == lineage
            assert candidate["training_evaluation"]["action_match_rate"] is not None
            assert candidate["artifact_checksum"]
        _assert_no_seed_leak(candidates)


def test_discovery_skips_observe_and_ineligible_records() -> None:
    """Only learning-eligible learn-kind versions are listed for training."""

    authority_module = _load_authority_module()
    authority = authority_module.AgoraDatasetAuthority(
        records=[
            _agora_record("dsv-learn", order=1),
            _agora_record("dsv-observe", dataset_kind="observe", order=2),
            _agora_record("dsv-opted-out", learning_eligible=False, order=3),
        ]
    )
    versions = authority.list_dataset_versions(tenant_id="tenant-a")
    assert [v.dataset_version_id for v in versions] == ["dsv-learn"]


# ---------------------------------------------------------------------------
# Proof 2 — tenant isolation and no-seed negatives
# ---------------------------------------------------------------------------


def test_discovery_is_scoped_to_one_tenant() -> None:
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        _install_authority(
            svc,
            [
                _agora_record("dsv-a1", tenant_id="tenant-a", order=1),
                _agora_record("dsv-b1", tenant_id="tenant-b", order=2),
                _agora_record("dsv-b2", tenant_id="tenant-b", order=3),
            ],
        )
        client_a = _client(svc, "tenant-a")
        client_b = _client(svc, "tenant-b")

        tick_a = client_a.post(
            "/api/policy-learning/shadow-eval-tick",
            json={
                "tick_id": "tick-a",
                "tenant_id": "tenant-a",
                "dataset_refs": [{"id": "dsv-a1", "dataset_version_id": "dsv-a1", "tenant_id": "tenant-a"}],
            },
        ).json()
        assert tick_a["candidate_count"] == 1
        assert tick_a["tenant_id"] == "tenant-a"

        tick_b = client_b.post(
            "/api/policy-learning/shadow-eval-tick",
            json={
                "tick_id": "tick-b",
                "tenant_id": "tenant-b",
                "dataset_refs": [
                    {"id": "dsv-b1", "dataset_version_id": "dsv-b1", "tenant_id": "tenant-b"},
                    {"id": "dsv-b2", "dataset_version_id": "dsv-b2", "tenant_id": "tenant-b"},
                ],
            },
        ).json()
        assert tick_b["candidate_count"] == 2

        # Each tenant only ever sees its own candidates.
        assert {
            candidate["tick_id"]
            for candidate in client_a.get("/api/policy-learning/candidates").json()
        } == {"tick-a"}
        assert {
            candidate["tick_id"]
            for candidate in client_b.get("/api/policy-learning/candidates").json()
        } == {"tick-b"}
        by_tick = {
            candidate["tick_id"]: candidate["dataset_ref"]["tenant_id"]
            for candidate in svc.store.list_candidates()
        }
        assert by_tick == {"tick-a": "tenant-a", "tick-b": "tenant-b"}


def test_cross_tenant_dataset_resolution_degrades_without_seed() -> None:
    """A candidate citing another tenant's version degrades; it never trains."""

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        _install_authority(svc, [_agora_record("dsv-secret", tenant_id="tenant-b", order=1)])
        client = _client(svc)

        tick = client.post(
            "/api/policy-learning/shadow-eval-tick",
            json={
                "tick_id": "tick-cross",
                "dataset_refs": [
                    {"dataset_version_id": "dsv-secret", "id": "dsv-secret", "tenant_id": "tenant-a"}
                ],
            },
        )
        candidate_id = tick.json()["candidate_ids"][0]

        result = client.post(
            "/api/policy-learning/worker/process", json={"worker_id": "worker-1"}
        ).json()
        assert result["degraded_count"] == 1
        assert result["processed_count"] == 0

        candidate = client.get(f"/api/policy-learning/candidates/{candidate_id}").json()
        assert candidate["status"] == "degraded"
        assert candidate["degradation"]["reason"] == "dataset_version_not_found"
        assert candidate["degradation"]["seed_fallback_used"] is False
        assert candidate["seed_fallback_used"] is False
        assert candidate["authoritative"] is False
        assert "metrics" not in candidate
        assert "policy_weights" not in candidate
        _assert_no_seed_leak(candidate)

        # Degraded work is visible separately from the DLQ.
        assert len(client.get("/api/policy-learning/worker/degraded").json()) == 1
        assert client.get("/api/policy-learning/worker/dlq").json() == []


def test_unreachable_authority_degrades_every_candidate_without_seed() -> None:
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        _install_authority(svc, [_agora_record("dsv-1", order=1)])
        authority_module = _service_authority_module()
        client = _client(svc)
        client.post(
            "/api/policy-learning/shadow-eval-tick",
            json={
                "tick_id": "tick-drop",
                "dataset_refs": [{"id": "dsv-1", "dataset_version_id": "dsv-1"}],
            },
        )

        # The authority goes away between proposal and processing.
        svc.DATASET_AUTHORITY = authority_module.AgoraDatasetAuthority(backend="", dsn="")
        result = client.post(
            "/api/policy-learning/worker/process", json={"worker_id": "worker-1"}
        ).json()
        assert result["degraded_count"] == 1

        candidate = client.get("/api/policy-learning/candidates").json()[0]
        assert candidate["status"] == "degraded"
        assert candidate["degradation"]["reason"] == "agora_authority_unconfigured"
        _assert_no_seed_leak(candidate)


def test_tick_requires_a_tenant_scope() -> None:
    """The tenant comes from the verified request, and is mandatory."""

    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        _install_authority(svc, [_agora_record("dsv-1", order=1)])
        os.environ.pop("POLICY_LEARNING_AGORA_TENANT_ID", None)

        unscoped = TestClient(
            svc.app,
            headers={"Authorization": f"Bearer {TEST_SERVICE_TOKEN}"},
        )
        resp = unscoped.post("/api/policy-learning/shadow-eval-tick", json={"tick_id": "tick-noscope"})
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "TENANT_REQUIRED"
        assert svc.store.list_candidates() == []


def test_seed_mode_is_explicitly_non_product() -> None:
    """The seed fixture stays reachable for development, clearly labelled."""

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir, env={"POLICY_LEARNING_DATASET_MODE": "seed"})
        with mock.patch.dict("os.environ", {"POLICY_LEARNING_DATASET_MODE": "seed"}):
            payload, lineage = svc.resolve_candidate_dataset(
                {"dataset_ref": {"dataset_version_id": "ds-dev"}}
            )
        assert payload["strategy_id"] == "alpha-mean-reversion"
        assert lineage["seed_fallback_used"] is True
        assert lineage["authoritative"] is False
        assert lineage["product_mode"] is False


def test_product_mode_is_the_default() -> None:
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        os.environ.pop("POLICY_LEARNING_DATASET_MODE", None)
        assert svc._dataset_mode() == "product"
        assert svc._product_mode() is True


# ---------------------------------------------------------------------------
# Proof 3 — duplicate ticks, two-worker claims, restart and replay
# ---------------------------------------------------------------------------


def test_duplicate_tick_creates_no_duplicate_candidates() -> None:
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        _install_authority(svc, [_agora_record("dsv-1", order=1), _agora_record("dsv-2", order=2)])
        client = _client(svc)

        first = client.post(
            "/api/policy-learning/shadow-eval-tick",
            json={
                "tick_id": "tick-dup",
                "dataset_refs": [
                    {"id": "dsv-1", "dataset_version_id": "dsv-1"},
                    {"id": "dsv-2", "dataset_version_id": "dsv-2"},
                ],
            },
        ).json()
        second = client.post(
            "/api/policy-learning/shadow-eval-tick",
            json={
                "tick_id": "tick-dup",
                "dataset_refs": [
                    {"id": "dsv-1", "dataset_version_id": "dsv-1"},
                    {"id": "dsv-2", "dataset_version_id": "dsv-2"},
                ],
            },
        ).json()
        assert first["candidate_count"] == 2
        assert second["candidate_count"] == 0
        assert second["skipped_count"] == 2
        assert len(client.get("/api/policy-learning/candidates").json()) == 2


def test_same_dataset_version_in_two_tenants_is_not_deduped_together() -> None:
    """Dedupe keys are tenant-qualified, so one tenant cannot mask another."""

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        _install_authority(
            svc,
            [
                _agora_record("dsv-shared", tenant_id="tenant-a", evidence_id="ev-a", order=1),
                _agora_record("dsv-shared", tenant_id="tenant-b", evidence_id="ev-b", order=2),
            ],
        )
        client_a = _client(svc, "tenant-a")
        client_b = _client(svc, "tenant-b")

        created_a = client_a.post(
            "/api/policy-learning/shadow-eval-tick",
            json={
                "tick_id": "tick-shared",
                "tenant_id": "tenant-a",
                "dataset_refs": [
                    {"id": "dsv-shared", "dataset_version_id": "dsv-shared", "evidence_id": "ev-a", "tenant_id": "tenant-a"}
                ],
            },
        ).json()
        created_b = client_b.post(
            "/api/policy-learning/shadow-eval-tick",
            json={
                "tick_id": "tick-shared",
                "tenant_id": "tenant-b",
                "dataset_refs": [
                    {"id": "dsv-shared", "dataset_version_id": "dsv-shared", "evidence_id": "ev-b", "tenant_id": "tenant-b"}
                ],
            },
        ).json()
        assert created_a["candidate_count"] == 1
        assert created_b["candidate_count"] == 1
        # Same tick id, same dataset version id, different tenants: the derived
        # candidate ids must not collide.
        assert created_a["candidate_ids"] != created_b["candidate_ids"]

        candidates = svc.store.list_candidates()
        assert len(candidates) == 2
        assert {c["dataset_ref"]["tenant_id"] for c in candidates} == {"tenant-a", "tenant-b"}


def test_two_workers_claim_disjoint_candidate_sets() -> None:
    """Concurrent claims never overlap, even across store instances."""

    store_module = _load_store_module()
    with tempfile.TemporaryDirectory() as data_dir:
        seeding_store = store_module.PolicyLearningStore(data_dir)
        for index in range(20):
            seeding_store.put_candidate(
                {
                    "candidate_id": f"sic-{index:03d}",
                    "status": "proposed",
                    "dataset_ref": {"dataset_version_id": f"dsv-{index}", "tenant_id": "tenant-a"},
                }
            )

        # Two independent store handles stand in for two worker processes
        # sharing the same durable backlog.
        results: dict[str, list[str]] = {}
        barrier = threading.Barrier(2)

        def claim(worker_id: str) -> None:
            worker_store = store_module.PolicyLearningStore(data_dir)
            barrier.wait()
            claimed = worker_store.claim_candidates(
                worker_id=worker_id, batch_size=20, lease_seconds=120
            )
            results[worker_id] = [record["candidate_id"] for record in claimed]

        threads = [threading.Thread(target=claim, args=(f"worker-{n}",)) for n in (1, 2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        claimed_a, claimed_b = results["worker-1"], results["worker-2"]
        assert not set(claimed_a) & set(claimed_b), "two workers claimed the same candidate"
        assert len(claimed_a) + len(claimed_b) == 20

        stored = {c["candidate_id"]: c for c in seeding_store.list_candidates()}
        for candidate_id in claimed_a:
            assert stored[candidate_id]["lease_owner"] == "worker-1"
        for candidate_id in claimed_b:
            assert stored[candidate_id]["lease_owner"] == "worker-2"
        assert all(c["status"] == "claimed" for c in stored.values())


def test_live_lease_is_not_reclaimable_but_expired_lease_is() -> None:
    store_module = _load_store_module()
    with tempfile.TemporaryDirectory() as data_dir:
        store = store_module.PolicyLearningStore(data_dir)
        store.put_candidate({"candidate_id": "sic-001", "status": "proposed"})
        now = store_module.utc_now_dt()

        first = store.claim_candidates(worker_id="worker-1", batch_size=5, lease_seconds=60, now=now)
        assert len(first) == 1

        # A peer inside the lease window gets nothing.
        assert store.claim_candidates(worker_id="worker-2", batch_size=5, lease_seconds=60, now=now) == []

        # After the lease expires the work is recoverable.
        later = now + timedelta(seconds=61)
        second = store.claim_candidates(worker_id="worker-2", batch_size=5, lease_seconds=60, now=later)
        assert [record["candidate_id"] for record in second] == ["sic-001"]
        assert second[0]["attempt_count"] == 2


def test_settling_a_lost_lease_is_rejected() -> None:
    """A revived worker cannot overwrite the new owner's result."""

    store_module = _load_store_module()
    with tempfile.TemporaryDirectory() as data_dir:
        store = store_module.PolicyLearningStore(data_dir)
        store.put_candidate({"candidate_id": "sic-001", "status": "proposed"})
        now = store_module.utc_now_dt()

        stale = store.claim_candidates(worker_id="worker-1", batch_size=1, lease_seconds=30, now=now)[0]
        fresh = store.claim_candidates(
            worker_id="worker-2", batch_size=1, lease_seconds=30, now=now + timedelta(seconds=31)
        )[0]

        store.settle_candidate({**fresh, "status": "processed"}, lease_token=fresh["lease_token"])
        with pytest.raises(store_module.LeaseLostError):
            store.settle_candidate({**stale, "status": "failed"}, lease_token=stale["lease_token"])
        assert store.get_candidate("sic-001")["status"] == "processed"


def test_restart_recovers_orphaned_claims_and_replays_them() -> None:
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        _install_authority(svc, [_agora_record("dsv-1", order=1), _agora_record("dsv-2", order=2)])
        client = _client(svc)
        client.post(
            "/api/policy-learning/shadow-eval-tick",
            json={
                "tick_id": "tick-restart",
                "dataset_refs": [
                    {"id": "dsv-1", "dataset_version_id": "dsv-1"},
                    {"id": "dsv-2", "dataset_version_id": "dsv-2"},
                ],
            },
        )

        # Worker A claims everything, then dies before settling.
        claimed = client.post(
            "/api/policy-learning/worker/claim",
            json={"worker_id": "worker-a", "batch_size": 10, "lease_seconds": 900},
        ).json()
        assert claimed["claimed_count"] == 2
        assert len(client.get("/api/policy-learning/worker/backlog").json()) == 0
        assert len(client.get("/api/policy-learning/worker/claims").json()) == 2

        # Worker B restarts.  Live leases are respected, so nothing moves yet.
        held = client.post(
            "/api/policy-learning/worker/restart", json={"worker_id": "worker-b"}
        ).json()
        assert held["released_count"] == 0
        assert held["processed_count"] == 0

        # Once worker A's leases expire, worker B recovers and replays the work.
        for candidate in svc.store.list_candidates():
            candidate["lease_expires_at"] = "2020-01-01T00:00:00Z"
            svc.store.put_candidate(candidate)

        recovered = client.post(
            "/api/policy-learning/worker/restart", json={"worker_id": "worker-b"}
        ).json()
        assert recovered["released_count"] == 2
        assert recovered["processed_count"] == 2

        candidates = client.get("/api/policy-learning/candidates").json()
        assert {c["status"] for c in candidates} == {"processed"}
        for candidate in candidates:
            assert candidate["dataset_lineage"]["seed_fallback_used"] is False


def test_dlq_replay_reruns_a_failed_candidate() -> None:
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        _install_authority(svc, [_agora_record("dsv-1", order=1)])
        client = _client(svc)
        tick = client.post(
            "/api/policy-learning/shadow-eval-tick",
            json={
                "tick_id": "tick-dlq",
                "dataset_refs": [{"id": "dsv-1", "dataset_version_id": "dsv-1"}],
            },
        )
        candidate_id = tick.json()["candidate_ids"][0]

        failed = svc.store.get_candidate(candidate_id)
        failed["status"] = "failed"
        failed["error_message"] = "injected trainer failure"
        svc.store.put_candidate(failed)
        assert len(client.get("/api/policy-learning/worker/dlq").json()) == 1

        replayed = client.post(f"/api/policy-learning/worker/dlq/{candidate_id}/replay").json()
        assert replayed["status"] == "processed"
        assert "error_message" not in replayed
        assert client.get("/api/policy-learning/worker/dlq").json() == []


def test_scheduler_window_tick_id_absorbs_duplicate_fires() -> None:
    scheduler = _load_scheduler_module()
    first = scheduler.window_tick_id(eval_type="shadow", interval_seconds=3600, now=1_800_000_000.0)
    same_window = scheduler.window_tick_id(eval_type="shadow", interval_seconds=3600, now=1_800_000_900.0)
    next_window = scheduler.window_tick_id(eval_type="shadow", interval_seconds=3600, now=1_800_003_700.0)
    assert first == same_window
    assert first != next_window


def test_scheduler_surfaces_dataset_authority_degradation() -> None:
    """A 503 degradation reaches the scheduler as a machine-readable reason."""

    import urllib.error

    scheduler = _load_scheduler_module()

    class FakeHTTPError(urllib.error.HTTPError):
        def __init__(self) -> None:
            super().__init__("http://test", 503, "Service Unavailable", {}, None)

        def read(self) -> bytes:
            return json.dumps(
                {
                    "detail": {
                        "status": "degraded",
                        "reason": "agora_authority_unreachable",
                        "seed_fallback_used": False,
                    }
                }
            ).encode("utf-8")

    with mock.patch("urllib.request.urlopen", side_effect=FakeHTTPError()):
        result = scheduler.run_tick(api_url="http://test", tenant_id="tenant-a")

    assert result["status"] == "error"
    assert result["code"] == 503
    assert result["reason"] == "agora_authority_unreachable"
    assert result["seed_fallback_used"] is False


# ---------------------------------------------------------------------------
# Proof 4 — candidate governance gate
# ---------------------------------------------------------------------------


def test_candidate_cannot_be_promoted_or_reach_runtime() -> None:
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        _install_authority(svc, [_agora_record("dsv-1", order=1)])
        client = _client(svc)
        tick = client.post(
            "/api/policy-learning/shadow-eval-tick",
            json={
                "tick_id": "tick-gate",
                "dataset_refs": [{"id": "dsv-1", "dataset_version_id": "dsv-1"}],
            },
        )
        candidate_id = tick.json()["candidate_ids"][0]
        client.post("/api/policy-learning/worker/process", json={"worker_id": "worker-1"})

        governance = client.get(f"/api/policy-learning/candidates/{candidate_id}/governance").json()
        assert governance["promotion_allowed"] is False
        assert governance["runtime_effect"] == "none"
        assert governance["production_training"] == "fail_closed"
        assert governance["gates"]["experiment_approval"]["satisfied"] is False
        assert governance["gates"]["deployment_gate"]["satisfied"] is False
        assert "runtime_binding_mutation" in governance["denied_authorities"]

        before = client.get(f"/api/policy-learning/candidates/{candidate_id}").json()
        promote = client.post(f"/api/policy-learning/candidates/{candidate_id}/promote")
        assert promote.status_code == 409
        assert promote.json()["detail"]["promotion_allowed"] is False
        # The refused promotion mutated nothing.
        assert client.get(f"/api/policy-learning/candidates/{candidate_id}").json() == before


def test_processed_candidate_carries_no_runtime_authority() -> None:
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        _install_authority(svc, [_agora_record("dsv-1", order=1)])
        client = _client(svc)
        client.post(
            "/api/policy-learning/shadow-eval-tick",
            json={
                "tick_id": "tick-authority",
                "dataset_refs": [{"id": "dsv-1", "dataset_version_id": "dsv-1"}],
            },
        )
        client.post("/api/policy-learning/worker/process", json={"worker_id": "worker-1"})

        candidate = client.get("/api/policy-learning/candidates").json()[0]
        assert candidate["production_training"] == "fail_closed"
        assert candidate["experiment_approval_gate"] == "required"
        assert candidate["runtime_effect"] == "none"
        assert candidate["lineage"]["dataset_lineage"]["authoritative"] is True
        # The research artifact keeps its research-only governance stamp.
        for forbidden in ("runtime_binding", "deployment_stage", "capital_binding", "broker_order"):
            assert forbidden not in json.dumps(candidate)
