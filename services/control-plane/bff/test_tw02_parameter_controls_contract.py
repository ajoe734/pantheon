from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

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


@contextmanager
def _seeded_client(*, service_backed_control_store: bool = False):
    tracked_env = {
        "PANTHEON_BFF_TRAINER_CONTROL_STORE": os.environ.get("PANTHEON_BFF_TRAINER_CONTROL_STORE"),
    }
    with tempfile.TemporaryDirectory() as td:
        control_store_path: Path | None = None
        if service_backed_control_store:
            control_store_path = Path(td) / "trainer_controls.json"
            control_store_path.write_text(
                json.dumps(
                    {
                        "trn-20260419-001": {
                            "session_id": "trn-20260419-001",
                            "status": "active",
                            "controls": [
                                {"parameter_key": "reversal_threshold", "current_value": 0.55},
                                {"parameter_key": "minimum_hold_bars", "current_value": 3},
                            ],
                        },
                        "trn-20260418-003": {"session_id": "trn-20260418-003", "status": "completed", "controls": []},
                    },
                    indent=2,
                    ensure_ascii=True,
                ),
                encoding="utf-8",
            )
            os.environ["PANTHEON_BFF_TRAINER_CONTROL_STORE"] = str(control_store_path)
        else:
            os.environ.pop("PANTHEON_BFF_TRAINER_CONTROL_STORE", None)

        store = create_training_read_surface_double()
        app = FastAPI()
        router = create_training_router(
            read_surface=store,
            get_read_store=lambda: store,
            extract_identity=_extract_identity,
            require_read_role=lambda _identity: None,
            bff_error=_bff_error,
            utc_now=lambda: "2026-04-20T19:50:00Z",
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
            yield client, control_store_path
        finally:
            for key, value in tracked_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def test_tw02_get_controls_returns_backend_owned_shape_and_degraded_snapshot_state() -> None:
    with _seeded_client() as (client, _control_store_path):
        response = client.get(
            "/api/v1/trainer/sessions/trn-20260419-001/controls",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["object_ref"] == {
            "type": "TrainerControlState",
            "id": "trn-20260419-001",
        }
        assert payload["session_id"] == "trn-20260419-001"
        assert payload["status"] == "active"
        assert [control["parameter_key"] for control in payload["controls"]] == [
            "reversal_threshold",
            "minimum_hold_bars",
        ]
        assert payload["allowedActions"] == {"canPatchControls": False}
        assert payload["meta"]["surfaces"]["trainer_controls"]["state"] == "degraded"
        assert payload["meta"]["staleness"]["status"] == "stale"


def test_tw02_patch_accepts_service_backed_patch_and_persists_diff() -> None:
    with _seeded_client(service_backed_control_store=True) as (client, control_store_path):
        assert control_store_path is not None

        response = client.post(
            "/api/v1/trainer/sessions/trn-20260419-001/patch",
            json={
                "patches": [
                    {
                        "parameter_key": "reversal_threshold",
                        "proposed_value": 0.65,
                    }
                ]
            },
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["session_id"] == "trn-20260419-001"
        assert payload["status"] == "accepted"
        assert payload["warnings"] == []
        assert payload["diff"]["updated_controls"] == [
            {
                "field": "reversal_threshold",
                "before": 0.55,
                "after": 0.65,
                "validation_status": "accepted",
            }
        ]
        assert payload["allowedActions"] == {"canPatchControls": True}
        assert payload["meta"]["surfaces"]["trainer_controls"]["state"] == "ok"

        current_control = next(
            row for row in payload["current_controls"] if row["parameter_key"] == "reversal_threshold"
        )
        assert current_control["current_value"] == 0.65

        persisted = json.loads(control_store_path.read_text(encoding="utf-8"))
        persisted_control = next(
            row
            for row in persisted["trn-20260419-001"]["controls"]
            if row["parameter_key"] == "reversal_threshold"
        )
        assert persisted_control["current_value"] == 0.65


def test_tw02_patch_returns_rejected_shape_for_invalid_control_update() -> None:
    with _seeded_client(service_backed_control_store=True) as (client, control_store_path):
        assert control_store_path is not None

        response = client.post(
            "/api/v1/trainer/sessions/trn-20260419-001/patch",
            json={
                "patches": [
                    {
                        "parameter_key": "minimum_hold_bars",
                        "proposed_value": 12,
                    }
                ]
            },
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["status"] == "rejected"
        assert payload["error_code"] == "CONTROL_PATCH_VALIDATION_FAILED"
        assert payload["field_errors"] == [
            {
                "field": "minimum_hold_bars",
                "reason": "exceeds_allowed_range",
                "current_value": 3,
                "requested_value": 12,
                "allowed_range": {
                    "min": 1,
                    "max": 8,
                },
            }
        ]
        assert payload["rejected_changes"] == []
        assert payload["allowedActions"] == {"canPatchControls": True}
        assert payload["meta"]["surfaces"]["trainer_controls"]["state"] == "ok"

        current_control = next(
            row for row in payload["current_controls"] if row["parameter_key"] == "minimum_hold_bars"
        )
        assert current_control["current_value"] == 3

        persisted = json.loads(control_store_path.read_text(encoding="utf-8"))
        persisted_control = next(
            row
            for row in persisted["trn-20260419-001"]["controls"]
            if row["parameter_key"] == "minimum_hold_bars"
        )
        assert persisted_control["current_value"] == 3


def test_tw02_patch_rejects_when_patch_authority_is_false() -> None:
    with _seeded_client() as (client, _control_store_path):
        response = client.post(
            "/api/v1/trainer/sessions/trn-20260419-001/patch",
            json={
                "patches": [
                    {
                        "parameter_key": "reversal_threshold",
                        "proposed_value": 0.65,
                    }
                ]
            },
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 409, response.text

        payload = response.json()
        assert payload["error"]["code"] == "PRECONDITION_FAILED"
        assert payload["error"]["details"]["precondition_failed"] == (
            "allowedActions.canPatchControls"
        )


def test_tw02_patch_rejects_non_active_session_status() -> None:
    with _seeded_client(service_backed_control_store=True) as (client, _control_store_path):
        response = client.post(
            "/api/v1/trainer/sessions/trn-20260418-003/patch",
            json={
                "patches": [
                    {
                        "parameter_key": "reversal_threshold",
                        "proposed_value": 0.65,
                    }
                ]
            },
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 409, response.text

        payload = response.json()
        assert payload["error"]["code"] == "OPERATION_NOT_ALLOWED"
        assert payload["error"]["details"]["precondition_failed"] == "status"
