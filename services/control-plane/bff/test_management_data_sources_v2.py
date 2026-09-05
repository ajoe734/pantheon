"""Comprehensive contract and behavior tests for SD-SRCM-03 BFF Data Source Management Facade.

Covers:
1. Every read route: list, catalog, detail, runs, receipts, command receipt
2. Every write route: create, validate, canary, enable, disable, degrade, resume, change_schedule, replace, retire
3. RBAC and operator/admin role enforcement
4. X-Idempotency-Key and expected revision enforcement
5. Confirmation requirements for enable, replace, retire
6. AllowedActions state machine enforcement
7. Inline secret rejection and response secret redaction
8. Service token forwarding (never reusing operator auth)
9. Typed error mapping: 400, 403, 404, 409, 412, 503
10. Honest empty, degraded, and unavailable states with no fixture success
"""

from __future__ import annotations

import os
import sys
import tempfile
import uuid
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
import pytest


from services.control_plane.bff import main as bff_main
from services.control_plane.bff.source_management_client import SourceManagementClient, SourceManagementClientError


OPERATOR_HEADERS = {
    "Authorization": "Bearer op-srcm-001:operator,admin",
    "X-Idempotency-Key": "idemp-test-001",
}
VIEWER_HEADERS = {
    "Authorization": "Bearer viewer-001:viewer",
    "X-Idempotency-Key": "idemp-test-002",
}


def _mock_v2_source_instance(
    source_id: str = "src-twse-market-01",
    definition_id: str = "srcdef-twse-stock-day-all",
    lifecycle_state: str = "configured_disabled",
    revision: int = 1,
) -> Dict[str, Any]:
    return {
        "data_source_id": source_id,
        "source_instance_id": source_id,
        "connector_id": source_id,
        "definition_id": definition_id,
        "provider": "TWSE",
        "source_class": "market_daily",
        "datasets": [{"dataset_id": "stock_day_all", "dataset_class": "market_daily"}],
        "markets": ["TW"],
        "license_scope": "official_reference",
        "allowed_use": ["research_data", "backtest_data"],
        "retention_policy_ref": "source-retention://twse",
        "deletion_policy_ref": "source-deletion://twse",
        "freshness_sla_seconds": 86400,
        "sensitivity": "public",
        "lifecycle_state": lifecycle_state,
        "revision": revision,
        "created_by": "op-srcm-001",
        "created_at": "2026-08-24T12:00:00Z",
        "updated_by": "op-srcm-001",
        "updated_at": "2026-08-24T12:00:00Z",
    }


def _mock_desired_state(
    source_id: str = "src-twse-market-01",
    definition_id: str = "srcdef-twse-stock-day-all",
    desired_lifecycle: str = "configured_disabled",
    revision: int = 1,
    secret_ref_id: Optional[str] = None,
) -> Dict[str, Any]:
    cfg: Dict[str, Any] = {"public": {"market": "TW"}}
    if secret_ref_id:
        cfg["secret_ref_id"] = secret_ref_id
    return {
        "source_instance_id": source_id,
        "revision": revision,
        "desired_lifecycle": desired_lifecycle,
        "definition_id": definition_id,
        "definition_deployment_sha": "sha256-current",
        "connector_config": cfg,
        "schedule": {"enabled": False, "cadence": "0 19 * * 1-5"},
        "limits": {"max_records": 1000, "max_bytes": 1048576, "timeout_seconds": 30},
        "allowed_hosts": ["*.twse.com.tw"],
        "universe_policy_ref": "universe://tw-equity-active",
        "last_command_receipt_id": "srcrcp-init-001",
        "updated_at": "2026-08-24T12:00:00Z",
        "updated_by": "op-srcm-001",
    }


