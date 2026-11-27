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

from services.control_plane.bff.agora.strategy_workshop.operations import CanonicalOperationError
from services.control_plane.bff.agora.strategy_workshop.runner import reconstruction_card_id, run_reconstruction_worker
from services.control_plane.bff.agora.strategy_workshop.store import MemoryWorkshopStore


@pytest.fixture(autouse=True)
def semantic_response(monkeypatch):
    """Explicit transport fake; durability tests never call a live provider."""
    from .semantic_provider import OpenClawOpsClient
    payload = {
        "strategy_map": {"hypothesis": {"status": "confirmed", "summary": "Momentum alpha",
                                        "details": {"message_numbers": [1]}}},
        "explicit_facts": ["Momentum alpha"], "inferences": [], "assumptions": [],
        "contradictions": [], "next_best_question": {
            "question_id": "q-1", "text": "Which universe?", "resolves": ["universe"],
            "why_now": "Universe is unspecified",
        }, "strategy_spec": None,
    }
    monkeypatch.setattr(OpenClawOpsClient, "_request", lambda *a, **kw: {
        "status": "ok", "data": {"provider": "openclaw", "status": "completed",
                                    "output": {"structured_data": payload}},
    })
    return payload


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


@pytest.mark.parametrize("mismatch", [None, "strategy_spec", "artifact_state", "registry_id", "version"])
def test_worker_creates_registry_draft_when_active_spec_and_grade_allow(semantic_response, mismatch) -> None:
    from services.research.strategy_spec.test_models import _strategy_spec_payload
    from .reconstruction import StrategyMap
    proposal = _strategy_spec_payload()
    proposal["strategy_id"] = "strat-1"
    proposal["lifecycle_state"] = "draft"
    proposal["governance"]["approval_required"] = True
    semantic_response["strategy_spec"] = proposal
    semantic_response["strategy_map"] = {
        name: {"status": "confirmed", "summary": name, "details": {"message_numbers": [1, 2]}}
        for name in StrategyMap.model_fields
    }
    semantic_response["strategy_map"]["universe"]["details"].update(proposal["market_scope"])
    semantic_response["strategy_map"]["exit_rules"]["details"]["rebalance_cadence"] = proposal["execution_profile"].get("rebalance_cadence")
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
    if mismatch:
        create = ops.create_strategy_spec
        def mismatched_readback(payload):
            readback = create(payload)
            readback["entry"][mismatch] = {} if mismatch == "strategy_spec" else "different"
            return readback
        ops.create_strategy_spec = mismatched_readback
    outcome = run_reconstruction_worker(
        store=store, canonical=ops, workshop_id=workshop_id,
        tenant_id="tenant-test", user_id="user-test",
    )
    assert outcome["result"]["completeness"]["grade"] != "insufficient"
    if mismatch:
        assert outcome["registry_draft_ref"] is None
        assert len(ops.create_calls) == 1
        return
    assert outcome["registry_draft_ref"] is not None
    assert outcome["registry_draft_ref"]["artifact_state"] == "draft"
    assert len(ops.create_calls) == 1
    created_payload = ops.create_calls[0]
    assert created_payload["artifact_state"] == "draft"
    assert created_payload["strategy_spec"] == proposal
    assert created_payload["strategy_spec"] != base_entry["metadata"]["strategy_spec"]
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


def test_worker_reads_private_owner_content_not_placeholder_and_upgrades_old_card() -> None:
    from services.control_plane.privacy.private_content_store import EphemeralKeyProvider, MemoryPrivateContentStore

    store = MemoryWorkshopStore()
    private = MemoryPrivateContentStore(key_provider=EphemeralKeyProvider())
    session = _new_session(store)
    workshop_id = session["workshop_id"]
    content = private.put(
        tenant_id="tenant-test", owner_user_id="user-test", workshop_id=workshop_id,
        event_id="private-message", content_type="text/plain", retention_class="workshop_default",
        idempotency_key="private-message", plaintext=b"Hypothesis: momentum alpha. Universe: SPY equities.",
    )
    store.create_event({"workshop_id": workshop_id, "actor_type": "operator", "event_type": "message",
                        "private_content_ref": content.private_content_ref,
                        "redacted_summary": "Private workshop message"})
    store.record_workshop_card({
        "card_id": reconstruction_card_id(workshop_id), "card_type": "strategy_reconstruction",
        "workshop_id": workshop_id, "status": "completed",
        "title": "Old placeholder-based reconstruction",
        "payload": {"based_on_sequence_no": 1, "reconstruction": {"stale": True}},
    })
    args = dict(store=store, canonical=_NoRegistryOperations(), workshop_id=workshop_id,
                tenant_id="tenant-test", user_id="user-test", private_content_store=private)
    result = run_reconstruction_worker(**args)
    assert result["job_status"] == "completed"
    assert result["result"]["strategy_map"]["hypothesis"]["status"] == "confirmed"
    assert len(private.audit_records) == 1
    assert private.audit_records[0].purpose == "workshop_reconstruction"
    assert run_reconstruction_worker(**args)["result"] == result["result"]
    assert len(private.audit_records) == 1, "same version replay must not decrypt or draft again"


