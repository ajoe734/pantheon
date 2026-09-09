from __future__ import annotations

import json
import os
import tempfile

from typing import Any, Callable, Optional
from unittest.mock import Mock

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.control_plane.bff.core.app_factory import create_settings_router
from services.control_plane.bff.models import OperatorIdentity
from services.control_plane.bff.settings_store import SettingsStore


_FIXED_IDENTITY = OperatorIdentity(
    operator_id="op-admin",
    roles=["admin"],
    mfa_verified=True,
    claims={},
)


def _make_settings_client(
    store: SettingsStore,
    *,
    extract_identity: Optional[Callable[..., Any]] = None,
    require_admin_mfa: Optional[Callable[..., Any]] = None,
) -> TestClient:
    app = FastAPI(title="Settings Contract Test")
    active_extract = extract_identity if extract_identity is not None else Mock(return_value=_FIXED_IDENTITY)
    active_guard = require_admin_mfa if require_admin_mfa is not None else Mock()

    router = create_settings_router(
        settings_store=store,
        extract_identity=active_extract,
        require_admin_mfa=active_guard,
    )
    app.include_router(router)
    client = TestClient(app)
    client.extract_identity = active_extract
    client.require_admin_mfa = active_guard
    return client


def test_settings_bundle_round_trip_and_export() -> None:
    with tempfile.TemporaryDirectory() as td:
        store = SettingsStore(os.path.join(td, "settings.json"))
        client = _make_settings_client(store)

        response = client.get(
            "/api/v1/settings",
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["general"]["language"] == "zh-TW"
        assert payload["trading"]["defaultLeverageLimit"] == 2.0
        assert payload["featureFlags"]["advanced_charts"] is True

        update_response = client.post(
            "/api/v1/settings",
            json={
                "settings": {
                    "general": {"theme": "light"},
                    "featureFlags": {"websocket": True},
                }
            },
        )
        assert update_response.status_code == 200, update_response.text
        client.require_admin_mfa.assert_called_with(_FIXED_IDENTITY, "update_settings")
        updated = update_response.json()["settings"]
        assert updated["general"]["theme"] == "light"
        assert updated["featureFlags"]["websocket"] is True
        assert updated["general"]["language"] == "zh-TW"

        export_response = client.get(
            "/api/v1/settings/export",
        )
        assert export_response.status_code == 200, export_response.text
        exported = json.loads(export_response.json()["jsonData"])
        assert exported["general"]["theme"] == "light"
        assert exported["featureFlags"]["websocket"] is True


def test_settings_update_and_import_require_admin_mfa() -> None:
    with tempfile.TemporaryDirectory() as td:
        store = SettingsStore(os.path.join(td, "settings.json"))
        guard_mock = Mock(side_effect=HTTPException(status_code=403, detail="Admin MFA required"))
        client = _make_settings_client(store, require_admin_mfa=guard_mock)

        update_response = client.post(
            "/api/v1/settings",
            json={"settings": {"general": {"theme": "light"}}},
        )
        assert update_response.status_code == 403, update_response.text
        guard_mock.assert_called_with(_FIXED_IDENTITY, "update_settings")

        guard_mock.reset_mock()
        import_response = client.post(
            "/api/v1/settings/import",
            json={"jsonData": "{}"},
        )
        assert import_response.status_code == 403, import_response.text
        guard_mock.assert_called_with(_FIXED_IDENTITY, "import_settings")


def test_settings_import_replaces_bundle_and_validates_json() -> None:
    with tempfile.TemporaryDirectory() as td:
        store = SettingsStore(os.path.join(td, "settings.json"))
        client = _make_settings_client(store)

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
            json={"jsonData": json.dumps(import_payload)},
        )
        assert import_response.status_code == 200, import_response.text
        imported = import_response.json()["settings"]
        assert imported["general"]["language"] == "en-US"
        assert imported["security"]["twoFAEnforced"] is True

        invalid_response = client.post(
            "/api/v1/settings/import",
            json={"jsonData": "{invalid"},
        )
        assert invalid_response.status_code == 400, invalid_response.text
