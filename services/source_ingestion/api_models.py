"""API request and response models for Source Ingestion route families."""

from __future__ import annotations

from typing import Any, Literal

try:
    from pydantic import BaseModel, ConfigDict, Field
except ImportError:  # pragma: no cover - compatibility with older pydantic.
    from pydantic import BaseModel, Field

    ConfigDict = None  # type: ignore[assignment]

from .active_universe import (
    ActiveUniverseMember,
    SourceUpdateRule,
    UniverseTier,
)
from .connectors import (
    AuthType,
    ConnectorMode,
    ConnectorStatus,
    SourceConnector,
    SourceRecord,
    SourceRecordStatus,
    SourceType,
)


class StrictBaseModel(BaseModel):
    if ConfigDict is not None:
        model_config = ConfigDict(extra="forbid")
    else:  # pragma: no cover - compatibility with older pydantic.

        class Config:
            extra = "forbid"


# ---------------------------------------------------------------------------
# Ingest & Connector Models
# ---------------------------------------------------------------------------


class ConnectorBody(StrictBaseModel):
    connector_id: str
    source_type: SourceType
    provider: str
    license_scope: str
    auth_type: AuthType = AuthType.NONE
    secret_ref_id: str | None = None
    supported_modes: list[ConnectorMode] = Field(default_factory=lambda: [ConnectorMode.BATCH])
    status: ConnectorStatus = ConnectorStatus.ENABLED
    rate_limit_policy_ref: str | None = None
    auth_policy: dict[str, Any] | None = None
    rate_limit_policy: dict[str, Any] | None = None
    license_policy: dict[str, Any] | None = None
    source_metadata: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_domain(self) -> SourceConnector:
        return SourceConnector(
            connector_id=self.connector_id,
            source_type=self.source_type.value,
            provider=self.provider,
            license_scope=self.license_scope,
            auth_type=self.auth_type.value,
            secret_ref_id=self.secret_ref_id,
            supported_modes=[mode.value for mode in self.supported_modes],
            status=self.status.value,
            rate_limit_policy_ref=self.rate_limit_policy_ref,
            auth_policy=self.auth_policy,
            rate_limit_policy=self.rate_limit_policy,
            license_policy=self.license_policy,
            source_metadata=self.source_metadata,
            metadata=self.metadata,
        )


class SourceRecordBody(StrictBaseModel):
    source_id: str
    connector_id: str
    source_type: SourceType
    title: str
    content_ref: str
    status: SourceRecordStatus = SourceRecordStatus.NORMALIZED
    metadata: dict[str, Any] = Field(default_factory=dict)
    trace_id: str = ""

    def to_domain(self) -> SourceRecord:
        return SourceRecord(
            source_id=self.source_id,
            connector_id=self.connector_id,
            source_type=self.source_type.value,
            title=self.title,
            content_ref=self.content_ref,
            status=self.status.value,
            metadata=self.metadata,
            trace_id=self.trace_id,
        )


class ConfiguredFetchRecordBody(StrictBaseModel):
    source_id: str
    title: str
    content_ref: str
    connector_id: str | None = None
    source_type: SourceType | None = None
    status: SourceRecordStatus = SourceRecordStatus.NORMALIZED
    metadata: dict[str, Any] = Field(default_factory=dict)
    trace_id: str = ""
    created_at: str | None = None

    def to_config(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "source_id": self.source_id,
            "title": self.title,
            "content_ref": self.content_ref,
            "status": self.status.value,
            "metadata": self.metadata,
            "trace_id": self.trace_id,
        }
        if self.connector_id:
            payload["connector_id"] = self.connector_id
        if self.source_type:
            payload["source_type"] = self.source_type.value
        if self.created_at:
            payload["created_at"] = self.created_at
        return payload


