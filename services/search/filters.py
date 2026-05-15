"""Search request and access filtering primitives."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence
from uuid import uuid4

from services.knowledge.evidence.models import KnowledgeObject


class SearchPolicyError(ValueError):
    """Raised when a search request violates governed-search policy."""


def _strings(values: Sequence[str] | None) -> tuple[str, ...]:
    return tuple(str(value).strip() for value in (values or ()) if str(value).strip())


@dataclass(frozen=True)
class SearchRequest:
    query: str
    request_id: str = field(default_factory=lambda: f"search-{uuid4().hex[:12]}")
    persona_id: str | None = None
    workspace_id: str | None = None
    source_types: Sequence[str] = field(default_factory=tuple)
    time_window: Mapping[str, Any] | None = None
    environment: str = "paper"
    top_k: int = 10
    require_citations: bool = True
    trace_id: str = field(default_factory=lambda: f"trace-search-{uuid4().hex[:12]}")
    filters_applied: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.query or "").strip():
            raise SearchPolicyError("query is required")
        if int(self.top_k) <= 0 or int(self.top_k) > 100:
            raise SearchPolicyError("top_k must be between 1 and 100")
        object.__setattr__(self, "query", str(self.query).strip())
        object.__setattr__(self, "top_k", int(self.top_k))
        object.__setattr__(self, "source_types", _strings(self.source_types))
        object.__setattr__(self, "filters_applied", dict(self.filters_applied))

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "query": self.query,
            "persona_id": self.persona_id,
            "workspace_id": self.workspace_id,
            "source_types": list(self.source_types),
            "time_window": dict(self.time_window or {}),
            "environment": self.environment,
            "top_k": self.top_k,
            "require_citations": self.require_citations,
            "trace_id": self.trace_id,
            "filters_applied": dict(self.filters_applied),
        }


@dataclass(frozen=True)
class SearchAccessContext:
    persona_id: str | None
    workspace_id: str | None
    environment: str = "paper"
    access_scopes: Sequence[str] = field(default_factory=lambda: ("public",))
    license_scopes: Sequence[str] = field(default_factory=lambda: ("internal", "open"))

    def __post_init__(self) -> None:
        object.__setattr__(self, "environment", str(self.environment or "").strip() or "paper")
        object.__setattr__(self, "access_scopes", _strings(self.access_scopes) or ("public",))
        object.__setattr__(self, "license_scopes", _strings(self.license_scopes) or ("internal", "open"))

    def require_persona_workspace(self) -> None:
        if not str(self.persona_id or "").strip() or not str(self.workspace_id or "").strip():
            raise SearchPolicyError("governed search requires persona_id and workspace_id")

    def permits(self, knowledge_object: KnowledgeObject) -> tuple[bool, str | None]:
        object_environments = set(knowledge_object.environment_scope)
        if object_environments and self.environment not in object_environments:
            return False, "environment"

        object_license = str(knowledge_object.license_scope or "").strip()
        if object_license and object_license not in set(self.license_scopes):
            return False, "license_scope"

        object_scopes = set(knowledge_object.access_scope or ("public",))
        allowed_scopes = set(self.access_scopes)
        if "public" not in object_scopes and object_scopes.isdisjoint(allowed_scopes):
            return False, "access_scope"

        if knowledge_object.persona_scope:
            if str(self.persona_id or "") not in set(knowledge_object.persona_scope):
                return False, "persona_scope"
        if knowledge_object.workspace_scope:
            if str(self.workspace_id or "") not in set(knowledge_object.workspace_scope):
                return False, "workspace_scope"
        return True, None