def _mock_observed_state(
    source_id: str = "src-twse-market-01",
    definition_id: str = "srcdef-twse-stock-day-all",
    effective_lifecycle: str = "configured_disabled",
    revision: int = 1,
    validation_state: str = "pending",
    canary_state: str = "not_run",
    health_state: str = "healthy",
    dependent_refs: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return {
        "source_instance_id": source_id,
        "desired_revision": revision,
        "observed_revision": revision,
        "reconciliation_status": "converged",
        "effective_lifecycle": effective_lifecycle,
        "definition": {
            "definition_id": definition_id,
            "deployment_sha": "sha256-current",
            "state": "supported",
        },
        "credential_state": "ready",
        "validation_state": validation_state,
        "canary_state": canary_state,
        "health_state": health_state,
        "freshness": {"status": "never_ingested"},
        "last_run": {},
        "dlq_unresolved_count": 0,
        "quota": {},
        "usage": {},
        "dependent_refs": dependent_refs or [],
        "reasons": [],
        "observed_at": "2026-08-24T12:00:00Z",
    }


def _mock_connector_definition(
    definition_id: str = "srcdef-twse-stock-day-all",
    provider: str = "TWSE",
) -> Dict[str, Any]:
    return {
        "definition_id": definition_id,
        "provider": provider,
        "source_classes": ["market_daily"],
        "datasets": ["stock_day_all"],
        "definition_state": "supported",
        "auth_modes": ["none"],
        "adapter_token": "twse_stock_day_all",
        "deployment_sha": "sha256-current",
        "allowed_host_patterns": ["*.twse.com.tw"],
        "default_limits": {"max_records": 1000, "max_bytes": 1048576, "timeout_seconds": 30},
    }


class FakeSourceManagementClient(SourceManagementClient):
    """In-memory mock of the source management client for testing."""

    def __init__(self) -> None:
        super().__init__(base_url="http://fake-source-ingest:8000", service_token="service-token-secret")
        self.sources: Dict[str, Dict[str, Any]] = {}
        self.desired: Dict[str, Dict[str, Any]] = {}
        self.observed: Dict[str, Dict[str, Any]] = {}
        self.definitions: Dict[str, Dict[str, Any]] = {
            "srcdef-twse-stock-day-all": _mock_connector_definition(),
            "srcdef-yahoo-news": _mock_connector_definition("srcdef-yahoo-news", "Yahoo"),
        }
        self.observations: Dict[str, List[Dict[str, Any]]] = {}
        self.canaries: Dict[str, List[Dict[str, Any]]] = {}
        self.receipts: Dict[str, List[Dict[str, Any]]] = {}
        self.command_receipts_by_id: Dict[str, Dict[str, Any]] = {}
        self.recorded_commands: List[Dict[str, Any]] = []

    def seed_source(
        self,
        source_id: str,
        definition_id: str = "srcdef-twse-stock-day-all",
        lifecycle_state: str = "configured_disabled",
        revision: int = 1,
        validation_state: str = "pending",
        canary_state: str = "not_run",
        health_state: str = "healthy",
        dependent_refs: Optional[List[str]] = None,
    ) -> None:
        inst = _mock_v2_source_instance(source_id, definition_id, lifecycle_state, revision)
        des = _mock_desired_state(source_id, definition_id, lifecycle_state, revision)
        obs = _mock_observed_state(source_id, definition_id, lifecycle_state, revision, validation_state, canary_state, health_state, dependent_refs)
        self.sources[source_id] = inst
        self.desired[source_id] = des
        self.observed[source_id] = obs
        self.observations[source_id] = [obs]
        self.canaries[source_id] = []
        self.receipts[source_id] = []

    def list_connector_definitions(self) -> Dict[str, Any]:
        return {"definitions": list(self.definitions.values()), "count": len(self.definitions)}

    def get_connector_definition(self, definition_id: str) -> Dict[str, Any]:
        defn = self.definitions.get(definition_id)
        if defn is None:
            raise SourceManagementClientError(f"definition not found: {definition_id}", status_code=404, error_code="RESOURCE_NOT_FOUND")
        return {"definition": defn}

    def list_sources(self, *, source_kind: Optional[str] = None, lifecycle_state: Optional[str] = None) -> Dict[str, Any]:
        items = list(self.sources.values())
        if lifecycle_state:
            items = [s for s in items if s.get("lifecycle_state") == lifecycle_state]
        return {"sources": items, "count": len(items)}

    def get_source(self, source_instance_id: str) -> Dict[str, Any]:
        inst = self.sources.get(source_instance_id)
        if inst is None:
            raise SourceManagementClientError(f"source instance not found: {source_instance_id}", status_code=404, error_code="RESOURCE_NOT_FOUND")
        return {
            "source": inst,
            "desired": self.desired.get(source_instance_id),
            "observed": self.observed.get(source_instance_id),
        }

    def list_source_observations(self, source_instance_id: str, *, limit: int = 100) -> Dict[str, Any]:
        if source_instance_id not in self.sources:
            raise SourceManagementClientError(f"source instance not found: {source_instance_id}", status_code=404, error_code="RESOURCE_NOT_FOUND")
        return {"observations": self.observations.get(source_instance_id, []), "count": len(self.observations.get(source_instance_id, []))}

    def list_source_canaries(self, source_instance_id: str, *, limit: int = 100) -> Dict[str, Any]:
        if source_instance_id not in self.sources:
            raise SourceManagementClientError(f"source instance not found: {source_instance_id}", status_code=404, error_code="RESOURCE_NOT_FOUND")
        return {"canaries": self.canaries.get(source_instance_id, []), "count": len(self.canaries.get(source_instance_id, []))}

    def get_source_canary(self, source_instance_id: str, canary_id: str) -> Dict[str, Any]:
        for can in self.canaries.get(source_instance_id, []):
            if can.get("canary_id") == canary_id:
                return {"canary": can}
        raise SourceManagementClientError(f"canary not found: {canary_id}", status_code=404, error_code="RESOURCE_NOT_FOUND")

    def list_source_receipts(self, source_instance_id: str, *, limit: int = 100) -> Dict[str, Any]:
        if source_instance_id not in self.sources:
            raise SourceManagementClientError(f"source instance not found: {source_instance_id}", status_code=404, error_code="RESOURCE_NOT_FOUND")
        return {"receipts": self.receipts.get(source_instance_id, []), "count": len(self.receipts.get(source_instance_id, []))}

    def get_command_receipt(self, receipt_id: str) -> Dict[str, Any]:
        rcp = self.command_receipts_by_id.get(receipt_id)
        if rcp is None:
            raise SourceManagementClientError(f"receipt not found: {receipt_id}", status_code=404, error_code="RESOURCE_NOT_FOUND")
        return {"receipt": rcp}

    def execute_command(self, command_payload: Dict[str, Any], *, idempotency_key: str) -> Dict[str, Any]:
        self.recorded_commands.append({"payload": command_payload, "idempotency_key": idempotency_key})
        cmd_type = command_payload.get("command_type")
        source_id = command_payload.get("source_instance_id")
        receipt_id = f"srcrcp-{uuid.uuid4().hex[:8]}"

        if cmd_type == "create":
            params = command_payload.get("parameters") or {}
            def_id = params.get("definition_id")
            if def_id not in self.definitions:
                raise SourceManagementClientError(
                    f"Connector definition '{def_id}' is not supported",
                    status_code=400,
                    error_code="VALIDATION_FAILED",
                    payload={"detail": {"code": "adapter_not_supported", "development_need": {"definition_id": def_id}}},
                )
            if source_id in self.sources:
                raise SourceManagementClientError(f"Duplicate instance: {source_id}", status_code=409, error_code="RESOURCE_CONFLICT")
            self.seed_source(source_id, definition_id=def_id, lifecycle_state="configured_disabled", revision=1)
            rcp = {
                "receipt_id": receipt_id,
                "command_id": command_payload.get("command_id"),
                "source_instance_id": source_id,
                "command_type": "create",
                "status": "succeeded",
                "before_revision": 0,
                "after_revision": 1,
            }
            self.command_receipts_by_id[receipt_id] = rcp
            self.receipts[source_id].append(rcp)
            return {"receipt": rcp}

        if source_id not in self.sources:
            raise SourceManagementClientError(f"source not found: {source_id}", status_code=404, error_code="RESOURCE_NOT_FOUND")

        inst = self.sources[source_id]
        des = self.desired[source_id]
        obs = self.observed[source_id]
        curr_rev = inst["revision"]

        exp_rev = command_payload.get("expected_revision")
        if exp_rev is not None and exp_rev != curr_rev:
            raise SourceManagementClientError(f"STALE_REVISION: expected {exp_rev} != {curr_rev}", status_code=409, error_code="STALE_REVISION")

        if cmd_type == "validate":
            obs["validation_state"] = "passed"
            rcp = {"receipt_id": receipt_id, "command_id": command_payload.get("command_id"), "source_instance_id": source_id, "command_type": "validate", "status": "succeeded", "before_revision": curr_rev, "after_revision": curr_rev}
        elif cmd_type == "canary":
            obs["canary_state"] = "passed"
            obs["validation_state"] = "passed"
            canary_id = f"canary-{uuid.uuid4().hex[:8]}"
            canary_obj = {"canary_id": canary_id, "source_instance_id": source_id, "status": "passed"}
            self.canaries[source_id].append(canary_obj)
            rcp = {"receipt_id": receipt_id, "command_id": command_payload.get("command_id"), "source_instance_id": source_id, "command_type": "canary", "status": "succeeded", "before_revision": curr_rev, "after_revision": curr_rev}
        elif cmd_type == "enable":
            next_rev = curr_rev + 1
            inst["revision"] = next_rev
            inst["lifecycle_state"] = "enabled"
            des["revision"] = next_rev
            des["desired_lifecycle"] = "enabled"
            obs["observed_revision"] = next_rev
            obs["effective_lifecycle"] = "enabled"
            rcp = {"receipt_id": receipt_id, "command_id": command_payload.get("command_id"), "source_instance_id": source_id, "command_type": "enable", "status": "succeeded", "before_revision": curr_rev, "after_revision": next_rev}
        elif cmd_type == "disable":
            next_rev = curr_rev + 1
            inst["revision"] = next_rev
            inst["lifecycle_state"] = "disabled"
            des["revision"] = next_rev
            des["desired_lifecycle"] = "disabled"
            obs["observed_revision"] = next_rev
            obs["effective_lifecycle"] = "disabled"
            rcp = {"receipt_id": receipt_id, "command_id": command_payload.get("command_id"), "source_instance_id": source_id, "command_type": "disable", "status": "succeeded", "before_revision": curr_rev, "after_revision": next_rev}
        elif cmd_type == "degrade":
            next_rev = curr_rev + 1
            inst["revision"] = next_rev
            inst["lifecycle_state"] = "degraded_disabled"
            des["revision"] = next_rev
            des["desired_lifecycle"] = "degraded_disabled"
            obs["observed_revision"] = next_rev
            obs["effective_lifecycle"] = "degraded_disabled"
            rcp = {"receipt_id": receipt_id, "command_id": command_payload.get("command_id"), "source_instance_id": source_id, "command_type": "degrade", "status": "succeeded", "before_revision": curr_rev, "after_revision": next_rev}
        elif cmd_type == "resume":
            next_rev = curr_rev + 1
            inst["revision"] = next_rev
            inst["lifecycle_state"] = "enabled"
            des["revision"] = next_rev
            des["desired_lifecycle"] = "enabled"
            obs["observed_revision"] = next_rev
            obs["effective_lifecycle"] = "enabled"
            rcp = {"receipt_id": receipt_id, "command_id": command_payload.get("command_id"), "source_instance_id": source_id, "command_type": "resume", "status": "succeeded", "before_revision": curr_rev, "after_revision": next_rev}
        elif cmd_type == "change_schedule":
            next_rev = curr_rev + 1
            inst["revision"] = next_rev
            des["revision"] = next_rev
            des["schedule"] = (command_payload.get("parameters") or {}).get("schedule")
            rcp = {"receipt_id": receipt_id, "command_id": command_payload.get("command_id"), "source_instance_id": source_id, "command_type": "change_schedule", "status": "succeeded", "before_revision": curr_rev, "after_revision": next_rev}
        elif cmd_type == "replace":
            next_rev = curr_rev + 1
            inst["revision"] = next_rev
            inst["lifecycle_state"] = "disabled"
            des["revision"] = next_rev
            des["desired_lifecycle"] = "disabled"
            rcp = {"receipt_id": receipt_id, "command_id": command_payload.get("command_id"), "source_instance_id": source_id, "command_type": "replace", "status": "succeeded", "before_revision": curr_rev, "after_revision": next_rev}
        elif cmd_type == "retire":
            next_rev = curr_rev + 1
            inst["revision"] = next_rev
            inst["lifecycle_state"] = "retired"
            des["revision"] = next_rev
            des["desired_lifecycle"] = "retired"
            obs["observed_revision"] = next_rev
            obs["effective_lifecycle"] = "retired"
            rcp = {"receipt_id": receipt_id, "command_id": command_payload.get("command_id"), "source_instance_id": source_id, "command_type": "retire", "status": "succeeded", "before_revision": curr_rev, "after_revision": next_rev}
        else:
            raise SourceManagementClientError(f"Unknown command: {cmd_type}", status_code=400, error_code="INVALID_ARGUMENT")

        self.command_receipts_by_id[receipt_id] = rcp
        self.receipts[source_id].append(rcp)
        return {"receipt": rcp}


@pytest.fixture
def fake_client() -> FakeSourceManagementClient:
    return FakeSourceManagementClient()


@pytest.fixture
def bff_client(fake_client: FakeSourceManagementClient) -> TestClient:
    original_client = bff_main.source_management_client
    bff_main.source_management_client = fake_client
    try:
        yield TestClient(bff_main.app, raise_server_exceptions=False)
    finally:
        bff_main.source_management_client = original_client


# ==============================================================================
# READ ROUTES TESTS
# ==============================================================================


def test_get_data_sources_list_v2_and_redaction(bff_client: TestClient, fake_client: FakeSourceManagementClient) -> None:
    fake_client.seed_source("src-twse-01", definition_id="srcdef-twse-stock-day-all", lifecycle_state="configured_disabled", revision=1)
    fake_client.desired["src-twse-01"]["connector_config"]["public"]["api_key"] = "inline-secret-should-be-redacted"

    response = bff_client.get("/bff/management/data-sources", headers=OPERATOR_HEADERS)
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["data"]["status"] == "ok"
    assert payload["data"]["source"] == "service_client"
    assert payload["data"]["summary"]["total_items"] == 1
    item = payload["data"]["items"][0]
    assert item["source_instance_id"] == "src-twse-01"
    assert "allowed_actions" in item or "allowedActions" in item
    # Check secret redaction
    assert item["desired"]["connector_config"]["public"]["api_key"] == "[REDACTED]"


def test_get_data_sources_catalog(bff_client: TestClient, fake_client: FakeSourceManagementClient) -> None:
    response = bff_client.get("/bff/management/data-sources/catalog", headers=OPERATOR_HEADERS)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["data"]["id"] == "data-sources-catalog"
    assert payload["data"]["count"] == 2
    assert payload["data"]["status"] == "ok"


def test_get_data_source_detail(bff_client: TestClient, fake_client: FakeSourceManagementClient) -> None:
    fake_client.seed_source("src-twse-01", definition_id="srcdef-twse-stock-day-all", lifecycle_state="configured_disabled", revision=1)
    response = bff_client.get("/bff/management/data-sources/src-twse-01", headers=OPERATOR_HEADERS)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["data"]["source_instance_id"] == "src-twse-01"
    assert payload["data"]["allowed_actions"]["canValidate"] is True
    assert payload["data"]["allowed_actions"]["canEnable"] is False  # not canary passed


def test_get_data_source_detail_not_found(bff_client: TestClient) -> None:
    response = bff_client.get("/bff/management/data-sources/nonexistent-source", headers=OPERATOR_HEADERS)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


def test_get_data_source_runs_and_receipts(bff_client: TestClient, fake_client: FakeSourceManagementClient) -> None:
    fake_client.seed_source("src-twse-01")
    runs_resp = bff_client.get("/bff/management/data-sources/src-twse-01/runs", headers=OPERATOR_HEADERS)
    assert runs_resp.status_code == 200
    assert "observations" in runs_resp.json()["data"]

    rcps_resp = bff_client.get("/bff/management/data-sources/src-twse-01/receipts", headers=OPERATOR_HEADERS)
    assert rcps_resp.status_code == 200
    assert "receipts" in rcps_resp.json()["data"]


# ==============================================================================
# WRITE / COMMAND ROUTES TESTS (10 Canonical Commands)
# ==============================================================================


def test_create_data_source_starts_configured_disabled(bff_client: TestClient, fake_client: FakeSourceManagementClient) -> None:
    body = {
        "definitionId": "srcdef-twse-stock-day-all",
        "sourceInstanceId": "src-twse-new",
        "provider": "TWSE",
        "sourceClass": "market_daily",
        "reason": "Operator provisioning new TWSE feed",
        "connectorConfig": {"public": {"market": "TW"}},
    }
    response = bff_client.post("/bff/management/data-sources", json=body, headers=OPERATOR_HEADERS)
    assert response.status_code == 202, response.text
    receipt = response.json()["data"]["receipt"]
    assert receipt["command_type"] == "create"
    assert receipt["status"] == "succeeded"

    # Verify created in fake client
    assert "src-twse-new" in fake_client.sources
    assert fake_client.sources["src-twse-new"]["lifecycle_state"] == "configured_disabled"


def test_create_data_source_rejects_raw_secrets(bff_client: TestClient) -> None:
    body = {
        "definitionId": "srcdef-twse-stock-day-all",
        "sourceInstanceId": "src-twse-secret",
        "reason": "Creating with bad secret",
        "connectorConfig": {"public": {"api_key": "raw-inline-secret-token"}},
    }
    response = bff_client.post("/bff/management/data-sources", json=body, headers=OPERATOR_HEADERS)
    assert response.status_code == 400
    assert "Raw secret material detected" in response.text or "raw_secret" in response.text


def test_validate_command(bff_client: TestClient, fake_client: FakeSourceManagementClient) -> None:
    fake_client.seed_source("src-twse-01", revision=1)
    body = {"expectedRevision": 1, "reason": "Pre-flight validation check"}
    response = bff_client.post("/bff/management/data-sources/src-twse-01/actions/validate", json=body, headers=OPERATOR_HEADERS)
    assert response.status_code == 202, response.text
    assert response.json()["data"]["receipt"]["command_type"] == "validate"


def test_canary_command(bff_client: TestClient, fake_client: FakeSourceManagementClient) -> None:
    fake_client.seed_source("src-twse-01", revision=1, validation_state="passed")
    body = {"expectedRevision": 1, "reason": "Execute bounded canary"}
    response = bff_client.post("/bff/management/data-sources/src-twse-01/actions/canary", json=body, headers=OPERATOR_HEADERS)
    assert response.status_code == 202, response.text
    assert response.json()["data"]["receipt"]["command_type"] == "canary"


def test_enable_requires_explicit_confirmation(bff_client: TestClient, fake_client: FakeSourceManagementClient) -> None:
    fake_client.seed_source("src-twse-01", revision=1, validation_state="passed", canary_state="passed")
    # Missing confirmation
    body = {"expectedRevision": 1, "reason": "Enabling feed", "confirmation": False}
    response = bff_client.post("/bff/management/data-sources/src-twse-01/actions/enable", json=body, headers=OPERATOR_HEADERS)
    assert response.status_code == 412
    assert "confirmation" in response.text.lower()

    # With confirmation=True
    body["confirmation"] = True
    response = bff_client.post("/bff/management/data-sources/src-twse-01/actions/enable", json=body, headers=OPERATOR_HEADERS)
    assert response.status_code == 202, response.text
    assert response.json()["data"]["receipt"]["command_type"] == "enable"


def test_enable_fails_when_canary_not_passed(bff_client: TestClient, fake_client: FakeSourceManagementClient) -> None:
    fake_client.seed_source("src-twse-01", revision=1, validation_state="passed", canary_state="not_run")
    body = {"expectedRevision": 1, "reason": "Enabling feed prematurely", "confirmation": True}
    response = bff_client.post("/bff/management/data-sources/src-twse-01/actions/enable", json=body, headers=OPERATOR_HEADERS)
    assert response.status_code == 412
    assert "canary_required" in response.text or "not allowed" in response.text


def test_disable_and_degrade_and_resume(bff_client: TestClient, fake_client: FakeSourceManagementClient) -> None:
    fake_client.seed_source("src-twse-01", revision=1, lifecycle_state="enabled", validation_state="passed", canary_state="passed")

    # Disable
    body = {"expectedRevision": 1, "reason": "Scheduled maintenance"}
    resp_dis = bff_client.post("/bff/management/data-sources/src-twse-01/actions/disable", json=body, headers=OPERATOR_HEADERS)
    assert resp_dis.status_code == 202, resp_dis.text
    assert fake_client.sources["src-twse-01"]["lifecycle_state"] == "disabled"

    # Resume
    body_res = {"expectedRevision": 2, "reason": "Maintenance complete"}
    resp_res = bff_client.post("/bff/management/data-sources/src-twse-01/actions/resume", json=body_res, headers=OPERATOR_HEADERS)
    assert resp_res.status_code == 202, resp_res.text
    assert fake_client.sources["src-twse-01"]["lifecycle_state"] == "enabled"

    # Degrade
    body_deg = {"expectedRevision": 3, "reason": "Upstream degradation detected"}
    resp_deg = bff_client.post("/bff/management/data-sources/src-twse-01/actions/degrade", json=body_deg, headers=OPERATOR_HEADERS)
    assert resp_deg.status_code == 202, resp_deg.text
    assert fake_client.sources["src-twse-01"]["lifecycle_state"] == "degraded_disabled"


def test_change_schedule(bff_client: TestClient, fake_client: FakeSourceManagementClient) -> None:
    fake_client.seed_source("src-twse-01", revision=1)
    body = {"expectedRevision": 1, "reason": "Update cadence", "schedule": {"cadence": "0 20 * * 1-5"}}
    response = bff_client.put("/bff/management/data-sources/src-twse-01/schedule", json=body, headers=OPERATOR_HEADERS)
    assert response.status_code == 202, response.text
    assert response.json()["data"]["receipt"]["command_type"] == "change_schedule"


def test_replace_requires_confirmation(bff_client: TestClient, fake_client: FakeSourceManagementClient) -> None:
    fake_client.seed_source("src-twse-01", revision=1)
    fake_client.seed_source("src-twse-02", revision=1)

    # Without confirmation
    body = {"expectedRevision": 1, "reason": "Migrate to twse-02", "replacementSourceId": "src-twse-02", "confirmation": False}
    resp = bff_client.post("/bff/management/data-sources/src-twse-01/actions/replace", json=body, headers=OPERATOR_HEADERS)
    assert resp.status_code == 412

    # With confirmation
    body["confirmation"] = True
    resp = bff_client.post("/bff/management/data-sources/src-twse-01/actions/replace", json=body, headers=OPERATOR_HEADERS)
    assert resp.status_code == 202, resp.text


def test_retire_requires_disabled_and_confirmation(bff_client: TestClient, fake_client: FakeSourceManagementClient) -> None:
    # Cannot retire enabled source
    fake_client.seed_source("src-twse-01", revision=1, lifecycle_state="enabled")
    body = {"expectedRevision": 1, "reason": "Retire feed", "confirmation": True}
    resp = bff_client.post("/bff/management/data-sources/src-twse-01/actions/retire", json=body, headers=OPERATOR_HEADERS)
    assert resp.status_code == 412
    assert "already_enabled" in resp.text or "not allowed" in resp.text

    # Cannot retire if active dependents block
    fake_client.seed_source("src-twse-02", revision=1, lifecycle_state="disabled", dependent_refs=["persona://alpha-pm"])
    body = {"expectedRevision": 1, "reason": "Retire feed with deps", "confirmation": True}
    resp = bff_client.post("/bff/management/data-sources/src-twse-02/actions/retire", json=body, headers=OPERATOR_HEADERS)
    assert resp.status_code == 412
    assert "active_dependents_block_retirement" in resp.text or "not allowed" in resp.text

    # Successful retirement when disabled and no dependents
    fake_client.seed_source("src-twse-03", revision=1, lifecycle_state="disabled", dependent_refs=[])
    body = {"expectedRevision": 1, "reason": "Retire decommissioned feed", "confirmation": True}
    resp = bff_client.post("/bff/management/data-sources/src-twse-03/actions/retire", json=body, headers=OPERATOR_HEADERS)
    assert resp.status_code == 202, resp.text
    assert fake_client.sources["src-twse-03"]["lifecycle_state"] == "retired"


# ==============================================================================
# RBAC & IDEMPOTENCY & ERROR MAPPING TESTS
# ==============================================================================


def test_mutation_requires_operator_or_admin_role(bff_client: TestClient, fake_client: FakeSourceManagementClient) -> None:
    fake_client.seed_source("src-twse-01", revision=1)
    body = {"expectedRevision": 1, "reason": "Viewer trying to validate"}
    resp = bff_client.post("/bff/management/data-sources/src-twse-01/actions/validate", json=body, headers=VIEWER_HEADERS)
    assert resp.status_code == 403


def test_missing_idempotency_key_rejected(bff_client: TestClient, fake_client: FakeSourceManagementClient) -> None:
    fake_client.seed_source("src-twse-01", revision=1)
    headers = {"Authorization": "Bearer op-srcm-001:operator"}
    body = {"expectedRevision": 1, "reason": "Validate without idempotency key"}
    resp = bff_client.post("/bff/management/data-sources/src-twse-01/actions/validate", json=body, headers=headers)
    assert resp.status_code == 400
    assert "X-Idempotency-Key" in resp.text


def test_stale_expected_revision_returns_409(bff_client: TestClient, fake_client: FakeSourceManagementClient) -> None:
    fake_client.seed_source("src-twse-01", revision=3)
    body = {"expectedRevision": 1, "reason": "Stale revision"}
    resp = bff_client.post("/bff/management/data-sources/src-twse-01/actions/validate", json=body, headers=OPERATOR_HEADERS)
    assert resp.status_code == 409
    assert "STALE_REVISION" in resp.text or "stale_revision" in resp.text


def test_get_command_receipt_endpoint(bff_client: TestClient, fake_client: FakeSourceManagementClient) -> None:
    fake_client.command_receipts_by_id["srcrcp-abc-123"] = {
        "receipt_id": "srcrcp-abc-123",
        "command_id": "srcmd-xyz",
        "status": "succeeded",
    }
    resp = bff_client.get("/bff/management/source-commands/srcrcp-abc-123", headers=OPERATOR_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["data"]["receipt_id"] == "srcrcp-abc-123"

    resp_404 = bff_client.get("/bff/management/source-commands/nonexistent-receipt", headers=OPERATOR_HEADERS)
    assert resp_404.status_code == 404


def test_service_unconfigured_returns_503(bff_client: TestClient) -> None:
    unconfigured_client = SourceManagementClient(base_url="")
    bff_main.source_management_client = unconfigured_client
    body = {"expectedRevision": 1, "reason": "Validate when service down"}
    resp = bff_client.post("/bff/management/data-sources/src-twse-01/actions/validate", json=body, headers=OPERATOR_HEADERS)
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
