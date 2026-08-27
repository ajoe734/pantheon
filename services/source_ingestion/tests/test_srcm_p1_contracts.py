"""Unit and contract tests for external source management contracts (SD-SRCM-01).

Covers:
- ConnectorDefinition projection, validation, fingerprinting, allowlist checking
- DataSourceEntry v2 extension and bidirectional v1/v2 compatibility
- SourceDesiredState contract & secret rejection
- SourceObservedState contract & lifecycle mapping
- ManagementDataSourceDTO composition
- Server-side allowed actions calculation
- JSON schema validation against canonical Draft-07 schemas
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest
from jsonschema import Draft7Validator

from services.source_ingestion.connector_definitions import (
    ConnectorDefinition,
    compute_definition_fingerprint,
    deployed_connector_definitions,
    get_connector_definition,
    get_connector_definition_by_adapter,
    list_connector_definitions,
    validate_connector_definition,
    calculate_source_allowed_actions,
    DefinitionState,
)
from services.source_ingestion.registry.data_source_registry import (
    DataSourceClass,
    DataSourceEntry,
    DataSourceEntryV2,
    DataSourceLifecycleState,
    DataSourceRegistry,
    DataSourceRegistryError,
)
from services.source_ingestion.registry.strategy_seed_source_registry import (
    StrategySeedSourceClass,
    StrategySeedSourceEntry,
    StrategySeedSourceLifecycleState,
    StrategySeedSourceRegistry,
)
from services.source_ingestion.source_management_models import (
    CanaryState,
    CredentialState,
    DesiredLifecycleState,
    EffectiveLifecycleState,
    HealthState,
    ManagementDataSourceDTO,
    ReconciliationStatus,
    SourceDesiredState,
    SourceManagementContractError,
    SourceObservedState,
    ValidationState,
    assert_no_raw_secrets,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def load_schema(rel_path: str) -> dict:
    path = REPO_ROOT / rel_path
    assert path.exists(), f"Schema not found: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


# ==============================================================================
# 1. JSON Schema Draft-07 Conformance Tests
# ==============================================================================

@pytest.mark.parametrize("schema_path", [
    "docs/contracts/connector_definition.schema.json",
    "docs/contracts/data_source_registry_entry.v2.schema.json",
    "docs/contracts/source_desired_state.schema.json",
    "docs/contracts/source_observed_state.schema.json",
    "docs/contracts/bff/management_data_source.v2.schema.json",
])
def test_schemas_are_valid_draft7(schema_path: str) -> None:
    schema = load_schema(schema_path)
    Draft7Validator.check_schema(schema)


# ==============================================================================
# 2. ConnectorDefinition Tests
# ==============================================================================

def test_list_connector_definitions_returns_stably_sorted_list() -> None:
    definitions = list_connector_definitions()
    assert len(definitions) >= 18
    ids = [d.definition_id for d in definitions]
    assert ids == sorted(ids)


def test_all_deployed_connector_definitions_are_valid_and_schema_compliant() -> None:
    schema = load_schema("docs/contracts/connector_definition.schema.json")
    validator = Draft7Validator(schema)

    definitions = list_connector_definitions()
    for defn in definitions:
        validate_connector_definition(defn)
        d = defn.to_dict()
        errors = list(validator.iter_errors(d))
        assert not errors, f"Definition {defn.definition_id} failed schema validation: {errors}"
        # Check fingerprint matches
        assert defn.fingerprint == compute_definition_fingerprint(defn._to_raw_dict_without_fp())


def test_connector_definition_lookups() -> None:
    defn = get_connector_definition("tw-twse-tpex-official-market")
    assert defn is not None
    assert defn.provider == "TWSE/TPEx"
    assert "openapi.twse.com.tw" in defn.allowed_host_patterns

    # Lookup by adapter token
    defn_by_adapter = get_connector_definition_by_adapter("TaiwanOfficialMarketDatasetAdapter.records_from_payload")
    assert defn_by_adapter is not None
    assert defn_by_adapter.definition_id == "tw-twse-tpex-official-market"

    # Non-existent
    assert get_connector_definition("non-existent-id") is None
    assert get_connector_definition_by_adapter("non.existent.adapter") is None


def test_disabled_by_build_definitions_have_reason() -> None:
    stooq = get_connector_definition("us-stooq-daily-ohlcv")
    assert stooq is not None
    assert stooq.definition_state == DefinitionState.DISABLED_BY_BUILD
    assert stooq.disabled_reason is not None
    assert "disabled" in stooq.disabled_reason.lower()


def test_connector_definition_rejects_unallowed_adapter() -> None:
    with pytest.raises(SourceManagementContractError, match="not in deployed allowlist"):
        ConnectorDefinition(
            definition_id="malicious-custom-adapter",
            adapter_token="ArbitraryMaliciousClass.do_evil",
            adapter_version="1.0.0",
            provider="Unknown",
            source_kinds=("data_source",),
            source_types=("market",),
            source_classes=("market_daily",),
            datasets=("evil_data",),
            auth_modes=("none",),
            fetch_modes=("provider_owned_adapter",),
            config_schema={},
            secret_fields=(),
            required_pit_fields=("event_time", "available_time", "ingest_time"),
            default_limits={"max_records": 10, "max_bytes": 1024, "timeout_seconds": 5},
            allowed_host_patterns=(),
        )


def test_connector_definition_rejects_raw_secret_in_config_schema() -> None:
    with pytest.raises(SourceManagementContractError, match="Raw secret material detected"):
        ConnectorDefinition(
            definition_id="leaky-definition",
            adapter_token="TaiwanOfficialMarketDatasetAdapter.records_from_payload",
            adapter_version="1.0.0",
            provider="TWSE",
            source_kinds=("data_source",),
            source_types=("market",),
            source_classes=("market_daily",),
            datasets=("tw_price_daily",),
            auth_modes=("none",),
            fetch_modes=("provider_owned_adapter",),
            config_schema={"api_key": "raw-unredacted-secret-value-12345"},
            secret_fields=(),
            required_pit_fields=("event_time", "available_time", "ingest_time"),
            default_limits={"max_records": 10, "max_bytes": 1024, "timeout_seconds": 5},
            allowed_host_patterns=(),
        )


# ==============================================================================
# 3. DataSourceEntry V2 and Backward Compatibility Tests
# ==============================================================================

def test_data_source_entry_v2_creation_and_schema_validation() -> None:
    schema = load_schema("docs/contracts/data_source_registry_entry.v2.schema.json")
    validator = Draft7Validator(schema)

    entry = DataSourceEntryV2(
        data_source_id="ds-twse-market-primary",
        definition_id="tw-twse-tpex-official-market",
        connector_id="twse-market-primary",
        provider="TWSE",
        source_class=DataSourceClass.MARKET_DAILY,
        datasets=[{
            "dataset_id": "tw_price_daily",
            "dataset_class": "market_daily",
            "markets": ["TW"],
            "asset_classes": ["equity"],
            "storage_tier": "raw",
            "point_in_time_fields": ["event_time", "available_time", "ingest_time"],
        }],
        markets=["TW"],
        license_scope="official_reference",
        entitlement_tags=["twse_official"],
        allowed_use=["research_data", "backtest_data", "monitoring"],
        retention_policy_ref="source-retention://twse",
        deletion_policy_ref="source-deletion://twse",
        freshness_sla_seconds=86400,
        sensitivity="public",
        lifecycle_state=DataSourceLifecycleState.CONFIGURED_DISABLED,
        revision=1,
        created_by="operator-123",
        created_at="2026-08-24T12:00:00Z",
        updated_by="operator-123",
        updated_at="2026-08-24T12:00:00Z",
    )

    data = entry.to_dict()
    errors = list(validator.iter_errors(data))
    assert not errors, f"V2 entry failed schema validation: {errors}"
    assert data["schema_version"] == "data_source_registry_entry.v2"


def test_v1_to_v2_and_v2_to_v1_roundtrip() -> None:
    v1_entry = DataSourceEntry(
        data_source_id="ds-finmind-tw-data",
        provider="FinMind",
        source_class=DataSourceClass.MARKET_DAILY,
        datasets=[{"dataset_id": "tw_price_daily", "dataset_class": "market_daily"}],
        license_scope="vendor_api",
        allowed_use=["research_data", "backtest_data"],
        update_frequency="daily_1800_tst",
        connector_id="tw-finmind-dataset",
        lifecycle_state=DataSourceLifecycleState.ENABLED,
    )

    # Convert v1 -> v2
    v2_entry = v1_entry.to_v2(definition_id="tw-finmind-dataset")
    assert v2_entry.data_source_id == "ds-finmind-tw-data"
    assert v2_entry.definition_id == "tw-finmind-dataset"
    assert v2_entry.schema_version == "data_source_registry_entry.v2"
    assert v2_entry.is_ingestable is True

    # Validate v2 against v2 schema
    v2_schema = load_schema("docs/contracts/data_source_registry_entry.v2.schema.json")
    Draft7Validator(v2_schema).validate(v2_entry.to_dict())

    # Convert v2 -> v1
    v1_reconstructed = v2_entry.to_v1()
    assert v1_reconstructed.data_source_id == v1_entry.data_source_id
    assert v1_reconstructed.provider == v1_entry.provider
    assert v1_reconstructed.lifecycle_state == v1_entry.lifecycle_state


def test_registry_handles_mixed_v1_and_v2_entries(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "registry.jsonl"
    reg = DataSourceRegistry.from_jsonl(jsonl_path)

    v1 = DataSourceEntry(
        data_source_id="ds-v1",
        provider="Provider1",
        source_class=DataSourceClass.NEWS,
        datasets=[{"dataset_id": "news_d1", "dataset_class": "news"}],
        license_scope="public",
        allowed_use=["research_data"],
        update_frequency="hourly",
    )
    v2 = DataSourceEntryV2(
        data_source_id="ds-v2",
        definition_id="tw-finmind-dataset",
        connector_id="finmind-1",
        provider="FinMind",
        source_class=DataSourceClass.TAIWAN_CHIP,
        datasets=[{"dataset_id": "chip_d1", "dataset_class": "taiwan_chip"}],
        markets=["TW"],
        license_scope="vendor",
        allowed_use=["research_data"],
        retention_policy_ref="source-retention://finmind",
        deletion_policy_ref="source-deletion://finmind",
        lifecycle_state=DataSourceLifecycleState.VALIDATED_DISABLED,
        created_by="op1",
        updated_by="op1",
    )

    reg.add(v1)
    reg.add(v2)

    # Verify reloading from JSONL
    reg_reloaded = DataSourceRegistry.from_jsonl(jsonl_path)
    assert len(reg_reloaded.list()) == 2
    assert reg_reloaded.get("ds-v1") is not None
    assert reg_reloaded.get("ds-v2") is not None

    # Test list_v2 auto-upgrades v1
    v2_list = reg_reloaded.list_v2()
    assert len(v2_list) == 2
    assert all(isinstance(e, DataSourceEntryV2) for e in v2_list)

    # Test lifecycle update on v2 increments revision
    updated_v2 = reg_reloaded.set_lifecycle("ds-v2", DataSourceLifecycleState.CANARY_PASSED_DISABLED, updated_by="op2")
    assert isinstance(updated_v2, DataSourceEntryV2)
    assert updated_v2.revision == 2
    assert updated_v2.updated_by == "op2"
    assert updated_v2.lifecycle_state == DataSourceLifecycleState.CANARY_PASSED_DISABLED


# ==============================================================================
# 4. SourceDesiredState and SourceObservedState Tests
# ==============================================================================

def test_source_desired_state_schema_and_invariants() -> None:
    schema = load_schema("docs/contracts/source_desired_state.schema.json")
    validator = Draft7Validator(schema)

    desired = SourceDesiredState(
        source_instance_id="ds-twse-market-primary",
        revision=3,
        desired_lifecycle=DesiredLifecycleState.ENABLED,
        definition_id="tw-twse-tpex-official-market",
        definition_deployment_sha="40de8fcb1c69fad0bf5e54d4c0bd6e508c9162e0",
        connector_config={
            "public": {"symbols": ["2330", "2317"]},
            "secret_ref_id": None,
        },
        schedule={
            "enabled": True,
            "cadence": "0 19 * * 1-5",
            "timezone": "Asia/Taipei",
            "jitter_seconds": 120,
        },
        limits={
            "max_records": 100,
            "max_bytes": 1048576,
            "timeout_seconds": 15,
        },
        allowed_hosts=["openapi.twse.com.tw"],
        last_command_receipt_id="srcmd-receipt-001",
        updated_at="2026-08-24T12:00:00Z",
        updated_by="operator-1",
    )

    data = desired.to_dict()
    errors = list(validator.iter_errors(data))
    assert not errors, f"DesiredState validation errors: {errors}"


def test_source_desired_state_rejects_inline_secrets() -> None:
    with pytest.raises(SourceManagementContractError, match="Raw secret material detected"):
        SourceDesiredState(
            source_instance_id="ds-leak",
            revision=1,
            desired_lifecycle=DesiredLifecycleState.CONFIGURED_DISABLED,
            definition_id="tw-finmind-dataset",
            definition_deployment_sha="40de8fcb1c69fad0bf5e54d4c0bd6e508c9162e0",
            connector_config={
                "public": {"token": "plain_api_key_string_abc123"},
            },
            schedule={"enabled": False, "cadence": "daily"},
            limits={"max_records": 100, "max_bytes": 1048576, "timeout_seconds": 15},
            allowed_hosts=["api.finmindtrade.com"],
        )


def test_source_observed_state_schema_and_invariants() -> None:
    schema = load_schema("docs/contracts/source_observed_state.schema.json")
    validator = Draft7Validator(schema)

    observed = SourceObservedState(
        source_instance_id="ds-twse-market-primary",
        desired_revision=3,
        observed_revision=9,
        reconciliation_status=ReconciliationStatus.CONVERGED,
        effective_lifecycle=EffectiveLifecycleState.ENABLED,
        definition={
            "definition_id": "tw-twse-tpex-official-market",
            "deployment_sha": "40de8fcb1c69fad0bf5e54d4c0bd6e508c9162e0",
            "state": "supported",
        },
        credential_state=CredentialState.NOT_REQUIRED,
        validation_state=ValidationState.PASSED,
        canary_state=CanaryState.PASSED,
        health_state=HealthState.FRESH,
        freshness={
            "last_success_at": "2026-08-24T11:55:00Z",
            "watermark": "2026-08-24T00:00:00Z",
            "age_seconds": 300,
            "sla_seconds": 86400,
        },
        last_run={
            "ingest_run_id": "ingest-run-001",
            "row_count": 100,
            "rejected_count": 0,
            "evidence_bundle_id": "evbundle-001",
            "search_snapshot_id": "search-index-001",
            "started_at": "2026-08-24T11:54:00Z",
            "completed_at": "2026-08-24T11:55:00Z",
        },
        dlq_unresolved_count=0,
        quota={},
        usage={"total_runs": 42, "total_records": 4200, "total_bytes": 44000000},
        dependent_refs=[],
        reasons=[],
        observed_at="2026-08-24T12:00:00Z",
    )

    data = observed.to_dict()
    errors = list(validator.iter_errors(data))
    assert not errors, f"ObservedState validation errors: {errors}"


# ==============================================================================
# 5. ManagementDataSourceDTO and Server-Side Allowed Actions Tests
# ==============================================================================

def test_management_data_source_dto_composition_and_schema() -> None:
    schema = load_schema("docs/contracts/bff/management_data_source.v2.schema.json")
    validator = Draft7Validator(schema)

    defn = get_connector_definition("tw-twse-tpex-official-market")
    assert defn is not None

    instance = DataSourceEntryV2(
        data_source_id="ds-twse-market-primary",
        definition_id="tw-twse-tpex-official-market",
        connector_id="twse-market-primary",
        provider="TWSE",
        source_class=DataSourceClass.MARKET_DAILY,
        datasets=[{"dataset_id": "tw_price_daily", "dataset_class": "market_daily"}],
        markets=["TW"],
        license_scope="official_reference",
        allowed_use=["research_data"],
        retention_policy_ref="source-retention://twse",
        deletion_policy_ref="source-deletion://twse",
        lifecycle_state=DataSourceLifecycleState.ENABLED,
        created_by="op1",
        updated_by="op1",
    )

    desired = SourceDesiredState(
        source_instance_id="ds-twse-market-primary",
        revision=1,
        desired_lifecycle=DesiredLifecycleState.ENABLED,
        definition_id="tw-twse-tpex-official-market",
        definition_deployment_sha=defn.deployment_sha,
        connector_config={"public": {}},
        schedule={"enabled": True, "cadence": "daily"},
        limits={"max_records": 100, "max_bytes": 1048576, "timeout_seconds": 15},
        allowed_hosts=["openapi.twse.com.tw"],
    )

    observed = SourceObservedState(
        source_instance_id="ds-twse-market-primary",
        desired_revision=1,
        observed_revision=1,
        reconciliation_status=ReconciliationStatus.CONVERGED,
        effective_lifecycle=EffectiveLifecycleState.ENABLED,
        definition={"definition_id": defn.definition_id, "deployment_sha": defn.deployment_sha, "state": "supported"},
        credential_state=CredentialState.NOT_REQUIRED,
        validation_state=ValidationState.PASSED,
        canary_state=CanaryState.PASSED,
        health_state=HealthState.FRESH,
    )

    actions = calculate_source_allowed_actions(defn, instance.to_dict(), desired.to_dict(), observed.to_dict())

    dto = ManagementDataSourceDTO(
        source_instance_id="ds-twse-market-primary",
        definition=defn.to_dict(),
        instance=instance.to_dict(),
        desired=desired.to_dict(),
        observed=observed.to_dict(),
        allowed_actions=actions,
        lineage_summary={"evidence_count": 10, "seed_count": 2, "active_consumer_count": 1, "search_indexed": True},
    )

    data = dto.to_dict()
    errors = list(validator.iter_errors(data))
    assert not errors, f"ManagementDataSourceDTO failed schema validation: {errors}"


def test_allowed_actions_lifecycle_state_machine_matrix() -> None:
    defn = get_connector_definition("tw-finmind-dataset")
    assert defn is not None

    # 1. Configured disabled, validation pending
    actions_initial = calculate_source_allowed_actions(
        defn,
        {"lifecycle_state": "configured_disabled"},
        {"desired_lifecycle": "configured_disabled"},
        {"validation_state": "pending", "canary_state": "not_run", "effective_lifecycle": "configured_disabled"},
    )
    assert actions_initial["canValidate"] is True
    assert actions_initial["canCanary"] is False
    assert actions_initial["canEnable"] is False
    assert "validation_required" in actions_initial["blockedReasons"]

    # 2. Validation passed, canary pending
    actions_validated = calculate_source_allowed_actions(
        defn,
        {"lifecycle_state": "validated_disabled"},
        {"desired_lifecycle": "validated_disabled"},
        {"validation_state": "passed", "canary_state": "not_run", "credential_state": "ready", "effective_lifecycle": "validated_disabled"},
    )
    assert actions_validated["canValidate"] is True
    assert actions_validated["canCanary"] is True
    assert actions_validated["canEnable"] is False
    assert "canary_required" in actions_validated["blockedReasons"]

    # 3. Canary passed -> ready to enable
    actions_canary_passed = calculate_source_allowed_actions(
        defn,
        {"lifecycle_state": "canary_passed_disabled"},
        {"desired_lifecycle": "canary_passed_disabled"},
        {"validation_state": "passed", "canary_state": "passed", "credential_state": "ready", "effective_lifecycle": "canary_passed_disabled"},
    )
    assert actions_canary_passed["canEnable"] is True
    assert actions_canary_passed["canDisable"] is False

    # 4. Enabled source
    actions_enabled = calculate_source_allowed_actions(
        defn,
        {"lifecycle_state": "enabled"},
        {"desired_lifecycle": "enabled"},
        {"validation_state": "passed", "canary_state": "passed", "effective_lifecycle": "enabled"},
    )
    assert actions_enabled["canEnable"] is False
    assert actions_enabled["canDisable"] is True
    assert actions_enabled["canDegrade"] is True
    assert "already_enabled" in actions_enabled["blockedReasons"]

    # 5. Disabled with active dependents -> retirement blocked
    actions_disabled_with_deps = calculate_source_allowed_actions(
        defn,
        {"lifecycle_state": "disabled"},
        {"desired_lifecycle": "disabled"},
        {"effective_lifecycle": "disabled", "dependent_refs": ["strategy-alpha-1"]},
    )
    assert actions_disabled_with_deps["canRetire"] is False
    assert "active_dependents_block_retirement" in actions_disabled_with_deps["blockedReasons"]

    # 6. Disabled with no dependents -> can retire
    actions_retirable = calculate_source_allowed_actions(
        defn,
        {"lifecycle_state": "disabled"},
        {"desired_lifecycle": "disabled"},
        {"effective_lifecycle": "disabled", "dependent_refs": []},
    )
    assert actions_retirable["canRetire"] is True

    # 7. Retired source -> terminal state
    actions_retired = calculate_source_allowed_actions(
        defn,
        {"lifecycle_state": "retired"},
        {"desired_lifecycle": "retired"},
        {"effective_lifecycle": "retired"},
    )
    assert actions_retired["canValidate"] is False
    assert actions_retired["canCanary"] is False
    assert actions_retired["canEnable"] is False
    assert actions_retired["canDisable"] is False
    assert actions_retired["canResume"] is False
    assert actions_retired["canChangeSchedule"] is False
    assert actions_retired["canReplace"] is False
    assert actions_retired["canRetire"] is False
    assert "source_retired" in actions_retired["blockedReasons"]

    # 8. Definition disabled by build -> cannot create/validate/canary/enable
    stooq_defn = get_connector_definition("us-stooq-daily-ohlcv")
    assert stooq_defn is not None
    actions_build_disabled = calculate_source_allowed_actions(
        stooq_defn,
        {"lifecycle_state": "configured_disabled"},
        {"desired_lifecycle": "configured_disabled"},
        {"validation_state": "pending", "canary_state": "not_run"},
    )
    assert actions_build_disabled["canValidate"] is False
    assert actions_build_disabled["canCanary"] is False
    assert actions_build_disabled["canEnable"] is False
    assert "definition_disabled_by_build" in actions_build_disabled["blockedReasons"]
