from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from typing import Any

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

_now_override: str | None = None


def _utc_now() -> str:
    return _now_override if _now_override is not None else default_utc_now()


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
            "last_known_at": snapshot_at or _utc_now(),
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
            utc_now=_utc_now,
            page_slice=_page_slice,
            dataset_surface_status=_dataset_surface_status,
        )
    )
    return app


@contextmanager
def _seeded_client(
    *,
    allow_local_snapshot_fallback: bool = True,
    service_backed_preview_store: bool = False,
):
    with tempfile.TemporaryDirectory() as td:
        original_preview_store = os.environ.get("PANTHEON_BFF_TRAINER_PREVIEW_STORE")
        if service_backed_preview_store:
            os.environ["PANTHEON_BFF_TRAINER_PREVIEW_STORE"] = os.path.join(
                td,
                "trainer_previews.json",
            )
        else:
            os.environ.pop("PANTHEON_BFF_TRAINER_PREVIEW_STORE", None)
        store = create_training_read_surface_double()
        app = _create_test_app(store)
        client = TestClient(app)
        try:
            yield client
        finally:
            if original_preview_store is None:
                os.environ.pop("PANTHEON_BFF_TRAINER_PREVIEW_STORE", None)
            else:
                os.environ["PANTHEON_BFF_TRAINER_PREVIEW_STORE"] = original_preview_store


def test_tw03_get_preview_returns_backend_owned_compare_payload() -> None:
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/trainer/sessions/trn-20260419-001/preview",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["session_id"] == "trn-20260419-001"
        assert payload["status"] == "complete"
        assert payload["eval_id"] == "teval-20260419-014"
        assert [warning["level"] for warning in payload["warnings"]] == [
            "high",
            "medium",
            "informational",
        ]
        assert payload["warning_count_by_level"] == {
            "critical": 0,
            "high": 1,
            "medium": 1,
            "informational": 1,
        }
        assert payload["polling"] == {
            "enabled": False,
            "poll_interval_ms": 3000,
            "max_wait_ms": 45000,
            "deadline_at": None,
        }
        assert payload["meta"]["surfaces"]["trainer_preview"] == "stale"
        assert payload["allowedActions"] == {"canRefreshPreview": True}
        assert payload["degraded_copy"] is not None


def test_tw03_pending_preview_supports_eval_lookup_and_polling_contract() -> None:
    global _now_override
    with _seeded_client() as client:
        _now_override = "2026-04-20T19:50:00Z"
        try:
            response = client.get(
                "/api/v1/trainer/sessions/trn-20260419-001/preview",
                params={"eval_id": "teval-20260419-015"},
                headers={"Authorization": OPERATOR_AUTH},
            )
        finally:
            _now_override = None
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["status"] == "pending"
        assert payload["eval_id"] == "teval-20260419-015"
        assert payload["metric_delta"] == []
        assert payload["warnings"] == []
        assert payload["allowedActions"] == {"canRefreshPreview": False}
        assert payload["polling"]["enabled"] is True
        assert payload["polling"]["poll_interval_ms"] == 3000
        assert payload["polling"]["max_wait_ms"] == 45000
        assert payload["polling"]["deadline_at"] == "2026-04-20T19:50:45Z"


def test_tw03_refresh_creates_pending_preview_and_reuses_existing_pending_eval() -> None:
    with _seeded_client(service_backed_preview_store=True) as client:
        first = client.post(
            "/api/v1/trainer/sessions/trn-20260419-001/preview",
            json={"refresh_mode": "manual"},
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert first.status_code == 200, first.text

        first_payload = first.json()
        assert first_payload["status"] == "pending"
        assert first_payload["eval_id"] is not None
        assert first_payload["allowedActions"] == {"canRefreshPreview": False}

        second = client.post(
            "/api/v1/trainer/sessions/trn-20260419-001/preview",
            json={"refresh_mode": "manual"},
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert second.status_code == 200, second.text
        assert second.json()["eval_id"] == first_payload["eval_id"]

        fetched = client.get(
            "/api/v1/trainer/sessions/trn-20260419-001/preview",
            params={"eval_id": first_payload["eval_id"]},
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["status"] == "pending"


def test_tw03_preview_unavailable_returns_structured_degraded_success_body() -> None:
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/trainer/sessions/trn-20260418-003/preview",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["status"] == "preview_unavailable"
        assert payload["eval_id"] is None
        assert payload["metric_delta"] == []
        assert payload["warnings"] == []
        assert payload["warning_count_by_level"] == {
            "critical": 0,
            "high": 0,
            "medium": 0,
            "informational": 0,
        }
        assert payload["preview_quality"] == "not_available"
        assert payload["allowedActions"] == {"canRefreshPreview": False}
        assert payload["polling"]["enabled"] is False
        assert payload["meta"]["surfaces"]["trainer_preview"] == "degraded"
        assert payload["degraded_copy"]["title"] == "Trainer preview is temporarily unavailable"
