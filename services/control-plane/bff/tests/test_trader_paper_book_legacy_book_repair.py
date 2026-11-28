"""PAPER-TRADER-BOOK-LEGACY-BOOK-REPAIR-20261128.

The hosted trader paper book (servant agora-servant-799a3ce8c021b91cbb54,
pool-persona-paper-3ca8da543bef6ede0a07, rb-77d962be4e324032808324109f2c154e)
was created before #7983: its ledger request had no ``symbols``, so its
approved artifact was minted on the legacy SPY default. The 2026-11-28 10:42Z
ensure projected that SPY universe onto the binding and the paper fleet
reconciler paused it with ``market_input_missing``. Its ledger row stayed in
``provisioning`` and every Persona directory read logged a conflicting owner
scope warning for it.
"""
from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from services.control_plane.bff.persona_provisioning import (
    MemoryPersonaProvisioningStore,
    ProvisioningRecord,
)
from services.control_plane.bff.persona_provisioning_coordinator import (
    PersonaProvisioningCoordinationError,
)
from services.control_plane.bff.personas import service as personas_service
from services.control_plane.bff.personas.trader_paper_book import (
    TRADER_PAPER_BOOK_PROVISIONED_STEP,
    _coordinate_paper_book_deploy_authorities,
    _resolve_paper_book_execution_projection,
    ensure_trader_paper_book,
    is_trader_paper_book_ledger_record,
    resolve_trader_paper_starting_capital,
    trader_paper_book_ids,
    trader_paper_book_provisioning_record,
    trader_paper_runtime_id,
)
from services.control_plane.bff.tests.test_trader_paper_book_provisioning import (  # noqa: F401
    _load_fleet_desired_state_builder,
    _reload_capital_bindings,
    _reload_capital_pools,
    owner_stack,
)

HOSTED_TENANT = "tenant-dev"
HOSTED_PERSONA = "agora-servant-799a3ce8c021b91cbb54"
HOSTED_BINDING = "rb-77d962be4e324032808324109f2c154e"
REQUESTER = "agora-trader-1"


def _legacy_ledger_record() -> ProvisioningRecord:
    """The pre-#7983 hosted ledger request: no symbols, book_kind or persona_class."""
    starting = resolve_trader_paper_starting_capital({}, as_of="2026-11-27T09:42:00Z")
    current = trader_paper_book_provisioning_record(
        HOSTED_TENANT,
        HOSTED_PERSONA,
        requested_by=REQUESTER,
        starting_capital=starting,
        created_at="2026-11-27T09:42:00Z",
    )
    assert current.request_payload["symbols"] == ["2330.TW"]
    legacy_payload = {
        k: v
        for k, v in current.request_payload.items()
        if k not in {"symbols", "book_kind", "persona_class"}
    }
    return ProvisioningRecord(
        tenant_id=current.tenant_id,
        idempotency_key=current.idempotency_key,
        request_hash=current.request_hash,
        normalized_name=current.normalized_name,
        persona_id=current.persona_id,
        request_payload=legacy_payload,
        created_at="2026-11-27T09:42:00Z",
        updated_at="2026-11-27T09:42:00Z",
    )


