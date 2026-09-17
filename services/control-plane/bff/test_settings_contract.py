from __future__ import annotations

import json
import os
import tempfile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.auth.policy import extract_identity_stub, require_admin_mfa
from services.control_plane.bff.core.app_factory import create_settings_router
from services.control_plane.bff.settings_store import SettingsStore


ADMIN_TOKEN = "Bearer op-admin:admin:mfa"
OPERATOR_TOKEN = "Bearer op-operator:operator"


def _make_client(settings_path: str) -> TestClient:
    store = SettingsStore(settings_path)
    app = FastAPI()
    app.include_router(
        create_settings_router(
            settings_store=store,
            extract_identity=lambda auth, mfa_token=None: extract_identity_stub(auth),
            require_admin_mfa=require_admin_mfa,
        )
    )
    return TestClient(app)


def test_settings_bundle_round_trip_and_export() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _make_client(os.path.join(td, "settings.json"))

        response = client.get(
            "/api/v1/settings",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["general"]["language"] == "zh-TW"
        assert payload["trading"]["defaultLeverageLimit"] == 2.0
        assert payload["featureFlags"]["advanced_charts"] is True

        update_response = client.post(
            "/api/v1/settings",
            headers={"Authorization": ADMIN_TOKEN},
            json={
                "settings": {
                    "general": {"theme": "light"},
                    "featureFlags": {"websocket": True},
                }
            },
        )
        assert update_response.status_code == 200, update_response.text
        updated = update_response.json()["settings"]
        assert updated["general"]["theme"] == "light"
        assert updated["featureFlags"]["websocket"] is True
        assert updated["general"]["language"] == "zh-TW"

        export_response = client.get(
            "/api/v1/settings/export",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert export_response.status_code == 200, export_response.text
        exported = json.loads(export_response.json()["jsonData"])
        assert exported["general"]["theme"] == "light"
        assert exported["featureFlags"]["websocket"] is True


def test_settings_update_and_import_require_admin_mfa() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _make_client(os.path.join(td, "settings.json"))

        update_response = client.post(
            "/api/v1/settings",
            headers={"Authorization": OPERATOR_TOKEN},
            json={"settings": {"general": {"theme": "light"}}},
        )
        assert update_response.status_code == 403, update_response.text

        import_response = client.post(
            "/api/v1/settings/import",
            headers={"Authorization": OPERATOR_TOKEN},
            json={"jsonData": "{}"},
        )
        assert import_response.status_code == 403, import_response.text


def test_settings_import_replaces_bundle_and_validates_json() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _make_client(os.path.join(td, "settings.json"))

        import_payload = {
            "general": {
                "language": "en-US",
                "timezone": "UTC",
                "currency": "USD",
                "theme": "system",
                "dateFormat": "YYYY-MM-DD",
            },
            "trading": {
                "defaultLeverageLimit": 1.5,
                "dayLossLimitPct": 4.0,
                "tradingHours": [{"market": "NYSE", "open": "09:30", "close": "16:00"}],
            },
            "risk": {
                "maxDrawdownLimitPct": 12.0,
                "var95WindowDays": 126,
                "positionLimitPct": 8.0,
            },
            "data": {
                "primary": "polygon",
                "fallbacks": ["yahoo"],
                "refreshSec": 15,
                "keys": {"polygon": "****"},
            },
            "notifications": {
                "channels": {"email": True, "inApp": True, "sms": True},
                "severityThreshold": "high",
                "digest": "weekly",
            },
            "ai": {
                "enableKnowledgeGraph": False,
                "xaiLevel": "high",
                "rlhfBatchDays": 14,
                "autoParamTuning": False,
            },
            "security": {
                "allowedIPs": ["10.0.0.1/32"],
                "twoFAEnforced": True,
            },
            "featureFlags": {"demo": False, "websocket": True},
        }
        import_response = client.post(
            "/api/v1/settings/import",
            headers={"Authorization": ADMIN_TOKEN},
            json={"jsonData": json.dumps(import_payload)},
        )
        assert import_response.status_code == 200, import_response.text
        imported = import_response.json()["settings"]
        assert imported["general"]["language"] == "en-US"
        assert imported["security"]["twoFAEnforced"] is True

        invalid_response = client.post(
            "/api/v1/settings/import",
            headers={"Authorization": ADMIN_TOKEN},
            json={"jsonData": "{invalid"},
        )
        assert invalid_response.status_code == 400, invalid_response.text
