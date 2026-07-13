from __future__ import annotations

import importlib
import os
import sys
from io import BytesIO
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, Optional
from urllib.error import HTTPError
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

import command_executor
import main as bff_main
import read_store as read_store_module
from command_queue import CommandStore
from read_store import ReadSurfaceStore


AUTHORITY_URL = "http://capital-authority.test"
HEADERS = {"Authorization": "Bearer op-2:operator"}


def rebalance_payload(**overrides: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "capital_pool_id": "pool-real",
        "ranking_snapshot_id": "rank-q3",
        "reason": "quarterly",
        "lines": [
            {
                "persona_id": "p-live",
                "stage": "live_running",
                "capital_scope": "pool",
                "capital_pool_id": "pool-real",
                "capital_sleeve_id": "sleeve-live",
                "current_weight": 0.10,
                "target_weight": 0.12,
                "delta": 0.02,
                "cap_reasons": ["quarterly_increase_cap_25pct"],
                "evidence_refs": ["ev-1"],
            }
        ],
        "simulation": {"status": "passed", "run_id": "sim-q3"},
        "constraints": {"pool_total_max": 1.0, "max_turnover": 0.25},
        "rollback_target": {
            "snapshot_id": "allocation-before-q3",
            "allocation_version": 7,
        },
        "audit_refs": ["audit-ranking-q3", "audit-simulation-q3"],
    }
    payload.update(overrides)
    return payload


