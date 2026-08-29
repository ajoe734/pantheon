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
from ports import create_in_memory_read_surface_ports


AUTHORITY_URL = "http://capital-authority.test"
HEADERS = {"Authorization": "Bearer op-2:operator"}
APPROVER_HEADERS = {"Authorization": "Bearer op-approval:approver"}
SECOND_OPERATOR_HEADERS = {"Authorization": "Bearer op-3:operator"}


def _assign_rebalance_lineage(payload: Dict[str, Any]) -> Dict[str, Any]:
    snapshot_id = str(payload.get("ranking_snapshot_id") or "rank-q3")
    policy_version = "persona-real-allocation-v1"
    basis_lines = [
        {
            key: value
            for key, value in line.items()
            if key not in {
                "ranking_snapshot_id",
                "allocation_evaluation_id",
                "allocation_line_digest",
                "allocation_policy_version",
            }
        }
        for line in payload.get("lines") or []
    ]
    evaluation_id = (
        "allocation-evaluation-"
        + bff_main._stable_json_hash(
            {
                "ranking_snapshot_id": snapshot_id,
                "allocation_policy_version": policy_version,
                "lines": basis_lines,
            }
        )[:24]
    )
    payload["ranking_snapshot_id"] = snapshot_id
    payload["allocation_evaluation_id"] = evaluation_id
    payload["allocation_policy_version"] = policy_version
    for line in payload.get("lines") or []:
        line["ranking_snapshot_id"] = snapshot_id
        line["allocation_evaluation_id"] = evaluation_id
        line["allocation_policy_version"] = policy_version
        line.pop("allocation_line_digest", None)
        line["allocation_line_digest"] = bff_main._pm12_allocation_line_digest(
            line
        )
    return payload


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
    return _assign_rebalance_lineage(payload)


