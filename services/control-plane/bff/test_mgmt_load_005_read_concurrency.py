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

``/bff/management/quarterly-ranking``, ``/drilldown``, and
``/recommendations`` (via native ``personas.routes.ranking.build_ranking_router``)
use FastAPI's native synchronous-handler threadpool dispatch (``def`` handlers
for synchronous route bodies) to ensure slow original ranking aggregation
cannot block the persistent event loop or delay unrelated routes like
``/health`` and strict auth rejections (MGMT-RANKING-ORIGINAL-READ-ISOLATION-20261012).

``/bff/management/evolution-journal`` (via native
``evolution.router.create_evolution_router``) uses FastAPI's native
synchronous-handler threadpool dispatch (``def`` handler for synchronous route
body) to ensure slow original evolution decisions / postmortems / freeze orders /
rollbacks aggregation cannot block the persistent event loop or delay unrelated
routes like ``/health`` and strict auth rejections
(BFF-EVOLUTION-JOURNAL-ORIGINAL-READ-ISOLATION-20261017).
"""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Iterator
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("PANTHEON_BFF_AUTH_STUB", "true")

from services.control_plane.bff.ports.research_knowledge_source import EvidenceReadResult
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
from services.control_plane.bff.personas import PersonaService
from services.control_plane.bff.personas.routes.common import (
    run_management_read as _real_run_management_read,
)
from services.control_plane.bff.ports import (
    ReadSurfacePorts,
    create_read_surface_ports,
)


HEADERS = {"Authorization": "Bearer op-mgmt-load-005:operator,admin:mfa"}


class _EmptyCommandLog:
    """No-op command log for a router build with no CommandStore under test.

    Without this, ManagementService's promotion_reviews contributor
    (BFF-MGMT-READ-DEFECT-REPAIR-001 defect 1) falls back to a lazy
    ``from ..main import command_store`` the first time any test in this
    file hits it, which -- because this file deliberately builds its app
    from router factories rather than importing ``main.py`` -- pays a
    one-time full ``main.py`` import/composition cost inside the read's
    timed budget instead of at process/module load time, producing a
    spurious ``read_timeout`` unrelated to this file's actual MGMT-LOAD-005
    concurrency contract. An explicit (empty) command log sidesteps the
    fallback entirely, matching how the real composition root
    (``core/app_factory.py``) and the promotion-review test file already
    wire an explicit command store.
    """

    def _get_all_commands(self) -> list:
        return []


def _tenant_a_identity(authorization=None, *args, **kwargs):
    identity = _default_extract_identity(authorization, *args, **kwargs)
    identity.claims = {"tenant_id": "tenant-a"}
    return identity


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
            get_command_store=lambda: _EmptyCommandLog(),
        )
    )
    app.include_router(
        create_governance_router(
            read_surface=store,
            extract_identity=_tenant_a_identity,
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
            resolve_final_idempotency_key=lambda header_key: header_key or "idem-key",
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

    with patch.dict(os.environ, {"PANTHEON_BFF_ALERTS_READ_TIMEOUT_SECONDS": "0.05"}):
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
    assert surface["reason"] == "read_timeout"


def test_alerts_budget_default_is_named_and_not_generic_0_6s() -> None:
    from services.control_plane.bff.incidents import router as incidents_router

    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("PANTHEON_BFF_ALERTS_READ_TIMEOUT_SECONDS", None)
        assert incidents_router._get_alerts_read_timeout() == 3.0
    with patch.dict(os.environ, {"PANTHEON_BFF_ALERTS_READ_TIMEOUT_SECONDS": "1.25"}):
        assert incidents_router._get_alerts_read_timeout() == 1.25
    # a 1s read now completes under the alerts budget while the generic 0.6s would not
    def one_second_payload(snapshot_at: str):
        time.sleep(0.8)
        return {"alerts": [{"alert_id": "kept"}], "summary": {}, "meta": {}}

    with _isolated_bff(build_operator_alerts_payload=one_second_payload) as (client, _store):
        response = client.get("/bff/alerts", headers=HEADERS)
    assert response.json()["alerts"] == [{"alert_id": "kept"}]


def test_alerts_fallback_reason_matches_failure_class(caplog) -> None:
    import logging

    def boom(snapshot_at: str):
        raise RuntimeError("owner exploded")

    with _isolated_bff(build_operator_alerts_payload=boom) as (client, _store):
        with caplog.at_level(logging.WARNING):
            response = client.get("/bff/alerts", headers=HEADERS)
    surface = response.json()["meta"]["surfaces"]["alerts"]
    assert response.status_code == 200
    assert surface["source"] == "timeout_fallback"
    assert surface["reason"] == "read_error"
    assert sum("bff.alerts degraded fallback: read_error (RuntimeError)" in r.getMessage() for r in caplog.records) == 1

    import threading
    from services.control_plane.bff.personas.routes import common

    class _Full(threading.BoundedSemaphore):
        def acquire(self, blocking=True, timeout=None):
            return False

    def fast(snapshot_at: str):
        return {"alerts": [], "summary": {}, "meta": {}}

    saturated = _Full(1)
    original = common._MANAGEMENT_READ_DEFAULT_SLOTS
    common._MANAGEMENT_READ_DEFAULT_SLOTS = saturated
    try:
        with _isolated_bff(build_operator_alerts_payload=fast) as (client, _store):
            response = client.get("/bff/alerts", headers=HEADERS)
    finally:
        common._MANAGEMENT_READ_DEFAULT_SLOTS = original
    surface = response.json()["meta"]["surfaces"]["alerts"]
    assert response.status_code == 200
    assert surface["source"] == "timeout_fallback"
    assert surface["reason"] == "read_saturated"


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
        def slow_read_evidence_refs(**_kwargs):
            # /bff/management/evidence now reads via the per-read
            # ``read_evidence_refs`` (EVIDENCE-OWNER-LIST-BOUNDED-20261104).
            time.sleep(0.2)
            return EvidenceReadResult([], 0, "ok", "typed_store")

        store.read_evidence_refs = slow_read_evidence_refs
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

    def slow_read_evidence_refs(**_kwargs):
        time.sleep(0.6)
        return EvidenceReadResult([{"ref_id": "should-not-appear"}], 1, "ok", "typed_store")

    with patch.dict(os.environ, {"PANTHEON_BFF_MANAGEMENT_READ_TIMEOUT_SECONDS": "0.05"}):
        with _isolated_bff() as (client, store):
            store.read_evidence_refs = slow_read_evidence_refs
            started = time.monotonic()
            response = client.get("/bff/management/evidence", headers=HEADERS)
            elapsed = time.monotonic() - started

    assert response.status_code == 200, response.text
    assert elapsed < 0.4, f"evidence route took {elapsed:.3f}s; it should degrade near the timeout budget"
    payload = response.json()
    surface = payload["meta"]["surfaces"]["management_evidence"]
    assert surface["status"] == "degraded"
    assert surface["reason"] == "read_timeout"
    assert surface["source"] == "timeout_fallback"
    assert payload["meta"]["surfaces"]["evidence_refs"]["status"] == "unavailable"
    assert payload["meta"]["surfaces"]["evidence_refs"]["reason"] == "read_timeout"
    assert payload["data"]["summary"]["total_evidence"] is None
    assert payload["data"]["summary"]["verified_count"] is None
    assert payload["data"]["summary"]["by_source_type"] is None


def test_evidence_610ms_owner_read_completes_under_evidence_route_budget() -> None:
    """BFF-EVIDENCE-BOUNDED-READ-20261106: a 0.62s bounded owner page + population
    summary read (matching the 491-612ms Dev diagnostic) must complete under the
    default Evidence route budget without tripping the generic 0.6s fallback, while
    concurrent /health remains responsive."""

    def owner_620ms_read(**_kwargs):
        time.sleep(0.62)
        items = [
            {
                "id": f"evi-{i:016x}",
                "ref_id": f"evi-{i:016x}",
                "evidence_type": "source_record",
                "display_label": f"TWSE 0050 #{i}",
                "source_document": {
                    "title": f"TWSE 0050 #{i}",
                    "source_type": "official_reference",
                    "source_ref": f"tw-official://0050/{i}",
                    "captured_at": "2026-11-05T14:30:00+08:00",
                },
                "link_type": "supporting",
                "credibility": {"tier": "verified", "verified": True},
                "resolved_link": {"route_href": None},
                "linked_object_summary": None,
            }
            for i in range(20)
        ]
        return EvidenceReadResult(
            items,
            518,
            "ok",
            "service_client",
            next_page_token="20",
            summary={
                "total": 518,
                "verified_count": 518,
                "verified_evidence": 518,
                "by_source_type": {"official_reference": 510, "source_record": 8},
                "by_link_type": {"supporting": 518},
                "by_credibility_tier": {"verified": 518},
            },
        )

    with _isolated_bff() as (client, store):
        store.read_evidence_refs = owner_620ms_read
        with ThreadPoolExecutor(max_workers=2) as pool:
            ev_future = pool.submit(client.get, "/bff/management/evidence?page_size=20", headers=HEADERS)
            time.sleep(0.05)
            health_started = time.monotonic()
            health_resp = client.get("/health")
            health_elapsed = time.monotonic() - health_started
            ev_resp = ev_future.result()

    assert health_resp.status_code == 200
    assert health_elapsed < 0.3
    assert ev_resp.status_code == 200, ev_resp.text
    payload = ev_resp.json()
    assert payload["page_info"]["total"] == 518
    assert len(payload["data"]["items"]) == 20
    assert payload["data"]["summary"]["total_evidence"] == 518
    assert payload["data"]["summary"]["verified_count"] == 518
    assert payload["data"]["summary"]["by_source_type"] == {"official_reference": 510, "source_record": 8}
    assert payload["meta"]["surfaces"]["management_evidence"]["status"] == "ok"


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

    with patch.dict(
        os.environ,
        {
            "PANTHEON_BFF_HUMAN_INBOX_SURFACE_TIMEOUT_SECONDS": "0.05",
            "PANTHEON_BFF_MANAGEMENT_READ_TIMEOUT_SECONDS": "0.05",
        },
    ):
        with _isolated_bff() as (client, store):
            store.list_governance_review_queue_items = slow_list_governance_review_queue_items
            started = time.monotonic()
            response = client.get("/bff/management/human-inbox", headers=HEADERS)
            elapsed = time.monotonic() - started

    assert response.status_code == 200, response.text
    assert elapsed < 0.4, f"human-inbox route took {elapsed:.3f}s; it should degrade near the timeout budget"
    payload = response.json()
    assert payload["data"]["items"] == []
    assert payload["meta"]["partial"] is True
    surface = payload["meta"]["surfaces"]["human_inbox"]
    assert surface["status"] == "degraded"
    gov_surface = payload["meta"]["surfaces"]["governance_review_queue"]
    assert gov_surface["status"] == "degraded"
    assert gov_surface["reason"] == "read_timeout"


@contextmanager
def _isolated_ranking_client(
    *,
    jwt_secret: str = "test-ranking-read-isolation-jwt-secret",
):
    """Mounts the production ranking router factories via g._isolated_client
    with native OwnerReadContextMiddleware on a persistent TestClient, in strict
    JWT authentication mode without MFA (MGMT-RANKING-ORIGINAL-READ-ISOLATION-20261012).
    """
    from services.runtime_auth_inbound import encode_jwt_hs256
    from services.control_plane.bff import test_bff_promotion_review_governance as g
    from services.control_plane.bff.core.owner_reads import OwnerReadContextMiddleware

    orig_build = g._build_promotion_review_app

    def _build_with_native_middleware(*args, **kwargs):
        app = orig_build(*args, **kwargs)
        app.add_middleware(OwnerReadContextMiddleware)
        return app

    token = encode_jwt_hs256(
        {
            "sub": "op-ranking",
            "roles": ["operator"],
            "tenant_id": "pantheon-dev",
            "exp": int(time.time()) + 3600,
        },
        secret=jwt_secret,
    )
    auth_headers = {
        "Authorization": f"Bearer {token}",
        "X-Tenant-Id": "pantheon-dev",
    }

    env_overrides = {
        "PANTHEON_BFF_JWT_SECRET": jwt_secret,
        "PANTHEON_BFF_AUTH_MODE": "strict",
        "PANTHEON_BFF_MFA_REQUIRED": "false",
    }
    with patch.dict(os.environ, env_overrides):
        prev_stub = os.environ.pop("PANTHEON_BFF_AUTH_STUB", None)
        try:
            with patch.object(g, "_build_promotion_review_app", side_effect=_build_with_native_middleware):
                with g._isolated_client() as (client, store, commands):
                    yield client, store, commands, auth_headers
        finally:
            if prev_stub is not None:
                os.environ["PANTHEON_BFF_AUTH_STUB"] = prev_stub


def test_health_and_auth_rejection_stay_responsive_while_ranking_read_is_slow() -> None:
    """A slow, synchronous quarterly ranking read must not block the asyncio
    event loop or delay concurrent /health or unauthenticated/tampered rejection requests
    (MGMT-RANKING-ORIGINAL-READ-ISOLATION-20261012).

    Uses native g._isolated_client with OwnerReadContextMiddleware and an entered-read
    event barrier to guarantee the slow ranking aggregation has genuinely entered its
    worker thread before probing concurrent routes.
    """
    from services.control_plane.bff.core import owner_reads

    entered_read = threading.Event()
    release_read = threading.Event()
    captured_context: dict[str, str | None] = {}
    orig_get_quarterly_ranking = PersonaService.get_quarterly_ranking

    def controlled_slow_ranking(self, *args, **kwargs):
        captured_context["authorization"] = owner_reads.authorization.get()
        captured_context["selected_tenant"] = owner_reads.selected_tenant.get()
        entered_read.set()
        if not release_read.wait(timeout=5.0):
            raise TimeoutError("Test did not release slow ranking read within timeout")
        return orig_get_quarterly_ranking(self, *args, **kwargs)

    with _isolated_ranking_client() as (client, store, _commands, auth_headers):
        with patch.object(PersonaService, "get_quarterly_ranking", controlled_slow_ranking):
            with ThreadPoolExecutor(max_workers=4) as pool:
                ranking_future = pool.submit(
                    client.get,
                    "/bff/management/quarterly-ranking",
                    headers=auth_headers,
                    params={"quarter": "2026-Q1"},
                )
                assert entered_read.wait(timeout=5.0), "Ranking read never entered execution"

                # 1. Core /health must stay responsive (< 0.3s) while slow ranking read is in flight
                health_started = time.monotonic()
                health_response = client.get("/health")
                health_elapsed = time.monotonic() - health_started

                # 2. Strict anonymous rejection (401) must stay responsive (< 0.3s)
                anon_started = time.monotonic()
                anon_response = client.get("/bff/management/quarterly-ranking")
                anon_elapsed = time.monotonic() - anon_started

                # 3. Strict tampered JWT rejection (401) must stay responsive (< 0.3s)
                tampered_started = time.monotonic()
                tampered_response = client.get(
                    "/bff/management/quarterly-ranking",
                    headers={"Authorization": "Bearer bad.jwt.token"},
                )
                tampered_elapsed = time.monotonic() - tampered_started

                # Controlled release of the slow ranking read
                release_read.set()
                ranking_response = ranking_future.result()

    assert health_response.status_code == 200
    assert health_elapsed < 0.3, (
        f"/health took {health_elapsed:.3f}s while slow ranking aggregation was in flight; "
        "event loop was blocked by synchronous ranking read"
    )

    assert anon_response.status_code == 401
    assert anon_elapsed < 0.3, (
        f"anonymous auth rejection took {anon_elapsed:.3f}s; "
        "auth rejection was delayed by slow ranking read"
    )

    assert tampered_response.status_code == 401
    assert tampered_elapsed < 0.3, (
        f"tampered JWT auth rejection took {tampered_elapsed:.3f}s; "
        "auth rejection was delayed by slow ranking read"
    )

    assert ranking_response.status_code == 200
    ranking_payload = ranking_response.json()
    assert "data" in ranking_payload
    assert "meta" in ranking_payload

    # Caller context vars preserved across synchronous threadpool dispatch
    assert captured_context.get("authorization") == auth_headers["Authorization"]
    assert captured_context.get("selected_tenant") == "pantheon-dev"


def test_ranking_drilldown_and_recommendations_preserve_synchronous_dispatch_and_errors() -> None:
    """Quarterly ranking drilldown and recommendations GET handlers dispatch
    via synchronous threadpool, preserving caller tenant context, true validation
    and not-found errors, and without blocking concurrent /health requests.
    """
    entered_rec = threading.Event()
    release_rec = threading.Event()
    orig_recommendations = PersonaService.get_quarterly_ranking_recommendations

    def controlled_slow_recommendations(self, *args, **kwargs):
        entered_rec.set()
        if not release_rec.wait(timeout=5.0):
            raise TimeoutError("Test did not release slow recommendations read within timeout")
        return orig_recommendations(self, *args, **kwargs)

    with _isolated_ranking_client() as (client, store, _commands, auth_headers):
        # Validation error: missing personaId on drilldown
        val_resp = client.get("/bff/management/quarterly-ranking/drilldown", headers=auth_headers)
        assert val_resp.status_code == 422, val_resp.text

        # Not found error: nonexistent persona on drilldown
        nf_resp = client.get(
            "/bff/management/quarterly-ranking/drilldown?persona_id=nonexistent-persona",
            headers=auth_headers,
        )
        assert nf_resp.status_code == 404, nf_resp.text

        # Drilldown success for seeded persona
        dd_resp = client.get(
            "/bff/management/quarterly-ranking/drilldown?persona_id=persona-us-equity&quarter=2026-Q1",
            headers=auth_headers,
        )
        assert dd_resp.status_code == 200, dd_resp.text
        dd_payload = dd_resp.json()
        assert "data" in dd_payload

        # Slow recommendations read does not block concurrent /health
        with patch.object(PersonaService, "get_quarterly_ranking_recommendations", controlled_slow_recommendations):
            with ThreadPoolExecutor(max_workers=2) as pool:
                rec_future = pool.submit(
                    client.get,
                    "/bff/management/quarterly-ranking/recommendations",
                    headers=auth_headers,
                    params={"quarter": "2026-Q1"},
                )
                assert entered_rec.wait(timeout=5.0), "Recommendations read never entered execution"
                health_started = time.monotonic()
                health_resp = client.get("/health")
                health_elapsed = time.monotonic() - health_started
                release_rec.set()
                rec_resp = rec_future.result()

    assert health_resp.status_code == 200
    assert health_elapsed < 0.3, f"/health took {health_elapsed:.3f}s during slow recommendations read"
    assert rec_resp.status_code == 200
    rec_payload = rec_resp.json()
    assert "data" in rec_payload


@contextmanager
def _isolated_journal_client(
    *,
    jwt_secret: str = "test-journal-read-isolation-jwt-secret",
    initial_decisions: list[dict] | None = None,
):
    """Mounts the production evolution router via create_evolution_router
    with native OwnerReadContextMiddleware on a persistent TestClient, in strict
    JWT authentication mode without MFA (BFF-EVOLUTION-JOURNAL-ORIGINAL-READ-ISOLATION-20261017).
    """
    from services.control_plane.bff.auth import policy as auth_policy
    from services.control_plane.bff.core.owner_reads import OwnerReadContextMiddleware
    from services.control_plane.bff.evolution.router import create_evolution_router
    from services.control_plane.bff.ports import create_in_memory_read_surface_ports
    from services.runtime_auth_inbound import encode_jwt_hs256

    decisions = initial_decisions if initial_decisions is not None else [
        {
            "decision_id": "dec-evo-isolation-001",
            "id": "dec-evo-isolation-001",
            "title": "Evolution isolation test decision",
            "target_type": "strategy",
            "target_id": "strat-evo-001",
            "action_type": "mutation",
            "status": "accepted",
            "risk_level": "low",
            "created_at": "2026-10-17T00:00:00Z",
            "occurred_at": "2026-10-17T00:00:00Z",
            "summary": "Synchronous read test summary",
        }
    ]
    store = create_in_memory_read_surface_ports(
        persona_capital_runtime_kwargs={"evolution_decisions": decisions},
    )

    app = FastAPI()
    register_error_handlers(app)
    app.add_middleware(OwnerReadContextMiddleware)
    app.include_router(create_core_router({}))
    app.include_router(
        create_evolution_router(
            read_surface=store,
            extract_identity=auth_policy.extract_identity,
            require_read_role=auth_policy.require_read_role,
            require_operator_role=auth_policy.require_operator_role,
            bff_error=auth_policy.bff_error,
        )
    )

    token = encode_jwt_hs256(
        {
            "sub": "op-evolution-journal",
            "roles": ["operator"],
            "tenant_id": "pantheon-dev",
            "exp": int(time.time()) + 3600,
        },
        secret=jwt_secret,
    )
    auth_headers = {
        "Authorization": f"Bearer {token}",
        "X-Tenant-Id": "pantheon-dev",
    }

    env_overrides = {
        "PANTHEON_BFF_JWT_SECRET": jwt_secret,
        "PANTHEON_BFF_AUTH_MODE": "strict",
        "PANTHEON_BFF_MFA_REQUIRED": "false",
    }
    with patch.dict(os.environ, env_overrides):
        prev_stub = os.environ.pop("PANTHEON_BFF_AUTH_STUB", None)
        try:
            with TestClient(app, raise_server_exceptions=False) as client:
                yield client, store, auth_headers
        finally:
            if prev_stub is not None:
                os.environ["PANTHEON_BFF_AUTH_STUB"] = prev_stub


def test_health_and_auth_rejection_stay_responsive_while_evolution_journal_read_is_slow() -> None:
    """A slow, synchronous evolution journal owner read must not block the
    asyncio event loop or delay concurrent /health or unauthenticated/tampered
    rejection requests (BFF-EVOLUTION-JOURNAL-ORIGINAL-READ-ISOLATION-20261017).

    Uses native create_evolution_router with OwnerReadContextMiddleware and an
    entered-read event barrier to guarantee the slow journal aggregation has
    genuinely entered its worker thread before probing concurrent routes.
    """
    from services.control_plane.bff.core import owner_reads

    entered_read = threading.Event()
    release_read = threading.Event()
    captured_context: dict[str, str | None] = {}

    with _isolated_journal_client() as (client, store, auth_headers):
        orig_list_decisions = store.list_evolution_decisions

        def controlled_slow_decisions(*args, **kwargs):
            captured_context["authorization"] = owner_reads.authorization.get()
            captured_context["selected_tenant"] = owner_reads.selected_tenant.get()
            entered_read.set()
            if not release_read.wait(timeout=5.0):
                raise TimeoutError("Test did not release slow journal read within timeout")
            return orig_list_decisions(*args, **kwargs)

        store.list_evolution_decisions = controlled_slow_decisions
        try:
            with ThreadPoolExecutor(max_workers=4) as pool:
                journal_future = pool.submit(
                    client.get,
                    "/bff/management/evolution-journal",
                    headers=auth_headers,
                    params={"action_type": "mutation"},
                )
                assert entered_read.wait(timeout=5.0), "Evolution journal read never entered execution"

                # 1. Core /health must stay responsive (< 0.3s) while slow journal read is in flight
                health_started = time.monotonic()
                health_response = client.get("/health")
                health_elapsed = time.monotonic() - health_started

                # 2. Strict anonymous rejection (401) must stay responsive (< 0.3s)
                anon_started = time.monotonic()
                anon_response = client.get("/bff/management/evolution-journal")
                anon_elapsed = time.monotonic() - anon_started

                # 3. Strict tampered JWT rejection (401) must stay responsive (< 0.3s)
                tampered_started = time.monotonic()
                tampered_response = client.get(
                    "/bff/management/evolution-journal",
                    headers={"Authorization": "Bearer bad.jwt.token"},
                )
                tampered_elapsed = time.monotonic() - tampered_started

                # Controlled release of the slow journal read
                release_read.set()
                journal_response = journal_future.result()
        finally:
            release_read.set()

    assert health_response.status_code == 200
    assert health_elapsed < 0.3, (
        f"/health took {health_elapsed:.3f}s while slow journal aggregation was in flight; "
        "event loop was blocked by synchronous journal read"
    )

    assert anon_response.status_code == 401
    assert anon_elapsed < 0.3, (
        f"anonymous auth rejection took {anon_elapsed:.3f}s; "
        "auth rejection was delayed by slow journal read"
    )

    assert tampered_response.status_code == 401
    assert tampered_elapsed < 0.3, (
        f"tampered JWT auth rejection took {tampered_elapsed:.3f}s; "
        "auth rejection was delayed by slow journal read"
    )

    assert journal_response.status_code == 200
    journal_payload = journal_response.json()
    assert "data" in journal_payload
    assert "meta" in journal_payload
    assert journal_payload["data"]["id"] == "management_evolution_journal"
    assert journal_payload["page_info"]["total"] >= 1
    assert any(
        it.get("source_id") == "dec-evo-isolation-001" or it.get("id") == "dec-evo-isolation-001"
        for it in journal_payload["data"]["items"]
    )

    # Caller context vars preserved across synchronous threadpool dispatch
    assert captured_context.get("authorization") == auth_headers["Authorization"]
    assert captured_context.get("selected_tenant") == "pantheon-dev"


def test_evolution_journal_preserves_synchronous_dispatch_filters_and_errors() -> None:
    """Evolution journal GET handler dispatches via synchronous threadpool,
    preserving caller tenant context, filters, pagination, true validation/auth
    errors, and without blocking concurrent /health requests.
    """
    entered_pm = threading.Event()
    release_pm = threading.Event()

    with _isolated_journal_client() as (client, store, auth_headers):
        # 1. Validation error: invalid page_size (< 1)
        val_resp = client.get(
            "/bff/management/evolution-journal",
            headers=auth_headers,
            params={"page_size": 0},
        )
        assert val_resp.status_code == 422, val_resp.text

        # 2. Filter matching no items returns empty items list, not 500 or degraded
        empty_resp = client.get(
            "/bff/management/evolution-journal",
            headers=auth_headers,
            params={"action_type": "nonexistent_action"},
        )
        assert empty_resp.status_code == 200, empty_resp.text
        assert empty_resp.json()["data"]["items"] == []
        assert empty_resp.json()["page_info"]["total"] == 0

        # 3. Slow postmortems read also runs in threadpool without blocking concurrent /health
        orig_list_pm = store.list_postmortems

        def controlled_slow_postmortems(*args, **kwargs):
            entered_pm.set()
            if not release_pm.wait(timeout=5.0):
                raise TimeoutError("Test did not release slow postmortem read within timeout")
            return orig_list_pm(*args, **kwargs)

        store.list_postmortems = controlled_slow_postmortems
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                journal_future = pool.submit(
                    client.get,
                    "/bff/management/evolution-journal",
                    headers=auth_headers,
                )
                assert entered_pm.wait(timeout=5.0), "Postmortems read never entered execution"
                health_started = time.monotonic()
                health_resp = client.get("/health")
                health_elapsed = time.monotonic() - health_started
                release_pm.set()
                journal_resp = journal_future.result()
        finally:
            release_pm.set()

    assert health_resp.status_code == 200
    assert health_elapsed < 0.3, f"/health took {health_elapsed:.3f}s during slow postmortems read"
    assert journal_resp.status_code == 200
    payload = journal_resp.json()
    assert "data" in payload
    assert payload["data"]["id"] == "management_evolution_journal"


def test_alerts_returns_completed_contributors_when_governance_times_out() -> None:
    """BFF-ALERTS-PARTIAL-20261109: when governance exceeds the route budget after
    incident, kill_switch, and runtime finished, GET /bff/alerts returns the
    completed alerts with source=bff_composed, status=degraded, reason=read_timeout,
    and marks unfinished governance surfaces read_timeout."""
    release = threading.Event()

    def slow_review_queue(**_kwargs):
        release.wait(0.5)
        return [
            {
                "item_id": "review-dec-001",
                "item_type": "ApprovalDecision",
                "risk_level": "high",
                "status": "pending",
                "submitted_at": "2026-11-09T08:04:00Z",
                "submitted_by": "governance-service",
                "governance_outcome": "pending",
            }
        ]

    previous = os.environ.get("PANTHEON_BFF_ALERTS_READ_TIMEOUT_SECONDS")
    os.environ["PANTHEON_BFF_ALERTS_READ_TIMEOUT_SECONDS"] = "0.05"
    try:
        with _isolated_bff() as (client, store):
            store.list_incidents = lambda **_kwargs: [
                {
                    "incident_id": "inc-001",
                    "title": "Unexpected drawdown in persona-alpha",
                    "severity": "high",
                    "status": "open",
                    "created_at": "2026-11-09T08:01:00Z",
                }
            ]
            store.get_kill_switch_status = lambda: {
                "active": True,
                "status": "triggered",
                "safe_mode_status": "soft",
                "last_confirmed_at": "2026-11-09T08:02:00Z",
                "last_triggered_at": "2026-11-09T08:02:30Z",
            }
            store.list_runtime_bindings = lambda: [
                {
                    "id": "runtime-042",
                    "runtime_id": "runtime-042",
                    "deployment_stage": "live",
                    "status": "running",
                    "plan_id": "plan-F-042",
                }
            ]
            store.get_telemetry_summary = lambda runtime_id: (
                {
                    "runtime_id": "runtime-042",
                    "window": "1h",
                    "drawdown": 0.125,
                    "fill_rate": 0.89,
                    "avg_slippage_bps": 4.2,
                    "collected_at": "2026-11-09T08:03:00Z",
                }
                if runtime_id == "runtime-042"
                else None
            )
            store.list_governance_review_queue_items = slow_review_queue
            store.list_approval_queue_items = lambda **_kwargs: []
            store.dataset_source = lambda dataset: "service_store"

            response = client.get("/bff/alerts", headers=HEADERS)
    finally:
        release.set()
        if previous is None:
            os.environ.pop("PANTHEON_BFF_ALERTS_READ_TIMEOUT_SECONDS", None)
        else:
            os.environ["PANTHEON_BFF_ALERTS_READ_TIMEOUT_SECONDS"] = previous

    assert response.status_code == 200, response.text
    payload = response.json()
    assert [a["alert_id"] for a in payload["alerts"]] == [
        "alert-runtime-runtime-042",
        "alert-kill-switch-state",
        "alert-incident-inc-001",
    ]
    assert payload["summary"]["total_active"] == 3
    assert payload["summary"]["by_category"] == {
        "incident": 1,
        "kill_switch": 1,
        "runtime": 1,
        "governance": 0,
    }
    surfaces = payload["meta"]["surfaces"]
    assert surfaces["alerts"]["status"] == "degraded"
    assert surfaces["alerts"]["source"] == "bff_composed"
    assert surfaces["alerts"]["reason"] == "read_timeout"
    assert surfaces["incident_feed"]["status"] == "ok"
    assert surfaces["kill_switch"]["status"] == "ok"
    assert surfaces["runtime_roster"]["status"] == "ok"
    assert surfaces["telemetry_summary"]["status"] == "ok"
    assert surfaces["review_queue"]["status"] == "degraded"
    assert surfaces["review_queue"]["reason"] == "read_timeout"
    assert surfaces["approval_queue"]["status"] == "degraded"
    assert surfaces["approval_queue"]["reason"] == "read_timeout"


def test_alerts_returns_timeout_fallback_when_incident_contributor_times_out() -> None:
    """BFF-ALERTS-PARTIAL-20261109: when the first contributor (incident) exceeds
    the route budget so no contributor finishes, GET /bff/alerts returns an empty
    alert list with source=timeout_fallback and reason=read_timeout."""
    release = threading.Event()

    def slow_incidents(**_kwargs):
        release.wait(0.5)
        return [
            {
                "incident_id": "inc-001",
                "title": "Slow incident owner",
                "severity": "high",
                "status": "open",
                "created_at": "2026-11-09T08:01:00Z",
            }
        ]

    previous = os.environ.get("PANTHEON_BFF_ALERTS_READ_TIMEOUT_SECONDS")
    os.environ["PANTHEON_BFF_ALERTS_READ_TIMEOUT_SECONDS"] = "0.05"
    try:
        with _isolated_bff() as (client, store):
            store.list_incidents = slow_incidents
            store.get_kill_switch_status = lambda: {
                "active": True,
                "status": "triggered",
                "safe_mode_status": "soft",
                "last_triggered_at": "2026-11-09T08:02:30Z",
            }
            store.list_runtime_bindings = lambda: []
            store.list_governance_review_queue_items = lambda **_kwargs: []
            store.list_approval_queue_items = lambda **_kwargs: []
            store.dataset_source = lambda dataset: "service_store"

            response = client.get("/bff/alerts", headers=HEADERS)
    finally:
        release.set()
        if previous is None:
            os.environ.pop("PANTHEON_BFF_ALERTS_READ_TIMEOUT_SECONDS", None)
        else:
            os.environ["PANTHEON_BFF_ALERTS_READ_TIMEOUT_SECONDS"] = previous

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["alerts"] == []
    assert payload["summary"]["total_active"] == 0
    alerts_surface = payload["meta"]["surfaces"]["alerts"]
    assert alerts_surface["status"] == "degraded"
    assert alerts_surface["source"] == "timeout_fallback"
    assert alerts_surface["reason"] == "read_timeout"
