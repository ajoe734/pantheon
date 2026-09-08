"""CP-1: the BFF service actor contract against the real strict Capital owner.

These are isolated owner-contract tests, not hosted acceptance evidence.  Only
the socket transport is replaced: the production ``_PersonaOwnerHttpTransport``
composes every JWT claim and header, the production
``PersonaProvisioningCoordinator`` composes every mutation body, and the real
Capital FastAPI application performs authentication, actor binding, idempotency
and durable JSON persistence.
"""
from __future__ import annotations

import importlib
import io
import json
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

from fastapi.testclient import TestClient
import pytest

from services.control_plane.bff.persona_provisioning import (
    MemoryPersonaProvisioningStore,
    ProvisioningRecord,
)
from services.control_plane.bff.persona_provisioning_coordinator import (
    PersonaProvisioningCoordinationError,
    PersonaProvisioningCoordinator,
    deterministic_provisioning_ids,
)
from services.control_plane.bff.personas import service as personas_service
from services.control_plane.bff.personas.service import (
    PERSONA_OWNER_SERVICE_ACTOR_ID,
    _PersonaOwnerHttpTransport,
)
from services.runtime_auth_inbound import encode_jwt_hs256


# The identity the coordinator defaulted to before CP-1.  Kept only so the
# reproduction test can prove the exact production failure mode.
_UNALIGNED_DEFAULT_ACTOR_ID = "pantheon-persona-provisioner"
_CAPITAL_SECRET = "cp1-capital-owner-contract-secret"
_CAPITAL_ISSUER = "cp1-isolated-issuer"
_CAPITAL_AUDIENCE = "cp1-capital-owner"
_OWNER_BASE_URL = "http://testserver"
_TENANT_ID = "tenant-cp1"


class _CapitalOwner:
    """The real Capital app plus the durable stores it was configured with."""

    def __init__(self, module: Any, client: TestClient, data_dir: Path) -> None:
        self.module = module
        self.client = client
        self.data_dir = data_dir
        self.calls: list[tuple[str, str, dict[str, Any] | None, int]] = []

    # --- durable, independent readback -------------------------------------
    def reload_pools(self) -> dict[str, Any]:
        """Read the owner's JSON store with a fresh store instance."""

        from services.capital.pg_store import build_capital_pool_store

        fresh = build_capital_pool_store(self.data_dir / "capital_pools.json")
        return {pool.pool_id: pool for pool in fresh.list()}

    def reload_bindings(self) -> dict[str, Any]:
        from services.capital.pg_store import build_capital_binding_store

        fresh = build_capital_binding_store(
            self.data_dir / "persona_capital_bindings.json"
        )
        return {binding.binding_id: binding for binding in fresh.list()}

    def audit_events(self) -> list[dict[str, Any]]:
        path = self.data_dir / "capital_audit.jsonl"
        if not path.exists():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def mutations(self) -> list[tuple[str, str, dict[str, Any] | None, int]]:
        return [call for call in self.calls if call[0] in {"POST", "PATCH", "PUT"}]


@pytest.fixture()
def capital_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    data_dir = tmp_path / "capital"
    data_dir.mkdir(parents=True, exist_ok=True)
    for name, value in {
        "CAPITAL_DATA_DIR": str(data_dir),
        "PANTHEON_GOVERNANCE_DATA_DIR": str(data_dir),
        "CAPITAL_STORE_BACKEND": "json",
        "CAPITAL_AUDIT_BACKEND": "jsonl",
        "CAPITAL_AUTH_DISABLED": "false",
        "CAPITAL_AUTH_MODE": "strict",
        "CAPITAL_JWT_SECRET": _CAPITAL_SECRET,
        "CAPITAL_JWT_ISSUER": _CAPITAL_ISSUER,
        "CAPITAL_JWT_AUDIENCE": _CAPITAL_AUDIENCE,
        "CAPITAL_ALLOWED_CALLER_SERVICES": "control-plane-bff",
        # The BFF side of the same strict boundary.
        "PANTHEON_CAPITAL_API_URL": _OWNER_BASE_URL,
        "PANTHEON_CAPITAL_JWT_SECRET": _CAPITAL_SECRET,
        "PANTHEON_BFF_TENANT_ID": _TENANT_ID,
    }.items():
        monkeypatch.setenv(name, value)
    sys.modules.pop("services.capital.main", None)
    module = importlib.reload(importlib.import_module("services.capital.main"))
    client = TestClient(module.app)
    owner = _CapitalOwner(module, client, data_dir)

    def fake_urlopen(request: Any, timeout: Any = None) -> io.BytesIO:
        payload = json.loads(request.data) if request.data else None
        response = client.request(
            request.get_method(),
            request.full_url,
            content=request.data,
            headers=dict(request.header_items()),
        )
        owner.calls.append(
            (request.get_method(), request.full_url, payload, response.status_code)
        )
        if response.is_error:
            raise HTTPError(
                request.full_url,
                response.status_code,
                response.text,
                response.headers,  # type: ignore[arg-type]
                io.BytesIO(response.content),
            )
        return io.BytesIO(response.content)

    monkeypatch.setattr(personas_service.urllib_request, "urlopen", fake_urlopen)
    try:
        yield owner
    finally:
        client.close()
        sys.modules.pop("services.capital.main", None)