def test_worker_missing_private_content_does_not_reconstruct_placeholder() -> None:
    from services.control_plane.privacy.private_content_models import PrivateContentStoreUnavailable

    store = MemoryWorkshopStore()
    session = _new_session(store)
    store.create_event({"workshop_id": session["workshop_id"], "actor_type": "operator",
                        "event_type": "message", "private_content_ref": "pcnt_missing",
                        "redacted_summary": "Private workshop message"})
    with pytest.raises(PrivateContentStoreUnavailable):
        run_reconstruction_worker(store=store, canonical=_NoRegistryOperations(),
                                  workshop_id=session["workshop_id"],
                                  tenant_id="tenant-test", user_id="user-test")
    # The job is recorded as failed with a typed reason; no placeholder result.
    [card] = store.list_workshop_cards(session["workshop_id"])
    assert card["status"] == "failed"
    assert card["payload"]["failure_reason"] == "private_content_unavailable"
    assert "reconstruction" not in card["payload"]


# --------------------------------------------------------------------------- #
# AGORA-WORKSHOP-RECONSTRUCTION-HOSTED-20261127: one queued job per message.
# --------------------------------------------------------------------------- #
from services.control_plane.bff.agora.strategy_workshop.runner import (  # noqa: E402
    admit_reconstruction_job,
    run_reconstruction_job,
)


@pytest.fixture
def provider_calls(monkeypatch, semantic_response):
    from .semantic_provider import OpenClawOpsClient
    calls: list = []

    def request(*a, **kw):
        calls.append(kw.get("body"))
        return {"status": "ok", "data": {"provider": "openclaw", "status": "completed",
                                            "output": {"structured_data": semantic_response}}}

    monkeypatch.setattr(OpenClawOpsClient, "_request", request)
    return calls


def _scope(workshop_id: str) -> Dict[str, Any]:
    return dict(workshop_id=workshop_id, tenant_id="tenant-test", user_id="user-test")


def test_admission_queues_without_provider_call_and_one_run_completes(provider_calls) -> None:
    store = MemoryWorkshopStore()
    workshop_id = _new_session(store)["workshop_id"]
    _post_message(store, workshop_id, "Momentum on BTC.")
    admission = admit_reconstruction_job(store=store, **_scope(workshop_id))
    assert admission["admitted"] and admission["job_status"] == "queued"
    assert admission["sequence_no"] == 1 and provider_calls == []
    # A second admission for the same message joins the queued job.
    again = admit_reconstruction_job(store=store, **_scope(workshop_id))
    assert not again["admitted"] and again["job_id"] == admission["job_id"]
    # An explicit /reconstruct while it is queued does not call the provider.
    in_flight = run_reconstruction_worker(store=store, canonical=_NoRegistryOperations(), **_scope(workshop_id))
    assert in_flight["job_status"] == "queued" and in_flight["result"] is None
    assert provider_calls == []

    done = run_reconstruction_job(store=store, canonical=_NoRegistryOperations(),
                                  job_id=admission["job_id"], **_scope(workshop_id))
    assert done["job_status"] == "completed" and len(provider_calls) == 1
    replay = run_reconstruction_worker(store=store, canonical=_NoRegistryOperations(), **_scope(workshop_id))
    assert replay["job_status"] == "replayed" and replay["result"] == done["result"]
    # Running the same job again is a no-op.
    assert run_reconstruction_job(store=store, canonical=_NoRegistryOperations(),
                                  job_id=admission["job_id"], **_scope(workshop_id))["job_status"] == "superseded"
    assert len(provider_calls) == 1


