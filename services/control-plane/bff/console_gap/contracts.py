from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


SurfaceStatus = Literal["ok", "degraded", "unavailable"]


class SurfaceState(BaseModel):
    status: SurfaceStatus
    source: str

    model_config = ConfigDict(extra="allow")


class PageInfo(BaseModel):
    next_page_token: Optional[str] = None
    total: int = 0
    page_size: Optional[int] = None
    returned: Optional[int] = None
    has_more: Optional[bool] = None

    model_config = ConfigDict(extra="allow")


class ManagementListMeta(BaseModel):
    snapshot_at: str
    status: SurfaceStatus
    source: str
    surfaces: Dict[str, SurfaceState] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow")


class ManagementRecordsData(BaseModel):
    id: str
    items: List[Dict[str, Any]]
    summary: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow")


class ManagementRecordsEnvelope(BaseModel):
    data: Union[ManagementRecordsData, List[Dict[str, Any]]]
    items: Optional[List[Dict[str, Any]]] = None
    page_info: PageInfo
    meta: ManagementListMeta

    model_config = ConfigDict(extra="allow")


class DataSourcesData(BaseModel):
    id: str
    items: List[Dict[str, Any]]
    summary: Dict[str, Any] = Field(default_factory=dict)
    status: SurfaceStatus
    source: str

    model_config = ConfigDict(extra="allow")


class DataSourcesEnvelope(BaseModel):
    data: DataSourcesData
    items: Optional[List[Dict[str, Any]]] = None
    page_info: PageInfo
    meta: ManagementListMeta

    model_config = ConfigDict(extra="allow")


class LineageGraphData(BaseModel):
    id: str
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]
    status: SurfaceStatus
    source: str

    model_config = ConfigDict(extra="allow")


class LineageEnvelope(BaseModel):
    data: LineageGraphData
    items: List[Dict[str, Any]]
    page_info: PageInfo
    meta: ManagementListMeta

    model_config = ConfigDict(extra="allow")


# ==============================================================================
# SD-SRCM-03 Contracts: DataSource V2 DTOs & Command Envelopes
# ==============================================================================


class DataSourceDetailData(BaseModel):
    id: str
    source_instance_id: str
    definition: Dict[str, Any]
    instance: Dict[str, Any]
    desired: Dict[str, Any]
    observed: Dict[str, Any]
    allowed_actions: Dict[str, Any] = Field(default_factory=dict, alias="allowedActions")
    lineage_summary: Dict[str, Any] = Field(default_factory=dict)
    status: SurfaceStatus
    source: str

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class DataSourceDetailEnvelope(BaseModel):
    data: DataSourceDetailData
    meta: ManagementListMeta

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class DataSourceCatalogData(BaseModel):
    id: str = "data-sources-catalog"
    definitions: List[Dict[str, Any]]
    count: int
    status: SurfaceStatus
    source: str
    policy_registry: Optional[Dict[str, Any]] = None
    financial_data_source_catalog: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class DataSourceCatalogEnvelope(BaseModel):
    data: DataSourceCatalogData
    meta: ManagementListMeta

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class DataSourceRunsData(BaseModel):
    id: str
    source_instance_id: str
    observations: List[Dict[str, Any]] = Field(default_factory=list)
    canaries: List[Dict[str, Any]] = Field(default_factory=list)
    status: SurfaceStatus
    source: str

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class DataSourceRunsEnvelope(BaseModel):
    data: DataSourceRunsData
    meta: ManagementListMeta

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class DataSourceReceiptsData(BaseModel):
    id: str
    source_instance_id: str
    receipts: List[Dict[str, Any]]
    count: int
    status: SurfaceStatus
    source: str

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class DataSourceReceiptsEnvelope(BaseModel):
    data: DataSourceReceiptsData
    meta: ManagementListMeta

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class SourceCommandReceiptData(BaseModel):
    id: str
    receipt_id: str
    receipt: Dict[str, Any]
    status: SurfaceStatus
    source: str

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class SourceCommandReceiptEnvelope(BaseModel):
    data: SourceCommandReceiptData
    meta: ManagementListMeta

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class CreateDataSourceRequest(BaseModel):
    definition_id: str = Field(alias="definitionId")
    source_instance_id: str = Field(alias="sourceInstanceId")
    connector_id: Optional[str] = Field(default=None, alias="connectorId")
    provider: Optional[str] = None
    source_class: Optional[str] = Field(default=None, alias="sourceClass")
    datasets: Optional[List[Any]] = None
    markets: Optional[List[str]] = None
    license_scope: Optional[str] = Field(default=None, alias="licenseScope")
    allowed_use: Optional[List[str]] = Field(default=None, alias="allowedUse")
    retention_policy_ref: Optional[str] = Field(default=None, alias="retentionPolicyRef")
    deletion_policy_ref: Optional[str] = Field(default=None, alias="deletionPolicyRef")
    freshness_sla_seconds: Optional[int] = Field(default=None, alias="freshnessSlaSeconds")
    sensitivity: Optional[str] = None
    connector_config: Optional[Dict[str, Any]] = Field(default=None, alias="connectorConfig")
    schedule: Optional[Dict[str, Any]] = None
    limits: Optional[Dict[str, Any]] = None
    allowed_hosts: Optional[List[str]] = Field(default=None, alias="allowedHosts")
    provider_account_ref: Optional[str] = Field(default=None, alias="providerAccountRef")
    entitlement_tags: Optional[List[str]] = Field(default=None, alias="entitlementTags")
    universe_policy_ref: Optional[str] = Field(default=None, alias="universePolicyRef")
    reason: str
    trace_id: Optional[str] = Field(default=None, alias="traceId")

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class ActionCommandRequest(BaseModel):
    expected_revision: int = Field(alias="expectedRevision")
    reason: str
    confirmation: Optional[bool] = False
    parameters: Optional[Dict[str, Any]] = Field(default_factory=dict)
    trace_id: Optional[str] = Field(default=None, alias="traceId")

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class ChangeScheduleRequest(BaseModel):
    expected_revision: int = Field(alias="expectedRevision")
    reason: str
    schedule: Dict[str, Any]
    trace_id: Optional[str] = Field(default=None, alias="traceId")

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class ReplaceDataSourceRequest(BaseModel):
    expected_revision: int = Field(alias="expectedRevision")
    reason: str
    replacement_source_id: str = Field(alias="replacementSourceId")
    confirmation: bool = False
    trace_id: Optional[str] = Field(default=None, alias="traceId")

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class RetireDataSourceRequest(BaseModel):
    expected_revision: int = Field(alias="expectedRevision")
    reason: str
    confirmation: bool = False
    trace_id: Optional[str] = Field(default=None, alias="traceId")

    model_config = ConfigDict(extra="allow", populate_by_name=True)
