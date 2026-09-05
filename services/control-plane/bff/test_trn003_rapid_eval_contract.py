"""
Contract tests for TRN-003: rapid-eval request / response.

Covers:
  POST /api/v1/trainer/sessions/{session_id}/rapid-eval
  GET  /api/v1/trainer/sessions/{session_id}/rapid-eval/{eval_id}
"""
from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from typing import Iterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from types import SimpleNamespace

from services.control_plane.bff.models import utc_now
from services.control_plane.bff.personas.service import _bff_error
from services.control_plane.bff.training.router import create_training_router
from test_training_session_service_client import create_training_read_surface_double


OPERATOR_AUTH = "Bearer test-operator:operator"
HEADERS = {"Authorization": OPERATOR_AUTH}

# Fixture session ids from the built-in local snapshot
_ACTIVE_SESSION = "trn-20260419-001"   # status=active
_COMPLETED_SESSION = "trn-20260418-003"  # status=completed


@contextmanager
def _client(*, service_backed: bool = False) -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        orig_env = os.environ.get("PANTHEON_BFF_RAPID_EVAL_STORE")
        if service_backed:
            os.environ["PANTHEON_BFF_RAPID_EVAL_STORE"] = os.path.join(td, "rapid_evals.json")
        else:
            os.environ.pop("PANTHEON_BFF_RAPID_EVAL_STORE", None)
        read_store = create_training_read_surface_double()
        app = FastAPI()

        @app.exception_handler(HTTPException)
        @app.exception_handler(StarletteHTTPException)
        async def _bff_err_handler(request, exc):
            if isinstance(exc.detail, dict) and "error" in exc.detail:
                return JSONResponse(status_code=exc.status_code, content=exc.detail)
            return JSONResponse(
                status_code=exc.status_code,
                content={"error": {"message": str(exc.detail), "details": {"reason": str(exc.detail)}}},
            )

        app.include_router(
            create_training_router(
                get_read_store=lambda: read_store,
                extract_identity=lambda _authorization: SimpleNamespace(
                    operator_id="test-operator",
                    roles=["operator", "read"],
                    claims={},
                ),
                require_read_role=lambda _identity: None,
                bff_error=_bff_error,
                utc_now=utc_now,
                page_slice=lambda items, _token, _size: (items, None),
                dataset_surface_status=lambda *_args, **_kwargs: {"status": "available"},
            )
        )
        try:
            yield TestClient(app)
        finally:
            if orig_env is None:
                os.environ.pop("PANTHEON_BFF_RAPID_EVAL_STORE", None)
            else:
                os.environ["PANTHEON_BFF_RAPID_EVAL_STORE"] = orig_env


_VALID_BODY = {
    "eval_scope": "persona_patch",
    "dataset_version_id": "dv-20260501-001",
    "max_runtime_seconds": 120,
}


# ------------------------------------------------------------------ #
# POST happy path
# ------------------------------------------------------------------ #

def test_trn003_create_rapid_eval_returns_queued_record() -> None:
    with _client(service_backed=True) as c:
        resp = c.post(
            f"/api/v1/trainer/sessions/{_ACTIVE_SESSION}/rapid-eval",
            json=_VALID_BODY,
            headers=HEADERS,
        )
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["rapid_eval_id"].startswith("reval-")
        assert payload["session_id"] == _ACTIVE_SESSION
        assert payload["status"] == "queued"
        assert payload["eval_scope"] == "persona_patch"
        assert payload["dataset_version_id"] == "dv-20260501-001"
        assert payload["max_runtime_seconds"] == 120
        assert payload["requested_at"] is not None
        assert payload["completed_at"] is None
        assert payload["advisory_note"] is not None
        assert payload["meta"]["surfaces"]["rapid_eval"] == "ok"


def test_trn003_create_rapid_eval_with_optional_fields() -> None:
    with _client(service_backed=True) as c:
        body = dict(
            _VALID_BODY,
            eval_scope="strategy_patch",
            patch_ref="patch-20260501-001",
            persona_id="persona-alpha",
            strategy_id="strat-001",
        )
        resp = c.post(
            f"/api/v1/trainer/sessions/{_ACTIVE_SESSION}/rapid-eval",
            json=body,
            headers=HEADERS,
        )
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["eval_scope"] == "strategy_patch"
        assert payload["patch_ref"] == "patch-20260501-001"
        assert payload["persona_id"] == "persona-alpha"
        assert payload["strategy_id"] == "strat-001"


def test_trn003_create_two_rapid_evals_produce_distinct_ids() -> None:
    with _client(service_backed=True) as c:
        first = c.post(
            f"/api/v1/trainer/sessions/{_ACTIVE_SESSION}/rapid-eval",
            json=_VALID_BODY,
            headers=HEADERS,
        )
        second = c.post(
            f"/api/v1/trainer/sessions/{_ACTIVE_SESSION}/rapid-eval",
            json=_VALID_BODY,
            headers=HEADERS,
        )
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert first.json()["rapid_eval_id"] != second.json()["rapid_eval_id"]


# ------------------------------------------------------------------ #
# GET happy path
# ------------------------------------------------------------------ #

