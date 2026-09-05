"""Adapter-level partial-effect lineage tests for Workshop operations.

These tests script the transport underneath ``WorkshopCanonicalOperations``
and inject failures between the individual downstream steps:

- after the research task is created (run POST fails),
- after the research run is accepted (authoritative readback fails),
- after the consultation request is created (submit / readback fails).

Every failure that leaves a downstream resource behind must raise a
``CanonicalOperationError`` carrying ``partial_effects``, and the resume path
must adopt recorded resources with readback instead of re-creating them.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional, Tuple

import pytest


from services.control_plane.bff.agora.strategy_workshop.operations import (  # noqa: E402
    CanonicalOperationError,
    WorkshopCanonicalOperations,
)


TASK_ID = "research-task-100"
RUN_ID = "research-run-100"
CONSULT_ID = "cr-ws-partial-100"

TASKS_PATH = "/api/research-orchestrator/tasks"
TASK_PATH = f"/api/research-orchestrator/tasks/{TASK_ID}"
RUNS_PATH = f"/api/research-orchestrator/tasks/{TASK_ID}/runs"
RUN_PATH = f"/api/research-orchestrator/runs/{RUN_ID}"
CONSULT_CREATE_PATH = "/api/consult/requests"
CONSULT_GET_PATH = f"/api/consult/requests/{CONSULT_ID}"
CONSULT_SUBMIT_PATH = f"/api/consult/requests/{CONSULT_ID}/submit"


def _unavailable(authority: str) -> CanonicalOperationError:
    return CanonicalOperationError(
        authority, "canonical service is unavailable", retryable=True
    )


def _not_found(authority: str) -> CanonicalOperationError:
    return CanonicalOperationError(
        authority, "resource was not found", status_code=404
    )


class ScriptedAdapter(WorkshopCanonicalOperations):
    """Adapter whose transport is a (method, path) -> response script."""

    def __init__(self, script: Dict[Tuple[str, str], Any]) -> None:
        super().__init__(
            registry_base_url="http://registry.test",
            research_base_url="http://research.test",
            consultation_base_url="http://consult.test",
            timeout_seconds=1,
        )
        self.script = script
        self.requests: List[Tuple[str, str, Optional[Dict[str, Any]]]] = []

    def _request_json(
        self,
        authority: str,
        method: str,
        base_url: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Any:
        self.requests.append((method, path, payload))
        try:
            handler = self.script[(method, path)]
        except KeyError:  # pragma: no cover - test wiring error
            raise AssertionError(f"unexpected canonical request {method} {path}")
        if isinstance(handler, list):
            handler = handler.pop(0)
        if isinstance(handler, Exception):
            raise handler
        return handler

    def calls(self, method: str, path: str) -> int:
        return sum(
            1 for m, p, _ in self.requests if m == method and p == path
        )


def _task_readback() -> Dict[str, Any]:
    return {"task_id": TASK_ID, "status": "accepted"}


def _run_readback(status: str = "queued") -> Dict[str, Any]:
    return {"run_id": RUN_ID, "task_id": TASK_ID, "status": status}


# --- research: failure injection between downstream steps ------------------


def test_research_run_post_failure_reports_created_task_partial_effect() -> None:
    adapter = ScriptedAdapter(
        {
            ("POST", TASKS_PATH): {"task_id": TASK_ID},
            ("GET", TASK_PATH): _task_readback(),
            ("POST", RUNS_PATH): _unavailable("research_orchestrator"),
        }
    )
    with pytest.raises(CanonicalOperationError) as excinfo:
        adapter.dispatch_research_run(task_payload={}, run_payload={})
    assert excinfo.value.partial_effects == {"research_task_id": TASK_ID}
    assert excinfo.value.retryable is True


def test_research_task_readback_failure_reports_created_task() -> None:
    adapter = ScriptedAdapter(
        {
            ("POST", TASKS_PATH): {"task_id": TASK_ID},
            ("GET", TASK_PATH): _unavailable("research_orchestrator"),
        }
    )
    with pytest.raises(CanonicalOperationError) as excinfo:
        adapter.dispatch_research_run(task_payload={}, run_payload={})
    assert excinfo.value.partial_effects == {"research_task_id": TASK_ID}


def test_research_run_readback_failure_reports_task_and_run() -> None:
    adapter = ScriptedAdapter(
        {
            ("POST", TASKS_PATH): {"task_id": TASK_ID},
            ("GET", TASK_PATH): _task_readback(),
            ("POST", RUNS_PATH): {"run_id": RUN_ID},
            ("GET", RUN_PATH): _unavailable("research_orchestrator"),
        }
    )
    with pytest.raises(CanonicalOperationError) as excinfo:
        adapter.dispatch_research_run(task_payload={}, run_payload={})
    assert excinfo.value.partial_effects == {
        "research_task_id": TASK_ID,
        "research_run_id": RUN_ID,
    }


def test_research_task_post_failure_has_no_partial_effects() -> None:
    adapter = ScriptedAdapter(
        {("POST", TASKS_PATH): _unavailable("research_orchestrator")}
    )
    with pytest.raises(CanonicalOperationError) as excinfo:
        adapter.dispatch_research_run(task_payload={}, run_payload={})
    assert excinfo.value.partial_effects is None
    assert excinfo.value.retryable is True


# --- research: resume adopts recorded downstream resources -----------------


def test_research_resume_adopts_task_and_run_without_any_post() -> None:
    adapter = ScriptedAdapter(
        {
            ("GET", TASK_PATH): _task_readback(),
            ("GET", RUN_PATH): _run_readback(),
        }
    )
    result = adapter.dispatch_research_run(
        task_payload={},
        run_payload={},
        resume={"research_task_id": TASK_ID, "research_run_id": RUN_ID},
    )
    assert result["task"]["task_id"] == TASK_ID
    assert result["run"]["run_id"] == RUN_ID
    assert all(method == "GET" for method, _, _ in adapter.requests)


def test_research_resume_with_task_only_creates_the_run_exactly_once() -> None:
    adapter = ScriptedAdapter(
        {
            ("GET", TASK_PATH): _task_readback(),
            ("POST", RUNS_PATH): {"run_id": RUN_ID},
            ("GET", RUN_PATH): _run_readback(),
        }
    )
    result = adapter.dispatch_research_run(
        task_payload={},
        run_payload={},
        resume={"research_task_id": TASK_ID},
    )
    assert result["run"]["run_id"] == RUN_ID
    assert adapter.calls("POST", TASKS_PATH) == 0
    assert adapter.calls("POST", RUNS_PATH) == 1


def test_research_resume_recreates_task_when_downstream_never_admitted_it() -> None:
    adapter = ScriptedAdapter(
        {
            ("GET", TASK_PATH): [_not_found("research_orchestrator"), _task_readback()],
            ("POST", TASKS_PATH): {"task_id": TASK_ID},
            ("POST", RUNS_PATH): {"run_id": RUN_ID},
            ("GET", RUN_PATH): _run_readback(),
        }
    )
    result = adapter.dispatch_research_run(
        task_payload={},
        run_payload={},
        resume={"research_task_id": TASK_ID, "research_run_id": RUN_ID},
    )
    assert result["run"]["run_id"] == RUN_ID
    # 404 on the recorded task falls back to creation with the same
    # downstream idempotency key carried in the payload.
    assert adapter.calls("POST", TASKS_PATH) == 1


# --- consultation: failure injection between downstream steps --------------


def test_consultation_submit_failure_reports_created_request() -> None:
    adapter = ScriptedAdapter(
        {
            ("POST", CONSULT_CREATE_PATH): {
                "request_id": CONSULT_ID,
                "status": "draft",
            },
            ("POST", CONSULT_SUBMIT_PATH): _unavailable("consultation_service"),
        }
    )
    with pytest.raises(CanonicalOperationError) as excinfo:
        adapter.open_consultation(payload={}, request_id=CONSULT_ID)
    assert excinfo.value.partial_effects == {
        "consultation_request_id": CONSULT_ID
    }


def test_consultation_readback_failure_reports_created_request() -> None:
    adapter = ScriptedAdapter(
        {
            ("POST", CONSULT_CREATE_PATH): {
                "request_id": CONSULT_ID,
                "status": "submitted",
            },
            ("GET", CONSULT_GET_PATH): _unavailable("consultation_service"),
        }
    )
    with pytest.raises(CanonicalOperationError) as excinfo:
        adapter.open_consultation(payload={}, request_id=CONSULT_ID)
    assert excinfo.value.partial_effects == {
        "consultation_request_id": CONSULT_ID
    }


def test_consultation_unknown_create_outcome_is_a_partial_effect() -> None:
    adapter = ScriptedAdapter(
        {("POST", CONSULT_CREATE_PATH): _unavailable("consultation_service")}
    )
    with pytest.raises(CanonicalOperationError) as excinfo:
        adapter.open_consultation(payload={}, request_id=CONSULT_ID)
    # Retryable transport failure: the deterministic request id may exist
    # downstream, so it must be recorded for adoption on retry.
    assert excinfo.value.partial_effects == {
        "consultation_request_id": CONSULT_ID
    }


def test_consultation_definite_create_rejection_has_no_partial_effects() -> None:
    adapter = ScriptedAdapter(
        {
            ("POST", CONSULT_CREATE_PATH): CanonicalOperationError(
                "consultation_service",
                "canonical request was rejected",
                status_code=422,
                retryable=False,
            )
        }
    )
    with pytest.raises(CanonicalOperationError) as excinfo:
        adapter.open_consultation(payload={}, request_id=CONSULT_ID)
    assert excinfo.value.partial_effects is None


# --- consultation: resume adopts the recorded request ----------------------


def test_consultation_resume_adopts_existing_submitted_request() -> None:
    adapter = ScriptedAdapter(
        {
            ("GET", CONSULT_GET_PATH): {
                "request_id": CONSULT_ID,
                "status": "submitted",
            }
        }
    )
    readback = adapter.open_consultation(
        payload={}, request_id=CONSULT_ID, resume=True
    )
    assert readback["status"] == "submitted"
    assert adapter.calls("POST", CONSULT_CREATE_PATH) == 0
    assert adapter.calls("POST", CONSULT_SUBMIT_PATH) == 0


def test_consultation_resume_submits_an_adopted_draft_request() -> None:
    adapter = ScriptedAdapter(
        {
            ("GET", CONSULT_GET_PATH): [
                {"request_id": CONSULT_ID, "status": "draft"},
                {"request_id": CONSULT_ID, "status": "submitted"},
            ],
            ("POST", CONSULT_SUBMIT_PATH): {},
        }
    )
    readback = adapter.open_consultation(
        payload={}, request_id=CONSULT_ID, resume=True
    )
    assert readback["status"] == "submitted"
    assert adapter.calls("POST", CONSULT_CREATE_PATH) == 0
    assert adapter.calls("POST", CONSULT_SUBMIT_PATH) == 1


def test_consultation_resume_creates_when_recorded_request_is_missing() -> None:
    adapter = ScriptedAdapter(
        {
            ("GET", CONSULT_GET_PATH): [
                _not_found("consultation_service"),
                {"request_id": CONSULT_ID, "status": "submitted"},
            ],
            ("POST", CONSULT_CREATE_PATH): {
                "request_id": CONSULT_ID,
                "status": "submitted",
            },
        }
    )
    readback = adapter.open_consultation(
        payload={"task": "review"}, request_id=CONSULT_ID, resume=True
    )
    assert readback["request_id"] == CONSULT_ID
    assert adapter.calls("POST", CONSULT_CREATE_PATH) == 1
    created_payload = next(
        payload
        for method, path, payload in adapter.requests
        if method == "POST" and path == CONSULT_CREATE_PATH
    )
    # Creation after a lost prior attempt reuses the same deterministic id.
    assert created_payload["request_id"] == CONSULT_ID