class ConfiguredFetchBody(StrictBaseModel):
    mode: Literal["static_records", "external_feed", "provider_owned_adapter"] = "static_records"
    records: list[ConfiguredFetchRecordBody] = Field(default_factory=list)
    url: str | None = None
    allowed_url_prefixes: list[str] = Field(default_factory=list)
    timeout_seconds: float = 5.0
    max_bytes: int = 1_000_000
    max_records: int = 100
    default_access_scope: list[str] = Field(default_factory=lambda: ["public"])
    respect_robots_txt: bool = True
    network_scope: Literal["external", "internal_service"] = "external"
    adapter: str | None = None
    adapter_config: dict[str, Any] = Field(default_factory=dict)
    request: dict[str, Any] = Field(default_factory=dict)
    allow_empty: bool = False
    empty_reason: str = ""
    next_watermark: str | None = None
    fail_until_attempt: int = 0
    failure_reason: str = "configured connector fetch failed"

    def to_config(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "mode": self.mode,
            "records": [record.to_config() for record in self.records],
            "next_watermark": self.next_watermark,
            "allow_empty": self.allow_empty,
            "empty_reason": self.empty_reason,
            "fail_until_attempt": self.fail_until_attempt,
            "failure_reason": self.failure_reason,
        }
        if self.mode == "external_feed":
            payload.update(
                {
                    "url": self.url,
                    "allowed_url_prefixes": self.allowed_url_prefixes,
                    "timeout_seconds": self.timeout_seconds,
                    "max_bytes": self.max_bytes,
                    "max_records": self.max_records,
                    "default_access_scope": self.default_access_scope,
                    "respect_robots_txt": self.respect_robots_txt,
                    "network_scope": self.network_scope,
                }
            )
        if self.mode == "provider_owned_adapter":
            payload.update(
                {
                    "adapter": self.adapter,
                    "adapter_config": self.adapter_config,
                    "request": self.request,
                    "max_records": self.max_records,
                }
            )
        return payload


class ConfigureConnectorRequest(StrictBaseModel):
    connector: ConnectorBody
    fetch: ConfiguredFetchBody


class TriggerIngestJobRequest(StrictBaseModel):
    connector: ConnectorBody | None = None
    connector_id: str | None = None
    trace_id: str
    trigger_type: str = "manual"
    records: list[SourceRecordBody] = Field(default_factory=list)
    next_watermark: str | None = None
    fetch: ConfiguredFetchBody | None = None
    job_parameters: dict[str, Any] = Field(default_factory=dict)


class SourceRecordIngestRequest(StrictBaseModel):
    connector: ConnectorBody | None = None
    connector_id: str | None = None
    trace_id: str
    trigger_type: str = "manual"
    records: list[SourceRecordBody] = Field(default_factory=list)
    next_watermark: str | None = None


class ReplayDlqRequest(StrictBaseModel):
    tag: str = "retry_exhausted"
    entry_ids: list[str] = Field(default_factory=list)
    reason: str = "operator-approved source ingest DLQ replay"
    actor_id: str = "source-ingest-operator"


class SetScheduleRequest(StrictBaseModel):
    interval_seconds: int = 0
    enabled: bool = False


class SetConnectorLifecycleRequest(StrictBaseModel):
    status: ConnectorStatus
    reason: str
    actor_id: str = "source-ingest-operator"
    trace_id: str | None = None


class RunScheduledRequest(StrictBaseModel):
    max_concurrency: int | None = None
    force_connector_ids: list[str] = Field(default_factory=list)
    exclusive_connector_ids: list[str] = Field(default_factory=list)


class ReplayFrontierRequest(StrictBaseModel):
    trace_id: str | None = None


# ---------------------------------------------------------------------------
# Catalog & Controller Models
# ---------------------------------------------------------------------------


class ActiveUniverseMemberBody(StrictBaseModel):
    symbol: str
    tier: UniverseTier
    market: str = "TW"
    venue: str | None = None
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_domain(self) -> ActiveUniverseMember:
        return ActiveUniverseMember(
            symbol=self.symbol,
            tier=self.tier.value,
            market=self.market,
            venue=self.venue,
            reason=self.reason,
            metadata=self.metadata,
        )


class SourceUpdateRuleBody(StrictBaseModel):
    connector_id: str
    dataset: str
    eligible_tiers: list[UniverseTier]
    cadence: str
    market: str = "TW"
    priority: int = 100
    max_symbols_per_run: int | None = None
    reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_domain(self) -> SourceUpdateRule:
        return SourceUpdateRule(
            connector_id=self.connector_id,
            dataset=self.dataset,
            eligible_tiers=[tier.value for tier in self.eligible_tiers],
            cadence=self.cadence,
            market=self.market,
            priority=self.priority,
            max_symbols_per_run=self.max_symbols_per_run,
            reason=self.reason,
            metadata=self.metadata,
        )


