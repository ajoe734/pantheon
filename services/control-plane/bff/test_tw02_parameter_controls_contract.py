from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient


from services.control_plane.bff import main as bff_main
from test_training_session_service_client import create_training_read_surface_double


OPERATOR_AUTH = "Bearer test-operator:operator"


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

        original_store = bff_main.read_store
        bff_main.read_store = create_training_read_surface_double()
        client = TestClient(bff_main.app)
        try:
            yield client, control_store_path
        finally:
            bff_main.read_store = original_store
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
