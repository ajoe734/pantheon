"""Comprehensive contract and behavior tests for SD-SRCM-03 BFF Data Source Management Facade.

Covers:
1. Every read route: list, catalog, detail, runs, receipts, command receipt
2. Every write route: create, validate, canary, enable, disable, degrade, resume, change_schedule, replace, retire
3. RBAC and operator/admin role enforcement
4. Idempotency-Key and expected revision enforcement
5. Confirmation requirements for enable, replace, retire
6. AllowedActions state machine enforcement
7. Inline secret rejection and response secret redaction
8. Service token forwarding (never reusing operator auth)
9. Typed error mapping: 400, 403, 404, 409, 412, 503
10. Honest empty, degraded, and unavailable states with no fixture success
"""

from __future__ import annotations

import tempfile
import uuid
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from services.control_plane.bff.auth.policy import (
    bff_error,
    extract_identity,
    require_operator_role,
    require_read_role,
)
from services.control_plane.bff.console_gap.datasources import create_datasources_router
from services.control_plane.bff.core.errors import register_error_handlers
from services.control_plane.bff.management_read_models.router import _default_snapshot_meta
from services.control_plane.bff.models import utc_now
from services.control_plane.bff.source_management_client import (
    SourceManagementClient,
    SourceManagementClientError,
)