def _seed_recorded_legacy_book(
    owner_stack: Dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    *,
    status: str,
    session_admission: Dict[str, Any] | None,
) -> Dict[str, Any]:
    """Seed the recorded hosted state: TWD pool, SPY artifact, SPY-projected binding."""
    from services.runtime_manager.runtime_binding import DeploymentMode, RuntimeBinding

    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", HOSTED_TENANT)
    coordinator = owner_stack["coordinator_for_tenant"](HOSTED_TENANT)
    ids = trader_paper_book_ids(HOSTED_TENANT, HOSTED_PERSONA)
    coordinator.ensure_chain_paper_capital(
        tenant_id=HOSTED_TENANT,
        persona_id=HOSTED_PERSONA,
        capital_pool_id=ids.capital_pool_id,
        binding_id=ids.persona_capital_binding_id,
        requested_by=REQUESTER,
        currency="TWD",
        name=f"{HOSTED_PERSONA} trader paper pool",
        budget=10_000_000.0,
        mandate="user_private_agora_servant_paper_book",
        labels={"task_id": "PAPER-TRADER-BOOK-PROVISION-20261127", "paper_only": True},
    )
    legacy = _legacy_ledger_record()
    # The legacy ledger row leases and approves the book's authorities on the
    # SPY default universe, exactly as before #7983.
    _coordinate_paper_book_deploy_authorities(coordinator, legacy, ids)
    ledger_before = coordinator.store.get(HOSTED_TENANT, legacy.idempotency_key)
    assert ledger_before is not None and ledger_before.state == "provisioning"
    assert is_trader_paper_book_ledger_record(ledger_before)

    # The 10:42Z repair projected the approved SPY artifact onto the binding.
    spy_projection = _resolve_paper_book_execution_projection(
        coordinator, legacy, ids, expected_currency="USD", coordinate_authorities=False
    )
    assert spy_projection["symbol"] == "SPY"
    metadata: Dict[str, Any] = {
        "tenant_id": HOSTED_TENANT,
        "persona_id": HOSTED_PERSONA,
        "sponsor_persona_id": HOSTED_PERSONA,
        "strategy_id": ids.strategy_id,
        "execution_context": "paper",
        "paper_only": True,
        "book_kind": "trader_servant_paper_book",
        "requested_by": REQUESTER,
        **spy_projection,
    }
    if session_admission is not None:
        metadata["session_admission"] = session_admission
    owner_stack["rm_service"]._store.create(
        RuntimeBinding(
            binding_id=HOSTED_BINDING,
            runtime_id=trader_paper_runtime_id(ids),
            capital_pool_id=ids.capital_pool_id,
            plan_id=ids.deployment_plan_id,
            artifact_id=ids.strategy_artifact_id,
            artifact_version=ids.version,
            deployment_mode=DeploymentMode.PAPER.value,
            status=status,
            effective_at="2026-11-27T09:42:01Z",
            persona_capital_binding_id=ids.persona_capital_binding_id,
            execution_mode="paper",
            metadata=metadata,
        )
    )
    return {
        "coordinator": coordinator,
        "ids": ids,
        "legacy": legacy,
        "spy_projection": spy_projection,
    }


