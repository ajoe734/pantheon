"""BFF-MGMT-READ-SINGLE-BOUND-20261007 regression coverage.

Verifies the convergence of Management read offloading onto a single bounded
`run_management_read` and the dedicated contributor bound for persona_readiness:

* Single bounded Management read offload:
  `personas/routes/common.py::run_management_read` is the single bounded offload
  point. When `capacity` is None, it uses the shared default pair
  `_MANAGEMENT_READ_DEFAULT_SLOTS` and `_MANAGEMENT_READ_DEFAULT_EXECUTOR`
  (pool of 12, former default 8 plus cockpit 4, thread_name_prefix bff-mgmt-read).
  The unbounded `asyncio.to_thread` branch is deleted, and a saturated pool raises
  `ManagementReadSaturated` before the callable runs. `main.py` no longer defines
  or binds `run_management_read` or dispatch shims; composition resolves
  `personas.routes.common.run_management_read` directly via `core/app_factory.py`.

* Dedicated persona_readiness contributor bound:
  The contributor bound lives in `management_read_models/service.py` with
  `_HUMAN_INBOX_READ_SLOT_COUNT = 4`, `_HUMAN_INBOX_READ_SLOTS`, and
  `_HUMAN_INBOX_READ_EXECUTOR` (thread_name_prefix bff-human-inbox-read).
  `ManagementService._bounded_persona_readiness_rows` runs
  `_build_persona_readiness_items` on this executor within
  `human_inbox_surface_timeout_seconds`. Saturated capacity yields
  `read_capacity_saturated` immediately, timeout yields `read_timeout`,
  and errors fail fast with `contributor_read_error` without unbounded inline
  retries.
"""
from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Iterator, Tuple

import pytest
from fastapi.testclient import TestClient

# Package-qualified import (not `sys.path.insert` + bare `import main`):
# this suite must exercise the same module identity production's
# `uvicorn services.control_plane.bff.main:app` uses, so that
# composition-root lookups by qualified name (e.g. core/app_factory.py's
# `_dep()`, and `governance/human_inbox.py`'s `from ..main import
# command_store`) resolve to the same live module this test manipulates.
from services.control_plane.bff import main as bff_main
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.core.app_factory import _resolve_default_dependency
from services.control_plane.bff.models import CommandType, ObjectType, TargetObject
from services.control_plane.bff.personas.routes import common as management_read_common
from services.control_plane.bff.ports import ReadSurfacePorts, create_read_surface_ports

HEADERS = {"Authorization": "Bearer op-mgmt-cap-001:operator,admin:mfa"}


@contextmanager
def _isolated_bff(tmp_path) -> Iterator[Tuple[TestClient, ReadSurfacePorts]]:
    """Isolated BFF app on the real composed ``bff_main.app``/``persona_service``.

    Mirrors the wiring `test_bff_promotion_review_governance.py::_isolated_client`
    uses: swap `bff_main.read_store`/`command_store`, *and* the already-composed
    `persona_service`'s own injected `_read_store`/`_command_store` (it is
    built once at import time with a fixed reference, so swapping only the
    module attribute is not enough to redirect persona-service-backed reads).
    """
    original_read_store = bff_main.read_store
    original_command_store = bff_main.command_store
    store = create_read_surface_ports()
    command_store = CommandStore(str(tmp_path / "commands.jsonl"))
    bff_main.read_store = store
    bff_main.command_store = command_store

    persona_service = bff_main.persona_service
    original_ps_read_store = None
    original_ps_command_store = None
    if persona_service is not None:
        original_ps_read_store = persona_service._read_store
        original_ps_command_store = persona_service._command_store
        persona_service._read_store = store
        persona_service._command_store = command_store

    from services.control_plane.bff.core import owner_reads
    original_list_decisions = owner_reads.approval_owner.list_decisions
    owner_reads.approval_owner.list_decisions = lambda *args, **kwargs: []
    try:
        with TestClient(bff_main.app, raise_server_exceptions=False) as client:
            yield client, store
    finally:
        owner_reads.approval_owner.list_decisions = original_list_decisions
        bff_main.read_store = original_read_store
        bff_main.command_store = original_command_store
        if persona_service is not None:
            persona_service._read_store = original_ps_read_store
            persona_service._command_store = original_ps_command_store