def test_non_message_events_do_not_start_a_second_job(provider_calls) -> None:
    store = MemoryWorkshopStore()
    workshop_id = _new_session(store)["workshop_id"]
    _post_message(store, workshop_id, "Momentum on BTC.")
    run_reconstruction_worker(store=store, canonical=_NoRegistryOperations(), **_scope(workshop_id))
    store.create_event({"workshop_id": workshop_id, "actor_type": "persona",
                        "event_type": "persona_opinion", "redacted_summary": "opinion"})
    assert run_reconstruction_worker(store=store, canonical=_NoRegistryOperations(),
                                     **_scope(workshop_id))["job_status"] == "replayed"
    assert len(provider_calls) == 1


def test_waiting_reconstruct_reads_the_job_finished_elsewhere(provider_calls, monkeypatch) -> None:
    from services.control_plane.bff.agora.strategy_workshop import runner

    store = MemoryWorkshopStore()
    workshop_id = _new_session(store)["workshop_id"]
    _post_message(store, workshop_id, "Momentum on BTC.")
    admission = admit_reconstruction_job(store=store, **_scope(workshop_id))

    def finish_on_first_poll(_seconds: float) -> None:
        # The background job (another thread/replica) finishes meanwhile.
        run_reconstruction_job(store=store, canonical=_NoRegistryOperations(),
                               job_id=admission["job_id"], **_scope(workshop_id))

    monkeypatch.setattr(runner.time, "sleep", finish_on_first_poll)
    outcome = run_reconstruction_worker(store=store, canonical=_NoRegistryOperations(),
                                        wait_seconds=30, **_scope(workshop_id))
    assert outcome["job_status"] == "replayed" and outcome["result"]["based_on_sequence_no"] == 1
    assert len(provider_calls) == 1


def test_deterministic_failure_is_replayed_with_its_reason_without_a_second_call(monkeypatch) -> None:
    from services.control_plane.bff.agora.strategy_workshop.semantic_provider import (
        OpenClawOpsClient, OpenClawOpsClientError, ReconstructionProviderError,
    )
    calls: list = []

    def refused(*a, **kw):
        calls.append(1)
        raise OpenClawOpsClientError("model answer is not valid JSON", status_code=422,
                                     error_code="OPENCLAW_TOOL_ARGS_INVALID_JSON")

    monkeypatch.setattr(OpenClawOpsClient, "_request", refused)
    store = MemoryWorkshopStore()
    workshop_id = _new_session(store)["workshop_id"]
    _post_message(store, workshop_id, "Momentum on BTC.")
    for _ in range(2):
        with pytest.raises(ReconstructionProviderError) as exc:
            run_reconstruction_worker(store=store, canonical=_NoRegistryOperations(), **_scope(workshop_id))
        assert exc.value.reason == "provider_output_invalid_json"
        assert exc.value.adapter_error_code == "OPENCLAW_TOOL_ARGS_INVALID_JSON"
    assert len(calls) == 1
    [card] = store.list_workshop_cards(workshop_id)
    assert card["status"] == "failed" and card["summary"] == "Reconstruction failed: provider_output_invalid_json"
    assert card["payload"]["failure_reason"] == "provider_output_invalid_json"
    assert "not valid JSON" not in str(card)
    # A new message is a new job.
    _post_message(store, workshop_id, "Universe: BTC and ETH.")
    with pytest.raises(ReconstructionProviderError):
        run_reconstruction_worker(store=store, canonical=_NoRegistryOperations(), **_scope(workshop_id))
    assert len(calls) == 2


def test_abandoned_job_is_taken_over_only_after_its_lease(provider_calls) -> None:
    store = MemoryWorkshopStore()
    workshop_id = _new_session(store)["workshop_id"]
    _post_message(store, workshop_id, "Momentum on BTC.")
    crashed = admit_reconstruction_job(store=store, clock=lambda: 1000.0, **_scope(workshop_id))
    fresh = admit_reconstruction_job(store=store, clock=lambda: 1100.0, **_scope(workshop_id))
    assert not fresh["admitted"]
    takeover = admit_reconstruction_job(store=store, clock=lambda: 1181.0, **_scope(workshop_id))
    assert takeover["admitted"] and takeover["job_id"] != crashed["job_id"]
    assert takeover["card"]["payload"]["attempt"] == 2
    # The crashed job can no longer write to the card.
    assert run_reconstruction_job(store=store, canonical=_NoRegistryOperations(),
                                  job_id=crashed["job_id"], **_scope(workshop_id))["job_status"] == "superseded"
    assert provider_calls == []


