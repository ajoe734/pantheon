"""BFF-MGMT-READ-DEFECT-REPAIR-001 regression coverage.

Covers two of the three repaired defects against the *real* production
composition (``services.control_plane.bff.main``'s already-assembled
``app``/``persona_service``/``run_management_read`` wiring, imported under
its production-qualified module name -- not a second app, router, or
test-only reimplementation):

* Defect 1 -- ``management_read_models/service.py::ManagementService.get_human_inbox``
  dropped every promotion-review record it fetched (fetch, derive a status
  string, never emit items). Durable ``QuarterlyRankingRecommendationSubmit``
  command-log submissions now surface as ``promotion_review`` items on
  ``/bff/management/human-inbox`` via the same canonical
  ``governance/human_inbox.py`` projection helpers the Management NL
  assistant surface already used (no second implementation).

* Defect 2 -- ``core/app_factory.py`` composed ``create_management_router``
  with an *unbounded* ``run_management_read`` (capacity=None ->
  unlimited ``asyncio.to_thread`` fan-out). Production now binds named,
  bounded capacity pools (``_HUMAN_INBOX_READ_SLOTS`` /
  ``_MANAGEMENT_COCKPIT_READ_SLOTS`` / ``_MANAGEMENT_READ_DEFAULT_SLOTS``)
  at composition time in ``main.py``, dispatched by the offloaded
  callable's name so one surface's saturation/timeout cannot silently
  starve or block another.

Note on scope: the task brief's acceptance criteria also describe a
finer-grained per-contributor timeout/capacity story (a `persona_readiness`
surface independently timing out with `meta.partial=True` while sibling
surfaces stay populated). Tracing the real `/bff/management/human-inbox`
route (management_read_models/service.py::get_human_inbox) shows each of
its six contributor blocks (approvals, governance reviews, interventions,
sentinel findings, persona readiness, promotion reviews) reads directly
from `store`/the command log inline, with no per-contributor async
offload, timeout, or `meta.partial` computation at all -- only the whole
`get_human_inbox` call is offloaded/bounded as a single unit. Building
genuine per-contributor bounded/timeout semantics (and a `meta.partial`
field) does not exist today and is a materially larger feature than
"pass capacity/executor into an existing call"; it is out of scope for
this repair pass. This file instead proves defect 2's actual fix -- a real,
composed, named capacity bound -- at the level that exists: the whole-route
`run_management_read` offload.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Iterator, Tuple

from fastapi.testclient import TestClient

# Package-qualified import (not `sys.path.insert` + bare `import main`):
# this suite must exercise the same module identity production's
# `uvicorn services.control_plane.bff.main:app` uses, so that
# composition-root lookups by qualified name (e.g. core/app_factory.py's
# `_dep()`, and `governance/human_inbox.py`'s `from ..main import
# command_store`) resolve to the same live module this test manipulates.
from services.control_plane.bff import main as bff_main
from services.control_plane.bff.command_queue import CommandStore
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
    try:
        with TestClient(bff_main.app, raise_server_exceptions=False) as client:
            yield client, store
    finally:
        bff_main.read_store = original_read_store
        bff_main.command_store = original_command_store
        if persona_service is not None:
            persona_service._read_store = original_ps_read_store
            persona_service._command_store = original_ps_command_store


def _append_submitted_promotion_review(command_store: CommandStore, *, recommendation_id: str, persona_id: str) -> None:
    command_store.submit_command(
        command_id=f"cmd-{recommendation_id}",
        command_type=CommandType.QUARTERLY_RANKING_RECOMMENDATION_SUBMIT,
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


def test_durable_promotion_review_submission_is_not_dropped_from_human_inbox(tmp_path) -> None:
    """Defect 1 regression: a durable QuarterlyRankingRecommendationSubmit
    command must appear as a `promotion_review` item on the real
    `/bff/management/human-inbox` route, not be silently discarded by the
    (fixed) "fetch records, never emit items" promotion-reviews contributor
    block in management_read_models/service.py::get_human_inbox."""
    with _isolated_bff(tmp_path) as (client, _store):
        recommendation_id = "pm12-2026-q3-persona-regression-promote_to_canary_candidate"
        _append_submitted_promotion_review(
            bff_main.command_store,
            recommendation_id=recommendation_id,
            persona_id="persona-regression",
        )

        response = client.get(
            "/bff/management/human-inbox",
            headers=HEADERS,
            params={"source_type": "promotion_review", "page_size": 10},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        items = body["data"]["items"]
        assert len(items) == 1, f"expected the durable submission to appear, got {items!r}"
        item = items[0]
        assert item["promotion_review_id"] == recommendation_id
        assert item["persona_id"] == "persona-regression"
        assert item["source_type"] == "promotion_review"

        surfaces = body["meta"]["surfaces"]
        assert surfaces["promotion_reviews"]["status"] == "ok"
        assert surfaces["promotion_reviews"]["source"] == "command_store"


def test_human_inbox_with_no_submissions_reports_unavailable_not_a_silent_empty_ok() -> None:
    """A read_store that never implements list_promotion_review(s) and an
    empty command log must report `unavailable`, matching every sibling
    contributor block's convention -- not silently claim `status: "ok"` for
    zero items (the original defect: `status: "ok" if records else
    "unavailable"` computed *before* records were ever populated)."""
    from services.control_plane.bff.management_read_models.service import ManagementService

    store = create_read_surface_ports()
    assert not hasattr(store, "list_promotion_reviews")
    assert not hasattr(store, "list_promotion_review_records")

    svc = ManagementService(get_read_store=lambda: store, utc_now=lambda: "2026-01-01T00:00:00Z")
    result = svc.get_human_inbox(source_type="promotion_review", page_size=10)
    assert result["data"]["items"] == []
    assert result["meta"]["surfaces"]["promotion_reviews"]["status"] == "unavailable"


def test_run_management_read_is_bounded_not_the_raw_unbounded_helper() -> None:
    """Defect 2 regression: the real composed app's `run_management_read`
    (what `create_management_router(...)` was actually given at
    `core/app_factory.py` composition time) must not be the raw
    `personas.routes.common.run_management_read` passed straight through --
    that helper's `capacity`/`executor` default to `None`, i.e. an unbounded
    `asyncio.to_thread` fan-out with no concurrency ceiling."""
    assert bff_main.run_management_read is not management_read_common.run_management_read
    for slot_name in (
        "_HUMAN_INBOX_READ_SLOTS",
        "_MANAGEMENT_COCKPIT_READ_SLOTS",
        "_MANAGEMENT_READ_DEFAULT_SLOTS",
    ):
        semaphore = getattr(bff_main, slot_name)
        assert hasattr(semaphore, "acquire") and hasattr(semaphore, "release"), (
            f"bff_main.{slot_name} must be a real bounded semaphore"
        )


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
        monkeypatch.setattr(bff_main, "_HUMAN_INBOX_READ_SLOTS", threading.BoundedSemaphore(1))

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
