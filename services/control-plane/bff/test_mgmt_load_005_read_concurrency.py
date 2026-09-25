"""MGMT-LOAD-005 contract tests for BFF read concurrency isolation.

Verifies that slow, synchronous management read aggregation cannot block
the asyncio event loop and delay unrelated routes such as ``/health``, and
that a read exceeding its timeout budget returns an explicit degraded
envelope instead of hanging, for the routes that carry the MGMT-LOAD-005
``run_management_read`` isolation wrapper. Jobs has an isolated router and
is covered here through its narrow read port.

Each router under test is built standalone from the real extracted router
factories (``jobs.router.create_jobs_router``, ``incidents.router
.create_incident_router``, ``management_read_models.router
.create_management_router``, ``governance.router.create_governance_router``,
``core.app_factory.create_core_router`` for ``/health``) against a
test-owned ``ReadSurfacePorts`` store, instead of driving the composed
``bff_main.app`` monolith. The isolation wrapper itself
(``personas.routes.common.run_management_read``, a bounded
``asyncio.to_thread`` dispatch) is the real production implementation, not
a reimplementation.

``/bff/alerts`` (via ``incidents.router.create_incident_router``),
``/bff/management/evidence`` and ``/bff/management/human-inbox`` (via
``management_read_models.router.create_management_router``'s
``run_management_read`` parameter, wired below in ``_build_app``), and
``/bff/approvals`` (via ``governance.router.create_governance_router``,
which defaults ``run_management_read`` to the real
``personas.routes.common.run_management_read`` wrapper when the caller does
not override it) are all wired with the real MGMT-LOAD-005
``run_management_read`` isolation/timeout wrapper. A slow read on these
routes therefore either completes within the wait budget (asserted by the
"slow read completes" sub-tests below) or degrades to an explicit envelope
once it exceeds the budget (asserted by the "timeout returns degraded
envelope" sub-tests below), the same MGMT-LOAD-005 contract as ``/bff/alerts``.
"""
from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Iterator
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("PANTHEON_BFF_AUTH_STUB", "true")

from services.control_plane.bff.core.app_factory import create_core_router
from services.control_plane.bff.core.errors import register_error_handlers
from services.control_plane.bff.governance.router import (
    _default_dataset_surface_status,
    create_governance_router,
)
from services.control_plane.bff.incidents.router import (
    _default_page_slice,
    _default_raise_if_read_surface_unavailable,
    _default_read_surface_meta,
    create_incident_router,
)
from services.control_plane.bff.jobs.router import create_jobs_router
from services.control_plane.bff.management_read_models.router import (
    _default_bff_error,
    _default_extract_identity,
    _default_require_read_role,
    _default_snapshot_meta,
    _utc_now_rfc3339,
    create_management_router,
)
from services.control_plane.bff.personas.routes.common import (
    run_management_read as _real_run_management_read,
)
from services.control_plane.bff.ports import ReadSurfacePorts, create_read_surface_ports


HEADERS = {"Authorization": "Bearer op-mgmt-load-005:operator,admin:mfa"}


def _build_app(
    store: ReadSurfacePorts,
    *,
    build_operator_alerts_payload=None,
    run_management_read=None,
) -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(create_core_router({}))
    app.include_router(
        create_management_router(
            read_surface=store,
            extract_identity=_default_extract_identity,
            require_read_role=_default_require_read_role,
            snapshot_meta=_default_snapshot_meta,
            utc_now=_utc_now_rfc3339,
            bff_error=_default_bff_error,
            run_management_read=run_management_read,
        )
    )
    app.include_router(
        create_governance_router(
            read_surface=store,
            extract_identity=_default_extract_identity,
            require_read_role=_default_require_read_role,
            utc_now=_utc_now_rfc3339,
            bff_error=_default_bff_error,
        )
    )
    app.include_router(
        create_incident_router(
            read_surface=store,
            extract_identity=_default_extract_identity,
            require_read_role=_default_require_read_role,
            require_operator_role=_default_require_read_role,
            bff_error=_default_bff_error,
            utc_now=_utc_now_rfc3339,
            page_slice=_default_page_slice,
            read_surface_meta=_default_read_surface_meta,
            dataset_surface_status=_default_dataset_surface_status,
            raise_if_read_surface_unavailable=_default_raise_if_read_surface_unavailable,
            run_management_read=run_management_read,
            build_operator_alerts_payload=build_operator_alerts_payload,
        )
    )
    app.include_router(
        create_jobs_router(
            read_surface=store,
            extract_identity=_default_extract_identity,
            require_read_role=_default_require_read_role,
            bff_error=_default_bff_error,
            utc_now=_utc_now_rfc3339,
            page_slice=_default_page_slice,
            read_surface_meta=_default_read_surface_meta,
            dataset_surface_status=_default_dataset_surface_status,
            raise_if_read_surface_unavailable=_default_raise_if_read_surface_unavailable,
            reject_body_idempotency_key=lambda payload: None,
            resolve_final_idempotency_key=lambda header_key, body_key: header_key or body_key or "idem-key",
            submit_job_action=lambda job_id, action_id, resolved_key, identity, payload: {},
        )
    )
    return app