def test_trn003_get_rapid_eval_returns_created_record() -> None:
    with _client(service_backed=True) as c:
        created = c.post(
            f"/api/v1/trainer/sessions/{_ACTIVE_SESSION}/rapid-eval",
            json=_VALID_BODY,
            headers=HEADERS,
        )
        assert created.status_code == 200, created.text
        eval_id = created.json()["rapid_eval_id"]

        fetched = c.get(
            f"/api/v1/trainer/sessions/{_ACTIVE_SESSION}/rapid-eval/{eval_id}",
            headers=HEADERS,
        )
        assert fetched.status_code == 200, fetched.text
        payload = fetched.json()
        assert payload["rapid_eval_id"] == eval_id
        assert payload["session_id"] == _ACTIVE_SESSION
        assert payload["status"] == "queued"
        assert payload["meta"]["surfaces"]["rapid_eval"] == "ok"


# ------------------------------------------------------------------ #
# 404 cases
# ------------------------------------------------------------------ #

def test_trn003_post_unknown_session_returns_404() -> None:
    with _client(service_backed=True) as c:
        resp = c.post(
            "/api/v1/trainer/sessions/trn-nonexistent/rapid-eval",
            json=_VALID_BODY,
            headers=HEADERS,
        )
        assert resp.status_code == 404, resp.text


def test_trn003_get_unknown_eval_returns_404() -> None:
    with _client(service_backed=True) as c:
        resp = c.get(
            f"/api/v1/trainer/sessions/{_ACTIVE_SESSION}/rapid-eval/reval-nonexistent",
            headers=HEADERS,
        )
        assert resp.status_code == 404, resp.text


def test_trn003_get_eval_wrong_session_returns_404() -> None:
    """An eval created under one session must not be found under another."""
    other_session = "trn-20260418-003"
    with _client(service_backed=True) as c:
        created = c.post(
            f"/api/v1/trainer/sessions/{_ACTIVE_SESSION}/rapid-eval",
            json=_VALID_BODY,
            headers=HEADERS,
        )
        assert created.status_code == 200, created.text
        eval_id = created.json()["rapid_eval_id"]

        resp = c.get(
            f"/api/v1/trainer/sessions/{other_session}/rapid-eval/{eval_id}",
            headers=HEADERS,
        )
        assert resp.status_code == 404, resp.text


# ------------------------------------------------------------------ #
# 409 invalid state
# ------------------------------------------------------------------ #

def test_trn003_post_completed_session_returns_409() -> None:
    with _client(service_backed=True) as c:
        resp = c.post(
            f"/api/v1/trainer/sessions/{_COMPLETED_SESSION}/rapid-eval",
            json=_VALID_BODY,
            headers=HEADERS,
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["error"]["details"]["precondition_failed"] == "status"


# ------------------------------------------------------------------ #
# 422 validation
# ------------------------------------------------------------------ #

def _precondition_failed(resp) -> str:
    return resp.json()["error"]["details"]["precondition_failed"]


def test_trn003_post_missing_eval_scope_returns_422() -> None:
    with _client(service_backed=True) as c:
        body = {k: v for k, v in _VALID_BODY.items() if k != "eval_scope"}
        resp = c.post(
            f"/api/v1/trainer/sessions/{_ACTIVE_SESSION}/rapid-eval",
            json=body,
            headers=HEADERS,
        )
        assert resp.status_code == 422, resp.text
        assert _precondition_failed(resp) == "eval_scope"


def test_trn003_post_invalid_eval_scope_returns_422() -> None:
    with _client(service_backed=True) as c:
        resp = c.post(
            f"/api/v1/trainer/sessions/{_ACTIVE_SESSION}/rapid-eval",
            json=dict(_VALID_BODY, eval_scope="unknown_scope"),
            headers=HEADERS,
        )
        assert resp.status_code == 422, resp.text
        assert _precondition_failed(resp) == "eval_scope"


def test_trn003_post_missing_dataset_version_id_returns_422() -> None:
    with _client(service_backed=True) as c:
        body = {k: v for k, v in _VALID_BODY.items() if k != "dataset_version_id"}
        resp = c.post(
            f"/api/v1/trainer/sessions/{_ACTIVE_SESSION}/rapid-eval",
            json=body,
            headers=HEADERS,
        )
        assert resp.status_code == 422, resp.text
        assert _precondition_failed(resp) == "dataset_version_id"


def test_trn003_post_missing_max_runtime_seconds_returns_422() -> None:
    with _client(service_backed=True) as c:
        body = {k: v for k, v in _VALID_BODY.items() if k != "max_runtime_seconds"}
        resp = c.post(
            f"/api/v1/trainer/sessions/{_ACTIVE_SESSION}/rapid-eval",
            json=body,
            headers=HEADERS,
        )
        assert resp.status_code == 422, resp.text
        assert _precondition_failed(resp) == "max_runtime_seconds"


def test_trn003_post_zero_max_runtime_seconds_returns_422() -> None:
    with _client(service_backed=True) as c:
        resp = c.post(
            f"/api/v1/trainer/sessions/{_ACTIVE_SESSION}/rapid-eval",
            json=dict(_VALID_BODY, max_runtime_seconds=0),
            headers=HEADERS,
        )
        assert resp.status_code == 422, resp.text
        assert _precondition_failed(resp) == "max_runtime_seconds"
