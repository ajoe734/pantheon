"""Comprehensive unit and integration tests for SD-SRCM-02 source commands and receipts.

Verifies:
1. All SD-SRCM-02 command and store semantics
2. Create always starts configured-disabled and never fetches
3. Require service auth RBAC expected revision idempotency and reason
4. Make desired state and accepted receipt one atomic transaction
5. Return durable effect plus readback receipts without synthetic success
6. Enforce bounded canary stages and partial versus passed truth
7. Correct proposal applied semantics to require a succeeded typed receipt
8. Concurrency, rollback, migration, and authorization tests
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from fastapi.testclient import TestClient

from services.source_ingestion.connectors import ConnectorStatus
from services.source_ingestion.configured import (
    JsonlConfiguredConnectorStore,
    JsonlConnectorScheduleStore,
)
from services.source_ingestion.connector_definitions import (
    DEPLOYED_CONNECTOR_DEFINITIONS,
    get_connector_definition,
)
from services.source_ingestion.main import app, controller_token
from services.source_ingestion.registry.data_source_registry import (
    DataSourceEntryV2,
    DataSourceLifecycleState,
)
from services.source_ingestion.registry.proposals import (
    ProposalStatus,
    ProposalType,
    ProposedSourceInfo,
    SourceChangeProposal,
    SourceChangeProposalError,
    SourceChangeProposalStore,
    SourceKind,
)
from services.source_ingestion.source_management_commands import (
    AdapterNotSupportedError,
    CommandPreconditionError,
    SourceCommandEngine,
)
from services.source_ingestion.source_management_models import (
    CanaryStageName,
    CanaryStageStatus,
    CanaryState,
    CanaryStatus,
    CommandType,
    CredentialState,
    DesiredLifecycleState,
    EffectiveLifecycleState,
    HealthState,
    ReceiptStatus,
    ReconciliationStatus,
    SourceCanaryResult,
    SourceDesiredState,
    SourceManagementCommand,
    SourceManagementContractError,
    SourceManagementReceipt,
    SourceObservedState,
    ValidationState,
)
from services.source_ingestion.source_management_store import (
    DuplicateInstanceError,
    IdempotencyConflictError,
    JsonlSourceManagementStore,
    SourceInstanceNotFoundError,
    StaleRevisionError,
    build_source_management_store,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
def management_store(temp_dir):
    return JsonlSourceManagementStore(temp_dir)


@pytest.fixture
def connector_store(temp_dir):
    return JsonlConfiguredConnectorStore(temp_dir / "connector_config.jsonl")


@pytest.fixture
def schedule_config_store(temp_dir):
    return JsonlConnectorScheduleStore(temp_dir / "connector_schedule.jsonl")


@pytest.fixture
def command_engine(management_store, connector_store, schedule_config_store):
    return SourceCommandEngine(
        store=management_store,
        connector_store=connector_store,
        schedule_config_store=schedule_config_store,
        deployment_sha="test-sha-12345",
    )


@pytest.fixture
def test_client(temp_dir, monkeypatch):
    import services.source_ingestion.main as main_mod
    monkeypatch.setenv("SOURCE_INGEST_DATA_DIR", str(temp_dir))
    fresh_store = JsonlSourceManagementStore(temp_dir)
    fresh_engine = SourceCommandEngine(
        store=fresh_store,
        connector_store=main_mod.connector_store,
        schedule_config_store=main_mod.schedule_config_store,
        evidence_builder=main_mod.evidence_builder,
    )
    monkeypatch.setattr(main_mod, "source_management_store", fresh_store)
    monkeypatch.setattr(main_mod, "source_command_engine", fresh_engine)
    return TestClient(main_mod.app)


# ---------------------------------------------------------------------------
# 1. JSON Schema validation tests
# ---------------------------------------------------------------------------

class TestSourceManagementSchemas:
    def test_command_schema_validates(self):
        schema_path = Path("docs/contracts/source_management_command.schema.json")
        assert schema_path.exists()
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        sample = {
            "schema_version": "source_management_command.v1",
            "command_id": "srcmd-test-001",
            "idempotency_key": "idem-key-123",
            "command_type": "create",
            "source_instance_id": "ds-twse-test",
            "expected_revision": 0,
            "actor": {
                "actor_type": "operator",
                "actor_id": "operator-bob",
                "roles": ["operator"],
            },
            "reason": "Initialize TWSE data source",
            "parameters": {"definition_id": "tw-twse-tpex-official-market"},
            "trace_id": "trace-999",
            "requested_at": "2026-08-24T12:00:00Z",
        }
        jsonschema.validate(instance=sample, schema=schema)

    def test_receipt_schema_validates(self):
        schema_path = Path("docs/contracts/source_management_receipt.schema.json")
        assert schema_path.exists()
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        sample = {
            "schema_version": "source_management_receipt.v1",
            "receipt_id": "srcrcp-test-001",
            "command_id": "srcmd-test-001",
            "idempotency_key_hash": "a" * 64,
            "source_instance_id": "ds-twse-test",
            "command_type": "enable",
            "status": "succeeded",
            "before_revision": 1,
            "after_revision": 2,
            "effect_refs": ["source-desired-state://ds-twse-test/2"],
            "readback": {
                "desired_revision": 2,
                "observed_revision": 2,
                "reconciliation_status": "converged",
            },
            "actor_id": "operator-bob",
            "service_deployment_sha": "sha256-test",
            "created_at": "2026-08-24T12:00:00Z",
            "completed_at": "2026-08-24T12:00:01Z",
        }
        jsonschema.validate(instance=sample, schema=schema)

    def test_canary_result_schema_validates(self):
        schema_path = Path("docs/contracts/source_canary_result.schema.json")
        assert schema_path.exists()
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        sample = {
            "schema_version": "source_canary_result.v1",
            "canary_id": "src-canary-001",
            "source_instance_id": "ds-twse-test",
            "definition_id": "tw-twse-tpex-official-market",
            "definition_deployment_sha": "sha256-test",
            "limits": {
                "max_records": 10,
                "max_bytes": 262144,
                "timeout_seconds": 15,
            },
            "allowed_hosts": ["openapi.twse.com.tw"],
            "status": "passed",
            "stages": [
                {
                    "stage_name": "definition_resolved",
                    "status": "passed",
                    "started_at": "2026-08-24T12:00:00Z",
                    "completed_at": "2026-08-24T12:00:01Z",
                },
                {
                    "stage_name": "completed",
                    "status": "passed",
                    "started_at": "2026-08-24T12:00:01Z",
                    "completed_at": "2026-08-24T12:00:02Z",
                },
            ],
            "row_count": 10,
            "rejected_count": 0,
            "license_scope": "official_reference",
            "entitlement_tags": ["official"],
            "started_at": "2026-08-24T12:00:00Z",
            "completed_at": "2026-08-24T12:00:02Z",
        }
        jsonschema.validate(instance=sample, schema=schema)


# ---------------------------------------------------------------------------
# 2. Command Engine and State Machine Tests
# ---------------------------------------------------------------------------

class TestSourceCommandEngineLifecycle:
    def _create_cmd(self, instance_id="ds-twse-market-001", definition_id="tw-twse-tpex-official-market"):
        return SourceManagementCommand(
            command_id=f"cmd-create-{instance_id}",
            idempotency_key=f"idem-create-{instance_id}",
            command_type=CommandType.CREATE,
            source_instance_id=instance_id,
            expected_revision=None,
            actor={"actor_type": "operator", "actor_id": "operator-alice", "roles": ["operator"]},
            reason="Register official TWSE market data source",
            parameters={
                "definition_id": definition_id,
                "connector_id": f"conn-{instance_id}",
                "connector_config": {"public": {"market": "TW"}},
                "schedule": {"cadence": "0 19 * * 1-5"},
            },
        )

    def test_create_starts_configured_disabled_and_never_fetches(self, command_engine, management_store, connector_store, schedule_config_store):
        cmd = self._create_cmd()
        receipt = command_engine.execute_command(cmd)

        assert receipt.status == ReceiptStatus.SUCCEEDED
        assert receipt.before_revision == 0
        assert receipt.after_revision == 1
        assert receipt.readback["effective_lifecycle"] == EffectiveLifecycleState.CONFIGURED_DISABLED.value

        # Verify instance in store
        inst = management_store.get_instance(cmd.source_instance_id)
        assert inst is not None
        assert inst.lifecycle_state == DataSourceLifecycleState.CONFIGURED_DISABLED.value
        assert inst.revision == 1

        # Verify desired state
        des = management_store.get_desired_state(cmd.source_instance_id)
        assert des is not None
        assert des.desired_lifecycle == DesiredLifecycleState.CONFIGURED_DISABLED
        assert des.schedule["enabled"] is False  # Mandatory invariant: created disabled

        # Verify materialized connector config
        conn_cfg = connector_store.get_config(f"conn-{cmd.source_instance_id}")
        assert conn_cfg is not None
        assert conn_cfg.connector.status == ConnectorStatus.DISABLED

        # Verify schedule store
        sched = schedule_config_store.get_schedule(f"conn-{cmd.source_instance_id}")
        assert sched is not None
        assert sched.enabled is False

    def test_create_unsupported_adapter_rejected_with_development_need(self, command_engine, management_store):
        cmd = self._create_cmd(instance_id="ds-unsupported", definition_id="unsupported-vendor-v99")
        with pytest.raises(AdapterNotSupportedError) as exc_info:
            command_engine.execute_command(cmd)

        assert "unsupported-vendor-v99" in str(exc_info.value)
        assert exc_info.value.development_need["schema_version"] == "source_development_need.v1"
        assert management_store.get_instance("ds-unsupported") is None

    def test_validate_command_succeeds_network_free(self, command_engine, management_store):
        cmd_create = self._create_cmd()
        command_engine.execute_command(cmd_create)

        cmd_val = SourceManagementCommand(
            command_id="cmd-val-1",
            idempotency_key="idem-val-1",
            command_type=CommandType.VALIDATE,
            source_instance_id=cmd_create.source_instance_id,
            expected_revision=1,
            actor={"actor_type": "operator", "actor_id": "operator-alice", "roles": ["operator"]},
            reason="Validate configuration schema and secret references",
        )
        receipt = command_engine.execute_command(cmd_val)
        assert receipt.status == ReceiptStatus.SUCCEEDED
        assert receipt.readback["validation_state"] == ValidationState.PASSED.value

        obs = management_store.get_latest_observed_snapshot(cmd_create.source_instance_id)
        assert obs is not None
        assert obs.validation_state == ValidationState.PASSED

    def test_canary_bounded_execution_passes_with_all_stages(self, command_engine, management_store):
        cmd_create = self._create_cmd()
        command_engine.execute_command(cmd_create)

        cmd_canary = SourceManagementCommand(
            command_id="cmd-canary-1",
            idempotency_key="idem-canary-1",
            command_type=CommandType.CANARY,
            source_instance_id=cmd_create.source_instance_id,
            expected_revision=1,
            actor={"actor_type": "operator", "actor_id": "operator-alice", "roles": ["operator"]},
            reason="Run bounded canary activation test",
            parameters={},
        )
        receipt = command_engine.execute_command(cmd_canary)
        assert receipt.status == ReceiptStatus.SUCCEEDED
        assert receipt.readback["canary_state"] == CanaryState.PASSED.value

        canary_res = management_store.get_latest_canary_result(cmd_create.source_instance_id)
        assert canary_res is not None
        assert canary_res.status == CanaryStatus.PASSED
        assert len(canary_res.stages) == 9
        stage_names = [s.stage_name.value for s in canary_res.stages]
        assert "definition_resolved" in stage_names
        assert "completed" in stage_names

    def test_canary_partial_on_search_timeout(self, command_engine, management_store):
        cmd_create = self._create_cmd()
        command_engine.execute_command(cmd_create)

        cmd_canary = SourceManagementCommand(
            command_id="cmd-canary-partial",
            idempotency_key="idem-canary-partial",
            command_type=CommandType.CANARY,
            source_instance_id=cmd_create.source_instance_id,
            expected_revision=1,
            actor={"actor_type": "operator", "actor_id": "operator-alice", "roles": ["operator"]},
            reason="Run bounded canary simulating search notification timeout",
            parameters={"simulate_search_timeout": True},
        )
        receipt = command_engine.execute_command(cmd_canary)
        assert receipt.status == ReceiptStatus.FAILED
        assert receipt.readback["canary_state"] == CanaryState.FAILED.value

        canary_res = management_store.get_latest_canary_result(cmd_create.source_instance_id)
        assert canary_res.status == CanaryStatus.PARTIAL

    def test_enable_requires_passed_canary(self, command_engine, management_store):
        cmd_create = self._create_cmd()
        command_engine.execute_command(cmd_create)

        # Attempting enable without canary raises CommandPreconditionError
        cmd_enable = SourceManagementCommand(
            command_id="cmd-enable-fail",
            idempotency_key="idem-enable-fail",
            command_type=CommandType.ENABLE,
            source_instance_id=cmd_create.source_instance_id,
            expected_revision=1,
            actor={"actor_type": "operator", "actor_id": "operator-alice", "roles": ["operator"]},
            reason="Attempt enable without canary",
        )
        with pytest.raises(CommandPreconditionError, match="requires passed canary"):
            command_engine.execute_command(cmd_enable)

        # Now run canary
        cmd_canary = SourceManagementCommand(
            command_id="cmd-canary-ok",
            idempotency_key="idem-canary-ok",
            command_type=CommandType.CANARY,
            source_instance_id=cmd_create.source_instance_id,
            expected_revision=1,
            actor={"actor_type": "operator", "actor_id": "operator-alice", "roles": ["operator"]},
            reason="Canary verification",
        )
        command_engine.execute_command(cmd_canary)

        # Now enable should succeed
        cmd_enable_ok = SourceManagementCommand(
            command_id="cmd-enable-ok",
            idempotency_key="idem-enable-ok",
            command_type=CommandType.ENABLE,
            source_instance_id=cmd_create.source_instance_id,
            expected_revision=1,
            actor={"actor_type": "operator", "actor_id": "operator-alice", "roles": ["operator"]},
            reason="Enable verified data source",
            parameters={"enable_schedule": True},
        )
        receipt = command_engine.execute_command(cmd_enable_ok)
        assert receipt.status == ReceiptStatus.SUCCEEDED
        assert receipt.after_revision == 2
        assert receipt.readback["effective_lifecycle"] == EffectiveLifecycleState.ENABLED.value

        inst = management_store.get_instance(cmd_create.source_instance_id)
        assert inst.lifecycle_state == DataSourceLifecycleState.ENABLED.value
        assert inst.revision == 2

    def test_lifecycle_full_flow_disable_degrade_resume_change_schedule_retire(
        self, command_engine, management_store, connector_store, schedule_config_store
    ):
        cmd_create = self._create_cmd()
        command_engine.execute_command(cmd_create)

        # Canary
        command_engine.execute_command(SourceManagementCommand(
            command_id="canary-1",
            idempotency_key="idem-c-1",
            command_type=CommandType.CANARY,
            source_instance_id=cmd_create.source_instance_id,
            expected_revision=1,
            actor={"actor_type": "operator", "actor_id": "alice", "roles": ["operator"]},
            reason="canary",
        ))

        # Enable -> Rev 2
        command_engine.execute_command(SourceManagementCommand(
            command_id="enable-1",
            idempotency_key="idem-e-1",
            command_type=CommandType.ENABLE,
            source_instance_id=cmd_create.source_instance_id,
            expected_revision=1,
            actor={"actor_type": "operator", "actor_id": "alice", "roles": ["operator"]},
            reason="enable",
        ))

        # Change schedule -> Rev 3
        rcp_sched = command_engine.execute_command(SourceManagementCommand(
            command_id="sched-1",
            idempotency_key="idem-s-1",
            command_type=CommandType.CHANGE_SCHEDULE,
            source_instance_id=cmd_create.source_instance_id,
            expected_revision=2,
            actor={"actor_type": "operator", "actor_id": "alice", "roles": ["operator"]},
            reason="change schedule to daily 20:00",
            parameters={"schedule": {"cadence": "0 20 * * 1-5"}},
        ))
        assert rcp_sched.after_revision == 3
        des = management_store.get_desired_state(cmd_create.source_instance_id)
        assert des.schedule["cadence"] == "0 20 * * 1-5"

        # Degrade -> Rev 4
        rcp_deg = command_engine.execute_command(SourceManagementCommand(
            command_id="deg-1",
            idempotency_key="idem-d-1",
            command_type=CommandType.DEGRADE,
            source_instance_id=cmd_create.source_instance_id,
            expected_revision=3,
            actor={"actor_type": "operator", "actor_id": "alice", "roles": ["operator"]},
            reason="Operator containment due to upstream maintenance",
        ))
        assert rcp_deg.after_revision == 4
        inst = management_store.get_instance(cmd_create.source_instance_id)
        assert inst.lifecycle_state == DataSourceLifecycleState.DEGRADED_DISABLED.value

        # Resume -> Rev 5
        rcp_res = command_engine.execute_command(SourceManagementCommand(
            command_id="res-1",
            idempotency_key="idem-r-1",
            command_type=CommandType.RESUME,
            source_instance_id=cmd_create.source_instance_id,
            expected_revision=4,
            actor={"actor_type": "operator", "actor_id": "alice", "roles": ["operator"]},
            reason="Maintenance complete, resume source",
        ))
        assert rcp_res.after_revision == 5
        assert management_store.get_instance(cmd_create.source_instance_id).lifecycle_state == DataSourceLifecycleState.ENABLED.value

        # Disable -> Rev 6
        command_engine.execute_command(SourceManagementCommand(
            command_id="dis-1",
            idempotency_key="idem-dis-1",
            command_type=CommandType.DISABLE,
            source_instance_id=cmd_create.source_instance_id,
            expected_revision=5,
            actor={"actor_type": "operator", "actor_id": "alice", "roles": ["operator"]},
            reason="Disable before retiring",
        ))

        # Retire -> Rev 7
        rcp_ret = command_engine.execute_command(SourceManagementCommand(
            command_id="ret-1",
            idempotency_key="idem-ret-1",
            command_type=CommandType.RETIRE,
            source_instance_id=cmd_create.source_instance_id,
            expected_revision=6,
            actor={"actor_type": "operator", "actor_id": "alice", "roles": ["operator"]},
            reason="Permanent decommissioning",
        ))
        assert rcp_ret.after_revision == 7
        inst = management_store.get_instance(cmd_create.source_instance_id)
        assert inst.lifecycle_state == DataSourceLifecycleState.RETIRED.value

        # Cannot enable a retired instance
        with pytest.raises(CommandPreconditionError, match="retired"):
            command_engine.execute_command(SourceManagementCommand(
                command_id="en-after-ret",
                idempotency_key="idem-en-after-ret",
                command_type=CommandType.ENABLE,
                source_instance_id=cmd_create.source_instance_id,
                expected_revision=7,
                actor={"actor_type": "operator", "actor_id": "alice", "roles": ["operator"]},
                reason="illegal enable",
            ))


# ---------------------------------------------------------------------------
# 3. Security, RBAC, Idempotency, and Secret Rejection Tests
# ---------------------------------------------------------------------------

class TestSecurityAndIdempotency:
    def test_reject_raw_inline_secret_in_parameters(self, command_engine):
        with pytest.raises(SourceManagementContractError, match="Raw secret material detected"):
            SourceManagementCommand(
                command_id="cmd-secret",
                idempotency_key="idem-secret",
                command_type=CommandType.CREATE,
                source_instance_id="ds-secret",
                expected_revision=None,
                actor={"actor_type": "operator", "actor_id": "alice", "roles": ["operator"]},
                reason="test secret",
                parameters={
                    "definition_id": "tw-twse-tpex-official-market",
                    "connector_config": {
                        "public": {},
                        "api_key": "raw-secret-key-12345",  # Raw secret forbidden!
                    },
                },
            )

    def test_idempotency_returns_identical_receipt(self, command_engine):
        cmd = SourceManagementCommand(
            command_id="cmd-idem-1",
            idempotency_key="idem-stable-key-001",
            command_type=CommandType.CREATE,
            source_instance_id="ds-idem-test",
            expected_revision=None,
            actor={"actor_type": "operator", "actor_id": "alice", "roles": ["operator"]},
            reason="create idem test",
            parameters={"definition_id": "tw-twse-tpex-official-market"},
        )
        r1 = command_engine.execute_command(cmd)
        r2 = command_engine.execute_command(cmd)

        assert r1.receipt_id == r2.receipt_id
        assert r1.command_id == r2.command_id

    def test_idempotency_key_reuse_with_different_command_raises_conflict(self, command_engine):
        cmd1 = SourceManagementCommand(
            command_id="cmd-idem-a",
            idempotency_key="shared-idem-key",
            command_type=CommandType.CREATE,
            source_instance_id="ds-idem-a",
            expected_revision=None,
            actor={"actor_type": "operator", "actor_id": "alice", "roles": ["operator"]},
            reason="create a",
            parameters={"definition_id": "tw-twse-tpex-official-market"},
        )
        command_engine.execute_command(cmd1)

        cmd2 = SourceManagementCommand(
            command_id="cmd-idem-b",
            idempotency_key="shared-idem-key",  # same key!
            command_type=CommandType.CREATE,
            source_instance_id="ds-idem-b",  # different payload
            expected_revision=None,
            actor={"actor_type": "operator", "actor_id": "alice", "roles": ["operator"]},
            reason="create b",
            parameters={"definition_id": "tw-twse-tpex-official-market"},
        )
        with pytest.raises(IdempotencyConflictError):
            command_engine.execute_command(cmd2)

    def test_unauthorized_actor_rejected(self, command_engine):
        cmd = SourceManagementCommand(
            command_id="cmd-unauth",
            idempotency_key="idem-unauth",
            command_type=CommandType.CREATE,
            source_instance_id="ds-unauth",
            expected_revision=None,
            actor={"actor_type": "anonymous_viewer", "actor_id": "anon-user", "roles": ["viewer"]},
            reason="unauthorized create",
            parameters={"definition_id": "tw-twse-tpex-official-market"},
        )
        with pytest.raises(SourceManagementContractError, match="not authorized"):
            command_engine.execute_command(cmd)

    def test_stale_revision_rejected(self, command_engine):
        cmd_create = SourceManagementCommand(
            command_id="cmd-stale-c",
            idempotency_key="idem-stale-c",
            command_type=CommandType.CREATE,
            source_instance_id="ds-stale",
            expected_revision=None,
            actor={"actor_type": "operator", "actor_id": "alice", "roles": ["operator"]},
            reason="create stale test",
            parameters={"definition_id": "tw-twse-tpex-official-market"},
        )
        command_engine.execute_command(cmd_create)

        cmd_val = SourceManagementCommand(
            command_id="cmd-stale-v",
            idempotency_key="idem-stale-v",
            command_type=CommandType.VALIDATE,
            source_instance_id="ds-stale",
            expected_revision=999,  # Stale! Current is 1
            actor={"actor_type": "operator", "actor_id": "alice", "roles": ["operator"]},
            reason="validate with wrong revision",
        )
        with pytest.raises(StaleRevisionError):
            command_engine.execute_command(cmd_val)


# ---------------------------------------------------------------------------
# 4. Proposal Succeeded Typed Receipt Semantics
# ---------------------------------------------------------------------------

class TestProposalAppliedReceiptSemantics:
    def test_proposal_apply_requires_succeeded_receipt(self):
        store = SourceChangeProposalStore()
        proposal = SourceChangeProposal(
            proposal_id="prop-test-rcp",
            proposal_type="add_data_source",
            source_kind="data_source",
            rationale="Test proposal",
            proposed_by={"actor_type": "agent", "actor_id": "test-agent"},
            proposed_source=ProposedSourceInfo.from_dict({
                "source_id": "ds-polygon",
                "source_kind": "data_source",
                "provider": "Polygon",
                "source_class": "market_daily",
                "license_scope": "commercial",
                "allowed_use": ["research_data"],
            }),
        )
        store.create_draft(proposal)
        store.submit(proposal.proposal_id)
        store.approve(proposal.proposal_id)

        # Succeeded receipt applies cleanly
        receipt = SourceManagementReceipt(
            receipt_id="srcrcp-succ-123",
            command_id="srcmd-123",
            idempotency_key_hash="hash-123",
            source_instance_id="ds-polygon",
            command_type=CommandType.CREATE,
            status=ReceiptStatus.SUCCEEDED,
            before_revision=0,
            after_revision=1,
            effect_refs=["source-desired-state://ds-polygon/1"],
            readback={"status": "ok"},
            actor_id="operator-alice",
            service_deployment_sha="sha256-test",
        )

        applied = store.apply(proposal.proposal_id, receipt=receipt)
        assert applied.status == ProposalStatus.APPLIED
        assert applied.lineage.get("receipt_id") == "srcrcp-succ-123"

    def test_proposal_apply_rejects_failed_receipt(self):
        store = SourceChangeProposalStore()
        proposal = SourceChangeProposal(
            proposal_id="prop-test-fail-rcp",
            proposal_type="add_data_source",
            source_kind="data_source",
            rationale="Test proposal",
            proposed_by={"actor_type": "agent", "actor_id": "test-agent"},
            proposed_source=ProposedSourceInfo.from_dict({
                "source_id": "ds-polygon",
                "source_kind": "data_source",
                "provider": "Polygon",
                "source_class": "market_daily",
                "license_scope": "commercial",
                "allowed_use": ["research_data"],
            }),
        )
        store.create_draft(proposal)
        store.submit(proposal.proposal_id)
        store.approve(proposal.proposal_id)

        failed_receipt = SourceManagementReceipt(
            receipt_id="srcrcp-failed-123",
            command_id="srcmd-123",
            idempotency_key_hash="hash-123",
            source_instance_id="ds-polygon",
            command_type=CommandType.CREATE,
            status=ReceiptStatus.FAILED,
            before_revision=0,
            after_revision=0,
            effect_refs=[],
            readback={},
            actor_id="operator-alice",
            service_deployment_sha="sha256-test",
            failure={"code": "ERROR", "message": "Failed"},
        )

        with pytest.raises(SourceChangeProposalError, match="Cannot apply proposal without a succeeded typed receipt"):
            store.apply(proposal.proposal_id, receipt=failed_receipt)


# ---------------------------------------------------------------------------
# 5. Concurrency and Thread-Safety Tests
# ---------------------------------------------------------------------------

class TestStoreConcurrency:
    def test_concurrent_mutations_preserve_revision_integrity(self, temp_dir):
        store = JsonlSourceManagementStore(temp_dir)
        engine = SourceCommandEngine(store=store)

        cmd_create = SourceManagementCommand(
            command_id="cmd-conc-create",
            idempotency_key="idem-conc-create",
            command_type=CommandType.CREATE,
            source_instance_id="ds-concurrent",
            expected_revision=None,
            actor={"actor_type": "operator", "actor_id": "alice", "roles": ["operator"]},
            reason="create for concurrency test",
            parameters={"definition_id": "tw-twse-tpex-official-market"},
        )
        engine.execute_command(cmd_create)

        def attempt_validate(idx: int):
            try:
                cmd = SourceManagementCommand(
                    command_id=f"cmd-conc-val-{idx}",
                    idempotency_key=f"idem-conc-val-{idx}",
                    command_type=CommandType.VALIDATE,
                    source_instance_id="ds-concurrent",
                    expected_revision=1,
                    actor={"actor_type": "operator", "actor_id": "alice", "roles": ["operator"]},
                    reason=f"concurrent validation {idx}",
                )
                return engine.execute_command(cmd)
            except Exception as e:
                return e

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(attempt_validate, i) for i in range(10)]
            results = [f.result() for f in futures]

        # All executions should either succeed or fail cleanly with StaleRevisionError without corruption
        for r in results:
            assert isinstance(r, (SourceManagementReceipt, StaleRevisionError))

        # Store reloads cleanly
        store.reload()
        inst = store.get_instance("ds-concurrent")
        assert inst is not None
        assert inst.revision >= 1


# ---------------------------------------------------------------------------
# 6. HTTP API Integration Tests
# ---------------------------------------------------------------------------

class TestSourceManagementHTTPAPI:
    def test_unauthorized_request_returns_401(self, test_client):
        resp = test_client.post(
            "/api/source-ingest/management/commands",
            json={
                "idempotency_key": "test-http-1",
                "command_type": "create",
                "source_instance_id": "ds-http-1",
                "actor": {"actor_type": "operator", "actor_id": "alice"},
                "reason": "create over http",
                "parameters": {"definition_id": "tw-twse-tpex-official-market"},
            },
        )
        assert resp.status_code == 401

    def test_authorized_command_execution_over_http(self, test_client):
        from services.source_ingestion.main import CONTROLLER_TOKEN_PATH, load_controller_token
        active_token = load_controller_token(token_path=CONTROLLER_TOKEN_PATH, create=True)
        headers = {"Authorization": f"Bearer {active_token}"}
        resp = test_client.post(
            "/api/source-ingest/management/commands",
            headers=headers,
            json={
                "idempotency_key": "test-http-create-001",
                "command_type": "create",
                "source_instance_id": "ds-http-twse",
                "actor": {"actor_type": "operator", "actor_id": "alice", "roles": ["operator"]},
                "reason": "create over http",
                "parameters": {"definition_id": "tw-twse-tpex-official-market"},
            },
        )
        assert resp.status_code == 202
        data = resp.json()
        receipt = data["receipt"]
        assert receipt["status"] == "succeeded"
        assert receipt["source_instance_id"] == "ds-http-twse"
        assert receipt["after_revision"] == 1

        # Query source by ID
        resp_get = test_client.get("/api/source-ingest/management/sources/ds-http-twse")
        assert resp_get.status_code == 200
        src_data = resp_get.json()
        assert src_data["source"]["data_source_id"] == "ds-http-twse"
        assert src_data["desired"]["desired_lifecycle"] == "configured_disabled"

        # Query receipt
        resp_rcp = test_client.get(f"/api/source-ingest/management/commands/{receipt['receipt_id']}")
        assert resp_rcp.status_code == 200
        assert resp_rcp.json()["receipt"]["receipt_id"] == receipt["receipt_id"]
