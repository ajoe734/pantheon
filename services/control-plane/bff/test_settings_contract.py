from __future__ import annotations

import json
import os
import sys
import tempfile
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

from services.control_plane.bff import main as bff_main
from settings_store import SettingsStore


ADMIN_TOKEN = "Bearer op-admin:admin:mfa"
OPERATOR_TOKEN = "Bearer op-operator:operator"

# These tests exercise settings contract logic; auth is satisfied via stub mode.
_STUB_ENV = {"PANTHEON_BFF_AUTH_STUB": "true"}


@patch.dict(os.environ, _STUB_ENV, clear=False)
def test_settings_bundle_round_trip_and_export() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.settings_store
        bff_main.settings_store = SettingsStore(os.path.join(td, "settings.json"))
        client = TestClient(bff_main.app)

        try:
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
        finally:
            bff_main.settings_store = original_store


@patch.dict(os.environ, _STUB_ENV, clear=False)
def test_settings_update_and_import_require_admin_mfa() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.settings_store
        bff_main.settings_store = SettingsStore(os.path.join(td, "settings.json"))
        client = TestClient(bff_main.app)

        try:
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
        finally:
            bff_main.settings_store = original_store


@patch.dict(os.environ, _STUB_ENV, clear=False)
def test_settings_import_replaces_bundle_and_validates_json() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.settings_store
        bff_main.settings_store = SettingsStore(os.path.join(td, "settings.json"))
        client = TestClient(bff_main.app)

        try:
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
        finally:
            bff_main.settings_store = original_store