def _spy_transitions(owner_stack: Dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> List[str]:
    rm_client = owner_stack["rm_client"]
    calls: List[str] = []
    orig_transition = rm_client.transition

    def _spy(binding_id: str, new_status: str, **kwargs: Any) -> Any:
        calls.append(f"{binding_id}:{new_status}")
        return orig_transition(binding_id, new_status, **kwargs)

    monkeypatch.setattr(rm_client, "transition", _spy)
    return calls


MARKET_INPUT_MISSING_PAUSE = {
    "reason_code": "market_input_missing",
    "source_snapshot_id": None,
    "source_event_time": None,
    "observed_at": "2026-11-28T10:46:00Z",
    "max_age_seconds": 86400,
    "pause_command_ref": "cmd-stale-pause-rb-77d962be4e324032808324109f2c154e-0a1b2c3d",
    "resume_snapshot_id": None,
    "resumed_at": None,
}


@pytest.mark.parametrize(
    ("initial_status", "session_admission"),
    [
        ("paused", MARKET_INPUT_MISSING_PAUSE),
        ("active", None),
    ],
    ids=["hosted-reconciler-paused", "active-spy-projection"],
)
def test_recorded_legacy_spy_book_fails_closed_without_owner_approved_universe(
    owner_stack: Dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    initial_status: str,
    session_admission: Dict[str, Any] | None,
) -> None:
    """AC1 review disposition: the approved artifact of the legacy book is SPY, so the BFF
    never rewrites the projected symbol. Ensure refuses with zero Runtime-Manager transitions,
    no second pool/binding, and leaves the ledger row unchanged until an owner-approved
    revision exists."""
    seeded = _seed_recorded_legacy_book(
        owner_stack, monkeypatch, status=initial_status, session_admission=session_admission
    )
    ids = seeded["ids"]
    coordinator = seeded["coordinator"]
    rm_service = owner_stack["rm_service"]
    capital_dir: Path = owner_stack["capital_dir"]
    before_binding = rm_service.get_binding(HOSTED_BINDING).to_dict()
    ledger_before = coordinator.store.get(HOSTED_TENANT, seeded["legacy"].idempotency_key)

    transitions = _spy_transitions(owner_stack, monkeypatch)
    with pytest.raises(PersonaProvisioningCoordinationError) as exc:
        ensure_trader_paper_book(
            HOSTED_TENANT,
            HOSTED_PERSONA,
            requested_by=REQUESTER,
            agora_user_id=REQUESTER,
            coordinator=coordinator,
            runtime_client=owner_stack["rm_client"],
        )
    assert exc.value.reason_code == "trader_paper_book_universe_unapproved"
    assert transitions == []
    assert rm_service.get_binding(HOSTED_BINDING).to_dict() == before_binding
    assert rm_service.get_binding(HOSTED_BINDING).metadata["symbol"] == "SPY"
    assert list(_reload_capital_pools(capital_dir)) == [ids.capital_pool_id]
    assert list(_reload_capital_bindings(capital_dir)) == [ids.persona_capital_binding_id]
    assert [b.binding_id for b in rm_service.find_bindings_for_pool(ids.capital_pool_id)] == [
        HOSTED_BINDING
    ]
    ledger_after = coordinator.store.get(HOSTED_TENANT, seeded["legacy"].idempotency_key)
    assert ledger_after.state == ledger_before.state == "provisioning"
    assert ledger_after.attempt_count == ledger_before.attempt_count


@pytest.mark.parametrize(
    "session_admission",
    [None, {"reason_code": "operator_requested_pause", "observed_at": "2026-11-28T10:46:00Z"}],
    ids=["no-admission-record", "operator-pause"],
)
def test_operator_paused_legacy_book_is_never_resumed_by_ensure(
    owner_stack: Dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    session_admission: Dict[str, Any] | None,
) -> None:
    """Only a reconciler market_input_* pause may be repaired; an operator pause stays paused."""
    seeded = _seed_recorded_legacy_book(
        owner_stack, monkeypatch, status="paused", session_admission=session_admission
    )
    transitions = _spy_transitions(owner_stack, monkeypatch)
    with pytest.raises(PersonaProvisioningCoordinationError) as exc:
        ensure_trader_paper_book(
            HOSTED_TENANT,
            HOSTED_PERSONA,
            requested_by=REQUESTER,
            agora_user_id=REQUESTER,
            coordinator=seeded["coordinator"],
            runtime_client=owner_stack["rm_client"],
        )
    assert exc.value.reason_code == "runtime_binding_not_active"
    assert transitions == []
    binding = owner_stack["rm_service"].get_binding(HOSTED_BINDING)
    assert binding.status == "paused"
    assert binding.metadata["symbol"] == "SPY"
    ledger = seeded["coordinator"].store.get(HOSTED_TENANT, seeded["legacy"].idempotency_key)
    assert ledger.state == "provisioning"


def _registry_artifact_symbols(coordinator: Any, ids: Any) -> List[str]:
    receipt = coordinator._owner_get(
        "registry", f"/api/registry/strategy-artifacts/{ids.strategy_artifact_id}"
    )
    entry = receipt.get("entry") if isinstance(receipt.get("entry"), dict) else receipt
    return list(entry["metadata"]["strategy_artifact"]["parameters"]["symbols"])


def test_twd_book_symbol_derives_from_approved_registry_receipt_and_replay_is_noop(
    owner_stack: Dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC1/AC2: a TWD book's projected symbol is exactly the approved owner artifact universe
    (no BFF override, no book_universe annotation); ensure on it again changes nothing and its
    ledger row is terminal."""
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", HOSTED_TENANT)
    coordinator = owner_stack["coordinator_for_tenant"](HOSTED_TENANT)
    rm_service = owner_stack["rm_service"]
    rm_client = owner_stack["rm_client"]
    ids = trader_paper_book_ids(HOSTED_TENANT, HOSTED_PERSONA)
    book = ensure_trader_paper_book(
        HOSTED_TENANT,
        HOSTED_PERSONA,
        requested_by=REQUESTER,
        coordinator=coordinator,
        runtime_client=rm_client,
    )
    binding = rm_service.get_binding(book["runtime_binding_id"])
    approved_symbols = _registry_artifact_symbols(coordinator, ids)
    assert approved_symbols == ["2330.TW"]
    assert binding.status == "active"
    assert binding.metadata["symbol"] == approved_symbols[0]
    assert "book_universe" not in binding.metadata
    assert book["provisioning_ledger_state"] == "succeeded"
    ledger = coordinator.store.get(
        HOSTED_TENANT, f"trader-paper-book:{HOSTED_TENANT}:{HOSTED_PERSONA}"
    )
    assert ledger.state == "succeeded"
    assert ledger.current_step == TRADER_PAPER_BOOK_PROVISIONED_STEP

    transitions = _spy_transitions(owner_stack, monkeypatch)
    replay = ensure_trader_paper_book(
        HOSTED_TENANT,
        HOSTED_PERSONA,
        requested_by=REQUESTER,
        coordinator=coordinator,
        runtime_client=rm_client,
    )
    assert transitions == []
    assert replay["runtime_binding_id"] == book["runtime_binding_id"]
    assert rm_service.get_binding(book["runtime_binding_id"]).to_dict() == binding.to_dict()
    replay_ledger = coordinator.store.get(HOSTED_TENANT, ledger.idempotency_key)
    assert replay_ledger.attempt_count == ledger.attempt_count
    assert replay_ledger.updated_at == ledger.updated_at


@pytest.mark.parametrize(
    ("symbols", "currency"),
    [
        (("2330.TW",), "TWD"),
        (("2330.TW", "2317.TW"), "TWD"),
        (("SPY",), "USD"),
        (("SPY", "QQQ"), "TWD"),
    ],
)
def test_nonlegacy_explicit_universes_are_preserved(symbols: tuple, currency: str) -> None:
    """Only an exact legacy SPY default under a currency with another book universe fails
    closed; every other explicit approved universe is projected unchanged."""
    from services.control_plane.bff.personas.trader_paper_book import (
        _fail_closed_on_unapproved_book_universe,
    )

    projection: Dict[str, Any] = (
        {"symbol": symbols[0]} if len(symbols) == 1 else {"symbols": list(symbols)}
    )
    _fail_closed_on_unapproved_book_universe(projection, expected_currency=currency)
    with pytest.raises(PersonaProvisioningCoordinationError) as exc:
        _fail_closed_on_unapproved_book_universe({"symbol": "SPY"}, expected_currency="TWD")
    assert exc.value.reason_code == "trader_paper_book_universe_unapproved"


def test_executable_twd_book_paused_on_market_admission_is_left_to_the_reconciler(
    owner_stack: Dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC3: once the book has the TWD universe, resuming a stale-market pause is the paper
    fleet reconciler's existing admission path, not ensure's."""
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", HOSTED_TENANT)
    coordinator = owner_stack["coordinator_for_tenant"](HOSTED_TENANT)
    rm_service = owner_stack["rm_service"]
    rm_client = owner_stack["rm_client"]
    book = ensure_trader_paper_book(
        HOSTED_TENANT,
        HOSTED_PERSONA,
        requested_by=REQUESTER,
        coordinator=coordinator,
        runtime_client=rm_client,
    )
    binding_id = book["runtime_binding_id"]
    stale_pause = {**MARKET_INPUT_MISSING_PAUSE, "reason_code": "market_input_stale"}
    rm_client.transition(binding_id, "pending_pause", metadata_patch={"session_admission": stale_pause})
    rm_client.transition(binding_id, "paused", metadata_patch={"session_admission": stale_pause})
    paused = rm_service.get_binding(binding_id).to_dict()

    transitions = _spy_transitions(owner_stack, monkeypatch)
    with pytest.raises(PersonaProvisioningCoordinationError) as exc:
        ensure_trader_paper_book(
            HOSTED_TENANT,
            HOSTED_PERSONA,
            requested_by=REQUESTER,
            coordinator=coordinator,
            runtime_client=rm_client,
        )
    assert exc.value.reason_code == "runtime_binding_not_active"
    assert transitions == []
    assert rm_service.get_binding(binding_id).to_dict() == paused


class _OwnerRecords:
    def __init__(self, personas: Dict[str, Dict[str, Any]]) -> None:
        self.personas = personas

    def list_personas(self, **_: Any) -> List[Dict[str, Any]]:
        return [dict(p) for p in self.personas.values()]

    def get_persona(self, persona_id: str) -> Dict[str, Any] | None:
        found = self.personas.get(persona_id)
        return dict(found) if found is not None else None


def test_directory_read_skips_book_ledger_row_but_still_skips_and_logs_real_conflict(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AC4/AC5: the book's ledger row is never projected onto the servant Persona and
    logs nothing; a real Persona provisioning conflict is still skipped and still logged."""
    store = MemoryPersonaProvisioningStore()
    book = _legacy_ledger_record()
    store.reserve(
        tenant_id=book.tenant_id,
        idempotency_key=book.idempotency_key,
        request_hash=book.request_hash,
        normalized_name=book.normalized_name,
        persona_id=book.persona_id,
        request_payload=book.request_payload,
    )
    store.reserve(
        tenant_id=HOSTED_TENANT,
        idempotency_key="create-real-conflict",
        request_hash="c" * 64,
        normalized_name="ledger name",
        persona_id="persona-real-conflict",
        request_payload={"name": "Ledger Name", "requested_by": "operator"},
    )
    owner = _OwnerRecords(
        {
            HOSTED_PERSONA: {
                "id": HOSTED_PERSONA,
                "persona_id": HOSTED_PERSONA,
                "name": "Agora Servant",
                "tenant_id": HOSTED_TENANT,
                "metadata": {"tenant_id": HOSTED_TENANT},
                "lifecycle_state": "draft",
                "created_at": "2026-11-27T09:40:00Z",
            },
            "persona-real-conflict": {
                "id": "persona-real-conflict",
                "persona_id": "persona-real-conflict",
                "name": "Owner Name",
                "tenant_id": HOSTED_TENANT,
                "metadata": {"tenant_id": HOSTED_TENANT},
                "lifecycle_state": "draft",
                "created_at": "2026-11-27T09:41:00Z",
            },
        }
    )
    monkeypatch.setattr(personas_service, "_persona_provisioning_store", lambda: store)
    monkeypatch.setattr(personas_service, "_get_active_write_owner", lambda: SimpleNamespace())
    monkeypatch.setattr(
        personas_service, "_get_active_read_store", lambda explicit=None: explicit or owner
    )

    def conflict_warnings() -> int:
        return sum(
            1
            for r in caplog.records
            if "conflicting owner scope" in r.getMessage() and r.levelno == logging.WARNING
        )

    caplog.set_level(logging.WARNING)
    records = personas_service._list_persona_records(HOSTED_TENANT, read_store=owner)
    by_id = {r["persona_id"]: r for r in records}
    assert by_id[HOSTED_PERSONA]["name"] == "Agora Servant"
    assert "provisioning_idempotency_key" not in by_id[HOSTED_PERSONA]["metadata"]
    assert by_id["persona-real-conflict"]["name"] == "Owner Name"
    # One warning per directory read: only the real conflict (was two before this task).
    assert conflict_warnings() == 1

    caplog.clear()
    detail = personas_service._get_persona_record_for_detail(
        HOSTED_PERSONA, HOSTED_TENANT, read_store=owner
    )
    assert detail is not None and detail["name"] == "Agora Servant"
    assert conflict_warnings() == 0
    personas_service._get_persona_record_for_detail(
        "persona-real-conflict", HOSTED_TENANT, read_store=owner
    )
    assert conflict_warnings() == 1


def test_book_ledger_classifier_requires_key_prefix_and_book_mandate() -> None:
    legacy = _legacy_ledger_record()
    assert is_trader_paper_book_ledger_record(legacy)
    current_payload = {**legacy.request_payload, "book_kind": "trader_servant_paper_book"}
    assert is_trader_paper_book_ledger_record(
        ProvisioningRecord(**{**legacy.to_dict(), "request_payload": current_payload})
    )
    for payload in (
        {**legacy.request_payload, "mandate": "momentum"},
        {**legacy.request_payload, "book_kind": "other_book"},
    ):
        assert not is_trader_paper_book_ledger_record(
            ProvisioningRecord(**{**legacy.to_dict(), "request_payload": payload})
        )
    assert not is_trader_paper_book_ledger_record(
        ProvisioningRecord(**{**legacy.to_dict(), "idempotency_key": "create-1"})
    )
