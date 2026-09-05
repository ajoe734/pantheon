from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

from services.control_plane.bff import main as bff_main
from ports import DefaultResearchKnowledgeSourcePort


OPERATOR_AUTH = "Bearer test-operator:operator"

_SEEDED_EXPERIMENTS = {
    "exp-20260419-012": {
        "experiment_id": "exp-20260419-012",
        "ticket_id": "rt-20260419-007",
        "experiment_name": "Momentum decay replay on March volatility cluster",
        "status": "completed",
        "queued_at": "2026-04-19T19:00:00Z",
        "started_at": "2026-04-19T19:03:00Z",
        "completed_at": "2026-04-19T20:15:00Z",
        "progress": {"percent": 100, "phase": "aggregation", "message": "Aggregation complete."},
        "strategy_selector": {"strategy_id": "strat-momentum-v4", "variant_id": "var-short-halflife"},
        "parameter_set": {"half_life_days": 5},
        "run_config": {
            "dataset_ref": "equities-us-2026Q1",
            "time_range": {"start_at": "2026-03-01T00:00:00Z", "end_at": "2026-03-31T23:59:59Z"},
            "execution_mode": "backtest",
            "priority": "high",
            "requested_by": "persona-risk-chief",
        },
        "launch_context": {"analysis_refs": ["analysis-20260418-004-b"]},
        "failure": {"reason_code": None, "message": None},
    },
    "exp-20260418-009": {
        "experiment_id": "exp-20260418-009",
        "ticket_id": "rt-20260419-007",
        "experiment_name": "Momentum decay baseline",
        "status": "running",
        "queued_at": "2026-04-18T14:00:00Z",
        "started_at": "2026-04-18T14:05:00Z",
        "completed_at": None,
        "progress": {"percent": 62, "phase": "signal_aggregation", "message": "Aggregating."},
        "strategy_selector": {"strategy_id": "strat-momentum-v4", "variant_id": "var-baseline"},
        "run_config": {
            "dataset_ref": "equities-us-2026Q1",
            "time_range": {"start_at": "2026-02-01T00:00:00Z", "end_at": "2026-04-17T23:59:59Z"},
            "execution_mode": "backtest",
            "priority": "normal",
            "requested_by": "persona-risk-chief",
        },
        "failure": {"reason_code": None, "message": None},
    },
    "exp-20260417-004": {
        "experiment_id": "exp-20260417-004",
        "ticket_id": "rt-20260415-001",
        "experiment_name": "Macro event signal quality",
        "status": "failed",
        "queued_at": "2026-04-17T12:00:00Z",
        "started_at": "2026-04-17T12:02:00Z",
        "completed_at": "2026-04-17T12:07:00Z",
        "progress": {"percent": None, "phase": None, "message": None},
        "strategy_selector": {"strategy_id": "strat-macro-event-v2", "variant_id": None},
        "run_config": {
            "dataset_ref": "equities-us-2026Q1",
            "time_range": {"start_at": "2026-01-01T00:00:00Z", "end_at": "2026-04-16T23:59:59Z"},
            "execution_mode": "simulation",
            "priority": "normal",
            "requested_by": "persona-risk-chief",
        },
        "failure": {
            "reason_code": "MISSING_DATA_PARTITION",
            "message": "Macro calendar partition for Q1 was missing.",
        },
    },
}

LAUNCH_PAYLOAD = {
    "ticket_id": "rt-20260419-007",
    "experiment_name": "Momentum decay fast path test",
    "strategy_selector": {"strategy_id": "strat-momentum-v4", "variant_id": "var-short-halflife"},
    "parameter_set": {"half_life_days": 5, "signal_threshold": 0.62},
    "run_config": {
        "dataset_ref": "equities-us-2026Q1",
        "time_range": {"start_at": "2026-03-01T00:00:00Z", "end_at": "2026-03-31T23:59:59Z"},
        "execution_mode": "backtest",
        "priority": "high",
        "requested_by": "persona-risk-chief",
    },
    "launch_context": {"analysis_refs": ["analysis-20260418-004-b"]},
}


@contextmanager
def _seeded_client():
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        bff_main.read_store = DefaultResearchKnowledgeSourcePort(
            research_experiments_store=_SEEDED_EXPERIMENTS,
        )
        client = TestClient(bff_main.app)
        try:
            yield client
        finally:
            bff_main.read_store = original_store


@contextmanager
def _no_fallback_client():
    """Client with allow_local_snapshot_fallback=False — the production path."""
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        bff_main.read_store = DefaultResearchKnowledgeSourcePort()
        client = TestClient(bff_main.app)
        try:
            yield client
        finally:
            bff_main.read_store = original_store


# ---------------------------------------------------------------------------
# POST /api/v1/experiments/launch
# ---------------------------------------------------------------------------

