"""
Contract tests for TRN-003: rapid-eval request / response.

Covers:
  POST /api/v1/trainer/sessions/{session_id}/rapid-eval
  GET  /api/v1/trainer/sessions/{session_id}/rapid-eval/{eval_id}
"""
from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from typing import Any, Iterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from services.control_plane.bff.auth.policy import (
    bff_error,
    default_utc_now,
    extract_identity_stub,
    require_read_role,
)
from services.control_plane.bff.test_training_session_service_client import (
    create_training_read_surface_double,
)
from services.control_plane.bff.training.router import create_training_router


OPERATOR_AUTH = "Bearer test-operator:operator"
HEADERS = {"Authorization": OPERATOR_AUTH}

# Fixture session ids from the built-in local snapshot
_ACTIVE_SESSION = "trn-20260419-001"   # status=active
_COMPLETED_SESSION = "trn-20260418-003"  # status=completed


def _page_slice(
    items: list[dict[str, Any]],
    page_token: str | None,
    page_size: int,
) -> tuple[list[dict[str, Any]], str | None]:
    start = int(page_token) if page_token else 0
    end = start + page_size
    next_page = str(end) if end < len(items) else None
    return items[start:end], next_page


def _dataset_surface_status(
    dataset: str,
    *,
    snapshot_at: str | None = None,
    has_data: bool | None = None,
    missing_message: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    return {
        "status": "degraded" if source == "local_snapshot" else "ok",
        "source": source or "local_snapshot",
        "staleness": {
            "served_from": "local_snapshot",
            "last_known_at": snapshot_at or default_utc_now(),
        },
    }


def _create_test_app(read_surface: Any) -> FastAPI:
    app = FastAPI()

    @app.exception_handler(HTTPException)
    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(request: Any, exc: Any) -> JSONResponse:
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": "HTTP_ERROR", "message": str(exc.detail)}},
        )

    app.include_router(
        create_training_router(
            read_surface=read_surface,
            extract_identity=extract_identity_stub,
            require_read_role=require_read_role,
            bff_error=bff_error,
            utc_now=default_utc_now,
            page_slice=_page_slice,
            dataset_surface_status=_dataset_surface_status,
        )
    )
    return app


@contextmanager
def _client(*, service_backed: bool = False) -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        orig_env = os.environ.get("PANTHEON_BFF_RAPID_EVAL_STORE")
        if service_backed:
            os.environ["PANTHEON_BFF_RAPID_EVAL_STORE"] = os.path.join(td, "rapid_evals.json")
        else:
            os.environ.pop("PANTHEON_BFF_RAPID_EVAL_STORE", None)
        store = create_training_read_surface_double()
        app = _create_test_app(store)
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