class CapitalBffAuthorityHarness:
    """Run BFF tests against the real, durable Capital service boundary."""

    _ENV_KEYS = (
        "BFF_COMMIT",
        "CAPITAL_AUDIT_BACKEND",
        "CAPITAL_AUTH_DISABLED",
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

    def __init__(self, root: Path, *, seed_allocation: bool = True) -> None:
        self.root = Path(root)
        self.seed_allocation = seed_allocation
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
        self._original_get_json = command_executor._get_json
        self._original_http_json_get = read_store_module._http_json_get
        self._capital_idempotency = dict(bff_main._CAPITAL_BFF_IDEMPOTENCY)
        self._command_auth_context = dict(bff_main._COMMAND_AUTH_CONTEXT)
        self._persona_overlay = dict(bff_main._PERSONA_BFF_OVERLAY)

        for key in self._ENV_KEYS:
            os.environ.pop(key, None)
        os.environ.update(
            {
                "CAPITAL_AUDIT_BACKEND": "jsonl",
                "CAPITAL_AUTH_DISABLED": "true",
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
        command_executor._get_json = self._get_json
        read_store_module._http_json_get = self._http_json_get
        self._reset_bff_process_state()

        assert self.client is not None
        response = self.client.post(
            "/bff/capital-pools",
            json={
                "pool_id": "pool-real",
                "name": "Regression Pool",
                "owner_id": "fund-real",
                "owner_type": "fund",
                "risk_policy_ref": "risk-main",
            },
            headers={**HEADERS, "Idempotency-Key": "create-pool-real"},
        )
        assert response.status_code == 201, response.text
        assert response.json()["pool_id"] == "pool-real"
        assert response.json()["status"] == "active"

        response = self.client.post(
            "/api/v1/bindings",
            json={
                "binding_id": "binding-live",
                "persona_id": "p-live",
                "capital_pool_id": "pool-real",
                "capital_sleeve_id": "sleeve-live",
                "role": "live_owner",
                "allowed_deployment_scope": "live",
            },
            headers={**HEADERS, "Idempotency-Key": "create-binding-live"},
        )
        assert response.status_code == 201, response.text
        assert response.json()["binding_id"] == "binding-live"
        assert response.json()["capital_sleeve_id"] == "sleeve-live"
        assert response.json()["status"] == "pending"
        if self.seed_allocation:
            self._seed_authoritative_allocation()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.client is not None:
            self.client.close()
        if self.capital_client is not None:
            self.capital_client.close()

        command_executor._post_json = self._original_post_json
        command_executor._get_json = self._original_get_json
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
        bff_main.read_store = create_in_memory_read_surface_ports()
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

    def admit_rebalance_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Test-only admission fixture for the exact server-materialized lines."""
        _assign_rebalance_lineage(payload)
        snapshot_id = str(payload["ranking_snapshot_id"])
        evaluation_id = str(payload["allocation_evaluation_id"])
        policy_version = str(payload["allocation_policy_version"])
        bff_main.read_store.put_ranking_snapshot({
            "ranking_snapshot_id": snapshot_id,
            "surface": "quarterly",
            "period": "test",
            "formula_version": "pm12-default-v1",
            "content_digest": bff_main._stable_json_hash(
                {
                    "surface": "quarterly",
                    "period": "test",
                    "formula_version": "pm12-default-v1",
                    "items": [],
                }
            ),
            "items": [],
            "evidence_assertion_digests": {},
        })
        lines = [dict(line) for line in payload.get("lines") or []]
        bff_main.read_store.put_allocation_evaluation({
            "allocation_evaluation_id": evaluation_id,
            "ranking_snapshot_id": snapshot_id,
            "allocation_policy_version": policy_version,
            "content_digest": bff_main._stable_json_hash(
                {
                    "ranking_snapshot_id": snapshot_id,
                    "allocation_evaluation_id": evaluation_id,
                    "allocation_policy_version": policy_version,
                    "lines": lines,
                }
            ),
            "lines": lines,
            "applied": False,
        })
        return payload

    def _seed_authoritative_allocation(self) -> None:
        """Owner-only fixture bootstrap; product apply paths still enter via BFF."""
        assert self.capital_client is not None
        seed_line = {
            "ranking_snapshot_id": "rank-seed",
            "allocation_evaluation_id": "allocation-evaluation-seed",
            "allocation_policy_version": "persona-real-allocation-v1",
            "persona_id": "p-live",
            "stage": "live_running",
            "capital_scope": "pool",
            "capital_pool_id": "pool-real",
            "capital_sleeve_id": "sleeve-live",
            "current_weight": 0.0,
            "target_weight": 0.10,
            "delta": 0.10,
            "cap_reasons": [],
            "evidence_refs": [],
        }
        seed_line["allocation_line_digest"] = (
            bff_main._pm12_allocation_line_digest(seed_line)
        )
        created = self.capital_client.post(
            "/api/rebalances",
            json={
                "actor_id": "op-2",
                "actor_role": "operator",
                "idempotency_key": "seed-allocation-proposal",
                "request_hash": "seed-allocation-proposal-v1",
                "rebalance_id": "rb-seed-allocation",
                "capital_pool_id": "pool-real",
                "ranking_snapshot_id": "rank-seed",
                "allocation_evaluation_id": "allocation-evaluation-seed",
                "allocation_policy_version": "persona-real-allocation-v1",
                "reason": "Seed authoritative test baseline",
                "lines": [seed_line],
            },
        )
        assert created.status_code == 201, created.text
        applied = self.capital_client.post(
            "/api/rebalances/rb-seed-allocation/apply",
            json={
                "actor_id": "op-2",
                "actor_role": "operator",
                "idempotency_key": "seed-allocation-apply",
                "request_hash": "seed-allocation-apply-v1",
                "command_id": "cmd-seed-allocation",
                "approval_ref": "approval-seed-allocation",
            },
        )
        assert applied.status_code == 200, applied.text
        assert applied.json()["allocation_readback"][0]["current_weight"] == 0.10

    def apply_evidence(
        self,
        rebalance_id: str,
        *,
        suffix: str,
    ) -> tuple[Dict[str, Any], Dict[str, str]]:
        """Create restart-safe approval, confirm-token, and two-man evidence."""
        assert self.client is not None
        approval_id = f"approval-{suffix}"
        signature_id = f"tms-{suffix}"
        token_id = f"ct-{suffix}"

        approved = self.client.post(
            f"/bff/rebalances/{rebalance_id}/approve",
            json={"approval_decision_id": approval_id, "memo": "Regression approval"},
            headers={**APPROVER_HEADERS, "Idempotency-Key": f"approve-{suffix}"},
        )
        assert approved.status_code == 201, approved.text

        confirmed = self.client.post(
            "/bff/confirm-tokens",
            json={
                "tokenId": token_id,
                "command": "ApprovedApply",
                "target": {"type": "Rebalance", "id": rebalance_id},
                "operator_id": "op-2",
                "reason": "Confirm authoritative rebalance apply",
            },
            headers={**HEADERS, "Idempotency-Key": f"confirm-{suffix}"},
        )
        assert confirmed.status_code == 201, confirmed.text

        first = self.client.post(
            f"/bff/rebalances/{rebalance_id}/two-man-sign",
            json={"two_man_signature_id": signature_id},
            headers={**HEADERS, "Idempotency-Key": f"sign-first-{suffix}"},
        )
        assert first.status_code == 202, first.text
        assert first.json()["data"]["complete"] is False
        second = self.client.post(
            f"/bff/rebalances/{rebalance_id}/two-man-sign",
            json={"two_man_signature_id": signature_id},
            headers={**SECOND_OPERATOR_HEADERS, "Idempotency-Key": f"sign-second-{suffix}"},
        )
        assert second.status_code == 202, second.text
        assert second.json()["data"]["complete"] is True

        return (
            {
                "approval_decision_id": approval_id,
                "two_man_signature_id": signature_id,
            },
            {**HEADERS, "X-Confirm-Token": token_id},
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

    def _get_json(
        self,
        url: str,
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Any:
        del auth_token, mfa_token
        assert self.capital_client is not None
        parsed = urlsplit(url)
        path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        response = self.capital_client.get(path)
        if response.status_code >= 400:
            raise HTTPError(
                url,
                response.status_code,
                response.reason_phrase,
                response.headers,
                BytesIO(response.content),
            )
        if not response.content:
            return None
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
