"""Durability contract tests for the reconstruction worker (PFG-AGORA-RECON-WORKER-20260820).

Exercises the single worker path in ``runner.py``: one durable admission per
conversation sequence identity, replay when the conversation hasn't
advanced, a fresh effective result when it has, and best-effort Registry
draft creation without a second reconstruction model.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

_CONTROL_PLANE_DIR = Path(__file__).resolve().parents[3]

from services.control_plane.bff.agora.strategy_workshop.operations import CanonicalOperationError
from services.control_plane.bff.agora.strategy_workshop.runner import reconstruction_card_id, run_reconstruction_worker
from services.control_plane.bff.agora.strategy_workshop.store import MemoryWorkshopStore


class _NoRegistryOperations:
    """A canonical-operations stub that always fails closed (no Registry configured)."""

    def get_strategy_spec(self, registry_id: str) -> Dict[str, Any]:
        raise CanonicalOperationError("strategy_registry", "not configured", retryable=True)

    def create_strategy_spec(self, payload: Dict[str, Any]) -> Dict[str, Any]:  # pragma: no cover
        raise AssertionError("create_strategy_spec must not be called without an active StrategySpec")


class _FakeRegistryOperations:
    """Records create_strategy_spec calls and returns a deterministic readback."""

    def __init__(self, base_entry: Dict[str, Any]) -> None:
        self._base_entry = base_entry
        self.create_calls: list[Dict[str, Any]] = []

    def get_strategy_spec(self, registry_id: str) -> Dict[str, Any]:
        assert registry_id == self._base_entry["registry_id"]
        return {"entry": dict(self._base_entry)}

    def create_strategy_spec(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.create_calls.append(payload)
        entry = {**payload, "registry_id": payload["registry_id"]}
        return {"entry": entry}


def _new_session(store: MemoryWorkshopStore, **overrides: Any) -> Dict[str, Any]:
    session = {
        "workshop_id": overrides.pop("workshop_id", "ws-recon-worker-1"),
        "tenant_id": "tenant-test",
        "user_id": "user-test",
        **overrides,
    }
    return store.create_session(session)


def _post_message(store: MemoryWorkshopStore, workshop_id: str, text: str) -> Dict[str, Any]:
    return store.create_event({
        "workshop_id": workshop_id,
        "actor_type": "operator",
        "event_type": "message",
        "redacted_summary": text,
    })


def test_worker_persists_one_effective_result_and_replays_without_recompute() -> None:
    store = MemoryWorkshopStore()
    session = _new_session(store, workshop_id="ws-recon-worker-replay")
    workshop_id = session["workshop_id"]
    _post_message(store, workshop_id, "I want a momentum strategy for BTC and ETH.")

    ops = _NoRegistryOperations()
    first = run_reconstruction_worker(
        store=store, canonical=ops, workshop_id=workshop_id,
        tenant_id="tenant-test", user_id="user-test",
    )
    assert first["job_status"] == "completed"
    assert first["result"]["workshop_id"] == workshop_id
    assert first["result"]["based_on_sequence_no"] == 1

    card_id = reconstruction_card_id(workshop_id)
    cards = [c for c in store.list_workshop_cards(workshop_id) if c["card_id"] == card_id]
    assert len(cards) == 1, "one deterministic card slot per workshop, not one per run"

    second = run_reconstruction_worker(
        store=store, canonical=ops, workshop_id=workshop_id,
        tenant_id="tenant-test", user_id="user-test",
    )
    assert second["job_status"] == "replayed"
    assert second["result"] == first["result"], "replay must return the same effective result"

    cards_after_replay = [c for c in store.list_workshop_cards(workshop_id) if c["card_id"] == card_id]
    assert len(cards_after_replay) == 1


def test_worker_recomputes_after_conversation_advances() -> None:
    store = MemoryWorkshopStore()
    session = _new_session(store, workshop_id="ws-recon-worker-stale")
    workshop_id = session["workshop_id"]
    _post_message(store, workshop_id, "Hello, I want to trade.")

    ops = _NoRegistryOperations()
    first = run_reconstruction_worker(
        store=store, canonical=ops, workshop_id=workshop_id,
        tenant_id="tenant-test", user_id="user-test",
    )
    assert first["result"]["based_on_sequence_no"] == 1

    _post_message(
        store, workshop_id,
        "The signal is a 20-day moving average crossover with a 2% stop loss on Crypto Top 10.",
    )
    second = run_reconstruction_worker(
        store=store, canonical=ops, workshop_id=workshop_id,
        tenant_id="tenant-test", user_id="user-test",
    )
    assert second["job_status"] == "completed", "a stale prior result must not be replayed"
    assert second["result"]["based_on_sequence_no"] == 2
    assert second["result"] != first["result"]

    card_id = reconstruction_card_id(workshop_id)
    cards = [c for c in store.list_workshop_cards(workshop_id) if c["card_id"] == card_id]
    assert len(cards) == 1, "the newer result supersedes the old one in the same durable slot"
    assert cards[0]["payload"]["based_on_sequence_no"] == 2


def test_worker_survives_a_crash_after_running_before_completed() -> None:
    """A card left "running" (process died mid-flight) must not wedge the next attempt."""
    store = MemoryWorkshopStore()
    session = _new_session(store, workshop_id="ws-recon-worker-crash")
    workshop_id = session["workshop_id"]
    _post_message(store, workshop_id, "I want a mean reversion strategy for SP500 with a 5% risk limit.")

    card_id = reconstruction_card_id(workshop_id)
    store.record_workshop_card({
        "card_id": card_id,
        "card_type": "strategy_reconstruction",
        "workshop_id": workshop_id,
        "status": "running",
        "title": "Strategy reconstruction in progress",
        "payload": {"based_on_sequence_no": 1, "job_status": "running"},
    })

    ops = _NoRegistryOperations()
    outcome = run_reconstruction_worker(
        store=store, canonical=ops, workshop_id=workshop_id,
        tenant_id="tenant-test", user_id="user-test",
    )
    assert outcome["job_status"] == "completed"
    assert outcome["result"]["based_on_sequence_no"] == 1
    cards = [c for c in store.list_workshop_cards(workshop_id) if c["card_id"] == card_id]
    assert len(cards) == 1
    assert cards[0]["status"] == "completed"


def test_worker_creates_registry_draft_when_active_spec_and_grade_allow() -> None:
    store = MemoryWorkshopStore()
    base_entry = {
        "registry_id": "reg-base-1",
        "strategy_id": "strat-1",
        "version": "1.0.0",
        "metadata": {"strategy_spec": {"spec_version": "1.0", "hypothesis": "base"}},
    }
    session = _new_session(
        store, workshop_id="ws-recon-worker-registry",
        strategy_id="strat-1", active_strategy_spec_registry_id="reg-base-1",
    )
    workshop_id = session["workshop_id"]
    _post_message(store, workshop_id, "Hypothesis: momentum. Universe: BTC, ETH, crypto top 10.")
    _post_message(
        store, workshop_id,
        "Signal: 20-day moving average crossover indicator. Entry: buy on cross above. "
        "Exit: sell on cross below or stop loss. Sizing: fixed weight allocation. "
        "Risk: max drawdown limit with leverage cap. Cost: slippage and commission modeled. "
        "Validation: walk forward out of sample backtest. Regime: pause during high vol crash. "
        "Governance: requires sponsor approval within compliance limit.",
    )

    ops = _FakeRegistryOperations(base_entry)
    outcome = run_reconstruction_worker(
        store=store, canonical=ops, workshop_id=workshop_id,
        tenant_id="tenant-test", user_id="user-test",
    )
    assert outcome["result"]["completeness"]["grade"] != "insufficient"
    assert outcome["registry_draft_ref"] is not None
    assert outcome["registry_draft_ref"]["artifact_state"] == "draft"
    assert len(ops.create_calls) == 1
    created_payload = ops.create_calls[0]
    assert created_payload["artifact_state"] == "draft"
    assert created_payload["strategy_spec"] == base_entry["metadata"]["strategy_spec"]
    assert created_payload["metadata"]["reconstruction_id"] == outcome["result"]["reconstruction_id"]

    # A replay of the same conversation state must not create a second draft.
    replay = run_reconstruction_worker(
        store=store, canonical=ops, workshop_id=workshop_id,
        tenant_id="tenant-test", user_id="user-test",
    )
    assert replay["job_status"] == "replayed"
    assert len(ops.create_calls) == 1


def test_worker_skips_registry_draft_without_active_strategy_spec() -> None:
    store = MemoryWorkshopStore()
    session = _new_session(store, workshop_id="ws-recon-worker-no-registry")
    workshop_id = session["workshop_id"]
    _post_message(store, workshop_id, "Hello, I want to trade.")

    ops = _NoRegistryOperations()
    outcome = run_reconstruction_worker(
        store=store, canonical=ops, workshop_id=workshop_id,
        tenant_id="tenant-test", user_id="user-test",
    )
    assert outcome["registry_draft_ref"] is None
