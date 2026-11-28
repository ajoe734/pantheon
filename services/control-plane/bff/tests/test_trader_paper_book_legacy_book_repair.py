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
    read_trader_paper_book,
    trader_paper_book_generation_ids,
    trader_paper_book_ids,
    trader_paper_book_provisioning_record,
    trader_paper_runtime_id,
)
from services.control_plane.bff.personas import trader_paper_book
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


def _servant_retirement_spies(coordinator: Any) -> List[str]:
    """Record any attempt to retire the servant Persona or its first-evaluation schedule."""
    calls: List[str] = []

    def _archive(persona_id: str) -> Dict[str, Any]:
        calls.append(f"archive:{persona_id}")
        return {"status": "archived", "persona_id": persona_id}

    def _remove_schedule(persona_id: str) -> Dict[str, Any]:
        calls.append(f"schedule:{persona_id}")
        return {"registered": False}

    coordinator.persona_archiver = _archive
    coordinator.schedule_remover = _remove_schedule
    return calls


def _ensure_hosted(owner_stack: Dict[str, Any], coordinator: Any) -> Dict[str, Any]:
    return ensure_trader_paper_book(
        HOSTED_TENANT,
        HOSTED_PERSONA,
        requested_by=REQUESTER,
        agora_user_id=REQUESTER,
        coordinator=coordinator,
        runtime_client=owner_stack["rm_client"],
    )


