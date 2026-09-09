"""Search request, access context, and pre-retrieval policy filtering."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from uuid import uuid4

from services.knowledge.evidence.models import EvidenceBundle, EvidenceItem, KnowledgeObject


class SearchPolicyError(ValueError):
    """Raised when a search request violates governed-search policy."""


class SearchCapabilityUnavailableError(SearchPolicyError):
    """Raised when a requested search retrieval mode or capability is unavailable."""


RETRIEVAL_MODES = ("keyword", "full_text", "semantic", "hybrid", "structured_alpha")


def _strings(values: Sequence[str] | None) -> tuple[str, ...]:
    return tuple(str(value).strip() for value in (values or ()) if str(value).strip())


def _parse_iso_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    raw = str(value).strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _format_iso(dt: datetime | str | None) -> str | None:
    if dt is None:
        return None
    if isinstance(dt, str):
        parsed = _parse_iso_datetime(dt)
        return parsed.replace(microsecond=0).isoformat().replace("+00:00", "Z") if parsed else dt
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class SearchFilters:
    source_types: tuple[str, ...] = field(default_factory=tuple)
    license_scopes: tuple[str, ...] = field(default_factory=tuple)
    sensitivity: tuple[str, ...] = field(default_factory=tuple)
    capital_pool_scope: tuple[str, ...] = field(default_factory=tuple)
    event_time_gte: str | None = None
    event_time_lte: str | None = None
    available_time_lte: str | None = None
    asset_class: tuple[str, ...] = field(default_factory=tuple)
    strategy_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_types", _strings(self.source_types))
        object.__setattr__(self, "license_scopes", _strings(self.license_scopes))
        object.__setattr__(self, "sensitivity", _strings(self.sensitivity))
        object.__setattr__(self, "capital_pool_scope", _strings(self.capital_pool_scope))
        object.__setattr__(self, "asset_class", _strings(self.asset_class))
        if self.event_time_gte is not None:
            object.__setattr__(self, "event_time_gte", _format_iso(self.event_time_gte))
        if self.event_time_lte is not None:
            object.__setattr__(self, "event_time_lte", _format_iso(self.event_time_lte))
        if self.available_time_lte is not None:
            object.__setattr__(self, "available_time_lte", _format_iso(self.available_time_lte))
        if self.strategy_id is not None:
            object.__setattr__(self, "strategy_id", str(self.strategy_id).strip() or None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_types": list(self.source_types),
            "license_scopes": list(self.license_scopes),
            "sensitivity": list(self.sensitivity),
            "capital_pool_scope": list(self.capital_pool_scope),
            "event_time_gte": self.event_time_gte,
            "event_time_lte": self.event_time_lte,
            "available_time_lte": self.available_time_lte,
            "asset_class": list(self.asset_class),
            "strategy_id": self.strategy_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "SearchFilters":
        if not data:
            return cls()
        return cls(
            source_types=_strings(data.get("source_types")),
            license_scopes=_strings(data.get("license_scopes")),
            sensitivity=_strings(data.get("sensitivity")),
            capital_pool_scope=_strings(data.get("capital_pool_scope")),
            event_time_gte=data.get("event_time_gte"),
            event_time_lte=data.get("event_time_lte"),
            available_time_lte=data.get("available_time_lte"),
            asset_class=_strings(data.get("asset_class")),
            strategy_id=data.get("strategy_id"),
        )


@dataclass(frozen=True)
class SearchRequest:
    query: str = ""
    request_id: str = field(default_factory=lambda: f"search-{uuid4().hex[:12]}")
    schema_version: str = "governed_search_request.v2"
    retrieval_mode: str = "keyword"
    actor_ref: str | None = None
    persona_id: str | None = None
    workspace_id: str | None = None
    role_refs: Sequence[str] = field(default_factory=lambda: ("researcher",))
    environment: str = "paper"
    purpose: str = "research"
    source_types: Sequence[str] = field(default_factory=tuple)
    time_window: Mapping[str, Any] | None = None
    filters: SearchFilters | Mapping[str, Any] | None = None
    structured_alpha: Mapping[str, Any] | None = None
    top_k: int = 10
    require_citations: bool = True
    trace_id: str = field(default_factory=lambda: f"trace-search-{uuid4().hex[:12]}")
    filters_applied: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        mode = str(self.retrieval_mode or "keyword").strip().lower()
        if mode not in RETRIEVAL_MODES:
            raise SearchPolicyError(f"Invalid retrieval_mode '{mode}'; must be one of {RETRIEVAL_MODES}")
        object.__setattr__(self, "retrieval_mode", mode)

        if mode == "structured_alpha":
            if self.structured_alpha is None:
                raise SearchPolicyError("structured_alpha AST is required when retrieval_mode is 'structured_alpha'")
        else:
            if not str(self.query or "").strip():
                raise SearchPolicyError("query is required")

        if int(self.top_k) <= 0 or int(self.top_k) > 100:
            raise SearchPolicyError("top_k must be between 1 and 100")

        object.__setattr__(self, "query", str(self.query or "").strip())
        object.__setattr__(self, "top_k", int(self.top_k))
        object.__setattr__(self, "role_refs", _strings(self.role_refs) or ("researcher",))
        object.__setattr__(self, "source_types", _strings(self.source_types))
        object.__setattr__(self, "filters_applied", dict(self.filters_applied or {}))

        # Reconcile filters and time_window
        event_gte = None
        event_lte = None
        if self.time_window is not None:
            if not isinstance(self.time_window, Mapping):
                raise SearchPolicyError("Ambiguous or invalid time_window in search request; explicit event bounds required")
            if self.time_window:
                allowed_keys = {
                    "event_time_gte", "event_time_lte",
                    "start", "end",
                    "from", "to",
                    "gte", "lte",
                    "since", "until",
                }
                for k in self.time_window.keys():
                    if k not in allowed_keys:
                        raise SearchPolicyError(
                            f"Ambiguous or invalid time_window key '{k}' in search request; explicit event bounds required"
                        )
                raw_start = (
                    self.time_window.get("event_time_gte")
                    or self.time_window.get("start")
                    or self.time_window.get("from")
                    or self.time_window.get("gte")
                    or self.time_window.get("since")
                )
                raw_end = (
                    self.time_window.get("event_time_lte")
                    or self.time_window.get("end")
                    or self.time_window.get("to")
                    or self.time_window.get("lte")
                    or self.time_window.get("until")
                )
                if raw_start is not None:
                    parsed_start = _parse_iso_datetime(raw_start)
                    if parsed_start is None:
                        raise SearchPolicyError("Ambiguous or invalid time_window in search request; explicit event bounds required")
                    event_gte = _format_iso(parsed_start)
                if raw_end is not None:
                    parsed_end = _parse_iso_datetime(raw_end)
                    if parsed_end is None:
                        raise SearchPolicyError("Ambiguous or invalid time_window in search request; explicit event bounds required")
                    event_lte = _format_iso(parsed_end)

        base_filter_dict: dict[str, Any] = {}
        if isinstance(self.filters, SearchFilters):
            base_filter_dict = self.filters.to_dict()
        elif isinstance(self.filters, Mapping):
            base_filter_dict = dict(self.filters)

        # Merge source_types if not already in filters
        if self.source_types and not base_filter_dict.get("source_types"):
            base_filter_dict["source_types"] = list(self.source_types)

        # Override or merge event bounds from time_window translation
        if event_gte is not None:
            base_filter_dict["event_time_gte"] = event_gte
        if event_lte is not None:
            base_filter_dict["event_time_lte"] = event_lte

        resolved_filters = SearchFilters.from_dict(base_filter_dict)
        object.__setattr__(self, "filters", resolved_filters)
        if resolved_filters.source_types and not self.source_types:
            object.__setattr__(self, "source_types", resolved_filters.source_types)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "query": self.query,
            "retrieval_mode": self.retrieval_mode,
            "actor_ref": self.actor_ref,
            "persona_id": self.persona_id,
            "workspace_id": self.workspace_id,
            "role_refs": list(self.role_refs),
            "environment": self.environment,
            "purpose": self.purpose,
            "source_types": list(self.source_types),
            "time_window": dict(self.time_window or {}),
            "filters": self.filters.to_dict() if isinstance(self.filters, SearchFilters) else dict(self.filters or {}),
            "structured_alpha": dict(self.structured_alpha) if self.structured_alpha is not None else None,
            "top_k": self.top_k,
            "require_citations": self.require_citations,
            "trace_id": self.trace_id,
            "filters_applied": dict(self.filters_applied),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SearchRequest":
        filters_data = data.get("filters")
        return cls(
            schema_version=str(data.get("schema_version") or "governed_search_request.v2"),
            request_id=str(data.get("request_id") or f"search-{uuid4().hex[:12]}"),
            query=str(data.get("query") or ""),
            retrieval_mode=str(data.get("retrieval_mode") or "keyword"),
            actor_ref=data.get("actor_ref"),
            persona_id=data.get("persona_id"),
            workspace_id=data.get("workspace_id"),
            role_refs=list(data.get("role_refs") or ("researcher",)),
            environment=str(data.get("environment") or "paper"),
            purpose=str(data.get("purpose") or "research"),
            source_types=list(data.get("source_types") or ()),
            time_window=data.get("time_window"),
            filters=SearchFilters.from_dict(filters_data) if isinstance(filters_data, Mapping) else None,
            structured_alpha=data.get("structured_alpha"),
            top_k=int(data.get("top_k") or 10),
            require_citations=bool(data.get("require_citations", True)),
            trace_id=str(data.get("trace_id") or f"trace-search-{uuid4().hex[:12]}"),
            filters_applied=dict(data.get("filters_applied") or {}),
        )


@dataclass(frozen=True)
class SearchAccessContext:
    actor_ref: str | None = None
    persona_id: str | None = None
    workspace_id: str | None = None
    role_refs: Sequence[str] = field(default_factory=lambda: ("researcher",))
    environment: str = "paper"
    access_scopes: Sequence[str] = field(default_factory=lambda: ("public",))
    license_scopes: Sequence[str] = field(default_factory=lambda: ("internal", "open"))
    sensitivity_scopes: Sequence[str] = field(default_factory=lambda: ("public", "internal"))
    capital_pool_scopes: Sequence[str] = field(default_factory=tuple)
    entitlements: Sequence[str] = field(default_factory=tuple)
    as_of: datetime | str | None = None
    tenant_id: str = "default"

    def __post_init__(self) -> None:
        object.__setattr__(self, "environment", str(self.environment or "").strip() or "paper")
        object.__setattr__(self, "role_refs", _strings(self.role_refs) or ("researcher",))
        object.__setattr__(self, "access_scopes", _strings(self.access_scopes) or ("public",))
        object.__setattr__(self, "license_scopes", _strings(self.license_scopes) or ("internal", "open"))
        object.__setattr__(self, "sensitivity_scopes", _strings(self.sensitivity_scopes) or ("public", "internal"))
        object.__setattr__(self, "capital_pool_scopes", _strings(self.capital_pool_scopes))
        object.__setattr__(self, "entitlements", _strings(self.entitlements))
        object.__setattr__(self, "tenant_id", str(self.tenant_id or "default").strip() or "default")
        if self.as_of is not None:
            object.__setattr__(self, "as_of", _format_iso(self.as_of))

    def require_persona_workspace(self) -> None:
        if not str(self.persona_id or "").strip() or not str(self.workspace_id or "").strip():
            raise SearchPolicyError("governed search requires persona_id and workspace_id")

    def permits(
        self,
        knowledge_object: KnowledgeObject,
        evidence_item: EvidenceItem | None = None,
        bundle: EvidenceBundle | None = None,
        filters: SearchFilters | None = None,
        now: datetime | None = None,
        require_citations: bool = True,
    ) -> tuple[bool, str | None]:
        # 0. Tenant scope
        metadata = dict(knowledge_object.metadata or {})
        object_tenant = (
            getattr(knowledge_object, "tenant_id", None)
            or metadata.get("tenant_id")
        )
        if object_tenant and str(object_tenant).strip() != self.tenant_id:
            return False, "tenant_scope"

        # 1. Environment scope
        object_environments = set(knowledge_object.environment_scope)
        if object_environments and self.environment not in object_environments:
            return False, "environment"

        # 2. Access scope
        object_scopes = set(knowledge_object.access_scope or ("public",))
        allowed_scopes = set(self.access_scopes)
        if "public" not in object_scopes and object_scopes.isdisjoint(allowed_scopes):
            return False, "access_scope"

        # 3. License scope
        object_license = str(knowledge_object.license_scope or "").strip()
        if object_license:
            if object_license not in set(self.license_scopes):
                return False, "license_scope"
            if filters and filters.license_scopes and object_license not in set(filters.license_scopes):
                return False, "license_scope"

        # 4. Persona scope
        if knowledge_object.persona_scope:
            if str(self.persona_id or "") not in set(knowledge_object.persona_scope):
                return False, "persona_scope"

        # 5. Workspace scope
        if knowledge_object.workspace_scope:
            if str(self.workspace_id or "") not in set(knowledge_object.workspace_scope):
                return False, "workspace_scope"

        # 6. Role scope
        metadata = dict(knowledge_object.metadata or {})
        object_roles = metadata.get("role_scope") or metadata.get("allowed_roles")
        if object_roles:
            role_set = set(_strings(object_roles if isinstance(object_roles, (list, tuple, set)) else [object_roles]))
            if role_set and set(self.role_refs).isdisjoint(role_set):
                return False, "role_scope"

        # 7. Source types (from filters)
        if filters and filters.source_types:
            if knowledge_object.source_type not in set(filters.source_types):
                return False, "source_type"

        # 8. Sensitivity / Data classification
        object_sensitivity = metadata.get("sensitivity") or metadata.get("data_classification")
        if object_sensitivity:
            sens_str = str(object_sensitivity).strip()
            if self.sensitivity_scopes and sens_str not in set(self.sensitivity_scopes):
                return False, "sensitivity"
            if filters and filters.sensitivity and sens_str not in set(filters.sensitivity):
                return False, "sensitivity"

        # 9. Capital pool scope
        object_capital = metadata.get("capital_pool_scope") or metadata.get("capital_pools") or metadata.get("capital_pool")
        if object_capital:
            cap_set = set(_strings(object_capital if isinstance(object_capital, (list, tuple, set)) else [object_capital]))
            if self.capital_pool_scopes and cap_set and set(self.capital_pool_scopes).isdisjoint(cap_set):
                return False, "capital_pool"
            if filters and filters.capital_pool_scope and cap_set and set(filters.capital_pool_scope).isdisjoint(cap_set):
                return False, "capital_pool"

        # 10. Asset class
        object_asset = metadata.get("asset_class") or metadata.get("asset_classes")
        if object_asset and filters and filters.asset_class:
            asset_set = set(_strings(object_asset if isinstance(object_asset, (list, tuple, set)) else [object_asset]))
            if asset_set and set(filters.asset_class).isdisjoint(asset_set):
                return False, "asset_class"

        # 11. Strategy ID
        object_strategy = metadata.get("strategy_id") or metadata.get("strategy_scope")
        if object_strategy and filters and filters.strategy_id:
            strat_set = set(_strings(object_strategy if isinstance(object_strategy, (list, tuple, set)) else [object_strategy]))
            if strat_set and str(filters.strategy_id).strip() not in strat_set:
                return False, "strategy_id"

        # 12. Citations / bundle check
        if require_citations:
            if bundle is None or not bundle.citation_refs:
                return False, "missing_citation"

        # 13. Event time bounds
        if filters and (filters.event_time_gte or filters.event_time_lte):
            raw_event_time = (
                (evidence_item.event_time if evidence_item else None)
                or metadata.get("event_time")
            )
            if raw_event_time is None:
                return False, "event_time"
            parsed_event = _parse_iso_datetime(raw_event_time)
            if parsed_event is None:
                return False, "event_time"
            if filters.event_time_gte:
                gte_dt = _parse_iso_datetime(filters.event_time_gte)
                if gte_dt and parsed_event < gte_dt:
                    return False, "event_time"
            if filters.event_time_lte:
                lte_dt = _parse_iso_datetime(filters.event_time_lte)
                if lte_dt and parsed_event > lte_dt:
                    return False, "event_time"

        # 14. Available time (cutoff & no future leak)
        effective_now = now or datetime.now(timezone.utc)
        if self.as_of:
            as_of_dt = _parse_iso_datetime(self.as_of)
            if as_of_dt:
                effective_now = min(effective_now, as_of_dt)
        cutoff_dt = effective_now
        if filters and filters.available_time_lte:
            user_cutoff = _parse_iso_datetime(filters.available_time_lte)
            if user_cutoff:
                cutoff_dt = min(cutoff_dt, user_cutoff)

        raw_avail = (
            (evidence_item.available_time if evidence_item else None)
            or (bundle.available_time if bundle else None)
            or metadata.get("available_time")
        )
        if raw_avail not in (None, ""):
            parsed_avail = _parse_iso_datetime(raw_avail)
            if parsed_avail is not None and parsed_avail > cutoff_dt:
                return False, "available_time"

        return True, None
