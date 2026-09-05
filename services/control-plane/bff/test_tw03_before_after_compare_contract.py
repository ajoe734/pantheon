from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from unittest import mock

from fastapi.testclient import TestClient


from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services.control_plane.bff.models import ErrorCode, OperatorIdentity
from services.control_plane.bff.training.router import create_training_router
from test_training_session_service_client import create_training_read_surface_double


OPERATOR_AUTH = "Bearer test-operator:operator"


def _extract_identity(authorization: str | None = None) -> OperatorIdentity:
    if not authorization or not authorization.startswith("Bearer "):
        return OperatorIdentity(operator_id="anonymous", roles=[])
    token = authorization[len("Bearer "):].strip()
    parts = token.split(":")
    op = parts[0]
    roles = parts[1].split(",") if len(parts) > 1 else ["operator"]
    return OperatorIdentity(operator_id=op, roles=roles)


def _bff_error(status_code: int, code: Any, message: str, reason: str = "", precondition_failed: str | None = None, **kwargs):
    code_val = getattr(code, "value", str(code))
    details = {"reason": reason}
    if precondition_failed:
        details["precondition_failed"] = precondition_failed
    return HTTPException(
        status_code=status_code,
        detail={
            "error": {
                "code": code_val,
                "message": message,
                "details": details,
            }
        },
    )


def _utc_now() -> str:
    return "2026-04-20T19:50:00Z"


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
        app = FastAPI()
        router = create_training_router(
            read_surface=store,
            get_read_store=lambda: store,
            extract_identity=_extract_identity,
            require_read_role=lambda _identity: None,
            bff_error=_bff_error,
            utc_now=lambda: _utc_now(),
            page_slice=lambda items, _tok, _sz: (items, None),
            dataset_surface_status=lambda *_args, **_kwargs: {"status": "available"},
        )
        app.include_router(router)

        @app.exception_handler(HTTPException)
        def _exc_handler(request: Request, exc: HTTPException):
            content = exc.detail if isinstance(exc.detail, dict) else {"error": {"message": str(exc.detail)}}
            return JSONResponse(status_code=exc.status_code, content=content)

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
    with _seeded_client() as client:
        with mock.patch("services.control_plane.bff.test_tw03_before_after_compare_contract._utc_now", return_value="2026-04-20T19:50:00Z"):
            response = client.get(
                "/api/v1/trainer/sessions/trn-20260419-001/preview",
                params={"eval_id": "teval-20260419-015"},
                headers={"Authorization": OPERATOR_AUTH},
            )
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