def test_rw04_launch_returns_queued_status() -> None:
    with _seeded_client() as client:
        response = client.post(
            "/api/v1/experiments/launch",
            json=LAUNCH_PAYLOAD,
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["status"] == "queued"
        assert payload["ticket_id"] == "rt-20260419-007"
        assert payload["experiment_id"].startswith("exp-")
        assert payload["queued_at"] is not None
        assert payload["allowedActions"]["canCancel"] is True
        assert "/api/v1/experiments/" in payload["links"]["self"]
        assert "/research/experiments/" in payload["links"]["workbench_detail"]


def test_rw04_launch_rejects_missing_ticket_id() -> None:
    with _seeded_client() as client:
        bad_payload = {k: v for k, v in LAUNCH_PAYLOAD.items() if k != "ticket_id"}
        response = client.post(
            "/api/v1/experiments/launch",
            json=bad_payload,
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "VALIDATION_FAILED"


def test_rw04_launch_rejects_invalid_execution_mode() -> None:
    with _seeded_client() as client:
        bad_payload = dict(LAUNCH_PAYLOAD)
        bad_payload["run_config"] = dict(LAUNCH_PAYLOAD["run_config"])
        bad_payload["run_config"]["execution_mode"] = "live"
        response = client.post(
            "/api/v1/experiments/launch",
            json=bad_payload,
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 422, response.text
        assert response.json()["error"]["details"]["precondition_failed"] == "execution_mode"


# ---------------------------------------------------------------------------
# GET /api/v1/experiments
# ---------------------------------------------------------------------------

def test_rw04_list_returns_seeded_experiments() -> None:
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/experiments",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["page_info"]["total"] >= 3
        ids = [item["experiment_id"] for item in payload["data"]]
        assert "exp-20260419-012" in ids
        assert "exp-20260418-009" in ids
        assert "exp-20260417-004" in ids
        assert payload["meta"]["surfaces"]["experiment_history"] in {"fresh", "stale", "degraded"}


def test_rw04_list_filters_by_status() -> None:
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/experiments?status=running",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert all(item["status"] == "running" for item in payload["data"])
        assert payload["page_info"]["total"] >= 1


def test_rw04_list_filters_by_ticket_id() -> None:
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/experiments?ticket_id=rt-20260415-001",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        for item in payload["data"]:
            assert item["ticket_id"] == "rt-20260415-001"


def test_rw04_list_rejects_invalid_status() -> None:
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/experiments?status=archived",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "VALIDATION_FAILED"


def test_rw04_list_summary_can_cancel_invariant() -> None:
    with _seeded_client() as client:
        response = client.get("/api/v1/experiments", headers={"Authorization": OPERATOR_AUTH})
        assert response.status_code == 200
        for item in response.json()["data"]:
            status = item["status"]
            can_cancel = item["allowedActions"]["canCancel"]
            if status in {"completed", "failed", "canceled"}:
                assert can_cancel is False, f"Terminal run {item['experiment_id']} must have canCancel=false"
            elif status in {"queued", "running"}:
                assert isinstance(can_cancel, bool)


def test_rw04_list_includes_links() -> None:
    with _seeded_client() as client:
        response = client.get("/api/v1/experiments", headers={"Authorization": OPERATOR_AUTH})
        assert response.status_code == 200
        for item in response.json()["data"]:
            assert "self" in item["links"]
            assert "workbench_detail" in item["links"]


# ---------------------------------------------------------------------------
# GET /api/v1/experiments/{experiment_id}
# ---------------------------------------------------------------------------

def test_rw04_detail_returns_full_contract() -> None:
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/experiments/exp-20260419-012",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["experiment_id"] == "exp-20260419-012"
        assert payload["ticket_id"] == "rt-20260419-007"
        assert payload["status"] == "completed"
        assert payload["progress"] is not None
        assert "percent" in payload["progress"]
        assert payload["strategy_selector"]["strategy_id"] == "strat-momentum-v4"
        assert "dataset_ref" in payload["run_config"]
        assert "start_at" in payload["run_config"]["time_range"]
        assert "end_at" in payload["run_config"]["time_range"]
        assert payload["allowedActions"]["canCancel"] is False
        assert payload["links"]["self"] == "/api/v1/experiments/exp-20260419-012"
        assert payload["links"]["linked_ticket_detail"] == "/research/tickets/rt-20260419-007"
        assert payload["meta"]["surfaces"]["experiment_status"] in {"fresh", "stale", "degraded"}


def test_rw04_detail_running_can_cancel_true() -> None:
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/experiments/exp-20260418-009",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "running"
        assert payload["allowedActions"]["canCancel"] is True


def test_rw04_detail_failed_has_failure_fields() -> None:
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/experiments/exp-20260417-004",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "failed"
        assert payload["failure"]["reason_code"] == "MISSING_DATA_PARTITION"
        assert payload["failure"]["message"] is not None
        assert payload["allowedActions"]["canCancel"] is False


def test_rw04_detail_404_for_missing_experiment() -> None:
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/experiments/exp-does-not-exist",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


# ---------------------------------------------------------------------------
# POST /api/v1/experiments/{experiment_id}/cancel
# ---------------------------------------------------------------------------

def test_rw04_cancel_queued_to_canceled() -> None:
    with _seeded_client() as client:
        # Launch a new experiment so it is in queued state
        launch_resp = client.post(
            "/api/v1/experiments/launch",
            json=LAUNCH_PAYLOAD,
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert launch_resp.status_code == 200
        experiment_id = launch_resp.json()["experiment_id"]

        cancel_resp = client.post(
            f"/api/v1/experiments/{experiment_id}/cancel",
            json={"reason": "Testing queued-to-canceled direct path"},
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert cancel_resp.status_code == 200, cancel_resp.text
        payload = cancel_resp.json()
        assert payload["experiment_id"] == experiment_id
        assert payload["status"] == "canceled"
        assert payload["completed_at"] is not None
        assert payload["allowedActions"]["canCancel"] is False


def test_rw04_cancel_running_experiment() -> None:
    with _seeded_client() as client:
        response = client.post(
            "/api/v1/experiments/exp-20260418-009/cancel",
            json={"reason": "Operator-initiated stop"},
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["status"] == "canceled"
        assert payload["allowedActions"]["canCancel"] is False


def test_rw04_cancel_terminal_experiment_rejects() -> None:
    with _seeded_client() as client:
        response = client.post(
            "/api/v1/experiments/exp-20260419-012/cancel",
            json={"reason": "Should be rejected"},
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "OPERATION_NOT_ALLOWED"


def test_rw04_cancel_requires_reason() -> None:
    with _seeded_client() as client:
        response = client.post(
            "/api/v1/experiments/exp-20260418-009/cancel",
            json={"reason": ""},
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 422, response.text
        assert response.json()["error"]["details"]["precondition_failed"] == "reason"


def test_rw04_cancel_404_for_missing_experiment() -> None:
    with _seeded_client() as client:
        response = client.post(
            "/api/v1/experiments/exp-does-not-exist/cancel",
            json={"reason": "Ghost cancel"},
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# State machine invariants
# ---------------------------------------------------------------------------

def test_rw04_canceled_experiment_is_not_cancelable_again() -> None:
    with _seeded_client() as client:
        # launch + cancel
        launch_resp = client.post(
            "/api/v1/experiments/launch",
            json=LAUNCH_PAYLOAD,
            headers={"Authorization": OPERATOR_AUTH},
        )
        experiment_id = launch_resp.json()["experiment_id"]
        client.post(
            f"/api/v1/experiments/{experiment_id}/cancel",
            json={"reason": "First cancel"},
            headers={"Authorization": OPERATOR_AUTH},
        )
        # second cancel must be rejected
        second_cancel = client.post(
            f"/api/v1/experiments/{experiment_id}/cancel",
            json={"reason": "Second cancel attempt"},
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert second_cancel.status_code == 409

        # detail must confirm canCancel=false
        detail = client.get(
            f"/api/v1/experiments/{experiment_id}",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert detail.json()["allowedActions"]["canCancel"] is False


# ---------------------------------------------------------------------------
# Non-fallback regression: production path (allow_local_snapshot_fallback=False)
# ---------------------------------------------------------------------------

def test_rw04_no_fallback_launch_list_detail_cancel_round_trip() -> None:
    """With fallback disabled a launched experiment must be listable, fetchable, and cancelable."""
    with _no_fallback_client() as client:
        # Launch
        launch_resp = client.post(
            "/api/v1/experiments/launch",
            json=LAUNCH_PAYLOAD,
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert launch_resp.status_code == 200, launch_resp.text
        experiment_id = launch_resp.json()["experiment_id"]
        assert launch_resp.json()["status"] == "queued"

        # List must include the new experiment
        list_resp = client.get("/api/v1/experiments", headers={"Authorization": OPERATOR_AUTH})
        assert list_resp.status_code == 200, list_resp.text
        ids = [item["experiment_id"] for item in list_resp.json()["data"]]
        assert experiment_id in ids, f"{experiment_id} not found in list: {ids}"

        # Detail must be fetchable
        detail_resp = client.get(
            f"/api/v1/experiments/{experiment_id}",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert detail_resp.status_code == 200, detail_resp.text
        assert detail_resp.json()["status"] == "queued"
        assert detail_resp.json()["allowedActions"]["canCancel"] is True

        # Cancel must succeed
        cancel_resp = client.post(
            f"/api/v1/experiments/{experiment_id}/cancel",
            json={"reason": "Non-fallback path regression test"},
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert cancel_resp.status_code == 200, cancel_resp.text
        assert cancel_resp.json()["status"] == "canceled"
        assert cancel_resp.json()["allowedActions"]["canCancel"] is False

        # Post-cancel detail must reflect terminal state
        post_cancel_detail = client.get(
            f"/api/v1/experiments/{experiment_id}",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert post_cancel_detail.status_code == 200
        assert post_cancel_detail.json()["status"] == "canceled"
        assert post_cancel_detail.json()["allowedActions"]["canCancel"] is False


def test_rw04_no_fallback_missing_experiment_returns_404() -> None:
    with _no_fallback_client() as client:
        resp = client.get(
            "/api/v1/experiments/exp-does-not-exist",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
