"""Data models for Agora live widget data adapters and allowlisted query contract."""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class WidgetDataStatus(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class WidgetUnavailableReason(str, Enum):
    UNWIRED_WIDGET_TYPE = "UNWIRED_WIDGET_TYPE"
    STALE_DATA = "STALE_DATA"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    TENANT_MISMATCH = "TENANT_MISMATCH"
    FILTERED_CUTOFF = "FILTERED_CUTOFF"
    DATA_MISSING = "DATA_MISSING"
    LIVE_PROFILE_NO_FIXTURES = "LIVE_PROFILE_NO_FIXTURES"


class WidgetLineageRef(BaseModel):
    model_config = {"extra": "forbid"}

    source_name: str = Field(min_length=1)
    record_count: int = Field(ge=0)
    point_in_time: str = Field(min_length=1)
    query_hash: str = Field(min_length=1)
    pipeline_version: str = "v1"


class WidgetDataQueryRequest(BaseModel):
    model_config = {"extra": "forbid"}

    tenant_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    widget_type: str = Field(min_length=1)
    cutoff: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)


class WidgetDataQueryResponse(BaseModel):
    model_config = {"extra": "forbid"}

    widget_type: str = Field(min_length=1)
    status: Literal["ok", "degraded", "unavailable"]
    source: str = Field(min_length=1)
    as_of: str = Field(min_length=1)
    cutoff: str = Field(min_length=1)
    lineage: List[WidgetLineageRef] = Field(default_factory=list)
    data: Any = Field(default_factory=dict)
    unavailable_reason: Optional[str] = None
