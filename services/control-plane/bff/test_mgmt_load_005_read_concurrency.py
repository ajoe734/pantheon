"""MGMT-LOAD-005 contract tests for BFF read concurrency isolation.

Verifies that slow, synchronous management read aggregation (Evidence,
alerts, approvals) cannot block the asyncio event loop and delay
unrelated routes such as /health, and that a read exceeding its timeout
budget returns an explicit degraded envelope instead of hanging. Jobs now
has an isolated router and is covered here through its narrow read port.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Iterator

from fastapi.testclient import TestClient

os.environ.setdefault("PANTHEON_BFF_AUTH_STUB", "true")
sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main  # noqa: E402
from ports import ReadSurfacePorts, create_read_surface_ports  # noqa: E402


HEADERS = {"Authorization": "Bearer op-mgmt-load-005:operator,admin:mfa"}


@contextmanager
def _isolated_bff(monkeypatch) -> Iterator[tuple[TestClient, ReadSurfacePorts]]:
    """Isolated BFF app on a *persistent* TestClient portal (single event loop).

    Using TestClient as a context manager keeps one event loop alive for the
    whole `with` block instead of spinning up a fresh portal/event loop per
    request. That persistent-loop shape is what a real long-lived uvicorn
    worker looks like, and it is required for these concurrency tests: a
    bare `TestClient(app).get(...)` call (no `with`) creates and tears down
    its own event loop per call, which would make two "concurrent" requests
    run on two unrelated event loops and silently defeat the point of the
    test (see MGMT-LOAD-005 read-isolation fix in main.py).
    """
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        store = create_read_surface_ports()
        store.list_jobs_bff = lambda **_kwargs: []
        bff_main.read_store = store
        bff_main._SHELL_SUMMARY_COUNT_CACHE.clear()
        bff_main._GOV_BFF_JOB_OVERLAY.clear()
        bff_main._ACKNOWLEDGED_ALERTS.clear()
        bff_main.app.openapi_schema = None
        try:
            with TestClient(bff_main.app, raise_server_exceptions=False) as client:
                yield client, store
        finally:
            bff_main.read_store = original_store
            bff_main._SHELL_SUMMARY_COUNT_CACHE.clear()
            bff_main._GOV_BFF_JOB_OVERLAY.clear()
            bff_main._ACKNOWLEDGED_ALERTS.clear()
            bff_main.app.openapi_schema = None


def _empty_evidence_payload(**_kwargs):
    return {
        "data": {
            "id": "management-evidence",
            "items": [],
            "summary": {
                "total_evidence": 0,
                "returned_evidence": 0,
                "visible_evidence": 0,
                "redacted_evidence": 0,
                "verified_evidence": 0,
                "by_source_type": {},
                "by_link_type": {},
                "by_credibility_tier": {},
            },
            "facets": {
                "source_types": {},
                "link_types": {},
                "credibility_tiers": {},
            },
        },
        "page_info": {"next_page_token": None, "total": 0, "page_size": 0},
        "meta": {"surfaces": {}},
    }


def test_health_stays_fast_while_evidence_read_is_slow(monkeypatch) -> None:
    """A slow Evidence aggregation must not delay a concurrent /health request."""

    def slow_evidence_payload(**kwargs):
        time.sleep(0.5)
        return _empty_evidence_payload(**kwargs)

    monkeypatch.setattr(bff_main, "_build_management_evidence_payload", slow_evidence_payload)

    with _isolated_bff(monkeypatch) as (client, _store):
        with ThreadPoolExecutor(max_workers=2) as pool:
            evidence_future = pool.submit(client.get, "/bff/management/evidence", headers=HEADERS)
            time.sleep(0.05)  # let the slow evidence request start first
            health_started = time.monotonic()
            health_response = client.get("/health")
            health_elapsed = time.monotonic() - health_started
            evidence_response = evidence_future.result()

    assert health_response.status_code == 200
    assert health_elapsed < 0.3, (
        f"/health took {health_elapsed:.3f}s while a slow Evidence read was in flight; "
        "the event loop must not be blocked by unrelated synchronous read work"
    )
    assert evidence_response.status_code == 200


def test_jobs_router_uses_narrow_port_without_delaying_health(monkeypatch) -> None:
    def retired_main_reader(*_args, **_kwargs):
        raise AssertionError("the isolated jobs router must use its injected read port")

    monkeypatch.setattr(bff_main, "_list_bff_jobs", retired_main_reader)

    with _isolated_bff(monkeypatch) as (client, store):
        monkeypatch.setattr(store, "list_jobs_bff", lambda **_kwargs: [])
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


def test_evidence_timeout_returns_degraded_envelope_without_hanging(monkeypatch) -> None:
    def slow_evidence_payload(**kwargs):
        time.sleep(0.3)
        return {**_empty_evidence_payload(**kwargs), "data": ["should-not-appear"]}

    monkeypatch.setattr(bff_main, "_build_management_evidence_payload", slow_evidence_payload)
    monkeypatch.setattr(bff_main, "_management_read_timeout_seconds", lambda: 0.05)

    with _isolated_bff(monkeypatch) as (client, _store):
        started = time.monotonic()
        response = client.get("/bff/management/evidence", headers=HEADERS)
        elapsed = time.monotonic() - started

    assert response.status_code == 200, response.text
    assert elapsed < 0.25, f"evidence route took {elapsed:.3f}s; it should degrade near the timeout budget"
    payload = response.json()
    assert payload["data"]["items"] == []
    assert payload["data"]["summary"]["total_evidence"] == 0
    assert "items" not in payload
    surface = payload["meta"]["surfaces"]["management_evidence"]
    assert surface["status"] == "degraded"
    assert surface["reason"] == "read_timeout"


def test_alerts_timeout_returns_degraded_envelope_without_hanging(monkeypatch) -> None:
    def slow_alerts_payload(snapshot_at: str):
        time.sleep(0.3)
        return {"alerts": [{"alert_id": "should-not-appear"}], "summary": {}, "meta": {}}

    monkeypatch.setattr(bff_main, "_build_operator_alerts_payload", slow_alerts_payload)
    monkeypatch.setattr(bff_main, "_management_read_timeout_seconds", lambda: 0.05)

    with _isolated_bff(monkeypatch) as (client, _store):
        started = time.monotonic()
        response = client.get("/bff/alerts", headers=HEADERS)
        elapsed = time.monotonic() - started

    assert response.status_code == 200, response.text
    assert elapsed < 0.25, f"alerts route took {elapsed:.3f}s; it should degrade near the timeout budget"
    payload = response.json()
    assert payload["alerts"] == []
    surface = payload["meta"]["surfaces"]["alerts"]
    assert surface["status"] == "degraded"
    assert surface["reason"] == "read_timeout"


def test_approvals_timeout_returns_degraded_envelope_without_hanging(monkeypatch) -> None:
    def slow_list_approval_queue_items():
        time.sleep(0.3)
        return [{"decision_id": "should-not-appear", "decision_state": "pending"}]

    with _isolated_bff(monkeypatch) as (client, store):
        monkeypatch.setattr(store, "list_approval_queue_items", slow_list_approval_queue_items)
        monkeypatch.setattr(bff_main, "_management_read_timeout_seconds", lambda: 0.05)

        started = time.monotonic()
        response = client.get("/bff/approvals", headers=HEADERS)
        elapsed = time.monotonic() - started

    assert response.status_code == 200, response.text
    assert elapsed < 0.25, f"approvals route took {elapsed:.3f}s; it should degrade near the timeout budget"
    payload = response.json()
    assert payload["items"] == []
    assert payload["count"] == 0
    surface = payload["meta"]["surfaces"]["approvals"]
    assert surface["status"] == "degraded"
    assert surface["reason"] == "read_timeout"


def test_jobs_router_returns_narrow_port_records_without_legacy_reader(monkeypatch) -> None:
    def retired_main_reader(*_args, **_kwargs):
        raise AssertionError("the isolated jobs router must use its injected read port")

    monkeypatch.setattr(bff_main, "_list_bff_jobs", retired_main_reader)

    with _isolated_bff(monkeypatch) as (client, store):
        monkeypatch.setattr(
            store,
            "list_jobs_bff",
            lambda **_kwargs: [{"job_id": "job-narrow-port", "status": "running"}],
        )
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


def test_evidence_returns_normal_payload_when_fast(monkeypatch) -> None:
    """Sanity check: the timeout wrapper must not alter the happy path."""
    with _isolated_bff(monkeypatch) as (client, _store):
        response = client.get("/bff/management/evidence", headers=HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert "management_evidence" in payload["meta"]["surfaces"]
    # The isolated fixture's fresh local-snapshot store may legitimately report
    # "degraded" for unrelated dataset-source reasons; what must not happen is
    # a spurious read_timeout on the fast (non-monkeypatched) path.
    assert payload["meta"]["surfaces"]["management_evidence"].get("reason") != "read_timeout"


def test_human_inbox_timeout_returns_degraded_envelope_without_hanging(monkeypatch) -> None:
    def slow_list_governance_review_queue_items():
        time.sleep(0.3)
        return [{"item_id": "review-should-not-appear", "item_type": "DeploymentPlan"}]

    with _isolated_bff(monkeypatch) as (client, store):
        monkeypatch.setattr(store, "list_governance_review_queue_items", slow_list_governance_review_queue_items)
        monkeypatch.setattr(bff_main, "_human_inbox_surface_timeout_seconds", lambda: 0.05)

        started = time.monotonic()
        response = client.get("/bff/management/human-inbox", headers=HEADERS)
        elapsed = time.monotonic() - started

    assert response.status_code == 200, response.text
    assert elapsed < 0.25, f"human-inbox route took {elapsed:.3f}s; it should degrade near the timeout budget"
    payload = response.json()
    assert not any(item.get("id") == "review-should-not-appear" for item in payload["data"]["items"])
    surface = payload["meta"]["surfaces"]["governance_review_queue"]
    assert surface["status"] == "degraded"
    assert surface["reason"] == "read_timeout"


def test_human_inbox_returns_normal_payload_when_fast(monkeypatch) -> None:
    with _isolated_bff(monkeypatch) as (client, _store):
        response = client.get("/bff/management/human-inbox", headers=HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert "human_inbox" in payload["meta"]["surfaces"]
    assert payload["meta"]["surfaces"]["human_inbox"].get("reason") != "read_timeout"