OPERATOR_HEADERS = {
    "Authorization": "Bearer op-srcm-001:operator,admin",
    "Idempotency-Key": "idemp-test-001",
}
VIEWER_HEADERS = {
    "Authorization": "Bearer viewer-001:viewer",
    "Idempotency-Key": "idemp-test-002",
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


def _mounted_client(client_holder: Dict[str, Any]) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(
        create_datasources_router(
            extract_identity=extract_identity,
            require_read_role=require_read_role,
            require_operator_role=require_operator_role,
            snapshot_meta=_default_snapshot_meta,
            utc_now=utc_now,
            get_source_management_client=lambda: client_holder["client"],
            bff_error=bff_error,
        )
    )
    client = TestClient(app, raise_server_exceptions=False)
    client.source_management_client_holder = client_holder  # type: ignore[attr-defined]
    return client


@pytest.fixture(autouse=True)
def _source_commands_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Existing tests exercise the enabled write path; kill-switch tests below unset this."""
    monkeypatch.setenv("PANTHEON_BFF_SOURCE_MANAGEMENT_COMMANDS_ENABLED", "1")


@pytest.fixture
def fake_client() -> FakeSourceManagementClient:
    return FakeSourceManagementClient()


@pytest.fixture
def bff_client(fake_client: FakeSourceManagementClient) -> TestClient:
    return _mounted_client({"client": fake_client})


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
    for secret_field, secret_val in (
        ("api_key", "raw-inline-secret-token"),
        ("password", "inline-password-123"),
        ("token", "inline-bearer-token-xyz"),
        ("secret_value", "inline-secret-value-abc"),
    ):
        body = {
            "definitionId": "srcdef-twse-stock-day-all",
            "sourceInstanceId": f"src-twse-{secret_field}",
            "reason": "Creating with bad secret",
            "connectorConfig": {"public": {secret_field: secret_val}},
        }
        response = bff_client.post("/bff/management/data-sources", json=body, headers=OPERATOR_HEADERS)
        assert response.status_code == 400, response.text
        err = response.json()["error"]
        assert err["code"] == "VALIDATION_FAILED"
        assert err["details"]["reason"] == "raw_secret_forbidden"
        assert f"Raw secret material detected at connector_config.public.{secret_field}" in err["message"]


def test_create_data_source_accepts_execute_plans_wizard_payload_with_secret_scope(
    bff_client: TestClient,
    fake_client: FakeSourceManagementClient,
) -> None:
    from services.control_plane.bff.console_gap import datasources as bff_datasources
    from services.source_ingestion import source_management_models as owner_models

    assert bff_datasources.assert_no_raw_secrets is owner_models.assert_no_raw_secrets

    fake_client.definitions["tw-twse-tpex-official-market"] = _mock_connector_definition(
        "tw-twse-tpex-official-market",
        "TWSE/TPEx",
    )
    wizard_payload = {
        "source_instance_id": "ds-twse-tpex-m2abc123",
        "definition_id": "tw-twse-tpex-official-market",
        "connector_id": "ds-twse-tpex-m2abc123",
        "provider": "TWSE/TPEx",
        "source_class": "market_daily",
        "datasets": ["tw_price_daily"],
        "markets": ["TW"],
        "license_scope": "official_reference",
        "secret_scope": "runtime_read_only",
        "allowed_use": ["research_data", "backtest_data", "monitoring"],
        "retention_policy_ref": "source-retention://standard-1y",
        "deletion_policy_ref": "source-deletion://standard",
        "freshness_sla_seconds": 86400,
        "sensitivity": "public",
        "connector_config": {
            "public": {},
            "secret_ref_id": None,
            "secret_scope": "runtime_read_only",
        },
        "schedule": {
            "enabled": False,
            "cadence": "0 19 * * 1-5",
            "timezone": "Asia/Taipei",
            "jitter_seconds": 120,
        },
        "universe_policy_ref": "active_universe_scheduling_policy.v1",
        "limits": {
            "max_records": 100,
            "max_bytes": 10485760,
            "timeout_seconds": 15,
        },
        "reason": "Operator created new configured_disabled data source via wizard",
        "trace_id": "corr-wizard-twse-20261020",
    }

    response = bff_client.post("/bff/management/data-sources", json=wizard_payload, headers=OPERATOR_HEADERS)
    assert response.status_code == 202, response.text
    receipt = response.json()["data"]["receipt"]
    assert receipt["command_type"] == "create"
    assert receipt["source_instance_id"] == "ds-twse-tpex-m2abc123"
    assert receipt["status"] == "succeeded"

    forwarded_params = fake_client.recorded_commands[-1]["payload"]["parameters"]
    assert forwarded_params["secret_scope"] == "runtime_read_only"
    assert forwarded_params["connector_config"]["secret_scope"] == "runtime_read_only"


def test_response_redaction_remains_strict_for_secret_like_keys() -> None:
    from services.control_plane.bff.console_gap.datasources import redact_sensitive_values

    raw_response = {
        "api_key": "raw-key-123",
        "password": "raw-password-123",
        "token": "raw-token-123",
        "secret_value": "raw-secret-val",
        "custom_secret_header": "raw-custom-secret",
        "secret_ref_id": "vault://pantheon/dev/finmind#token",
        "env_token": "env://FINMIND_TOKEN",
        "ref_secret": "ref://secret-store/item",
        "empty_secret": "",
    }
    redacted = redact_sensitive_values(raw_response)
    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["password"] == "[REDACTED]"
    assert redacted["token"] == "[REDACTED]"
    assert redacted["secret_value"] == "[REDACTED]"
    assert redacted["custom_secret_header"] == "[REDACTED]"
    assert redacted["secret_ref_id"] == "vault://pantheon/dev/finmind#token"
    assert redacted["env_token"] == "env://FINMIND_TOKEN"
    assert redacted["ref_secret"] == "ref://secret-store/item"
    assert redacted["empty_secret"] == ""


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
    assert "Idempotency-Key" in resp.text


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
    bff_client.source_management_client_holder["client"] = unconfigured_client  # type: ignore[attr-defined]
    body = {"expectedRevision": 1, "reason": "Validate when service down"}
    resp = bff_client.post("/bff/management/data-sources/src-twse-01/actions/validate", json=body, headers=OPERATOR_HEADERS)
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"


def test_source_management_client_auth_token_resolution(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.request
    from pathlib import Path

    token_file = Path(tmp_path) / "controller_token"
    token_file.write_text("controller-token-secret", encoding="utf-8")
    monkeypatch.setenv("SOURCE_INGEST_CONTROLLER_TOKEN_FILE", str(token_file))
    monkeypatch.delenv("PANTHEON_SOURCE_INGEST_SERVICE_TOKEN", raising=False)
    monkeypatch.delenv("SOURCE_INGEST_SERVICE_TOKEN", raising=False)
    monkeypatch.delenv("PANTHEON_SERVICE_TOKEN", raising=False)

    client = SourceManagementClient(base_url="http://fake-source:8097")
    with pytest.raises(SourceManagementClientError) as exc_info:
        client.execute_command({"command_type": "validate"}, idempotency_key="idemp-1")
    assert exc_info.value.error_code == "SERVICE_TOKEN_NOT_CONFIGURED"

    monkeypatch.setenv("PANTHEON_SOURCE_INGEST_SERVICE_TOKEN", "valid-service-token")
    client_with_token = SourceManagementClient(base_url="http://fake-source:8097")
    captured_request: dict[str, Any] = {}

    class DummyResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b'{"status": "ok"}'

    def fake_urlopen(req, timeout=None):
        captured_request["headers"] = dict(req.headers)
        return DummyResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    res = client_with_token.execute_command({"command_type": "validate"}, idempotency_key="idemp-2")
    assert res == {"status": "ok"}
    assert captured_request["headers"].get("Authorization") == "Bearer valid-service-token"


# ==============================================================================
# EMPTY V2 STORE FALLBACK TO DEGRADED LEGACY PROJECTION (BFF-DATASOURCES-EMPTY-V2-FALLBACK-20261110)
# ==============================================================================

_REAL_REGISTRY_ENTRY_KEYS = frozenset({
    "connector_id",
    "crawler_policy",
    "fetch_policy",
    "freshness",
    "health_metrics",
    "metadata",
    "policy",
    "provider",
    "schedule",
    "schema_version",
    "source_type",
    "state",
    "status",
    "supported_modes",
})


def _make_real_registry_connector_entry(
    connector_id: str,
    provider: str,
    status: str,
    *,
    source_type: str = "market_data",
    extra_fields: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "schema_version": "source_connector_registry_entry.v1",
        "connector_id": connector_id,
        "provider": provider,
        "source_type": source_type,
        "status": status,
        "supported_modes": ["scheduled", "on_demand"],
        "policy": {
            "license_scope": "official_reference",
            "allowed_use": ["research_data", "backtest_data"],
            "retention_days": 365,
        },
        "metadata": {
            "market": "TW" if connector_id.startswith("tw-") else "US",
            "dataset": f"{connector_id}-daily",
        },
        "fetch_policy": {
            "mode": "external_feed",
            "max_records": 1000,
            "timeout_seconds": 30,
        },
        "schedule": {
            "connector_id": connector_id,
            "enabled": status == "enabled",
            "interval_seconds": 3600,
        },
        "freshness": {
            "connector_id": connector_id,
            "status": "fresh" if status == "enabled" else "idle",
            "last_success_at": "2026-11-10T08:00:00Z" if status == "enabled" else None,
            "last_watermark": "2026-11-10" if status == "enabled" else None,
            "staleness_seconds": 120 if status == "enabled" else None,
        },
        "health_metrics": {
            "schema_version": "source_connector_health_metrics.v1",
            "last_success_at": "2026-11-10T08:00:00Z" if status == "enabled" else None,
            "row_count": 250 if status == "enabled" else None,
            "expected_rows": 250,
            "watermark": "2026-11-10" if status == "enabled" else None,
            "schema_hash": "a1b2c3d4e5f60718",
            "staleness_seconds": 120 if status == "enabled" else None,
            "source_error": None,
        },
        "state": {
            "connector_id": connector_id,
            "attempts": 5 if status == "enabled" else 0,
            "successful_attempts": 5 if status == "enabled" else 0,
            "failed_attempts": 0,
            "last_error": None,
            "updated_at": "2026-11-10T08:00:00Z" if status == "enabled" else None,
        },
        "crawler_policy": {
            "connector_id": connector_id,
            "rate_limit_per_minute": 30,
            "respect_robots_txt": True,
        },
    }
    if extra_fields:
        entry.update(extra_fields)
    return entry


def _real_seven_registry_connectors() -> List[Dict[str, Any]]:
    return [
        _make_real_registry_connector_entry(
            "tw-twse-tpex-official-market",
            "TWSE/TPEx",
            "enabled",
        ),
        _make_real_registry_connector_entry(
            "tw-taifex-futures-options-chip",
            "TAIFEX",
            "disabled",
        ),
        _make_real_registry_connector_entry(
            "dev-paper-us-equity-simulation",
            "Explicit controlled simulation",
            "disabled",
        ),
        _make_real_registry_connector_entry(
            "conn-dev-product-20260913T040439Z-7935481ca72f49629606e01098c3a54e",
            "Explicit controlled simulation",
            "enabled",
        ),
        _make_real_registry_connector_entry(
            "hosted-all12-h12-01a11-20261023-9aeb-conn",
            "L12 deployed E2E controlled source",
            "enabled",
        ),
        _make_real_registry_connector_entry(
            "hosted-all12-h12-01a11-20261026-d834-conn",
            "L12 deployed E2E controlled source",
            "enabled",
        ),
        _make_real_registry_connector_entry(
            "hosted-all12-h12-claude-20261027-4923-conn",
            "L12 deployed E2E controlled source",
            "enabled",
        ),
    ]


class _FakeLegacyReadStore:
    def __init__(self, registry_payload: Dict[str, Any]) -> None:
        self._registry_payload = registry_payload

    def get_source_connector_registry(self) -> Dict[str, Any]:
        return dict(self._registry_payload)


def _mounted_client_with_store(
    client_Obj: SourceManagementClient,
    registry_payload: Dict[str, Any],
) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)
    store = _FakeLegacyReadStore(registry_payload)
    app.include_router(
        create_datasources_router(
            read_surface=store,
            extract_identity=extract_identity,
            require_read_role=require_read_role,
            require_operator_role=require_operator_role,
            snapshot_meta=_default_snapshot_meta,
            utc_now=utc_now,
            get_source_management_client=lambda: client_Obj,
            bff_error=bff_error,
        )
    )
    return TestClient(app, raise_server_exceptions=False)


def test_empty_v2_store_falls_back_to_seven_connector_degraded_legacy_projection(
    fake_client: FakeSourceManagementClient,
) -> None:
    """AC1 & AC4: Configured v2 client returning 0 sources falls back to the 7 source-ingest
    registry connectors with meta.status='degraded', meta.source='legacy_projection',
    an empty-store reason, and no allowed_actions or inferred persona fleet status.
    """
    assert fake_client.configured is True
    assert fake_client.list_sources() == {"sources": [], "count": 0}

    seven_connectors = _real_seven_registry_connectors()
    client = _mounted_client_with_store(
        fake_client,
        {
            "source": "service_client",
            "schema_version": "source_connector_registry.v1",
            "connectors": seven_connectors,
            "provider_examples": ["TWSE/TPEx", "TAIFEX"],
            "policy_registry": {"max_records_per_job": 1000},
            "financial_data_source_catalog": {"catalog_version": "v1"},
            "active_universe_policy": {"policy_id": "tw-us-default"},
        },
    )

    response = client.get("/bff/management/data-sources", headers=OPERATOR_HEADERS)
    assert response.status_code == 200, response.text
    payload = response.json()

    meta = payload["meta"]
    assert meta["status"] == "degraded"
    assert meta["source"] == "legacy_projection"
    assert "empty" in meta["degradation"]["reason"].lower()
    assert "v2" in meta["degradation"]["reason"].lower()
    assert meta["reason"] == meta["degradation"]["reason"]
    assert meta["surfaces"]["data_sources"]["status"] == "degraded"
    assert meta["surfaces"]["data_sources"]["source"] == "legacy_projection"
    assert "empty" in meta["surfaces"]["data_sources"]["reason"].lower()

    data = payload["data"]
    assert data["status"] == "degraded"
    assert data["source"] == "legacy_projection"
    assert "empty" in data["reason"].lower()
    assert data["summary"]["status"] == "degraded"
    assert data["summary"]["source"] == "legacy_projection"
    assert data["summary"]["total_items"] == 7
    assert data["summary"]["returned_items"] == 7

    page_info = payload["page_info"]
    assert page_info["total"] == 7
    assert page_info["returned"] == 7
    assert page_info["has_more"] is False
    assert page_info["next_page_token"] is None

    items = data["items"]
    assert len(items) == 7
    expected_ids = [
        "tw-twse-tpex-official-market",
        "tw-taifex-futures-options-chip",
        "dev-paper-us-equity-simulation",
        "conn-dev-product-20260913T040439Z-7935481ca72f49629606e01098c3a54e",
        "hosted-all12-h12-01a11-20261023-9aeb-conn",
        "hosted-all12-h12-01a11-20261026-d834-conn",
        "hosted-all12-h12-claude-20261027-4923-conn",
    ]
    assert [item["connector_id"] for item in items] == expected_ids

    for item in items:
        assert set(item.keys()) == _REAL_REGISTRY_ENTRY_KEYS
        assert item["schema_version"] == "source_connector_registry_entry.v1"
        assert "allowed_actions" not in item
        assert "allowedActions" not in item
        assert "persona_fleet_status" not in item
        assert "personaFleetStatus" not in item
        assert "inferred_persona_fleet_status" not in item


def test_legacy_projection_strips_allowed_actions_and_inferred_persona_fleet_status(
    fake_client: FakeSourceManagementClient,
) -> None:
    """AC4: Even if upstream registry connector dicts contain action or persona fleet status keys,
    legacy projection rows strip them so no command can be offered for legacy rows.
    """
    polluted_connectors = _real_seven_registry_connectors()
    polluted_connectors[0] = _make_real_registry_connector_entry(
        "tw-twse-tpex-official-market",
        "TWSE/TPEx",
        "enabled",
        extra_fields={
            "allowed_actions": {"canEnable": True, "canValidate": True},
            "allowedActions": {"canEnable": True, "canValidate": True},
            "persona_fleet_status": "ready",
            "personaFleetStatus": "ready",
            "inferred_persona_fleet_status": "active",
            "inferredPersonaFleetStatus": "active",
            "persona_status": "online",
            "fleet_status": "healthy",
        },
    )
    client = _mounted_client_with_store(
        fake_client,
        {
            "source": "service_client",
            "connectors": polluted_connectors,
        },
    )

    response = client.get("/bff/management/data-sources", headers=OPERATOR_HEADERS)
    assert response.status_code == 200, response.text
    items = response.json()["data"]["items"]
    assert len(items) == 7
    for item in items:
        assert set(item.keys()) == _REAL_REGISTRY_ENTRY_KEYS
        assert "allowed_actions" not in item
        assert "allowedActions" not in item
        assert "persona_fleet_status" not in item
        assert "personaFleetStatus" not in item
        assert "inferred_persona_fleet_status" not in item
        assert "inferredPersonaFleetStatus" not in item
        assert "persona_status" not in item
        assert "fleet_status" not in item


def test_populated_v2_store_response_stays_field_for_field_identical(
    fake_client: FakeSourceManagementClient,
) -> None:
    """AC2: When the v2 store returns instances, the response stays field-for-field identical
    to today even when the legacy registry also has the 7 connectors.
    """
    fake_client.seed_source(
        "src-twse-01",
        definition_id="srcdef-twse-stock-day-all",
        lifecycle_state="configured_disabled",
        revision=1,
    )
    client = _mounted_client_with_store(
        fake_client,
        {
            "source": "service_client",
            "connectors": _real_seven_registry_connectors(),
        },
    )

    response = client.get("/bff/management/data-sources", headers=OPERATOR_HEADERS)
    assert response.status_code == 200, response.text
    payload = response.json()

    assert set(payload.keys()) == {"data", "page_info", "meta"}
    assert payload["page_info"] == {
        "next_page_token": None,
        "total": 1,
        "page_size": 50,
        "returned": 1,
        "has_more": False,
    }
    assert payload["meta"]["status"] == "ok"
    assert payload["meta"]["source"] == "service_client"
    assert "degradation" not in payload["meta"]
    assert "reason" not in payload["meta"]
    assert payload["meta"]["surfaces"] == {
        "data_sources": {
            "status": "ok",
            "source": "service_client",
        }
    }

    data = payload["data"]
    assert set(data.keys()) == {"id", "items", "summary", "status", "source"}
    assert data["id"] == "management-data-sources"
    assert data["status"] == "ok"
    assert data["source"] == "service_client"
    assert data["summary"] == {
        "total_items": 1,
        "returned_items": 1,
        "status": "ok",
        "source": "service_client",
    }
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert set(item.keys()) == {
        "schema_version",
        "source_instance_id",
        "connector_id",
        "provider",
        "source_class",
        "definition",
        "instance",
        "desired",
        "observed",
        "allowed_actions",
        "allowedActions",
        "lineage_summary",
    }
    assert item["schema_version"] == "management_data_source.v2"
    assert item["source_instance_id"] == "src-twse-01"
    assert item["allowed_actions"] == item["allowedActions"]


def test_client_errors_unconfigured_client_and_unavailable_or_empty_legacy_registry_keep_status(
    fake_client: FakeSourceManagementClient,
) -> None:
    """AC3: Client errors, unconfigured client, unavailable legacy registry, and empty legacy
    registry keep today's status and error.
    """
    # 1. Client error on list_sources with unavailable legacy store -> status="unavailable"
    error_client = FakeSourceManagementClient()

    def _raise_client_error(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        raise SourceManagementClientError(
            "source-ingest management store unreachable",
            status_code=503,
            error_code="DEPENDENCY_UNAVAILABLE",
        )

    error_client.list_sources = _raise_client_error  # type: ignore[method-assign]
    client_err_unavail = _mounted_client_with_store(
        error_client,
        {"source": "unavailable", "connectors": [], "reason": "upstream_connection_refused"},
    )
    resp_err_unavail = client_err_unavail.get("/bff/management/data-sources", headers=OPERATOR_HEADERS)
    assert resp_err_unavail.status_code == 200
    body_err_unavail = resp_err_unavail.json()
    assert body_err_unavail["meta"]["status"] == "unavailable"
    assert body_err_unavail["meta"]["source"] == "unavailable"
    assert body_err_unavail["meta"]["degradation"]["reason"] == "upstream_connection_refused"
    assert body_err_unavail["data"]["items"] == []

    # 2. Unconfigured client with populated legacy registry -> keeps today's status="ok", source="service_client"
    unconfigured_client = SourceManagementClient(base_url="")
    assert unconfigured_client.configured is False
    client_unconf = _mounted_client_with_store(
        unconfigured_client,
        {"source": "service_client", "connectors": _real_seven_registry_connectors()},
    )
    resp_unconf = client_unconf.get("/bff/management/data-sources", headers=OPERATOR_HEADERS)
    assert resp_unconf.status_code == 200
    body_unconf = resp_unconf.json()
    assert body_unconf["meta"]["status"] == "ok"
    assert body_unconf["meta"]["source"] == "service_client"
    assert body_unconf["data"]["status"] == "ok"
    assert body_unconf["data"]["source"] == "service_client"
    assert len(body_unconf["data"]["items"]) == 7

    # 3. Empty v2 store + unavailable legacy registry (source="missing" and source="unavailable")
    for unavail_source in ("missing", "unavailable"):
        client_unavail = _mounted_client_with_store(
            fake_client,
            {"source": unavail_source, "connectors": []},
        )
        resp_unavail = client_unavail.get("/bff/management/data-sources", headers=OPERATOR_HEADERS)
        assert resp_unavail.status_code == 200
        body_unavail = resp_unavail.json()
        assert body_unavail["meta"]["status"] == "unavailable"
        assert body_unavail["meta"]["source"] == unavail_source
        assert body_unavail["data"]["status"] == "unavailable"
        assert body_unavail["data"]["source"] == unavail_source
        assert body_unavail["data"]["items"] == []
        assert body_unavail["meta"]["degradation"]["reason"]

    # 4. Empty v2 store + empty legacy registry (source="service_client", connectors=[])
    client_empty_leg = _mounted_client_with_store(
        fake_client,
        {"source": "service_client", "connectors": []},
    )
    resp_empty_leg = client_empty_leg.get("/bff/management/data-sources", headers=OPERATOR_HEADERS)
    assert resp_empty_leg.status_code == 200
    body_empty_leg = resp_empty_leg.json()
    assert body_empty_leg["meta"]["status"] == "degraded"
    assert body_empty_leg["meta"]["source"] == "service_client"
    assert body_empty_leg["meta"]["surfaces"]["data_sources"]["message"] == (
        "Source-ingest registry is readable but currently empty."
    )
    assert body_empty_leg["data"]["items"] == []




# ==============================================================================
# SOURCE COMMANDS KILL SWITCH (PANTHEON_BFF_SOURCE_MANAGEMENT_COMMANDS_ENABLED)
# ==============================================================================

_FLAG = "PANTHEON_BFF_SOURCE_MANAGEMENT_COMMANDS_ENABLED"

_WRITE_ROUTES: List[Any] = [
    ("post", "/bff/management/data-sources", {"definitionId": "srcdef-twse-stock-day-all", "sourceInstanceId": "src-new", "provider": "TWSE", "sourceClass": "market_daily", "reason": "create"}),
    ("post", "/bff/management/data-sources/src-twse-01/actions/validate", {"expectedRevision": 1, "reason": "r"}),
    ("post", "/bff/management/data-sources/src-twse-01/actions/canary", {"expectedRevision": 1, "reason": "r"}),
    ("post", "/bff/management/data-sources/src-twse-01/actions/enable", {"expectedRevision": 1, "reason": "r", "confirmation": True}),
    ("post", "/bff/management/data-sources/src-twse-01/actions/disable", {"expectedRevision": 1, "reason": "r"}),
    ("post", "/bff/management/data-sources/src-twse-01/actions/degrade", {"expectedRevision": 1, "reason": "r"}),
    ("post", "/bff/management/data-sources/src-twse-01/actions/resume", {"expectedRevision": 1, "reason": "r"}),
    ("put", "/bff/management/data-sources/src-twse-01/schedule", {"expectedRevision": 1, "reason": "r", "schedule": {"cron": "0 * * * *"}}),
    ("post", "/bff/management/data-sources/src-twse-01/actions/replace", {"expectedRevision": 1, "reason": "r", "confirmation": True, "replacementSourceId": "src-other"}),
    ("post", "/bff/management/data-sources/src-twse-01/actions/retire", {"expectedRevision": 1, "reason": "r", "confirmation": True}),
]


def _write_route_paths_registered(client: TestClient) -> set:
    paths = client.app.openapi()["paths"]  # type: ignore[attr-defined]
    return {
        (method, path)
        for path, ops in paths.items()
        if path.startswith("/bff/management/data-sources")
        for method in ops
        if method in {"post", "put"}
    }


def test_kill_switch_write_route_table_covers_every_registered_write_route(bff_client: TestClient) -> None:
    registered = _write_route_paths_registered(bff_client)
    covered = set()
    for method, path, _ in _WRITE_ROUTES:
        parts = path.split("/")
        if len(parts) > 4:
            parts[4] = "{source_instance_id}"
        covered.add((method, "/".join(parts)))
    assert covered == registered


@pytest.mark.parametrize("flag", [None, "0", "", "true", "yes", "2"])
@pytest.mark.parametrize("method,path,body", _WRITE_ROUTES)
def test_write_routes_rejected_unless_flag_exactly_one(
    bff_client: TestClient,
    fake_client: FakeSourceManagementClient,
    monkeypatch: pytest.MonkeyPatch,
    flag: Optional[str],
    method: str,
    path: str,
    body: Dict[str, Any],
) -> None:
    if flag is None:
        monkeypatch.delenv(_FLAG, raising=False)
    else:
        monkeypatch.setenv(_FLAG, flag)
    fake_client.seed_source("src-twse-01", definition_id="srcdef-twse-stock-day-all", lifecycle_state="configured_disabled", revision=1)
    calls: List[Any] = []
    original = fake_client.execute_command
    fake_client.execute_command = lambda *a, **k: calls.append((a, k)) or original(*a, **k)  # type: ignore[method-assign]

    response = getattr(bff_client, method)(path, json=body, headers={**OPERATOR_HEADERS, "Idempotency-Key": "kill-1"})
    assert response.status_code == 503, response.text
    err = response.json()["error"]
    assert err["code"] == "DEPENDENCY_UNAVAILABLE"
    assert err["details"]["reason"] == "source_management_commands_disabled"
    assert err["details"]["switch"] == _FLAG
    assert _FLAG in err["message"]
    assert calls == []
    assert "src-new" not in fake_client.sources


def test_kill_switch_still_requires_operator_role(bff_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_FLAG, raising=False)
    response = bff_client.post(
        "/bff/management/data-sources/src-twse-01/actions/validate",
        json={"expectedRevision": 1, "reason": "r"},
        headers={**VIEWER_HEADERS, "Idempotency-Key": "k"},
    )
    assert response.status_code == 403


def test_reads_report_all_actions_blocked_and_meta_flag_when_disabled(
    bff_client: TestClient, fake_client: FakeSourceManagementClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(_FLAG, raising=False)
    fake_client.seed_source("src-twse-01", definition_id="srcdef-twse-stock-day-all", lifecycle_state="enabled", revision=2)

    listing = bff_client.get("/bff/management/data-sources", headers=OPERATOR_HEADERS)
    assert listing.status_code == 200, listing.text
    payload = listing.json()
    assert payload["meta"]["source_commands_enabled"] is False
    for item in payload["data"]["items"]:
        for key in ("allowed_actions", "allowedActions"):
            actions = item[key]
            assert not any(v is True for k, v in actions.items() if k.startswith("can")), actions
            assert any(_FLAG in r for r in actions["blockedReasons"])

    detail = bff_client.get("/bff/management/data-sources/src-twse-01", headers=OPERATOR_HEADERS)
    assert detail.status_code == 200, detail.text
    actions = detail.json()["data"]["allowed_actions"]
    assert not any(v is True for k, v in actions.items() if k.startswith("can")), actions
    assert any(_FLAG in r for r in actions["blockedReasons"])


def test_reads_unchanged_and_meta_flag_true_when_enabled(
    bff_client: TestClient, fake_client: FakeSourceManagementClient
) -> None:
    from services.source_ingestion.connector_definitions import calculate_source_allowed_actions

    fake_client.seed_source("src-twse-01", definition_id="srcdef-twse-stock-day-all", lifecycle_state="enabled", revision=2)
    payload = bff_client.get("/bff/management/data-sources", headers=OPERATOR_HEADERS).json()
    assert payload["meta"]["source_commands_enabled"] is True
    item = payload["data"]["items"][0]
    expected = calculate_source_allowed_actions(item["definition"], item["instance"], item["desired"], item["observed"])
    assert item["allowed_actions"] == expected
    assert not any(_FLAG in r for r in item["allowed_actions"]["blockedReasons"])
    assert item["allowed_actions"]["canDisable"] is True


def test_meta_flag_reported_on_legacy_fallback_list(fake_client: FakeSourceManagementClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_FLAG, raising=False)
    client = _mounted_client_with_store(
        fake_client,
        {"source": "service_client", "connectors": _real_seven_registry_connectors()},
    )
    response = client.get("/bff/management/data-sources", headers=OPERATOR_HEADERS)
    assert response.status_code == 200, response.text
    assert response.json()["meta"]["source_commands_enabled"] is False