def _append_submitted_promotion_review(command_store: CommandStore, *, recommendation_id: str, persona_id: str) -> None:
    command_store.submit_command(
        command_id=f"cmd-{recommendation_id}",
        command_type="QuarterlyRankingRecommendationSubmit",
        target=TargetObject(type=ObjectType.RANKING, id=recommendation_id),
        submitted_at="2026-07-13T00:00:00Z",
        params={
            "quarter": "2026-Q3",
            "review_id": recommendation_id,
            "promotion_review_id": recommendation_id,
            "recommendation_id": recommendation_id,
            "recommendationId": recommendation_id,
            "recommendation_action_id": "promote_to_canary_candidate",
            "recommendationActionId": "promote_to_canary_candidate",
            "persona_id": persona_id,
            "stage_from": "paper",
            "stage_to": "canary_candidate",
            "review_kind": "paper_to_canary_review",
            "requires_human_gate_decision": True,
            "live_capital_mutation": False,
            "direct_live_capital_mutation": False,
            "runtime_mutation": False,
        },
        audit_context={"operator_id": "op-mgmt-cap-001", "reason": "BFF-MGMT-READ-DEFECT-REPAIR-001 regression"},
    )


def test_command_log_promotion_review_rows_are_not_projected_into_human_inbox(tmp_path) -> None:
    """The promotion_review inbox contributor is retired: live proposals are
    approval items, and legacy command-log rows are no longer projected."""
    with _isolated_bff(tmp_path) as (client, _store):
        _append_submitted_promotion_review(
            bff_main.command_store,
            recommendation_id="pm12-2026-q3-persona-regression-promote_to_canary_candidate",
            persona_id="persona-regression",
        )

        response = client.get(
            "/bff/management/human-inbox",
            headers=HEADERS,
            params={"page_size": 10},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert all(item["source_type"] != "promotion_review" for item in body["data"]["items"])
        assert "promotion_reviews" not in body["meta"]["surfaces"]


def test_run_management_read_is_bounded_not_the_raw_unbounded_helper() -> None:
    """AC 7: Shared pool of 12 slots raises ManagementReadSaturated when full without running callable."""
    assert management_read_common._MANAGEMENT_READ_DEFAULT_SLOT_COUNT == 12
    assert not hasattr(bff_main, "run_management_read")
    assert (
        _resolve_default_dependency("run_management_read", None)
        is management_read_common.run_management_read
    )

    semaphore = management_read_common._MANAGEMENT_READ_DEFAULT_SLOTS
    acquired = 0
    try:
        for _ in range(management_read_common._MANAGEMENT_READ_DEFAULT_SLOT_COUNT):
            assert semaphore.acquire(timeout=5)
            acquired += 1

        called = False

        def dummy_read() -> str:
            nonlocal called
            called = True
            return "ok"

        with pytest.raises(management_read_common.ManagementReadSaturated):
            asyncio.run(management_read_common.run_management_read(dummy_read))
        assert not called, "callable must not run when capacity pool is saturated"
    finally:
        for _ in range(acquired):
            semaphore.release()

    result = asyncio.run(management_read_common.run_management_read(dummy_read))
    assert result == "ok"
    assert called


def test_human_inbox_persona_readiness_error_fails_fast_without_retry(
    tmp_path, monkeypatch
) -> None:
    """AC 8: A store list_personas that raises causes persona_readiness to degrade
    with contributor_read_error without retry, called with include_market_persona_defaults=True."""
    calls = []

    def failing_list_personas(*args, **kwargs):
        calls.append((args, kwargs))
        raise RuntimeError("simulated store failure")

    with _isolated_bff(tmp_path) as (client, store):
        monkeypatch.setattr(store, "list_personas", failing_list_personas)

        response = client.get(
            "/bff/management/human-inbox",
            headers=HEADERS,
            params={"page_size": 10},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert len(calls) == 1, f"expected exactly 1 call, got {len(calls)}"
        assert calls[0][1].get("include_market_persona_defaults") is True
        surfaces = body["meta"]["surfaces"]
        assert surfaces["persona_readiness"]["reason"] == "contributor_read_error"


def test_human_inbox_capacity_saturation_yields_immediate_degraded_response_and_recovers(
    tmp_path, monkeypatch
) -> None:
    """Occupying the real, composed `_HUMAN_INBOX_READ_SLOTS` capacity pool
    (size 1) with one in-flight human-inbox read must make a concurrent
    second request fail fast with a degraded envelope (HTTP 200, not a hang
    or a 500) instead of queueing behind the unbounded default -- proving
    the bound from defect 2 is enforced by the real composition, not by
    test-added wiring. After the first read releases, a fresh request must
    recover to a normal (non-degraded) response."""
    with _isolated_bff(tmp_path) as (client, store):
        from services.control_plane.bff.management_read_models import service as mgmt_service
        monkeypatch.setattr(mgmt_service, "_HUMAN_INBOX_READ_SLOTS", threading.BoundedSemaphore(1))

        release = threading.Event()
        entered = threading.Event()

        def slow_list_personas(*_args, **_kwargs):
            entered.set()
            release.wait(timeout=5)
            return []

        monkeypatch.setattr(store, "list_personas", slow_list_personas)

        with ThreadPoolExecutor(max_workers=2) as pool:
            first_future = pool.submit(client.get, "/bff/management/human-inbox", headers=HEADERS)
            assert entered.wait(timeout=2), "first request never entered the blocked read"

            started_at = time.monotonic()
            second_response = client.get("/bff/management/human-inbox", headers=HEADERS)
            second_elapsed = time.monotonic() - started_at

            release.set()
            first_response = first_future.result(timeout=5)

        assert first_response.status_code == 200, first_response.text
        assert second_response.status_code == 200, second_response.text
        assert second_elapsed < 1.0, (
            f"a saturated read took {second_elapsed:.3f}s to fail fast; "
            "capacity saturation must reject immediately, not queue"
        )
        degraded_surface = second_response.json()["meta"]["surfaces"]["human_inbox"]
        assert degraded_surface["status"] == "degraded"

        # Recovery: capacity was released, so a subsequent request must not
        # be degraded.
        recovered = client.get("/bff/management/human-inbox", headers=HEADERS)
        assert recovered.status_code == 200, recovered.text
        recovered_surfaces = recovered.json()["meta"]["surfaces"]
        assert recovered_surfaces.get("human_inbox", {}).get("status") != "degraded"


def test_human_inbox_contributor_carries_caller_auth_context_and_projects_live_approvals(
    tmp_path, monkeypatch
) -> None:
    """Regression test for B4 / BFF-HUMAN-INBOX-AUTH-CONTEXT-20261007:
    The Human Inbox contributor thread executor must run with a copy of the
    caller's contextvars context so that owner reads see the request's
    caller authorization. Without contextvars.copy_context(), authorization.get()
    is None in contributor worker threads, causing approval_queue and
    governance_review_queue to fail and degrade.
    """
    from services.control_plane.bff.core import owner_reads

    captured_call_auth = []
    captured_context_var_auth = []
    captured_thread_names = []

    def stub_list_decisions(authorization: str | None = None, **_kwargs) -> list[dict]:
        captured_call_auth.append(authorization)
        captured_context_var_auth.append(owner_reads.authorization.get())
        captured_thread_names.append(threading.current_thread().name)
        return [
            {
                "id": "decision-canary-001",
                "decision_id": "decision-canary-001",
                "target_type": "canary_promotion",
                "target_id": "persona-alpha",
                "decision_state": "proposed",
                "status": "pending",
                "risk_level": "medium",
                "created_at": "2026-10-07T00:00:00Z",
                "submitted_at": "2026-10-07T00:00:00Z",
                "actor_id": "op-mgmt-cap-001",
                "rationale": "Promotion proposal for persona-alpha",
            }
        ]

    with _isolated_bff(tmp_path) as (client, _store):
        monkeypatch.setattr(owner_reads.approval_owner, "list_decisions", stub_list_decisions)

        response = client.get(
            "/bff/management/human-inbox",
            headers=HEADERS,
            params={"page_size": 10},
        )

        assert response.status_code == 200, response.text
        body = response.json()

        # 1. Contributor thread sees the request authorization both in the arg and contextvar
        assert len(captured_call_auth) > 0, "approval_owner.list_decisions was not called"
        for auth in captured_call_auth:
            assert auth == HEADERS["Authorization"]
        for cvar_auth in captured_context_var_auth:
            assert cvar_auth == HEADERS["Authorization"]
        assert any(
            name.startswith("mgmt_human_inbox_contributor") for name in captured_thread_names
        ), f"expected contributor thread name, got {captured_thread_names}"

        # 2. Live approval proposals appear in the human inbox
        items = body["data"]["items"]
        approval_items = [it for it in items if it.get("source_type") == "approval"]
        assert len(approval_items) >= 1, f"expected live approval proposal in items: {items}"
        assert approval_items[0]["decision_id"] == "decision-canary-001"
        assert approval_items[0]["status"] == "proposed"

        # 3. Contributing surface status is ok (not degraded)
        surfaces = body["meta"]["surfaces"]
        assert surfaces.get("approval_queue", {}).get("status") == "ok"

