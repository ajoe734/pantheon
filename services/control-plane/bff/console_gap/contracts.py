from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

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


class ManagementRecordsEnvelope(BaseModel):
    data: List[Dict[str, Any]]
    items: List[Dict[str, Any]]
    page_info: PageInfo
    meta: ManagementListMeta

    model_config = ConfigDict(extra="allow")


class DataSourcesData(BaseModel):
    id: str
    items: List[Dict[str, Any]]
    status: SurfaceStatus
    source: str

    model_config = ConfigDict(extra="allow")


class DataSourcesEnvelope(BaseModel):
    data: DataSourcesData
    items: List[Dict[str, Any]]
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