@pytest.mark.parametrize(
    ("initial_status", "session_admission"),
    [
        ("paused", MARKET_INPUT_MISSING_PAUSE),
        ("active", None),
    ],
    ids=["hosted-reconciler-paused", "active-spy-projection"],
)
def test_recorded_legacy_spy_book_is_replaced_by_generation_two_on_the_same_ledger_row(
    owner_stack: Dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    initial_status: str,
    session_admission: Dict[str, Any] | None,
) -> None:
    """Coordinator disposition b2 (AC1/AC4/AC5): the legacy SPY book (generation 1) is retired as
    a book through retire_case(retain_persona) with the RuntimeBinding retirer; the same ledger row
    is re-leased for generation 2, minted and approved on 2330.TW through the existing create path,
    and ends succeeded/trader_paper_book_provisioned. The approved SPY artifact is never rewritten
    and the servant Persona is never retired."""
    seeded = _seed_recorded_legacy_book(
        owner_stack, monkeypatch, status=initial_status, session_admission=session_admission
    )
    gen1 = seeded["ids"]
    coordinator = seeded["coordinator"]
    rm_service = owner_stack["rm_service"]
    capital_dir: Path = owner_stack["capital_dir"]
    key = seeded["legacy"].idempotency_key
    servant_calls = _servant_retirement_spies(coordinator)
    gen1_artifact_before = coordinator._owner_get(
        "registry", f"/api/registry/strategy-artifacts/{gen1.strategy_artifact_id}"
    )

    transitions = _spy_transitions(owner_stack, monkeypatch)
    book = _ensure_hosted(owner_stack, coordinator)

    gen2 = trader_paper_book_generation_ids(HOSTED_TENANT, HOSTED_PERSONA, 2)
    assert gen2.token != gen1.token
    assert book["book_generation"] == 2
    assert book["capital_pool_id"] == gen2.capital_pool_id
    assert book["persona_capital_binding_id"] == gen2.persona_capital_binding_id
    assert book["strategy_artifact_id"] == gen2.strategy_artifact_id
    assert book["runtime_id"] == trader_paper_runtime_id(gen2)
    assert book["provisioning_ledger_state"] == "succeeded"

    # Generation 1: retired through the Runtime-Manager status machine, capital revoked/archived,
    # its approved SPY artifact untouched.
    assert transitions == [f"{HOSTED_BINDING}:retired"]
    old_binding = rm_service.get_binding(HOSTED_BINDING)
    assert old_binding.status == "retired"
    assert old_binding.metadata["symbol"] == "SPY"
    assert coordinator._owner_get(
        "registry", f"/api/registry/strategy-artifacts/{gen1.strategy_artifact_id}"
    ) == gen1_artifact_before
    pools = _reload_capital_pools(capital_dir)
    pcbs = _reload_capital_bindings(capital_dir)
    assert sorted(pools) == sorted([gen1.capital_pool_id, gen2.capital_pool_id])
    assert sorted(pcbs) == sorted([gen1.persona_capital_binding_id, gen2.persona_capital_binding_id])
    assert str(getattr(pools[gen1.capital_pool_id].status, "value", pools[gen1.capital_pool_id].status)) == "archived"
    assert str(getattr(pcbs[gen1.persona_capital_binding_id].status, "value", pcbs[gen1.persona_capital_binding_id].status)) == "revoked"

    # Generation 2: one active binding with the approved 2330.TW projection.
    gen2_bindings = rm_service.find_bindings_for_pool(gen2.capital_pool_id)
    assert len(gen2_bindings) == 1
    binding = gen2_bindings[0]
    assert binding.binding_id == book["runtime_binding_id"] != HOSTED_BINDING
    assert binding.status == "active"
    assert binding.metadata["symbol"] == "2330.TW"
    assert binding.artifact_id == gen2.strategy_artifact_id
    assert binding.plan_id == gen2.deployment_plan_id
    assert _registry_artifact_symbols(coordinator, gen2) == ["2330.TW"]

    # One ledger row (same tenant, persona_id, idempotency key), generation history kept.
    rows = coordinator.store.list_all()
    assert [(r.tenant_id, r.persona_id, r.idempotency_key) for r in rows] == [
        (HOSTED_TENANT, HOSTED_PERSONA, key)
    ]
    ledger = rows[0]
    assert ledger.state == "succeeded"
    assert ledger.current_step == TRADER_PAPER_BOOK_PROVISIONED_STEP
    assert ledger.result["book_generation"] == 2
    assert ledger.result["runtime_binding_id"] == binding.binding_id
    assert ledger.references["book_generation"]["generation"] == 2
    assert ledger.references["book_generation"]["symbols"] == ["2330.TW"]
    assert "retirement" not in ledger.references
    history = ledger.references["book_generations"]
    assert list(history) == ["1"]
    gen1_entry = history["1"]
    assert gen1_entry["state"] == "retired"
    assert gen1_entry["ids"]["capital_pool_id"] == gen1.capital_pool_id
    assert gen1_entry["retirement"]["status"] == "retired"
    assert gen1_entry["retirement"]["scope"] == "book"
    assert gen1_entry["retirement"]["runtime_bindings"]["receipt"]["retired_by_case"] == [HOSTED_BINDING]
    assert "strategy_artifact_approved" in gen1_entry["references"]
    assert "persona" not in gen1_entry["retirement"]
    assert "first_evaluation_schedule" not in gen1_entry["retirement"]

    # The servant Persona, its schedule and its owner scope are never retired.
    assert servant_calls == []
    monkeypatch.setattr(personas_service, "_persona_provisioning_store", lambda: coordinator.store)
    assert personas_service.plan_persona_provisioning_case_retirement(
        HOSTED_TENANT, HOSTED_PERSONA
    ) is None

    # Replay: nothing moves, nothing is written.
    transitions.clear()
    replay = _ensure_hosted(owner_stack, coordinator)
    assert transitions == []
    assert replay["runtime_binding_id"] == binding.binding_id
    assert replay["book_generation"] == 2
    replay_ledger = coordinator.store.get(HOSTED_TENANT, key)
    assert replay_ledger.attempt_count == ledger.attempt_count
    assert replay_ledger.updated_at == ledger.updated_at
    assert len(_reload_capital_pools(capital_dir)) == 2
    assert len(rm_service.find_bindings_for_pool(gen2.capital_pool_id)) == 1

    readback = read_trader_paper_book(
        HOSTED_TENANT,
        HOSTED_PERSONA,
        agora_user_id=REQUESTER,
        coordinator=coordinator,
        runtime_client=owner_stack["rm_client"],
    )
    assert readback is not None
    assert readback["book_generation"] == 2
    assert readback["runtime_binding_id"] == binding.binding_id
    assert readback["capital_pool_id"] == gen2.capital_pool_id