def _transport() -> _PersonaOwnerHttpTransport:
    return _PersonaOwnerHttpTransport(tenant_id=_TENANT_ID)


def _store_and_record(
    *,
    idempotency_key: str = "cp1-create-persona",
) -> tuple[MemoryPersonaProvisioningStore, ProvisioningRecord]:
    store = MemoryPersonaProvisioningStore()
    record, created = store.reserve(
        tenant_id=_TENANT_ID,
        idempotency_key=idempotency_key,
        request_hash="sha256:cp1-persona-request",
        normalized_name="cp1 trader",
        persona_id="persona-cp1",
        request_payload={
            "name": "CP1 Trader",
            "requested_by": "operator-human-cp1",
            "mandate": "Paper-only owner contract verification",
            "budget": 25000,
        },
    )
    assert created
    return store, record


def _coordinator(
    store: MemoryPersonaProvisioningStore,
    *,
    actor_id: str | None = None,
    lease_owner: str = "cp1-worker",
) -> PersonaProvisioningCoordinator:
    def _registrar(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("schedule registration is out of this contract's scope")

    kwargs: dict[str, Any] = {}
    if actor_id is not None:
        kwargs["actor_id"] = actor_id
    return PersonaProvisioningCoordinator(
        store=store,
        transport=_transport(),
        schedule_registrar=_registrar,
        lease_owner=lease_owner,
        **kwargs,
    )


def _leased_step(
    coordinator: PersonaProvisioningCoordinator,
    store: MemoryPersonaProvisioningStore,
    record: ProvisioningRecord,
    ids: Any,
    *,
    step: str = "_coordinate_capital_pool",
) -> ProvisioningRecord:
    """Run one coordination step under a real store lease, as ``coordinate`` does."""

    active = store.acquire(
        record.tenant_id,
        record.idempotency_key,
        lease_owner=coordinator.lease_owner,
        lease_seconds=coordinator.lease_seconds,
    )
    assert active is not None
    try:
        return getattr(coordinator, step)(active, ids)
    finally:
        latest = store.get(record.tenant_id, record.idempotency_key)
        if latest is not None:
            store.release(
                latest,
                lease_owner=coordinator.lease_owner,
                lease_seconds=coordinator.lease_seconds,
            )


def _service_headers(**overrides: str) -> dict[str, str]:
    """Production-composed strict Capital headers, with explicit deviations."""

    headers = _transport()._headers("capital", {"tenant_id": _TENANT_ID})
    headers.update(overrides)
    return headers


def _capital_pool_payload(actor_id: str) -> dict[str, Any]:
    """The exact body the coordinator composes for this record and actor."""

    store, record = _store_and_record()
    coordinator = _coordinator(store, actor_id=actor_id)
    ids = deterministic_provisioning_ids(record)
    captured: dict[str, Any] = {}

    class _Capture:
        def get(self, owner: str, path: str) -> None:
            return None

        def post(self, owner: str, path: str, payload: Any) -> dict[str, Any]:
            captured.update(dict(payload))
            raise ConnectionError("payload capture only")

        def patch(self, owner: str, path: str, payload: Any) -> dict[str, Any]:
            raise AssertionError("unexpected PATCH")

    coordinator.transport = _Capture()  # type: ignore[assignment]
    with pytest.raises(PersonaProvisioningCoordinationError):
        coordinator._coordinate_capital_pool(record, ids)
    assert captured
    return captured


def test_default_service_actor_is_rejected_by_strict_capital_before_persistence(
    capital_owner: _CapitalOwner,
) -> None:
    """Reproduce the deployed defect: unaligned actor_id => 403, no pool."""

    store, record = _store_and_record()
    ids = deterministic_provisioning_ids(record)
    coordinator = _coordinator(store)  # coordinator default actor_id

    assert coordinator.actor_id == _UNALIGNED_DEFAULT_ACTOR_ID
    with pytest.raises(PersonaProvisioningCoordinationError) as failure:
        coordinator._coordinate_capital_pool(record, ids)

    assert "capital did not persist" in str(failure.value)
    assert "403" in str(failure.value)
    rejected = [call for call in capital_owner.mutations() if call[3] == 403]
    assert len(rejected) == 1
    assert rejected[0][2]["actor_id"] == _UNALIGNED_DEFAULT_ACTOR_ID
    detail = capital_owner.client.post(
        "/api/capital-pools",
        json=rejected[0][2],
        headers=_service_headers(),
    )
    assert detail.status_code == 403
    assert detail.json()["detail"] == (
        "Mutation actor_id does not match the authenticated actor"
    )

    assert capital_owner.reload_pools() == {}
    assert not (capital_owner.data_dir / "capital_pools.json").exists()
    assert capital_owner.audit_events() == []


def test_aligned_service_actor_creates_capital_pool_and_survives_reload(
    capital_owner: _CapitalOwner,
) -> None:
    store, record = _store_and_record()
    ids = deterministic_provisioning_ids(record)
    coordinator = _coordinator(store, actor_id=PERSONA_OWNER_SERVICE_ACTOR_ID)

    checkpointed = _leased_step(coordinator, store, record, ids)

    assert checkpointed.current_step == "capital_pool_readback"
    receipt = checkpointed.references["capital_pool"]
    assert receipt["pool_id"] == ids.capital_pool_id
    assert receipt["status"] == "active"
    assert receipt["single_runtime_enforced"] is True

    # Independent durable readback through a fresh owner store instance.
    reloaded = capital_owner.reload_pools()
    assert set(reloaded) == {ids.capital_pool_id}
    pool = reloaded[ids.capital_pool_id]
    assert pool.owner_id == _TENANT_ID
    assert pool.status == "active"
    # The human requester stays audit metadata; it is never the actor.
    assert pool.metadata["requested_by"] == "operator-human-cp1"
    assert pool.metadata["execution_context"] == "paper"

    created = [
        event
        for event in capital_owner.audit_events()
        if event.get("event_type") == "capital_pool_created"
    ]
    assert len(created) == 1
    assert created[0]["actor_id"] == PERSONA_OWNER_SERVICE_ACTOR_ID
    assert created[0]["actor_role"] == "admin"


def test_aligned_replay_is_idempotent_and_creates_no_duplicate_pool(
    capital_owner: _CapitalOwner,
) -> None:
    store, record = _store_and_record()
    ids = deterministic_provisioning_ids(record)

    first = _leased_step(
        _coordinator(
            store,
            actor_id=PERSONA_OWNER_SERVICE_ACTOR_ID,
            lease_owner="cp1-worker-a",
        ),
        store,
        record,
        ids,
    )
    posts_after_first = len(capital_owner.mutations())

    # GET-first reconciliation: a replay must not re-POST at all.
    second = _leased_step(
        _coordinator(
            store,
            actor_id=PERSONA_OWNER_SERVICE_ACTOR_ID,
            lease_owner="cp1-worker-b",
        ),
        store,
        record,
        ids,
    )
    assert len(capital_owner.mutations()) == posts_after_first
    assert second.references["capital_pool"]["pool_id"] == (
        first.references["capital_pool"]["pool_id"]
    )

    # And the owner itself replays the identical key/hash without duplicating.
    payload = _capital_pool_payload(PERSONA_OWNER_SERVICE_ACTOR_ID)
    replay = capital_owner.client.post(
        "/api/capital-pools",
        json=payload,
        headers=_service_headers(),
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["idempotent_replay"] is True
    assert set(capital_owner.reload_pools()) == {ids.capital_pool_id}


def test_actor_participates_in_capital_request_hash(
    capital_owner: _CapitalOwner,
) -> None:
    aligned = _capital_pool_payload(PERSONA_OWNER_SERVICE_ACTOR_ID)
    unaligned = _capital_pool_payload(_UNALIGNED_DEFAULT_ACTOR_ID)

    assert aligned["actor_id"] == PERSONA_OWNER_SERVICE_ACTOR_ID
    assert unaligned["actor_id"] == _UNALIGNED_DEFAULT_ACTOR_ID
    assert aligned["idempotency_key"] == unaligned["idempotency_key"]
    assert aligned["request_hash"] != unaligned["request_hash"]


def _bounded_production_create(monkeypatch, store, record):
    """Run the actual forward composition, stopping before unrelated owners.

    Capital auth, payload, HTTP handler, persistence, ledger and coordinate()
    terminal/lease handling are real. Registry is intentionally rejected before
    its first call; no fake approval, deployment or paper success is supplied.
    Persona UI projection is outside this Capital contract and is isolated.
    """
    checkpoints = []

    def stop_before_registry(self, active, ids, *, baseline=False):
        assert baseline is True
        checkpoints.append(active.to_dict())
        raise PersonaProvisioningCoordinationError("isolated stop before Registry")

    monkeypatch.setattr(
        PersonaProvisioningCoordinator, "_coordinate_strategy_spec", stop_before_registry
    )
    monkeypatch.setattr(personas_service, "_PERSONA_PROVISIONING_STORE", store)
    monkeypatch.setattr(
        personas_service, "_persona_record_for_provisioning",
        lambda active, **kwargs: ({"id": active.persona_id}, {"state": active.state}),
    )
    active, _, _, packet = personas_service._coordinate_persona_create(
        record, payload=record.request_payload, owner="operator-human-cp1"
    )
    assert packet is None
    assert len(checkpoints) == 1, active.error
    assert active.state == "failed"
    assert active.current_step == "baseline_strategy_spec_candidate_failed"
    assert active.error["terminal_reason"] == "isolated stop before Registry"
    return active, checkpoints[0]


def test_production_forward_constructor_reaches_real_capital_readback(
    capital_owner: _CapitalOwner, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, record = _store_and_record()
    ids = deterministic_provisioning_ids(record)

    active, capital_checkpoint = _bounded_production_create(monkeypatch, store, record)

    assert capital_checkpoint["current_step"] == "capital_pool_readback"
    assert capital_checkpoint["references"]["capital_pool"]["pool_id"] == ids.capital_pool_id
    assert active.result is None  # The intentional stop is not paper success.
    assert capital_owner.mutations()[0][2]["actor_id"] == PERSONA_OWNER_SERVICE_ACTOR_ID
    assert capital_owner.mutations()[0][3] == 201
    assert capital_owner.reload_pools()[ids.capital_pool_id].metadata[
        "requested_by"
    ] == "operator-human-cp1"
    assert store.get(record.tenant_id, record.idempotency_key).lease_owner is None


def test_failed_no_receipt_baseline_retries_forward_under_aligned_actor(
    capital_owner: _CapitalOwner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The durable ledger state observed in the failed run: failed, no refs."""

    store, record = _store_and_record()
    ids = deterministic_provisioning_ids(record)
    failed = store.acquire(
        record.tenant_id,
        record.idempotency_key,
        lease_owner="prior-worker",
        lease_seconds=60,
    )
    failed.state = "failed"
    failed.current_step = "capital_pool_failed"
    failed.error = {"failed_step": "capital_pool", "terminal_reason": "HTTP Error 403"}
    failed.references = {}
    failed.compensation = None
    failed.attempt_count = 16
    failed = store.checkpoint(failed, lease_owner="prior-worker", lease_seconds=60)
    store.release(failed, lease_owner="prior-worker", lease_seconds=60)

    checkpoint_states = []
    original_checkpoint = store.checkpoint

    def track_checkpoint(active, **kwargs):
        checkpoint_states.append((active.state, active.current_step, active.error))
        return original_checkpoint(active, **kwargs)

    monkeypatch.setattr(store, "checkpoint", track_checkpoint)
    retried, capital_checkpoint = _bounded_production_create(monkeypatch, store, record)

    assert ("provisioning", "safe_early_failure_retry_started", None) in checkpoint_states
    assert capital_checkpoint["state"] == "provisioning"
    assert capital_checkpoint["error"] is None
    assert retried.attempt_count == 17
    assert retried.references["capital_pool"]["pool_id"] == ids.capital_pool_id
    assert set(capital_owner.reload_pools()) == {ids.capital_pool_id}
    assert store.get(record.tenant_id, record.idempotency_key).lease_owner is None


def test_conflicting_persisted_pool_is_not_overwritten_by_retry(
    capital_owner: _CapitalOwner,
) -> None:
    """A semantically conflicting persisted owner object must fail closed."""

    store, record = _store_and_record()
    ids = deterministic_provisioning_ids(record)
    conflicting = _capital_pool_payload(PERSONA_OWNER_SERVICE_ACTOR_ID)
    conflicting.update(
        owner_id="tenant-someone-else",
        idempotency_key="cp1-foreign-create",
        request_hash="sha256:cp1-foreign-request",
    )
    seeded = capital_owner.client.post(
        "/api/capital-pools",
        json=conflicting,
        headers=_service_headers(),
    )
    assert seeded.status_code == 201, seeded.text

    with pytest.raises(PersonaProvisioningCoordinationError) as failure:
        _leased_step(
            _coordinator(store, actor_id=PERSONA_OWNER_SERVICE_ACTOR_ID),
            store,
            record,
            ids,
        )
    assert "CapitalPool readback does not match" in str(failure.value)

    # No overwrite, no duplicate, and no second create mutation.
    reloaded = capital_owner.reload_pools()
    assert set(reloaded) == {ids.capital_pool_id}
    assert reloaded[ids.capital_pool_id].owner_id == "tenant-someone-else"
    # GET-first reconciliation saw the conflict; no create was even attempted.
    assert capital_owner.mutations() == []

    # The owner also refuses to reuse one idempotency key for a new request.
    replayed_key = dict(conflicting, request_hash="sha256:cp1-different-request")
    conflict = capital_owner.client.post(
        "/api/capital-pools",
        json=replayed_key,
        headers=_service_headers(),
    )
    assert conflict.status_code >= 400
    assert capital_owner.reload_pools()[ids.capital_pool_id].owner_id == (
        "tenant-someone-else"
    )


@pytest.mark.parametrize(
    ("case", "expected_status"),
    [
        ("wrong_actor", 403),
        ("ungranted_role", 403),
        ("forbidden_service", 403),
        ("token_service_mismatch", 403),
        ("cross_tenant", 403),
        ("missing_token", 401),
        ("expired_token", 401),
        ("wrong_issuer", 401),
        ("wrong_audience", 401),
    ],
)
def test_strict_capital_negatives_leave_no_owner_state(
    capital_owner: _CapitalOwner,
    case: str,
    expected_status: int,
) -> None:
    payload = _capital_pool_payload(PERSONA_OWNER_SERVICE_ACTOR_ID)
    headers = _service_headers()

    if case == "wrong_actor":
        payload["actor_id"] = "operator-human-cp1"
    elif case == "ungranted_role":
        payload["actor_role"] = "unassigned_role"
    elif case == "forbidden_service":
        headers["X-Pantheon-Service"] = "rogue-service"
    elif case == "token_service_mismatch":
        headers["Authorization"] = "Bearer " + encode_jwt_hs256(
            {
                "sub": "some-other-service",
                "service": "some-other-service",
                "roles": ["admin", "service"],
                "allowed_tenants": [_TENANT_ID],
                "iss": _CAPITAL_ISSUER,
                "aud": _CAPITAL_AUDIENCE,
                "exp": int(time.time()) + 120,
            },
            secret=_CAPITAL_SECRET,
        )
    elif case == "cross_tenant":
        headers["X-Tenant-Id"] = "tenant-foreign"
    elif case == "missing_token":
        headers.pop("Authorization")
    elif case in {"expired_token", "wrong_issuer", "wrong_audience"}:
        headers["Authorization"] = "Bearer " + encode_jwt_hs256(
            {
                "sub": PERSONA_OWNER_SERVICE_ACTOR_ID,
                "service": PERSONA_OWNER_SERVICE_ACTOR_ID,
                "roles": ["admin", "service"],
                "allowed_tenants": [_TENANT_ID],
                "iss": "wrong-issuer" if case == "wrong_issuer" else _CAPITAL_ISSUER,
                "aud": "wrong-audience" if case == "wrong_audience" else _CAPITAL_AUDIENCE,
                "iat": int(time.time()) - 600,
                "exp": int(time.time()) + (-300 if case == "expired_token" else 120),
            },
            secret=_CAPITAL_SECRET,
        )
    else:  # pragma: no cover - parametrisation guard
        raise AssertionError(f"unknown negative case {case}")

    response = capital_owner.client.post(
        "/api/capital-pools",
        json=payload,
        headers=headers,
    )

    assert response.status_code == expected_status, response.text
    assert capital_owner.reload_pools() == {}
    assert capital_owner.audit_events() == []


def test_compensation_constructor_uses_the_shared_service_actor(
    capital_owner: _CapitalOwner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both production constructors must present the same authenticated actor."""

    captured: list[str] = []

    class _CapturingCoordinator(PersonaProvisioningCoordinator):
        def __init__(self, **kwargs: Any) -> None:
            captured.append(str(kwargs.get("actor_id")))
            super().__init__(**kwargs)

    store, record = _store_and_record(idempotency_key="cp1-compensation")
    monkeypatch.setattr(
        personas_service,
        "PersonaProvisioningCoordinator",
        _CapturingCoordinator,
    )
    monkeypatch.setattr(personas_service, "_PERSONA_PROVISIONING_STORE", store)

    personas_service._reconcile_persona_provisioning_compensation(
        {"tenant_id": _TENANT_ID, "provisioning_idempotency_key": "cp1-compensation"}
    )

    assert captured == [PERSONA_OWNER_SERVICE_ACTOR_ID]


def test_compensation_suspends_real_binding_only_with_the_aligned_actor(
    capital_owner: _CapitalOwner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, record = _store_and_record(idempotency_key="cp1-compensation-live")
    ids = deterministic_provisioning_ids(record)
    aligned = _coordinator(store, actor_id=PERSONA_OWNER_SERVICE_ACTOR_ID)
    _leased_step(aligned, store, record, ids)
    _leased_step(aligned, store, record, ids, step="_coordinate_binding_create")
    assert capital_owner.reload_bindings()[
        ids.persona_capital_binding_id
    ].status == "pending"

    # Seed the exact durable failure that requires fail-closed compensation.
    failed = store.acquire(
        record.tenant_id,
        record.idempotency_key,
        lease_owner="prior-worker",
        lease_seconds=60,
    )
    failed.state = "failed"
    failed.current_step = "persona_capital_binding_created_failed"
    failed.error = {"failed_step": "persona_capital_binding_created"}
    failed.compensation = None
    failed = store.checkpoint(failed, lease_owner="prior-worker", lease_seconds=60)
    store.release(failed, lease_owner="prior-worker", lease_seconds=60)

    # An unaligned coordinator is rejected by Capital and must not mutate.
    unaligned = PersonaProvisioningCoordinator(
        store=store,
        transport=_transport(),
        schedule_registrar=lambda *_a, **_k: None,
        lease_owner="cp1-unaligned-compensation",
    )
    rejected = unaligned.reconcile_failure_compensation(
        store.get(record.tenant_id, record.idempotency_key)
    )
    assert rejected.state == "failed"
    assert (rejected.compensation or {}).get("status") != "completed"
    denied = [
        call
        for call in capital_owner.mutations()
        if call[0] == "PATCH" and call[3] == 403
    ]
    assert len(denied) == 1
    assert denied[0][2]["actor_id"] == _UNALIGNED_DEFAULT_ACTOR_ID
    assert capital_owner.reload_bindings()[
        ids.persona_capital_binding_id
    ].status == "pending"

    monkeypatch.setattr(personas_service, "_PERSONA_PROVISIONING_STORE", store)
    resumed = personas_service._reconcile_persona_provisioning_compensation(
        {
            "tenant_id": _TENANT_ID,
            "provisioning_idempotency_key": "cp1-compensation-live",
        }
    )

    assert resumed is not None
    assert resumed["ledger_state"] == "compensated"
    assert resumed["status"] == "completed"
    reloaded = capital_owner.reload_bindings()[ids.persona_capital_binding_id]
    assert reloaded.status == "revoked"
