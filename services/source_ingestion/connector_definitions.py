"""ConnectorDefinition projection and capability catalog (SD-SRCM-01).

Build-owned projection answering \"what can this deployed version do?\".
Composes without copying mutable state:
- ALLOWED_PROVIDER_ADAPTERS and PROVIDER_ADAPTER_ALIASES
- Financial catalog config templates and adapter metadata
- Deployment identity (git SHA / build hash)

Every definition is stable-sorted, validated, and fingerprinted with a SHA-256 hash.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import os
from typing import Any, Mapping, Sequence

from .provider_adapters import (
    ALLOWED_PROVIDER_ADAPTERS,
    PROVIDER_ADAPTER_ALIASES,
    is_provider_adapter_allowed,
)
from .source_management_models import (
    DefinitionState,
    DesiredLifecycleState,
    EffectiveLifecycleState,
    SourceManagementContractError,
    assert_no_raw_secrets,
)

CONNECTOR_DEFINITION_SCHEMA_VERSION = "connector_definition.v1"
DEFAULT_DEPLOYMENT_SHA = os.environ.get("PANTHEON_DEPLOYMENT_SHA", "40de8fcb1c69fad0bf5e54d4c0bd6e508c9162e0")


def _require(value: Any, name: str) -> str:
    s = str(value or "").strip()
    if not s:
        raise SourceManagementContractError(f"{name} is required")
    return s


def compute_definition_fingerprint(payload: Mapping[str, Any]) -> str:
    """Compute deterministic SHA-256 fingerprint for a ConnectorDefinition payload."""
    # Exclude fingerprint itself during calculation
    canonical = {k: v for k, v in payload.items() if k != "fingerprint"}
    serialized = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ConnectorDefinition:
    """Code-owned build projection of deployed adapter capability and metadata."""

    definition_id: str
    adapter_token: str
    adapter_version: str
    provider: str
    source_kinds: Sequence[str]
    source_types: Sequence[str]
    source_classes: Sequence[str]
    datasets: Sequence[str]
    auth_modes: Sequence[str]
    fetch_modes: Sequence[str]
    config_schema: Mapping[str, Any]
    secret_fields: Sequence[str]
    required_pit_fields: Sequence[str]
    default_limits: Mapping[str, Any]
    allowed_host_patterns: Sequence[str]
    definition_state: DefinitionState | str = DefinitionState.SUPPORTED
    disabled_reason: str | None = None
    deployment_sha: str = DEFAULT_DEPLOYMENT_SHA
    test_manifest_ref: str | None = None
    cursor_modes: Sequence[str] = field(default_factory=tuple)
    output_schema_versions: Sequence[str] = field(default_factory=tuple)
    rate_limit_capability: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    fingerprint: str = field(default="", init=False)

    schema_version: str = field(default=CONNECTOR_DEFINITION_SCHEMA_VERSION, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", CONNECTOR_DEFINITION_SCHEMA_VERSION)
        object.__setattr__(self, "definition_id", _require(self.definition_id, "definition_id"))
        object.__setattr__(self, "adapter_token", _require(self.adapter_token, "adapter_token"))
        object.__setattr__(self, "adapter_version", _require(self.adapter_version, "adapter_version"))
        object.__setattr__(self, "provider", _require(self.provider, "provider"))
        object.__setattr__(self, "deployment_sha", _require(self.deployment_sha, "deployment_sha"))

        try:
            ds_val = self.definition_state.value if isinstance(self.definition_state, Enum) else str(self.definition_state)
            ds = DefinitionState(ds_val)
        except ValueError:
            allowed = ", ".join(s.value for s in DefinitionState)
            raise SourceManagementContractError(f"definition_state must be one of: {allowed}")
        object.__setattr__(self, "definition_state", ds)

        # Enforce allowlisted adapter
        if not is_provider_adapter_allowed(self.adapter_token) and not self.adapter_token.startswith("StrategySeed"):
            raise SourceManagementContractError(f"adapter_token \"{self.adapter_token}\" is not in deployed allowlist")

        # Sequences normalization
        object.__setattr__(self, "source_kinds", tuple(str(k).strip() for k in self.source_kinds if str(k).strip()))
        if not self.source_kinds:
            raise SourceManagementContractError("source_kinds must not be empty")

        object.__setattr__(self, "source_types", tuple(str(t).strip() for t in self.source_types if str(t).strip()))
        if not self.source_types:
            raise SourceManagementContractError("source_types must not be empty")

        object.__setattr__(self, "source_classes", tuple(str(c).strip() for c in self.source_classes if str(c).strip()))
        if not self.source_classes:
            raise SourceManagementContractError("source_classes must not be empty")

        object.__setattr__(self, "datasets", tuple(str(d).strip() for d in self.datasets if str(d).strip()))
        if not self.datasets:
            raise SourceManagementContractError("datasets must not be empty")

        object.__setattr__(self, "auth_modes", tuple(str(m).strip() for m in self.auth_modes if str(m).strip()))
        object.__setattr__(self, "fetch_modes", tuple(str(f).strip() for f in self.fetch_modes if str(f).strip()))
        object.__setattr__(self, "secret_fields", tuple(str(s).strip() for s in self.secret_fields if str(s).strip()))
        object.__setattr__(self, "required_pit_fields", tuple(str(p).strip() for p in self.required_pit_fields if str(p).strip()))
        object.__setattr__(self, "allowed_host_patterns", tuple(str(h).strip() for h in self.allowed_host_patterns if str(h).strip()))
        object.__setattr__(self, "cursor_modes", tuple(str(c).strip() for c in self.cursor_modes if str(c).strip()))
        object.__setattr__(self, "output_schema_versions", tuple(str(v).strip() for v in self.output_schema_versions if str(v).strip()))

        # Config schema & limits checks
        cfg_schema = dict(self.config_schema)
        assert_no_raw_secrets(cfg_schema)
        object.__setattr__(self, "config_schema", cfg_schema)

        limits = dict(self.default_limits)
        for req_limit in ("max_records", "max_bytes", "timeout_seconds"):
            if req_limit not in limits or int(limits[req_limit]) < 1:
                raise SourceManagementContractError(f"default_limits.{req_limit} must be >= 1")
        object.__setattr__(self, "default_limits", limits)

        object.__setattr__(self, "metadata", dict(self.metadata))

        # Compute fingerprint
        raw_dict = self._to_raw_dict_without_fp()
        fp = compute_definition_fingerprint(raw_dict)
        object.__setattr__(self, "fingerprint", fp)

    def _to_raw_dict_without_fp(self) -> dict[str, Any]:
        res: dict[str, Any] = {
            "schema_version": self.schema_version,
            "definition_id": self.definition_id,
            "adapter_token": self.adapter_token,
            "adapter_version": self.adapter_version,
            "provider": self.provider,
            "source_kinds": list(self.source_kinds),
            "source_types": list(self.source_types),
            "source_classes": list(self.source_classes),
            "datasets": list(self.datasets),
            "auth_modes": list(self.auth_modes),
            "fetch_modes": list(self.fetch_modes),
            "config_schema": dict(self.config_schema),
            "secret_fields": list(self.secret_fields),
            "required_pit_fields": list(self.required_pit_fields),
            "default_limits": dict(self.default_limits),
            "allowed_host_patterns": list(self.allowed_host_patterns),
            "definition_state": self.definition_state.value if isinstance(self.definition_state, DefinitionState) else str(self.definition_state),
            "disabled_reason": self.disabled_reason,
            "deployment_sha": self.deployment_sha,
            "test_manifest_ref": self.test_manifest_ref,
        }
        if self.cursor_modes:
            res["cursor_modes"] = list(self.cursor_modes)
        if self.output_schema_versions:
            res["output_schema_versions"] = list(self.output_schema_versions)
        if self.rate_limit_capability is not None:
            res["rate_limit_capability"] = dict(self.rate_limit_capability)
        if self.metadata:
            res["metadata"] = dict(self.metadata)
        return res

    def to_dict(self) -> dict[str, Any]:
        d = self._to_raw_dict_without_fp()
        d["fingerprint"] = self.fingerprint
        return d

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ConnectorDefinition":
        return cls(
            definition_id=str(data["definition_id"]),
            adapter_token=str(data["adapter_token"]),
            adapter_version=str(data.get("adapter_version") or "1.0.0"),
            provider=str(data["provider"]),
            source_kinds=list(data.get("source_kinds") or ["data_source"]),
            source_types=list(data.get("source_types") or ["market"]),
            source_classes=list(data.get("source_classes") or ["market_daily"]),
            datasets=list(data.get("datasets") or []),
            auth_modes=list(data.get("auth_modes") or ["none"]),
            fetch_modes=list(data.get("fetch_modes") or ["provider_owned_adapter"]),
            config_schema=dict(data.get("config_schema") or {}),
            secret_fields=list(data.get("secret_fields") or []),
            required_pit_fields=list(data.get("required_pit_fields") or ["event_time", "available_time", "ingest_time"]),
            default_limits=dict(data.get("default_limits") or {"max_records": 100, "max_bytes": 1048576, "timeout_seconds": 15}),
            allowed_host_patterns=list(data.get("allowed_host_patterns") or []),
            definition_state=str(data.get("definition_state", DefinitionState.SUPPORTED.value)),
            disabled_reason=data.get("disabled_reason"),
            deployment_sha=str(data.get("deployment_sha") or DEFAULT_DEPLOYMENT_SHA),
            test_manifest_ref=data.get("test_manifest_ref"),
            cursor_modes=list(data.get("cursor_modes") or []),
            output_schema_versions=list(data.get("output_schema_versions") or []),
            rate_limit_capability=dict(data["rate_limit_capability"]) if data.get("rate_limit_capability") else None,
            metadata=dict(data.get("metadata") or {}),
        )


def calculate_source_allowed_actions(
    definition: ConnectorDefinition | Mapping[str, Any],
    instance: Mapping[str, Any],
    desired: Mapping[str, Any],
    observed: Mapping[str, Any],
) -> dict[str, Any]:
    """Calculate server-side allowed management actions for a data source instance (SD §3.6)."""
    def_state = definition.definition_state.value if isinstance(definition, ConnectorDefinition) else str(definition.get("definition_state", "supported"))
    lifecycle = str(desired.get("desired_lifecycle") or instance.get("lifecycle_state") or "configured_disabled")
    effective_lifecycle = str(observed.get("effective_lifecycle") or lifecycle)
    validation_state = str(observed.get("validation_state") or "pending")
    canary_state = str(observed.get("canary_state") or "not_run")
    credential_state = str(observed.get("credential_state") or "ready")
    dependent_refs = list(observed.get("dependent_refs") or [])

    blocked_reasons: list[str] = []

    is_retired = lifecycle == "retired" or effective_lifecycle == "retired"
    is_enabled = lifecycle == "enabled" or effective_lifecycle == "enabled"
    is_disabled_by_build = def_state == "disabled_by_build"

    # canValidate: source is disabled / candidate / not enabled; definition is supported
    can_validate = not is_retired and not is_disabled_by_build and lifecycle in (
        "configured_disabled", "validated_disabled", "canary_passed_disabled", "disabled", "degraded_disabled", "candidate"
    )

    # canCanary: validation passed, credential ready, definition supported, not retired
    can_canary = (
        not is_retired
        and not is_disabled_by_build
        and validation_state == "passed"
        and credential_state in ("not_required", "ready")
    )

    # canEnable: definition supported, canary passed, validation passed, not retired, not already enabled
    can_enable = (
        not is_retired
        and not is_disabled_by_build
        and not is_enabled
        and (canary_state == "passed" or lifecycle == "canary_passed_disabled")
        and validation_state == "passed"
    )

    # canDisable: currently enabled or degraded, not retired
    can_disable = not is_retired and is_enabled

    # canDegrade: currently enabled, not retired
    can_degrade = not is_retired and is_enabled and effective_lifecycle == "enabled"

    # canResume: disabled or degraded_disabled, not retired, definition supported, validation/canary valid
    can_resume = (
        not is_retired
        and not is_disabled_by_build
        and not is_enabled
        and lifecycle in ("disabled", "degraded_disabled")
        and canary_state == "passed"
    )

    # canChangeSchedule: not retired
    can_change_schedule = not is_retired

    # canReplace: not retired
    can_replace = not is_retired

    # canRetire: disabled or degraded_disabled or configured_disabled, no active blocking dependents
    can_retire = (
        not is_retired
        and not is_enabled
        and len(dependent_refs) == 0
    )

    if is_retired:
        blocked_reasons.append("source_retired")
    if is_disabled_by_build:
        blocked_reasons.append("definition_disabled_by_build")
    if not is_retired and not can_enable and not is_enabled:
        if validation_state != "passed":
            blocked_reasons.append("validation_required")
        if canary_state != "passed" and lifecycle != "canary_passed_disabled":
            blocked_reasons.append("canary_required")
        if credential_state not in ("not_required", "ready"):
            blocked_reasons.append("credential_unavailable")
    if is_enabled:
        blocked_reasons.append("already_enabled")
    if len(dependent_refs) > 0 and not is_retired:
        blocked_reasons.append("active_dependents_block_retirement")

    return {
        "canValidate": can_validate,
        "canCanary": can_canary,
        "canEnable": can_enable,
        "canDisable": can_disable,
        "canDegrade": can_degrade,
        "canResume": can_resume,
        "canChangeSchedule": can_change_schedule,
        "canReplace": can_replace,
        "canRetire": can_retire,
        "blockedReasons": blocked_reasons,
    }


# ==============================================================================
# Canonical Deployed Connector Definitions Inventory (SD §3.1 & SA §7)
# ==============================================================================

_CANONICAL_DEFINITIONS: tuple[ConnectorDefinition, ...] = (
    ConnectorDefinition(
        definition_id="tw-twse-tpex-official-market",
        adapter_token="TaiwanOfficialMarketDatasetAdapter.records_from_payload",
        adapter_version="1.0.0",
        provider="TWSE/TPEx",
        source_kinds=("data_source",),
        source_types=("market",),
        source_classes=("market_daily",),
        datasets=("tw_price_daily",),
        auth_modes=("none",),
        fetch_modes=("provider_owned_adapter",),
        cursor_modes=("time_watermark",),
        config_schema={
            "type": "object",
            "properties": {
                "symbols": {"type": "array", "items": {"type": "string"}},
                "market": {"type": "string"},
                "venues": {"type": "array", "items": {"type": "string"}},
                "max_records": {"type": "integer", "minimum": 1},
            },
        },
        secret_fields=(),
        required_pit_fields=("event_time", "available_time", "ingest_time"),
        default_limits={"max_records": 100, "max_bytes": 10485760, "timeout_seconds": 15, "max_rate_per_second": 5.0},
        allowed_host_patterns=("openapi.twse.com.tw", "www.tpex.org.tw"),
        definition_state=DefinitionState.SUPPORTED,
        test_manifest_ref="evidence://connector-definition/tw-twse-tpex-official-market",
    ),
    ConnectorDefinition(
        definition_id="tw-finmind-datasets",
        adapter_token="FinMindTaiwanDatasetAdapter.records_from_data_payload",
        adapter_version="1.0.0",
        provider="FinMind",
        source_kinds=("data_source",),
        source_types=("market",),
        source_classes=("market_daily", "taiwan_chip", "financial_fundamental"),
        datasets=(
            "tw_price_daily",
            "tw_institutional_investors",
            "tw_margin_purchase_short_sale",
            "tw_shareholding",
            "tw_financial_statement",
        ),
        auth_modes=("api_key",),
        fetch_modes=("provider_owned_adapter",),
        cursor_modes=("time_watermark",),
        config_schema={
            "type": "object",
            "properties": {
                "secret_ref_id": {"type": "string"},
                "dataset": {"type": "string"},
                "max_records": {"type": "integer", "minimum": 1},
            },
        },
        secret_fields=("secret_ref_id",),
        required_pit_fields=("event_time", "available_time", "ingest_time"),
        default_limits={"max_records": 500, "max_bytes": 4194304, "timeout_seconds": 20, "max_rate_per_second": 10.0},
        allowed_host_patterns=("api.finmindtrade.com",),
        definition_state=DefinitionState.SUPPORTED,
        test_manifest_ref="evidence://connector-definition/tw-finmind-datasets",
    ),
    ConnectorDefinition(
        definition_id="tw-finmind-broker-daily-report",
        adapter_token="FinMindTaiwanBrokerDailyReportAdapter.records_from_daily_report_payload",
        adapter_version="1.0.0",
        provider="FinMind",
        source_kinds=("data_source",),
        source_types=("market",),
        source_classes=("taiwan_chip",),
        datasets=("tw_broker_daily_report", "tw_broker_top"),
        auth_modes=("api_key",),
        fetch_modes=("provider_owned_adapter",),
        cursor_modes=("time_watermark",),
        config_schema={
            "type": "object",
            "properties": {
                "secret_ref_id": {"type": "string"},
                "max_rank": {"type": "integer", "minimum": 1},
                "entitlement_tier": {"type": "string"},
            },
        },
        secret_fields=("secret_ref_id",),
        required_pit_fields=("event_time", "available_time", "ingest_time"),
        default_limits={"max_records": 100, "max_bytes": 2097152, "timeout_seconds": 20, "max_rate_per_second": 5.0},
        allowed_host_patterns=("api.finmindtrade.com",),
        definition_state=DefinitionState.SUPPORTED,
        test_manifest_ref="evidence://connector-definition/tw-finmind-broker-daily-report",
    ),
    ConnectorDefinition(
        definition_id="tw-finmind-broker-bulk-parquet",
        adapter_token="FinMindTaiwanBrokerBulkBackfillAdapter.records_from_storage_objects_payload",
        adapter_version="1.0.0",
        provider="FinMind",
        source_kinds=("data_source",),
        source_types=("market",),
        source_classes=("taiwan_chip", "vendor_backfill"),
        datasets=("tw_broker_bulk_backfill",),
        auth_modes=("api_key",),
        fetch_modes=("provider_owned_adapter",),
        cursor_modes=("id_watermark",),
        config_schema={
            "type": "object",
            "properties": {
                "secret_ref_id": {"type": "string"},
            },
        },
        secret_fields=("secret_ref_id",),
        required_pit_fields=("event_time", "available_time", "ingest_time"),
        default_limits={"max_records": 1000, "max_bytes": 10485760, "timeout_seconds": 30, "max_rate_per_second": 2.0},
        allowed_host_patterns=("api.finmindtrade.com", "storage.googleapis.com"),
        definition_state=DefinitionState.SUPPORTED,
        test_manifest_ref="evidence://connector-definition/tw-finmind-broker-bulk-parquet",
    ),
    ConnectorDefinition(
        definition_id="tw-yahoo-broker-top15",
        adapter_token="YahooTaiwanBrokerTopAdapter.records_from_html",
        adapter_version="1.0.0",
        provider="Yahoo Taiwan Stock",
        source_kinds=("data_source",),
        source_types=("market",),
        source_classes=("taiwan_chip",),
        datasets=("tw_broker_top",),
        auth_modes=("none",),
        fetch_modes=("provider_owned_adapter",),
        cursor_modes=("none",),
        config_schema={
            "type": "object",
            "properties": {
                "max_rank": {"type": "integer", "minimum": 1},
            },
        },
        secret_fields=(),
        required_pit_fields=("event_time", "available_time", "ingest_time"),
        default_limits={"max_records": 50, "max_bytes": 1048576, "timeout_seconds": 15, "max_rate_per_second": 2.0},
        allowed_host_patterns=("tw.stock.yahoo.com",),
        definition_state=DefinitionState.DISABLED_BY_BUILD,
        test_manifest_ref="evidence://connector-definition/tw-yahoo-broker-top15",
    ),
    ConnectorDefinition(
        definition_id="tw-yahoo-stock-rss",
        adapter_token="YahooTaiwanRssAdapter.records_from_rss",
        adapter_version="1.0.0",
        provider="Yahoo Taiwan Stock",
        source_kinds=("data_source",),
        source_types=("news",),
        source_classes=("news",),
        datasets=("tw_news_metadata",),
        auth_modes=("none",),
        fetch_modes=("provider_owned_adapter",),
        cursor_modes=("time_watermark",),
        config_schema={
            "type": "object",
            "properties": {
                "feed_url": {"type": "string"},
                "max_records": {"type": "integer", "minimum": 1},
            },
        },
        secret_fields=(),
        required_pit_fields=("event_time", "available_time", "ingest_time"),
        default_limits={"max_records": 50, "max_bytes": 1048576, "timeout_seconds": 15, "max_rate_per_second": 1.0},
        allowed_host_patterns=("tw.stock.yahoo.com", "finance.yahoo.com"),
        definition_state=DefinitionState.DISABLED_BY_BUILD,
        test_manifest_ref="evidence://connector-definition/tw-yahoo-stock-rss",
    ),
    ConnectorDefinition(
        definition_id="tw-anue-news-rss",
        adapter_token="AnueTaiwanRssAdapter.records_from_rss",
        adapter_version="1.0.0",
        provider="Anue Cnyes",
        source_kinds=("data_source",),
        source_types=("news",),
        source_classes=("news",),
        datasets=("tw_news_metadata",),
        auth_modes=("none",),
        fetch_modes=("provider_owned_adapter",),
        cursor_modes=("time_watermark",),
        config_schema={
            "type": "object",
            "properties": {
                "feed_url": {"type": "string"},
                "max_records": {"type": "integer", "minimum": 1},
            },
        },
        secret_fields=(),
        required_pit_fields=("event_time", "available_time", "ingest_time"),
        default_limits={"max_records": 50, "max_bytes": 1048576, "timeout_seconds": 15, "max_rate_per_second": 1.0},
        allowed_host_patterns=("news.cnyes.com",),
        definition_state=DefinitionState.SUPPORTED,
        test_manifest_ref="evidence://connector-definition/tw-anue-news-rss",
    ),
    ConnectorDefinition(
        definition_id="tw-mops-official-disclosures",
        adapter_token="MopsSourceIngestAdapter.records_from_payload",
        adapter_version="1.0.0",
        provider="MOPS",
        source_kinds=("data_source",),
        source_types=("filing",),
        source_classes=("official_reference", "corporate_action", "financial_fundamental"),
        datasets=("tw_material_event", "tw_monthly_revenue", "tw_financial_statement", "tw_company_master", "tw_corporate_action"),
        auth_modes=("none",),
        fetch_modes=("provider_owned_adapter",),
        cursor_modes=("time_watermark",),
        config_schema={
            "type": "object",
            "properties": {
                "route_id": {"type": "string"},
                "max_records": {"type": "integer", "minimum": 1},
            },
        },
        secret_fields=(),
        required_pit_fields=("event_time", "available_time", "ingest_time"),
        default_limits={"max_records": 100, "max_bytes": 2097152, "timeout_seconds": 15, "max_rate_per_second": 3.0},
        allowed_host_patterns=("mops.twse.com.tw",),
        definition_state=DefinitionState.SUPPORTED,
        test_manifest_ref="evidence://connector-definition/tw-mops-official-disclosures",
    ),
    ConnectorDefinition(
        definition_id="tw-tej-research-datasets",
        adapter_token="TejSourceIngestAdapter.records_from_rows",
        adapter_version="1.0.0",
        provider="TEJ",
        source_kinds=("data_source",),
        source_types=("market",),
        source_classes=("market_daily", "financial_fundamental", "taiwan_chip", "vendor_backfill"),
        datasets=("tw_price_daily", "tw_financial_fundamentals", "tw_broker_top"),
        auth_modes=("api_key",),
        fetch_modes=("provider_owned_adapter",),
        cursor_modes=("time_watermark",),
        config_schema={
            "type": "object",
            "properties": {
                "secret_ref_id": {"type": "string"},
                "max_records": {"type": "integer", "minimum": 1},
            },
        },
        secret_fields=("secret_ref_id",),
        required_pit_fields=("event_time", "available_time", "ingest_time"),
        default_limits={"max_records": 500, "max_bytes": 4194304, "timeout_seconds": 25, "max_rate_per_second": 5.0},
        allowed_host_patterns=("api.tej.com.tw",),
        definition_state=DefinitionState.SUPPORTED,
        test_manifest_ref="evidence://connector-definition/tw-tej-research-datasets",
    ),
    ConnectorDefinition(
        definition_id="us-sec-edgar-filings",
        adapter_token="SecEdgarFilingAdapter.records_from_payload",
        adapter_version="1.0.0",
        provider="SEC EDGAR",
        source_kinds=("data_source",),
        source_types=("filing",),
        source_classes=("filing_event", "financial_fundamental"),
        datasets=("us_sec_company_facts", "us_sec_filing_event"),
        auth_modes=("none",),
        fetch_modes=("provider_owned_adapter",),
        cursor_modes=("time_watermark",),
        config_schema={
            "type": "object",
            "properties": {
                "user_agent": {"type": "string"},
                "user_agent_env": {"type": "string"},
                "max_records": {"type": "integer", "minimum": 1},
            },
        },
        secret_fields=(),
        required_pit_fields=("event_time", "available_time", "ingest_time"),
        default_limits={"max_records": 100, "max_bytes": 5242880, "timeout_seconds": 20, "max_rate_per_second": 10.0},
        allowed_host_patterns=("data.sec.gov", "www.sec.gov"),
        definition_state=DefinitionState.SUPPORTED,
        test_manifest_ref="evidence://connector-definition/us-sec-edgar-filings",
    ),
    ConnectorDefinition(
        definition_id="us-fred-macro",
        adapter_token="FredMacroSeriesAdapter.records_from_observations_payload",
        adapter_version="1.0.0",
        provider="FRED",
        source_kinds=("data_source",),
        source_types=("macro",),
        source_classes=("macro",),
        datasets=("us_macro_series",),
        auth_modes=("api_key",),
        fetch_modes=("provider_owned_adapter",),
        cursor_modes=("time_watermark",),
        config_schema={
            "type": "object",
            "properties": {
                "secret_ref_id": {"type": "string"},
                "max_records": {"type": "integer", "minimum": 1},
            },
        },
        secret_fields=("secret_ref_id",),
        required_pit_fields=("event_time", "available_time", "ingest_time"),
        default_limits={"max_records": 200, "max_bytes": 2097152, "timeout_seconds": 15, "max_rate_per_second": 5.0},
        allowed_host_patterns=("api.stlouisfed.org",),
        definition_state=DefinitionState.SUPPORTED,
        test_manifest_ref="evidence://connector-definition/us-fred-macro",
    ),
    ConnectorDefinition(
        definition_id="us-finra-short-sale",
        adapter_token="FinraShortSaleAdapter.records_from_short_volume_text",
        adapter_version="1.0.0",
        provider="FINRA",
        source_kinds=("data_source",),
        source_types=("market",),
        source_classes=("short_interest",),
        datasets=("us_short_sale_volume",),
        auth_modes=("none",),
        fetch_modes=("provider_owned_adapter",),
        cursor_modes=("time_watermark",),
        config_schema={
            "type": "object",
            "properties": {
                "max_records": {"type": "integer", "minimum": 1},
                "expected_publication_delay_hours": {"type": "integer"},
            },
        },
        secret_fields=(),
        required_pit_fields=("event_time", "available_time", "ingest_time"),
        default_limits={"max_records": 500, "max_bytes": 4194304, "timeout_seconds": 20, "max_rate_per_second": 2.0},
        allowed_host_patterns=("cdn.finra.org", "regsho.finra.org"),
        definition_state=DefinitionState.SUPPORTED,
        test_manifest_ref="evidence://connector-definition/us-finra-short-sale",
    ),
    ConnectorDefinition(
        definition_id="us-stooq-daily-ohlcv",
        adapter_token="StooqDailyOhlcvAdapter.records_from_csv",
        adapter_version="1.0.0",
        provider="Stooq",
        source_kinds=("data_source",),
        source_types=("market",),
        source_classes=("market_daily",),
        datasets=("us_price_daily",),
        auth_modes=("none",),
        fetch_modes=("provider_owned_adapter",),
        cursor_modes=("none",),
        config_schema={
            "type": "object",
            "properties": {
                "max_records": {"type": "integer", "minimum": 1},
            },
        },
        secret_fields=(),
        required_pit_fields=("event_time", "available_time", "ingest_time"),
        default_limits={"max_records": 100, "max_bytes": 1048576, "timeout_seconds": 15, "max_rate_per_second": 1.0},
        allowed_host_patterns=("stooq.com",),
        definition_state=DefinitionState.DISABLED_BY_BUILD,
        disabled_reason="Stooq automated scraping disabled by upstream rate limit policy and structural maintenance",
        test_manifest_ref="evidence://connector-definition/us-stooq-daily-ohlcv",
    ),
    ConnectorDefinition(
        definition_id="crypto-coingecko-spot",
        adapter_token="CoinGeckoSpotMarketAdapter.records_from_payload",
        adapter_version="1.0.0",
        provider="CoinGecko",
        source_kinds=("data_source",),
        source_types=("market",),
        source_classes=("market_daily", "intraday_quote"),
        datasets=("crypto_spot_ohlcv",),
        auth_modes=("none",),
        fetch_modes=("provider_owned_adapter",),
        cursor_modes=("time_watermark",),
        config_schema={
            "type": "object",
            "properties": {
                "api_base_url": {"type": "string"},
                "vs_currency": {"type": "string"},
                "ohlc_days": {"type": "integer"},
                "max_records": {"type": "integer", "minimum": 1},
            },
        },
        secret_fields=(),
        required_pit_fields=("event_time", "available_time", "ingest_time"),
        default_limits={"max_records": 100, "max_bytes": 1048576, "timeout_seconds": 15, "max_rate_per_second": 5.0},
        allowed_host_patterns=("api.coingecko.com",),
        definition_state=DefinitionState.SUPPORTED,
        test_manifest_ref="evidence://connector-definition/crypto-coingecko-spot",
    ),
    ConnectorDefinition(
        definition_id="us-polygon-daily-ohlcv",
        adapter_token="PolygonUsEquityDailyAdapter.records_from_aggs_payload",
        adapter_version="1.0.0",
        provider="Polygon",
        source_kinds=("data_source",),
        source_types=("market",),
        source_classes=("market_daily",),
        datasets=("us_price_daily",),
        auth_modes=("api_key",),
        fetch_modes=("provider_owned_adapter",),
        cursor_modes=("time_watermark",),
        config_schema={
            "type": "object",
            "properties": {
                "secret_ref_id": {"type": "string"},
                "max_records": {"type": "integer", "minimum": 1},
            },
        },
        secret_fields=("secret_ref_id",),
        required_pit_fields=("event_time", "available_time", "ingest_time"),
        default_limits={"max_records": 200, "max_bytes": 2097152, "timeout_seconds": 15, "max_rate_per_second": 10.0},
        allowed_host_patterns=("api.polygon.io",),
        definition_state=DefinitionState.SUPPORTED,
        test_manifest_ref="evidence://connector-definition/us-polygon-daily-ohlcv",
    ),
    ConnectorDefinition(
        definition_id="us-alpha-vantage-daily-ohlcv",
        adapter_token="AlphaVantageUsEquityDailyAdapter.records_from_time_series_payload",
        adapter_version="1.0.0",
        provider="Alpha Vantage",
        source_kinds=("data_source",),
        source_types=("market",),
        source_classes=("market_daily",),
        datasets=("us_price_daily",),
        auth_modes=("api_key",),
        fetch_modes=("provider_owned_adapter",),
        cursor_modes=("time_watermark",),
        config_schema={
            "type": "object",
            "properties": {
                "secret_ref_id": {"type": "string"},
                "max_records": {"type": "integer", "minimum": 1},
            },
        },
        secret_fields=("secret_ref_id",),
        required_pit_fields=("event_time", "available_time", "ingest_time"),
        default_limits={"max_records": 100, "max_bytes": 1048576, "timeout_seconds": 15, "max_rate_per_second": 1.0},
        allowed_host_patterns=("www.alphavantage.co",),
        definition_state=DefinitionState.DISABLED_BY_BUILD,
        disabled_reason="Alpha Vantage free tier quota constraints and disabled by default policy",
        test_manifest_ref="evidence://connector-definition/us-alpha-vantage-daily-ohlcv",
    ),
    ConnectorDefinition(
        definition_id="us-ibkr-broker-readback",
        adapter_token="IbkrBrokerReadbackAdapter.records_from_readback_file",
        adapter_version="1.0.0",
        provider="IBKR",
        source_kinds=("data_source",),
        source_types=("market",),
        source_classes=("broker_readback",),
        datasets=("broker_readback_evidence",),
        auth_modes=("broker_ref",),
        fetch_modes=("file_readback", "provider_owned_adapter"),
        cursor_modes=("none",),
        config_schema={
            "type": "object",
            "properties": {
                "readback_file_env": {"type": "string"},
            },
        },
        secret_fields=(),
        required_pit_fields=("event_time", "available_time", "ingest_time"),
        default_limits={"max_records": 100, "max_bytes": 1048576, "timeout_seconds": 10, "max_rate_per_second": 1.0},
        allowed_host_patterns=(),
        definition_state=DefinitionState.SUPPORTED,
        test_manifest_ref="evidence://connector-definition/us-ibkr-broker-readback",
    ),
    ConnectorDefinition(
        definition_id="tw-shioaji-broker-readback",
        adapter_token="ShioajiBrokerReadbackAdapter.records_from_readback_file",
        adapter_version="1.0.0",
        provider="Shioaji",
        source_kinds=("data_source",),
        source_types=("market",),
        source_classes=("broker_readback",),
        datasets=("broker_readback_evidence",),
        auth_modes=("broker_ref",),
        fetch_modes=("file_readback", "provider_owned_adapter"),
        cursor_modes=("none",),
        config_schema={
            "type": "object",
            "properties": {
                "readback_file_env": {"type": "string"},
            },
        },
        secret_fields=(),
        required_pit_fields=("event_time", "available_time", "ingest_time"),
        default_limits={"max_records": 100, "max_bytes": 1048576, "timeout_seconds": 10, "max_rate_per_second": 1.0},
        allowed_host_patterns=(),
        definition_state=DefinitionState.SUPPORTED,
        test_manifest_ref="evidence://connector-definition/tw-shioaji-broker-readback",
    ),
    ConnectorDefinition(
        definition_id="strategy-seed-paper-corpus",
        adapter_token="StrategySeedPaperCorpusAdapter.records_from_metadata",
        adapter_version="1.0.0",
        provider="ArXiv/OpenAlex/SSRN",
        source_kinds=("strategy_seed_source",),
        source_types=("paper",),
        source_classes=("paper",),
        datasets=("strategy_seed_metadata",),
        auth_modes=("none",),
        fetch_modes=("custom_crawler",),
        cursor_modes=("time_watermark",),
        config_schema={
            "type": "object",
            "properties": {
                "topic_filter": {"type": "string"},
                "max_records": {"type": "integer", "minimum": 1},
            },
        },
        secret_fields=(),
        required_pit_fields=("event_time", "available_time", "ingest_time"),
        default_limits={"max_records": 100, "max_bytes": 2097152, "timeout_seconds": 20, "max_rate_per_second": 2.0},
        allowed_host_patterns=("arxiv.org", "api.openalex.org", "papers.ssrn.com"),
        definition_state=DefinitionState.SUPPORTED,
        test_manifest_ref="evidence://connector-definition/strategy-seed-paper-corpus",
    ),
    ConnectorDefinition(
        definition_id="strategy-seed-allowlisted-repo",
        adapter_token="StrategySeedAllowlistedRepoAdapter.records_from_repo_tree",
        adapter_version="1.0.0",
        provider="GitHub Allowlist",
        source_kinds=("strategy_seed_source",),
        source_types=("repo",),
        source_classes=("repo",),
        datasets=("strategy_seed_repo_tree",),
        auth_modes=("api_key", "none"),
        fetch_modes=("custom_crawler",),
        cursor_modes=("id_watermark",),
        config_schema={
            "type": "object",
            "properties": {
                "repo_slug": {"type": "string"},
                "secret_ref_id": {"type": "string"},
            },
        },
        secret_fields=("secret_ref_id",),
        required_pit_fields=("event_time", "available_time", "ingest_time"),
        default_limits={"max_records": 100, "max_bytes": 5242880, "timeout_seconds": 25, "max_rate_per_second": 2.0},
        allowed_host_patterns=("api.github.com", "raw.githubusercontent.com"),
        definition_state=DefinitionState.SUPPORTED,
        test_manifest_ref="evidence://connector-definition/strategy-seed-allowlisted-repo",
    ),
    ConnectorDefinition(
        definition_id="tw-tdcc-shareholding-distribution",
        adapter_token="TdccShareholdingDistributionAdapter.records_from_payload",
        adapter_version="1.0.0",
        provider="TDCC",
        source_kinds=("data_source",),
        source_types=("market",),
        source_classes=("taiwan_chip",),
        datasets=("tdcc_shareholding_distribution",),
        auth_modes=("none",),
        fetch_modes=("provider_owned_adapter",),
        cursor_modes=("time_watermark",),
        config_schema={
            "type": "object",
            "properties": {
                "symbols": {"type": "array", "items": {"type": "string"}},
                "source_dataset": {"type": "string"},
                "max_records": {"type": "integer", "minimum": 1},
            },
        },
        secret_fields=(),
        required_pit_fields=("event_time", "available_time", "ingest_time"),
        default_limits={"max_records": 100, "max_bytes": 5242880, "timeout_seconds": 20, "max_rate_per_second": 5.0},
        allowed_host_patterns=("openapi.tdcc.com.tw", "smart.tdcc.com.tw", "www.tdcc.com.tw"),
        definition_state=DefinitionState.SUPPORTED,
        test_manifest_ref="evidence://connector-definition/tw-tdcc-shareholding-distribution",
    ),
    ConnectorDefinition(
        definition_id="tw-taifex-futures-options-chip",
        adapter_token="TaifexDerivativesChipAdapter.records_from_payload",
        adapter_version="1.0.0",
        provider="TAIFEX",
        source_kinds=("data_source",),
        source_types=("market",),
        source_classes=("taiwan_chip",),
        datasets=("taifex_futures_chip", "taifex_options_chip"),
        auth_modes=("none",),
        fetch_modes=("provider_owned_adapter",),
        cursor_modes=("time_watermark",),
        config_schema={
            "type": "object",
            "properties": {
                "contracts": {"type": "array", "items": {"type": "string"}},
                "dataset": {"type": "string"},
                "max_records": {"type": "integer", "minimum": 1},
            },
        },
        secret_fields=(),
        required_pit_fields=("event_time", "available_time", "ingest_time"),
        default_limits={"max_records": 100, "max_bytes": 2097152, "timeout_seconds": 20, "max_rate_per_second": 5.0},
        allowed_host_patterns=("openapi.taifex.com.tw", "www.taifex.com.tw"),
        definition_state=DefinitionState.SUPPORTED,
        test_manifest_ref="evidence://connector-definition/tw-taifex-futures-options-chip",
    ),
    ConnectorDefinition(
        definition_id="social-admitted-market-discussion",
        adapter_token="AdmittedSocialMediaAdapter.records_from_payload",
        adapter_version="1.0.0",
        provider="StockTwits",
        source_kinds=("data_source",),
        source_types=("social",),
        source_classes=("social",),
        datasets=("social_admitted_post",),
        auth_modes=("none", "api_key"),
        fetch_modes=("provider_owned_adapter",),
        cursor_modes=("time_watermark",),
        config_schema={
            "type": "object",
            "properties": {
                "secret_ref_id": {"type": "string"},
                "platform": {"type": "string"},
                "symbols": {"type": "array", "items": {"type": "string"}},
                "max_records": {"type": "integer", "minimum": 1},
            },
        },
        secret_fields=("secret_ref_id",),
        required_pit_fields=("event_time", "available_time", "ingest_time"),
        default_limits={"max_records": 100, "max_bytes": 2097152, "timeout_seconds": 20, "max_rate_per_second": 5.0},
        allowed_host_patterns=("api.stocktwits.com", "stocktwits.com"),
        definition_state=DefinitionState.SUPPORTED,
        test_manifest_ref="evidence://connector-definition/social-admitted-market-discussion",
        metadata={
            "terms_ref": "source-ingest://license/stocktwits-terms-v1",
            "retention_policy": "tombstone_purge_on_deletion_30d_cache",
            "full_text_rights": "display_snippets_and_derived_features_only_no_raw_redistribution",
            "community_scope": "public_streams_only",
        },
    ),
    ConnectorDefinition(
        definition_id="alpha-db-vendor-signals",
        adapter_token="ExternalAlphaDbAdapter.records_from_payload",
        adapter_version="1.0.0",
        provider="Financial Modeling Prep",
        source_kinds=("data_source",),
        source_types=("alpha_db",),
        source_classes=("vendor_backfill", "alpha_signal"),
        datasets=("alpha_signal_record",),
        auth_modes=("api_key",),
        fetch_modes=("provider_owned_adapter",),
        cursor_modes=("time_watermark",),
        config_schema={
            "type": "object",
            "properties": {
                "secret_ref_id": {"type": "string"},
                "alpha_vendor_id": {"type": "string"},
                "signal_id": {"type": "string"},
                "signal_version": {"type": "string"},
                "field_schema_version": {"type": "string"},
                "universe": {"type": "array", "items": {"type": "string"}},
                "max_records": {"type": "integer", "minimum": 1},
            },
        },
        secret_fields=("secret_ref_id",),
        required_pit_fields=("event_time", "available_time", "ingest_time"),
        default_limits={"max_records": 200, "max_bytes": 4194304, "timeout_seconds": 25, "max_rate_per_second": 5.0},
        allowed_host_patterns=("financialmodelingprep.com", "api.finmindtrade.com", "api.vendor-factors.io"),
        definition_state=DefinitionState.SUPPORTED,
        test_manifest_ref="evidence://connector-definition/alpha-db-vendor-signals",
    ),
)

DEPLOYED_CONNECTOR_DEFINITIONS: tuple[ConnectorDefinition, ...] = _CANONICAL_DEFINITIONS

_DEFINITION_ID_ALIASES: dict[str, str] = {
    "tw-finmind-dataset": "tw-finmind-datasets",
    "tw-finmind-broker-bulk-backfill": "tw-finmind-broker-bulk-parquet",
    "tw-tej-research-backfill": "tw-tej-research-datasets",
    "tw-mops-official-disclosure": "tw-mops-official-disclosures",
    "us-sec-edgar-company-facts": "us-sec-edgar-filings",
    "us-fred-macro-series": "us-fred-macro",
    "us-finra-short-volume": "us-finra-short-sale",
    "crypto-coingecko-spot-market": "crypto-coingecko-spot",
}

_DEFINITIONS_BY_ID: dict[str, ConnectorDefinition] = {
    defn.definition_id: defn for defn in _CANONICAL_DEFINITIONS
}

_DEFINITIONS_BY_ADAPTER: dict[str, ConnectorDefinition] = {
    defn.adapter_token: defn for defn in _CANONICAL_DEFINITIONS
}

# Verify at import time that no definition duplicates exist
if len(_DEFINITIONS_BY_ID) != len(_CANONICAL_DEFINITIONS):
    raise SourceManagementContractError("Duplicate definition_id detected in canonical connector definitions")


def list_connector_definitions() -> list[ConnectorDefinition]:
    """Return all deployed connector definitions sorted stably by definition_id."""
    return sorted(_CANONICAL_DEFINITIONS, key=lambda d: d.definition_id)


def deployed_connector_definitions() -> dict[str, ConnectorDefinition]:
    """Return mapping of definition_id -> ConnectorDefinition."""
    return dict(_DEFINITIONS_BY_ID)


def get_connector_definition(definition_id: str) -> ConnectorDefinition | None:
    """Lookup a deployed ConnectorDefinition by definition_id."""
    clean_id = str(definition_id).strip()
    canonical_id = _DEFINITION_ID_ALIASES.get(clean_id, clean_id)
    return _DEFINITIONS_BY_ID.get(canonical_id)


def get_connector_definition_by_adapter(adapter_token: str) -> ConnectorDefinition | None:
    """Lookup a deployed ConnectorDefinition by its adapter_token."""
    token = str(adapter_token or "").strip()
    # Resolve aliases if needed
    canonical_token = PROVIDER_ADAPTER_ALIASES.get(token, token)
    return _DEFINITIONS_BY_ADAPTER.get(canonical_token) or _DEFINITIONS_BY_ADAPTER.get(token)


def validate_connector_definition(definition: ConnectorDefinition) -> None:
    """Validate that a ConnectorDefinition conforms to all invariants."""
    # Re-verify fingerprint
    expected_fp = compute_definition_fingerprint(definition._to_raw_dict_without_fp())
    if definition.fingerprint != expected_fp:
        raise SourceManagementContractError(
            f"Fingerprint mismatch on definition {definition.definition_id}: expected {expected_fp}, got {definition.fingerprint}"
        )
    assert_no_raw_secrets(definition.config_schema)