def test_crash_after_generation_one_retired_resumes_to_generation_two_without_second_retire(
    owner_stack: Dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay after a crash between retiring generation 1 and provisioning generation 2 goes
    straight to generation 2: no second retire and no orphaned owner ids."""
    seeded = _seed_recorded_legacy_book(
        owner_stack, monkeypatch, status="paused", session_admission=MARKET_INPUT_MISSING_PAUSE
    )
    coordinator = seeded["coordinator"]
    key = seeded["legacy"].idempotency_key
    servant_calls = _servant_retirement_spies(coordinator)
    retire_calls: List[int] = []
    orig_retire_case = coordinator.retire_case

    def _counting_retire_case(*args: Any, **kwargs: Any) -> Any:
        retire_calls.append(1)
        return orig_retire_case(*args, **kwargs)

    monkeypatch.setattr(coordinator, "retire_case", _counting_retire_case)

    # Crash 1: generation 1 retired, process dies before the row moves to generation 2.
    orig_begin = trader_paper_book._begin_next_trader_paper_book_generation

    def _crash_begin(*_a: Any, **_kw: Any) -> Any:
        raise RuntimeError("bff crashed after retire")

    monkeypatch.setattr(trader_paper_book, "_begin_next_trader_paper_book_generation", _crash_begin)
    with pytest.raises(RuntimeError):
        _ensure_hosted(owner_stack, coordinator)
    retired_row = coordinator.store.get(HOSTED_TENANT, key)
    assert retired_row.state == "retired"
    assert retired_row.references["retirement"]["scope"] == "book"
    assert retire_calls == [1]
    # While generation 1 is retired no reader shows a provisioned book or a retired servant.
    monkeypatch.setattr(personas_service, "_persona_provisioning_store", lambda: coordinator.store)
    assert personas_service.plan_persona_provisioning_case_retirement(
        HOSTED_TENANT, HOSTED_PERSONA
    ) is None
    assert read_trader_paper_book(
        HOSTED_TENANT,
        HOSTED_PERSONA,
        coordinator=coordinator,
        runtime_client=owner_stack["rm_client"],
    ) is None

    # Crash 2: the row moved to generation 2, process dies before generation 2 is provisioned.
    monkeypatch.setattr(trader_paper_book, "_begin_next_trader_paper_book_generation", orig_begin)
    orig_generation = trader_paper_book._ensure_trader_paper_book_generation
    crashed: List[int] = []

    def _crash_generation_two(*, generation: int, **kwargs: Any) -> Any:
        if generation == 2 and not crashed:
            crashed.append(generation)
            raise RuntimeError("bff crashed before generation 2")
        return orig_generation(generation=generation, **kwargs)

    monkeypatch.setattr(trader_paper_book, "_ensure_trader_paper_book_generation", _crash_generation_two)
    with pytest.raises(RuntimeError):
        _ensure_hosted(owner_stack, coordinator)
    moved = coordinator.store.get(HOSTED_TENANT, key)
    assert moved.state == "provisioning"
    assert moved.references["book_generation"]["generation"] == 2
    assert retire_calls == [1]

    book = _ensure_hosted(owner_stack, coordinator)
    gen2 = trader_paper_book_generation_ids(HOSTED_TENANT, HOSTED_PERSONA, 2)
    assert book["book_generation"] == 2
    assert retire_calls == [1]
    assert servant_calls == []
    assert sorted(_reload_capital_pools(owner_stack["capital_dir"])) == sorted(
        [seeded["ids"].capital_pool_id, gen2.capital_pool_id]
    )
    assert len(owner_stack["rm_service"].find_bindings_for_pool(gen2.capital_pool_id)) == 1
    final = coordinator.store.get(HOSTED_TENANT, key)
    assert final.state == "succeeded"
    assert final.current_step == TRADER_PAPER_BOOK_PROVISIONED_STEP
    assert list(final.references["book_generations"]) == ["1"]


@pytest.mark.parametrize("pause_point", ["before_retire_case", "before_generation_advance"])
def test_stale_ensure_interleaved_with_completed_replacement_never_touches_newer_generation(
    owner_stack: Dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    pause_point: str,
) -> None:
    """Review P1 regression: request A derives generation 1 ids, pauses (immediately before
    retire_case, or after retiring generation 1 but before the advance), request B (an
    independent coordinator with its own lease owner) finishes the whole replacement to
    generation 2, then A resumes. A must not retire generation 2, advance to generation 3 or
    finalize a stale generation: it continues from the ledger's current generation 2."""
    seeded = _seed_recorded_legacy_book(
        owner_stack, monkeypatch, status="paused", session_admission=MARKET_INPUT_MISSING_PAUSE
    )
    key = seeded["legacy"].idempotency_key
    coordinator_a = seeded["coordinator"]
    coordinator_a.lease_owner = "request-a"
    coordinator_b = owner_stack["coordinator_for_tenant"](HOSTED_TENANT)
    coordinator_b.lease_owner = "request-b"
    assert coordinator_a is not coordinator_b and coordinator_a.store is coordinator_b.store
    servant_calls = _servant_retirement_spies(coordinator_a) + _servant_retirement_spies(
        coordinator_b
    )
    b_books: List[Dict[str, Any]] = []

    def _run_b() -> None:
        b_books.append(_ensure_hosted(owner_stack, coordinator_b))
        row = coordinator_b.store.get(HOSTED_TENANT, key)
        assert row.state == "succeeded"
        assert row.references["book_generation"]["generation"] == 2

    if pause_point == "before_retire_case":
        orig_retire_case = coordinator_a.retire_case

        def _paused_retire_case(*args: Any, **kwargs: Any) -> Any:
            if not b_books:
                _run_b()
            return orig_retire_case(*args, **kwargs)

        monkeypatch.setattr(coordinator_a, "retire_case", _paused_retire_case)
    else:
        orig_begin = trader_paper_book._begin_next_trader_paper_book_generation

        def _paused_begin(coordinator: Any, ledger: Any, **kwargs: Any) -> Any:
            if coordinator is coordinator_a and not b_books:
                assert ledger.state == "retired"
                _run_b()
            return orig_begin(coordinator, ledger, **kwargs)

        monkeypatch.setattr(
            trader_paper_book, "_begin_next_trader_paper_book_generation", _paused_begin
        )

    book_a = _ensure_hosted(owner_stack, coordinator_a)

    gen2 = trader_paper_book_generation_ids(HOSTED_TENANT, HOSTED_PERSONA, 2)
    gen3 = trader_paper_book_generation_ids(HOSTED_TENANT, HOSTED_PERSONA, 3)
    assert len(b_books) == 1 and b_books[0]["book_generation"] == 2
    assert book_a["book_generation"] == 2
    assert book_a["runtime_binding_id"] == b_books[0]["runtime_binding_id"]
    ledger = coordinator_a.store.get(HOSTED_TENANT, key)
    assert ledger.state == "succeeded"
    assert ledger.current_step == TRADER_PAPER_BOOK_PROVISIONED_STEP
    assert ledger.references["book_generation"]["generation"] == 2
    assert ledger.result["book_generation"] == 2
    assert "retirement" not in ledger.references
    assert list(ledger.references["book_generations"]) == ["1"]
    assert ledger.lease_owner is None
    gen2_bindings = owner_stack["rm_service"].find_bindings_for_pool(gen2.capital_pool_id)
    assert [b.status for b in gen2_bindings] == ["active"]
    assert owner_stack["rm_service"].find_bindings_for_pool(gen3.capital_pool_id) == []
    assert gen3.capital_pool_id not in _reload_capital_pools(owner_stack["capital_dir"])
    assert servant_calls == []
    readback = read_trader_paper_book(
        HOSTED_TENANT,
        HOSTED_PERSONA,
        agora_user_id=REQUESTER,
        coordinator=coordinator_a,
        runtime_client=owner_stack["rm_client"],
    )
    assert readback is not None
    assert readback["book_generation"] == 2
    assert readback["runtime_binding_id"] == gen2_bindings[0].binding_id


def test_stale_generation_fence_refuses_retire_advance_and_finalize_without_writes(
    owner_stack: Dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unit fence: with the ledger at generation 2, generation-1 ids can neither retire
    (retire_case fence, checked before any mutation), advance, nor finalize the row."""
    seeded = _seed_recorded_legacy_book(
        owner_stack, monkeypatch, status="paused", session_admission=MARKET_INPUT_MISSING_PAUSE
    )
    coordinator = seeded["coordinator"]
    key = seeded["legacy"].idempotency_key
    _servant_retirement_spies(coordinator)
    stale_row = coordinator.store.get(HOSTED_TENANT, key)
    _ensure_hosted(owner_stack, coordinator)
    current = coordinator.store.get(HOSTED_TENANT, key)
    assert current.references["book_generation"]["generation"] == 2
    gen1 = trader_paper_book_ids(HOSTED_TENANT, HOSTED_PERSONA)

    with pytest.raises(PersonaProvisioningCoordinationError) as exc:
        coordinator.retire_case(
            stale_row,
            retain_persona=True,
            ids=gen1,
            fence=trader_paper_book._trader_paper_book_generation_fence(
                stale_row, generation=1, ids=gen1
            ),
        )
    assert exc.value.reason_code == trader_paper_book.TRADER_PAPER_BOOK_GENERATION_STALE
    assert trader_paper_book._begin_next_trader_paper_book_generation(
        coordinator, stale_row, ids=gen1, expected_generation=1, currency="TWD"
    ).references["book_generation"]["generation"] == 2
    with pytest.raises(PersonaProvisioningCoordinationError) as exc:
        trader_paper_book._finalize_trader_paper_book_ledger_record(
            coordinator,
            stale_row,
            runtime_binding={"binding_id": HOSTED_BINDING},
            ids=gen1,
            provisioned_at="2026-11-28T18:30:00Z",
            generation=1,
        )
    assert exc.value.reason_code == trader_paper_book.TRADER_PAPER_BOOK_GENERATION_STALE
    after = coordinator.store.get(HOSTED_TENANT, key)
    assert after.references == current.references
    assert after.result == current.result
    assert after.state == "succeeded"
    assert after.lease_owner is None
    # The stale request's direct read path of the stale row also continues from generation 2.
    assert trader_paper_book._retire_and_advance_trader_paper_book_generation(
        coordinator, stale_row, runtime_client=owner_stack["rm_client"], currency="TWD"
    ).references["book_generation"]["generation"] == 2
    assert coordinator.store.get(HOSTED_TENANT, key).references == current.references


def test_generation_move_is_refused_for_non_book_rows_and_persona_scope_retirement(
    owner_stack: Dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guards: only a trader servant paper book row moves from retired to a new generation, only
    after a book-scope retirement; a retired Persona row is never re-leased."""
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", HOSTED_TENANT)
    coordinator = owner_stack["coordinator_for_tenant"](HOSTED_TENANT)
    store = coordinator.store

    persona_row, _ = store.reserve(
        tenant_id=HOSTED_TENANT,
        idempotency_key="create-persona-retired",
        request_hash="d" * 64,
        normalized_name="retired persona",
        persona_id="persona-retired",
        request_payload={"name": "Retired Persona", "mandate": "momentum"},
    )
    leased = store.acquire(HOSTED_TENANT, persona_row.idempotency_key, lease_owner="t", lease_seconds=60)
    leased.state = "retired"
    leased.current_step = "retired"
    leased.references = {"retirement": {"status": "retired", "retired_at": "2026-11-28T00:00:00Z"}}
    persona_row = store.release(leased, lease_owner="t")
    with pytest.raises(PersonaProvisioningCoordinationError) as exc:
        trader_paper_book._retire_and_advance_trader_paper_book_generation(
            coordinator, persona_row, runtime_client=owner_stack["rm_client"], currency="TWD"
        )
    assert exc.value.reason_code == "trader_paper_book_generation_not_book_row"
    unchanged = trader_paper_book._begin_next_trader_paper_book_generation(
        coordinator,
        persona_row,
        ids=trader_paper_book_ids(HOSTED_TENANT, "persona-retired"),
        expected_generation=1,
        currency="TWD",
    )
    assert unchanged.state == "retired"
    assert store.get(HOSTED_TENANT, persona_row.idempotency_key).references == persona_row.references
    # retire_case on a retired row is a no-op and acquire never revives a terminal state.
    assert coordinator.retire_case(persona_row).state == "retired"

    # A book row retired together with its Persona (full retire_case scope) is never re-leased.
    seeded = _seed_recorded_legacy_book(owner_stack, monkeypatch, status="active", session_admission=None)
    book_key = seeded["legacy"].idempotency_key
    leased = store.acquire(HOSTED_TENANT, book_key, lease_owner="t", lease_seconds=60)
    leased.state = "retired"
    leased.current_step = "retired"
    leased.references = {
        **leased.references,
        "retirement": {"status": "retired", "persona": {"receipt": {"status": "archived"}}},
    }
    store.release(leased, lease_owner="t")
    before = store.get(HOSTED_TENANT, book_key)
    with pytest.raises(PersonaProvisioningCoordinationError) as exc:
        _ensure_hosted(owner_stack, coordinator)
    assert exc.value.reason_code == "trader_paper_book_retired_with_persona"
    after = store.get(HOSTED_TENANT, book_key)
    assert after.state == "retired"
    assert after.references == before.references


def test_generation_history_is_capped_before_any_retirement_write(
    owner_stack: Dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runtime budget: a row at the last allowed generation is never retired or advanced again."""
    seeded = _seed_recorded_legacy_book(
        owner_stack, monkeypatch, status="paused", session_admission=MARKET_INPUT_MISSING_PAUSE
    )
    coordinator = seeded["coordinator"]
    key = seeded["legacy"].idempotency_key
    row = coordinator.store.get(HOSTED_TENANT, key)
    leased = coordinator.store.acquire(HOSTED_TENANT, key, lease_owner="t", lease_seconds=60)
    leased.references = {
        **leased.references,
        "book_generation": {
            "generation": trader_paper_book.TRADER_PAPER_BOOK_MAX_GENERATIONS,
            "symbols": ["2330.TW"],
        },
    }
    coordinator.store.release(leased, lease_owner="t")
    before = coordinator.store.get(HOSTED_TENANT, key)
    with pytest.raises(PersonaProvisioningCoordinationError) as exc:
        trader_paper_book._retire_and_advance_trader_paper_book_generation(
            coordinator, before, runtime_client=owner_stack["rm_client"], currency="TWD"
        )
    assert exc.value.reason_code == "trader_paper_book_generation_cap"
    after = coordinator.store.get(HOSTED_TENANT, key)
    assert after.references == before.references and after.state == before.state
    assert row is not None


def test_generation_move_is_only_reachable_from_ensure_trader_paper_book() -> None:
    """Guard: the retired-to-next-generation move has exactly one entrypoint."""
    import ast
    import inspect

    bff_root = Path(trader_paper_book.__file__).resolve().parents[1]
    movers = {
        "_retire_and_advance_trader_paper_book_generation",
        "_retire_and_advance_fenced",
        "_begin_next_trader_paper_book_generation",
    }
    callers: Dict[str, set] = {name: set() for name in movers}
    for path in bff_root.rglob("*.py"):
        if "tests" in path.parts or path.name.startswith("test_"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(func):
                name = (
                    node.id
                    if isinstance(node, ast.Name)
                    else node.attr
                    if isinstance(node, ast.Attribute)
                    else None
                )
                if name in movers and func.name != name:
                    callers[name].add((path.name, func.name))
    assert callers["_retire_and_advance_trader_paper_book_generation"] == {
        ("trader_paper_book.py", "ensure_trader_paper_book")
    }
    assert callers["_retire_and_advance_fenced"] == {
        ("trader_paper_book.py", "_retire_and_advance_trader_paper_book_generation")
    }
    assert callers["_begin_next_trader_paper_book_generation"] == {
        ("trader_paper_book.py", "_retire_and_advance_fenced")
    }
    fenced_source = inspect.getsource(trader_paper_book._retire_and_advance_fenced)
    assert "retain_persona=True" in fenced_source
    assert "fence=_trader_paper_book_generation_fence(" in fenced_source


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