def test_older_job_never_overwrites_the_newer_messages_job(provider_calls) -> None:
    store = MemoryWorkshopStore()
    workshop_id = _new_session(store)["workshop_id"]
    _post_message(store, workshop_id, "Momentum on BTC.")
    first = admit_reconstruction_job(store=store, **_scope(workshop_id))
    _post_message(store, workshop_id, "Universe: BTC and ETH.")
    second = admit_reconstruction_job(store=store, **_scope(workshop_id))
    assert second["admitted"] and second["sequence_no"] == 2
    assert run_reconstruction_job(store=store, canonical=_NoRegistryOperations(),
                                  job_id=first["job_id"], **_scope(workshop_id))["job_status"] == "superseded"
    done = run_reconstruction_job(store=store, canonical=_NoRegistryOperations(),
                                  job_id=second["job_id"], **_scope(workshop_id))
    assert done["result"]["based_on_sequence_no"] == 2 and len(provider_calls) == 1


def test_postgres_job_record_is_compare_and_set_on_the_card(request, provider_calls) -> None:
    import os
    import uuid

    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is not set")
    import psycopg
    from services.control_plane.bff.agora.strategy_workshop.store import PostgresWorkshopStore

    schema = f"agora_ws_recon_{uuid.uuid4().hex[:12]}"

    def cleanup_schema() -> None:
        with psycopg.connect(dsn) as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')

    request.addfinalizer(cleanup_schema)
    store = PostgresWorkshopStore(dsn=dsn, schema=schema)
    workshop_id = _new_session(store, workshop_id=f"ws-{uuid.uuid4().hex}")["workshop_id"]
    card_id = reconstruction_card_id(workshop_id)
    base = {"card_id": card_id, "card_type": "strategy_reconstruction", "workshop_id": workshop_id,
            "status": "running", "title": "t"}
    assert store.compare_and_set_workshop_card({**base, "payload": {"job_id": "a"}}, expected_job_id=None)
    assert store.compare_and_set_workshop_card({**base, "payload": {"job_id": "b"}}, expected_job_id=None) is None
    assert store.compare_and_set_workshop_card({**base, "payload": {"job_id": "b"}}, expected_job_id="x") is None
    assert store.compare_and_set_workshop_card({**base, "payload": {"job_id": "b"}}, expected_job_id="a")
    [card] = store.list_workshop_cards(workshop_id)
    assert card["payload"]["job_id"] == "b"

    _post_message(store, workshop_id, "Momentum on BTC.")
    first = admit_reconstruction_job(store=store, **_scope(workshop_id))
    assert first["admitted"]
    assert not admit_reconstruction_job(store=store, **_scope(workshop_id))["admitted"]
    done = run_reconstruction_job(store=store, canonical=_NoRegistryOperations(),
                                  job_id=first["job_id"], **_scope(workshop_id))
    assert done["job_status"] == "completed" and len(provider_calls) == 1
    restarted = PostgresWorkshopStore(dsn=dsn, schema=schema)
    replay = run_reconstruction_worker(store=restarted, canonical=_NoRegistryOperations(), **_scope(workshop_id))
    assert replay["job_status"] == "replayed" and len(provider_calls) == 1


def test_legacy_failed_card_without_a_job_is_re_admitted_once(provider_calls) -> None:
    """Hosted walkthrough workshops hold a pre-job failed card (no job_id, no
    reason); it must not be replayed as a permanent failure."""
    from services.control_plane.bff.agora.strategy_workshop.semantic_provider import ENGINE_VERSION

    store = MemoryWorkshopStore()
    workshop_id = _new_session(store)["workshop_id"]
    _post_message(store, workshop_id, "Momentum on BTC.")
    store.record_workshop_card({
        "card_id": reconstruction_card_id(workshop_id), "card_type": "strategy_reconstruction",
        "workshop_id": workshop_id, "status": "failed", "title": "Semantic reconstruction unavailable",
        "payload": {"based_on_sequence_no": 1, "job_status": "failed", "engine_version": ENGINE_VERSION},
    })
    outcome = run_reconstruction_worker(store=store, canonical=_NoRegistryOperations(), **_scope(workshop_id))
    assert outcome["job_status"] == "completed" and len(provider_calls) == 1
    assert run_reconstruction_worker(store=store, canonical=_NoRegistryOperations(),
                                     **_scope(workshop_id))["job_status"] == "replayed"
    assert len(provider_calls) == 1