class ActiveUniversePlanRequest(StrictBaseModel):
    members: list[ActiveUniverseMemberBody]
    rules: list[SourceUpdateRuleBody] = Field(default_factory=list)


class ActiveUniverseScheduleRequest(StrictBaseModel):
    members: list[ActiveUniverseMemberBody]
    rules: list[SourceUpdateRuleBody] = Field(default_factory=list)
    run_date: str
    default_max_symbols_per_job: int = 50
    enqueue: bool = True
    trace_id: str | None = None


class PersonaSourceProvisioningRequest(StrictBaseModel):
    persona: dict[str, Any] | None = None
    personas: list[dict[str, Any]] | None = None
    dry_run: bool = False
    authoritative_snapshot: bool = False
    desired_state_sha256: str | None = None
    source_authority: str | None = None


# ---------------------------------------------------------------------------
# Proposal Models
# ---------------------------------------------------------------------------


class ProposedSourceBody(StrictBaseModel):
    source_id: str
    source_kind: str
    provider: str
    source_class: str
    license_scope: str
    allowed_use: list[str]
    homepage_url: str | None = None
    docs_url: str | None = None
    entitlement_required: bool = False
    entitlement_tags: list[str] = Field(default_factory=list)
    expected_datasets: list[str] = Field(default_factory=list)
    update_frequency: str | None = None
    cost_notes: str | None = None


class ProposalRiskBody(StrictBaseModel):
    risk_type: str
    severity: str
    note: str


class CreateProposalRequest(StrictBaseModel):
    proposal_type: str
    source_kind: str
    rationale: str
    proposed_by: dict[str, Any]
    target_source_id: str | None = None
    proposed_source: ProposedSourceBody | None = None
    expected_value: dict[str, Any] = Field(default_factory=dict)
    risks: list[ProposalRiskBody] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class LLMProposalRequest(StrictBaseModel):
    """LLM-originated proposal — always creates a draft via the adapter."""

    proposal_type: str
    source_kind: str
    rationale: str
    agent_id: str
    trace_id: str | None = None
    target_source_id: str | None = None
    proposed_source: ProposedSourceBody | None = None
    expected_value: dict[str, Any] = Field(default_factory=dict)
    risks: list[ProposalRiskBody] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ApplyProposalRequest(StrictBaseModel):
    change_ref: str | None = None


# ---------------------------------------------------------------------------
# Observability Models
# ---------------------------------------------------------------------------


class UpsertHealthRequest(StrictBaseModel):
    source_id: str
    source_kind: str
    status: str = "ok"
    last_success_at: str | None = None
    last_failure_at: str | None = None
    latest_watermark: str | None = None
    row_count_last_run: int = 0
    rejected_count_last_run: int = 0
    schema_hash: str | None = None
    staleness_seconds: int | None = None
    error_rate_7d: float = 0.0
    cost_estimate_30d: float | None = None
    metadata: dict[str, Any] = {}


class UpsertUsageRequest(StrictBaseModel):
    date: str
    source_id: str
    source_kind: str
    ingest_run_count: int = 0
    query_count: int = 0
    search_hit_count: int = 0
    persona_match_count: int = 0
    strategy_seed_yield_count: int = 0
    strategy_promotion_count: int = 0
    experiment_dependency_count: int = 0
    active_strategy_dependency_count: int = 0
    cost_estimate: float | None = None


class GapReportRequest(StrictBaseModel):
    members: list[ActiveUniverseMemberBody]
    rules: list[SourceUpdateRuleBody] = Field(default_factory=list)
    run_date: str
    default_max_symbols_per_job: int = 50
    render_markdown: bool = False


# ---------------------------------------------------------------------------
# Management Models
# ---------------------------------------------------------------------------


class SourceCommandActorBody(StrictBaseModel):
    actor_type: str = "operator"
    actor_id: str
    roles: list[str] = Field(default_factory=lambda: ["operator"])


class SourceCommandRequestBody(StrictBaseModel):
    command_id: str | None = None
    idempotency_key: str
    command_type: str
    source_instance_id: str
    expected_revision: int | None = None
    actor: SourceCommandActorBody
    reason: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = None
    requested_at: str | None = None