class CapitalBffAuthorityHarness:
    """Run BFF tests against the real, durable Capital service boundary."""

    _ENV_KEYS = (
        "BFF_COMMIT",
        "CAPITAL_AUDIT_BACKEND",
        "CAPITAL_DATA_DIR",
        "CAPITAL_STORE_BACKEND",
        "PANTHEON_BFF_CAPITAL_ALLOCATION_STORE",
        "PANTHEON_BFF_CAPITAL_POOL_STORE",
        "PANTHEON_BFF_CONTAINMENT_STORE",
        "PANTHEON_BFF_PERSONA_REGISTRY_STORE",
        "PANTHEON_BFF_REBALANCE_STORE",
        "PANTHEON_CAPITAL_API_URL",
        "PANTHEON_CAPITAL_SERVICE_URL",
        "PANTHEON_ENV",
        "PANTHEON_GOVERNANCE_DATA_DIR",
        "PANTHEON_PERSONA_DATA_DIR",
        "PANTHEON_PERSISTENCE_POSTURE",
    )

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.capital_data_dir = self.root / "capital"
        self.read_path = self.root / "bff-read-surfaces.json"
        self.command_path = self.root / "bff-commands.jsonl"
        self.capital_module: Optional[ModuleType] = None
        self.capital_client: Optional[TestClient] = None
        self.client: Optional[TestClient] = None

    def __enter__(self) -> "CapitalBffAuthorityHarness":
        self.root.mkdir(parents=True, exist_ok=True)
        self.capital_data_dir.mkdir(parents=True, exist_ok=True)
        self._environment = {key: os.environ.get(key) for key in self._ENV_KEYS}
        self._previous_capital_module = sys.modules.get("services.capital.main")
        self._original_read_store = bff_main.read_store
        self._original_command_store = bff_main.command_store
        self._original_post_json = command_executor._post_json
        self._original_http_json_get = read_store_module._http_json_get
        self._capital_idempotency = dict(bff_main._CAPITAL_BFF_IDEMPOTENCY)
        self._command_auth_context = dict(bff_main._COMMAND_AUTH_CONTEXT)
        self._persona_overlay = dict(bff_main._PERSONA_BFF_OVERLAY)

        for key in self._ENV_KEYS:
            os.environ.pop(key, None)
        os.environ.update(
            {
                "CAPITAL_AUDIT_BACKEND": "jsonl",
                "CAPITAL_DATA_DIR": str(self.capital_data_dir),
                "CAPITAL_STORE_BACKEND": "json",
                "PANTHEON_CAPITAL_API_URL": AUTHORITY_URL,
                "PANTHEON_ENV": "dev",
                "PANTHEON_GOVERNANCE_DATA_DIR": str(self.capital_data_dir),
                "PANTHEON_PERSISTENCE_POSTURE": "dev",
            }
        )

        sys.modules.pop("services.capital.main", None)
        self.capital_module = importlib.import_module("services.capital.main")
        self.capital_client = TestClient(self.capital_module.app)
        command_executor._post_json = self._post_json
        read_store_module._http_json_get = self._http_json_get
        self._reset_bff_process_state()

        response = self.capital_client.post(
            "/api/capital-pools",
            json={
                "actor_id": "capital-admin-1",
                "actor_role": "capital.admin",
                "pool_id": "pool-real",
                "name": "Regression Pool",
                "owner_id": "fund-real",
                "owner_type": "fund",
                "status": "active",
                "risk_policy_ref": "risk-main",
            },
        )
        assert response.status_code == 201, response.text
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.client is not None:
            self.client.close()
        if self.capital_client is not None:
            self.capital_client.close()

        command_executor._post_json = self._original_post_json
        read_store_module._http_json_get = self._original_http_json_get
        bff_main.read_store = self._original_read_store
        bff_main.command_store = self._original_command_store
        bff_main._CAPITAL_BFF_IDEMPOTENCY.clear()
        bff_main._CAPITAL_BFF_IDEMPOTENCY.update(self._capital_idempotency)
        bff_main._COMMAND_AUTH_CONTEXT.clear()
        bff_main._COMMAND_AUTH_CONTEXT.update(self._command_auth_context)
        bff_main._PERSONA_BFF_OVERLAY.clear()
        bff_main._PERSONA_BFF_OVERLAY.update(self._persona_overlay)

        if self._previous_capital_module is None:
            sys.modules.pop("services.capital.main", None)
        else:
            sys.modules["services.capital.main"] = self._previous_capital_module
        for key, value in self._environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _reset_bff_process_state(self) -> None:
        if self.client is not None:
            self.client.close()
        bff_main.read_store = ReadSurfaceStore(
            str(self.read_path),
            allow_local_snapshot_fallback=False,
        )
        bff_main.command_store = CommandStore(str(self.command_path))
        bff_main._CAPITAL_BFF_IDEMPOTENCY.clear()
        bff_main._COMMAND_AUTH_CONTEXT.clear()
        bff_main._PERSONA_BFF_OVERLAY.clear()
        self.client = TestClient(bff_main.app)

    def restart(self) -> None:
        """Rebuild both owner and BFF process-local state over the same files."""
        assert self.capital_module is not None
        if self.capital_client is not None:
            self.capital_client.close()
        self.capital_module = importlib.reload(self.capital_module)
        self.capital_client = TestClient(self.capital_module.app)
        self._reset_bff_process_state()

    def create_persona(self, persona_id: str = "p-live") -> Dict[str, Any]:
        return bff_main.read_store.create_persona(
            persona_id=persona_id,
            name="Contained Live Persona",
            actor_id="operator-test",
            lifecycle_state="live_running",
            risk_level="high",
            mandate="systematic live trading",
            strategy_family="momentum",
        )

    def _post_json(
        self,
        url: str,
        payload: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        del auth_token, mfa_token
        assert self.capital_client is not None
        parsed = urlsplit(url)
        path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        response = self.capital_client.post(path, json=payload)
        if response.status_code >= 400:
            raise HTTPError(
                url,
                response.status_code,
                response.reason_phrase,
                response.headers,
                BytesIO(response.content),
            )
        return response.json()

    def _http_json_get(
        self,
        base_url: str,
        path: str,
        *,
        headers: Optional[Dict[str, str]] = None,
    ) -> tuple[bool, Any]:
        assert base_url.rstrip("/") == AUTHORITY_URL
        assert self.capital_client is not None
        response = self.capital_client.get(path, headers=headers or {})
        if response.status_code == 404:
            return True, None
        if response.status_code >= 400:
            return False, None
        if not response.content:
            return True, None
        return True, response.json()