@contextmanager
def _isolated_bff(
    *,
    build_operator_alerts_payload=None,
    run_management_read=_real_run_management_read,
) -> Iterator[tuple[TestClient, ReadSurfacePorts]]:
    """Standalone app on a *persistent* TestClient portal (single event loop).

    Using TestClient as a context manager keeps one event loop alive for the
    whole `with` block instead of spinning up a fresh portal/event loop per
    request. That persistent-loop shape is what a real long-lived uvicorn
    worker looks like, and it is required for these concurrency tests: a
    bare `TestClient(app).get(...)` call (no `with`) creates and tears down
    its own event loop per call, which would make two "concurrent" requests
    run on two unrelated event loops and silently defeat the point of the
    test (see MGMT-LOAD-005 read-isolation fix in main.py).
    """
    store = create_read_surface_ports()
    store.list_jobs_bff = lambda **_kwargs: []
    app = _build_app(
        store,
        build_operator_alerts_payload=build_operator_alerts_payload,
        run_management_read=run_management_read,
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, store


def test_jobs_router_uses_narrow_port_without_delaying_health() -> None:
    with _isolated_bff() as (client, store):
        store.list_jobs_bff = lambda **_kwargs: []
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs_future = pool.submit(client.get, "/bff/jobs", headers=HEADERS)
            time.sleep(0.05)
            health_started = time.monotonic()
            health_response = client.get("/health")
            health_elapsed = time.monotonic() - health_started
            jobs_response = jobs_future.result()

    assert health_response.status_code == 200
    assert health_elapsed < 0.3, f"/health took {health_elapsed:.3f}s while a slow jobs read was in flight"
    assert jobs_response.status_code == 200


def test_jobs_router_returns_narrow_port_records_without_legacy_reader() -> None:
    with _isolated_bff() as (client, store):
        store.list_jobs_bff = lambda **_kwargs: [{"job_id": "job-narrow-port", "status": "running"}]
        started = time.monotonic()
        response = client.get("/bff/jobs", headers=HEADERS)
        elapsed = time.monotonic() - started

    assert response.status_code == 200, response.text
    assert elapsed < 0.25, f"jobs route took {elapsed:.3f}s with an immediate narrow-port double"
    payload = response.json()
    assert payload["data"] == [{"job_id": "job-narrow-port", "status": "running"}]
    surface = payload["meta"]["surfaces"].get("jobs") or payload["meta"]["surfaces"].get("job_list")
    assert surface is not None
    assert surface.get("reason") != "read_timeout"


def test_health_stays_fast_while_alerts_read_is_slow() -> None:
    """A slow, isolation-wrapped Alerts aggregation must not delay a concurrent /health request."""

    def slow_alerts_payload(snapshot_at: str):
        time.sleep(0.5)
        return {"alerts": [], "summary": {}, "meta": {"surfaces": {"alerts": {"status": "ok"}}}}

    with _isolated_bff(build_operator_alerts_payload=slow_alerts_payload) as (client, _store):
        with ThreadPoolExecutor(max_workers=2) as pool:
            alerts_future = pool.submit(client.get, "/bff/alerts", headers=HEADERS)
            time.sleep(0.05)  # let the slow alerts request start first
            health_started = time.monotonic()
            health_response = client.get("/health")
            health_elapsed = time.monotonic() - health_started
            alerts_response = alerts_future.result()

    assert health_response.status_code == 200
    assert health_elapsed < 0.3, (
        f"/health took {health_elapsed:.3f}s while a slow, isolation-wrapped Alerts read was in "
        "flight; the event loop must not be blocked by unrelated synchronous read work"
    )
    assert alerts_response.status_code == 200


def test_alerts_timeout_returns_degraded_envelope_without_hanging() -> None:
    def slow_alerts_payload(snapshot_at: str):
        time.sleep(0.6)
        return {"alerts": [{"alert_id": "should-not-appear"}], "summary": {}, "meta": {}}

    with patch.dict(os.environ, {"PANTHEON_BFF_MANAGEMENT_READ_TIMEOUT_SECONDS": "0.05"}):
        with _isolated_bff(build_operator_alerts_payload=slow_alerts_payload) as (client, _store):
            started = time.monotonic()
            response = client.get("/bff/alerts", headers=HEADERS)
            elapsed = time.monotonic() - started

    assert response.status_code == 200, response.text
    assert elapsed < 0.4, f"alerts route took {elapsed:.3f}s; it should degrade near the timeout budget, not wait for the full 0.6s slow read"
    payload = response.json()
    assert payload["alerts"] == []
    surface = payload["meta"]["surfaces"]["alerts"]
    assert surface["status"] == "degraded"
    assert surface["source"] == "timeout_fallback"


def test_evidence_returns_normal_payload_when_fast() -> None:
    """Sanity check: the Evidence route returns the real payload for a fast read."""
    with _isolated_bff() as (client, _store):
        response = client.get("/bff/management/evidence", headers=HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert "management_evidence" in payload["meta"]["surfaces"]
    assert payload["meta"]["surfaces"]["management_evidence"].get("reason") != "read_timeout"


def test_evidence_slow_read_completes_without_hanging() -> None:
    """The Evidence route (management_read_models.router) carries the real
    MGMT-LOAD-005 isolation wrapper via ``run_management_read`` (see module
    docstring and ``_build_app``): a slow store read within the wait budget
    (default 0.6s) is offloaded to a worker thread and still returns the
    real payload once it completes, rather than degrading early.
    """
    with _isolated_bff() as (client, store):
        def slow_list_evidence_refs():
            time.sleep(0.2)
            return []

        store.list_evidence_refs = slow_list_evidence_refs
        started = time.monotonic()
        response = client.get("/bff/management/evidence", headers=HEADERS)
        elapsed = time.monotonic() - started

    assert response.status_code == 200, response.text
    assert elapsed >= 0.2
    assert elapsed < 1.0, f"evidence route took {elapsed:.3f}s; it should still complete promptly after the slow read"
    payload = response.json()
    assert payload["meta"]["surfaces"]["management_evidence"].get("reason") != "read_timeout"


def test_evidence_timeout_returns_degraded_envelope_without_hanging() -> None:
    """A slow Evidence read that exceeds the wait budget must degrade to an
    explicit timeout envelope instead of hanging, the same MGMT-LOAD-005
    contract already covered for ``/bff/alerts`` above.
    """

    def slow_list_evidence_refs():
        time.sleep(0.6)
        return [{"ref_id": "should-not-appear"}]

    with patch.dict(os.environ, {"PANTHEON_BFF_MANAGEMENT_READ_TIMEOUT_SECONDS": "0.05"}):
        with _isolated_bff() as (client, store):
            store.list_evidence_refs = slow_list_evidence_refs
            started = time.monotonic()
            response = client.get("/bff/management/evidence", headers=HEADERS)
            elapsed = time.monotonic() - started

    assert response.status_code == 200, response.text
    assert elapsed < 0.4, f"evidence route took {elapsed:.3f}s; it should degrade near the timeout budget"
    payload = response.json()
    surface = payload["meta"]["surfaces"]["management_evidence"]
    assert surface["status"] == "degraded"
    assert surface["reason"] == "read_timeout"


def test_approvals_slow_read_completes_without_hanging() -> None:
    """``/bff/approvals`` (governance.router) carries the real MGMT-LOAD-005
    isolation wrapper: ``create_governance_router`` defaults
    ``run_management_read`` to the real wrapper when the caller does not
    override it, so a slow store read within the wait budget is offloaded
    to a worker thread and the real (non-degraded) pending items are
    returned once it completes.
    """

    def slow_list_approval_queue_items(**_kwargs):
        time.sleep(0.2)
        return [{"decision_id": "should-appear", "decision_state": "pending"}]

    with _isolated_bff() as (client, store):
        store.list_approval_queue_items = slow_list_approval_queue_items
        started = time.monotonic()
        response = client.get("/bff/approvals", headers=HEADERS)
        elapsed = time.monotonic() - started

    assert response.status_code == 200, response.text
    assert elapsed >= 0.2
    assert elapsed < 1.0, f"approvals route took {elapsed:.3f}s; it should still complete promptly after the slow read"
    payload = response.json()
    assert payload["items"] == [{"decision_id": "should-appear", "decision_state": "pending"}]
    assert payload["count"] == 1


def test_approvals_timeout_returns_degraded_envelope_without_hanging() -> None:
    """A slow Approvals read that exceeds the wait budget must degrade to an
    explicit timeout envelope instead of hanging or returning stale data.
    """

    def slow_list_approval_queue_items(**_kwargs):
        time.sleep(0.6)
        return [{"decision_id": "should-not-appear", "decision_state": "pending"}]

    with patch.dict(os.environ, {"PANTHEON_BFF_MANAGEMENT_READ_TIMEOUT_SECONDS": "0.05"}):
        with _isolated_bff() as (client, store):
            store.list_approval_queue_items = slow_list_approval_queue_items
            started = time.monotonic()
            response = client.get("/bff/approvals", headers=HEADERS)
            elapsed = time.monotonic() - started

    assert response.status_code == 200, response.text
    assert elapsed < 0.4, f"approvals route took {elapsed:.3f}s; it should degrade near the timeout budget"
    payload = response.json()
    assert payload["items"] == []
    assert payload["count"] == 0
    surface = payload["meta"]["surfaces"]["approvals"]
    assert surface["status"] == "degraded"
    assert surface["reason"] == "read_timeout"


def test_human_inbox_returns_normal_payload_when_fast() -> None:
    with _isolated_bff() as (client, _store):
        response = client.get("/bff/management/human-inbox", headers=HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert "human_inbox" in payload["meta"]["surfaces"]
    assert payload["meta"]["surfaces"]["human_inbox"].get("reason") != "read_timeout"


def test_human_inbox_slow_read_completes_without_hanging() -> None:
    """``/bff/management/human-inbox`` (management_read_models.router)
    carries the real MGMT-LOAD-005 isolation wrapper via
    ``run_management_read`` (see module docstring and ``_build_app``): a
    slow governance review queue read within the wait budget is offloaded
    to a worker thread and the real payload is returned once it completes.
    """

    def slow_list_governance_review_queue_items(**_kwargs):
        time.sleep(0.2)
        return [{"item_id": "review-should-appear", "item_type": "DeploymentPlan"}]

    with _isolated_bff() as (client, store):
        store.list_governance_review_queue_items = slow_list_governance_review_queue_items
        started = time.monotonic()
        response = client.get("/bff/management/human-inbox", headers=HEADERS)
        elapsed = time.monotonic() - started

    assert response.status_code == 200, response.text
    assert elapsed >= 0.2
    assert elapsed < 1.0, f"human-inbox route took {elapsed:.3f}s; it should still complete promptly after the slow read"
    payload = response.json()
    assert any(item.get("item_id") == "review-should-appear" for item in payload["data"]["items"])


def test_human_inbox_timeout_returns_degraded_envelope_without_hanging() -> None:
    """A slow Human Inbox read that exceeds the wait budget must degrade to
    an explicit timeout envelope instead of hanging.
    """

    def slow_list_governance_review_queue_items(**_kwargs):
        time.sleep(0.6)
        return [{"item_id": "should-not-appear", "item_type": "DeploymentPlan"}]

    with patch.dict(os.environ, {"PANTHEON_BFF_MANAGEMENT_READ_TIMEOUT_SECONDS": "0.05"}):
        with _isolated_bff() as (client, store):
            store.list_governance_review_queue_items = slow_list_governance_review_queue_items
            started = time.monotonic()
            response = client.get("/bff/management/human-inbox", headers=HEADERS)
            elapsed = time.monotonic() - started

    assert response.status_code == 200, response.text
    assert elapsed < 0.4, f"human-inbox route took {elapsed:.3f}s; it should degrade near the timeout budget"
    payload = response.json()
    assert payload["data"]["items"] == []
    surface = payload["meta"]["surfaces"]["human_inbox"]
    assert surface["status"] == "degraded"
    assert surface["reason"] == "read_timeout"
