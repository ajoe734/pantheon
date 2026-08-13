from __future__ import annotations

import inspect
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .models import (
    AssistantActorContext,
    AssistantBackendContext,
    AssistantContextPack,
    AssistantContextPackRequest,
    AssistantBffReadSection,
    AssistantDocsRagSection,
    AssistantFrontendContext,
    AssistantInternalDebugContext,
    AssistantMode,
    AssistantOmittedSource,
    AssistantRedactionSummary,
    AssistantSourceRef,
    AssistantUiHintSection,
)
from .redaction import redact_payload


DEFAULT_CONTEXT_SOURCES = (
    "ui",
    "control_room",
    "jobs",
    "alerts",
    "audit",
    "recent_sse",
    "persona_health",
    "strategy_health",
    "docs_rag",
)

ALLOWLISTED_SOURCES = frozenset(
    {
        *DEFAULT_CONTEXT_SOURCES,
        "job_logs",
        "health_probes",
        "sanitized_logs",
        "repo_status",
        "management_nl",
        "docs_rag",
    }
)

KERNEL_ONLY_SOURCES = frozenset(
    {
        "job_logs",
        "health_probes",
        "sanitized_logs",
        "repo_status",
    }
)

SOURCE_ALIASES = {
    "frontend": "ui",
    "front_end": "ui",
    "events": "recent_sse",
    "sse": "recent_sse",
    "persona": "persona_health",
    "strategy": "strategy_health",
    "logs": "job_logs",
    "docs": "docs_rag",
    "rag": "docs_rag",
    "documentation": "docs_rag",
    "docs/rag": "docs_rag",
}

SOURCE_HREFS = {
    "control_room": "/bff/v5/control-room",
    "jobs": "/bff/jobs",
    "alerts": "/bff/alerts",
    "audit": "/bff/audit",
    "recent_sse": "/bff/events",
    "persona_health": "/bff/v5/execution/persona-health",
    "strategy_health": "/bff/v5/execution/strategy-health",
    "job_logs": "/bff/jobs/{job_id}/logs",
    "health_probes": "/bff/healthz",
    "sanitized_logs": "/bff/assistant/internal/sanitized-logs",
    "repo_status": "/bff/assistant/internal/repo-status",
    "management_nl": "/bff/management/nl/ask",
    "docs_rag": "docs://assistant/context",
}

BACKEND_PAYLOAD_SOURCES = frozenset(
    {
        "control_room",
        "jobs",
        "alerts",
        "audit",
        "persona_health",
        "strategy_health",
        "management_nl",
    }
)

DOCS_RAG_SOURCES = frozenset({"docs_rag"})


@dataclass(frozen=True)
class AssistantCollectedSource:
    source_id: str
    href: str
    payload: Any
    status: Optional[str] = None
    snapshot_at: Optional[str] = None
    staleness: Optional[Dict[str, Any]] = None
    source_kind: str = "bff"
    source_refs: Optional[List[Dict[str, Any]]] = None


AssistantSourceCollector = Callable[
    [str, AssistantContextPackRequest, str],
    Optional[AssistantCollectedSource],
]


class AssistantContextPolicyError(ValueError):
    def __init__(self, message: str, *, denied_sources: Optional[List[str]] = None) -> None:
        super().__init__(message)
        self.denied_sources = denied_sources or []


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _model_dump(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=False, exclude_none=True)
    if isinstance(value, dict):
        return dict(value)
    return {}


def _normalize_source_id(source_id: Any) -> str:
    clean = str(source_id or "").strip().lower().replace("-", "_")
    return SOURCE_ALIASES.get(clean, clean)


def requested_source_ids(request: AssistantContextPackRequest) -> List[str]:
    raw_sources = request.include or list(DEFAULT_CONTEXT_SOURCES)
    result: List[str] = []
    seen = set()
    for raw in raw_sources:
        source_id = _normalize_source_id(raw)
        if source_id and source_id not in seen:
            result.append(source_id)
            seen.add(source_id)
    return result or list(DEFAULT_CONTEXT_SOURCES)


def _enforce_mode_policy(mode: AssistantMode, sources: Iterable[str]) -> None:
    if mode != AssistantMode.USER:
        return
    denied = sorted({source for source in sources if source in KERNEL_ONLY_SOURCES})
    if denied:
        raise AssistantContextPolicyError(
            "User-mode assistant context cannot include kernel-only sources",
            denied_sources=denied,
        )


def _merged_frontend_context(request: AssistantContextPackRequest) -> AssistantFrontendContext:
    frontend = request.frontend
    route = request.route or frontend.route or "/"
    selected_entity = request.selected_entity or frontend.selected_entity
    if selected_entity is None and request.focus is not None:
        selected_entity = _model_dump(request.focus)

    visible_errors = list(frontend.visible_errors or [])
    visible_errors.extend(request.visible_errors or [])

    context_refs = list(frontend.context_refs or [])
    context_refs.extend(request.context_refs or [])

    return AssistantFrontendContext(
        route=route,
        selectedEntity=selected_entity,
        visibleErrors=visible_errors,
        contextRefs=context_refs,
    )


def _actor_context(actor: Any) -> AssistantActorContext:
    claims = getattr(actor, "claims", {}) if actor is not None else {}
    if not isinstance(claims, dict):
        claims = {}
    capabilities = claims.get("capabilities") or claims.get("capability") or []
    if isinstance(capabilities, str):
        capabilities = [item.strip() for item in re.split(r"[\s,]+", capabilities) if item.strip()]
    if not isinstance(capabilities, list):
        capabilities = []
    return AssistantActorContext(
        operatorId=str(getattr(actor, "operator_id", None) or "unknown"),
        roles=list(getattr(actor, "roles", []) or []),
        capabilities=[str(item) for item in capabilities],
    )


def _redact(value: Any) -> Tuple[Any, int]:
    result = redact_payload(value)
    summary = result.summary
    return result.value, summary.redacted_fields + summary.redacted_values


def _collect_source(
    collect_source: AssistantSourceCollector,
    source_id: str,
    request: AssistantContextPackRequest,
    snapshot_at: str,
    actor: Any,
) -> Optional[AssistantCollectedSource]:
    try:
        parameters = inspect.signature(collect_source).parameters
    except (TypeError, ValueError):
        parameters = {}
    if len(parameters) >= 4:
        return collect_source(source_id, request, snapshot_at, actor)  # type: ignore[misc]
    return collect_source(source_id, request, snapshot_at)


def _source_ref_from_payload(
    payload: Dict[str, Any],
    *,
    fallback_source_id: str,
    fallback_href: str,
    fallback_snapshot_at: str,
    fallback_status: str,
    fallback_staleness: Dict[str, Any],
    fallback_source_kind: str,
) -> Tuple[AssistantSourceRef, int]:
    raw = dict(payload)
    raw.setdefault("source_id", raw.get("sourceId") or fallback_source_id)
    raw.setdefault("href", fallback_href)
    raw.setdefault("snapshot_at", raw.get("snapshotAt") or fallback_snapshot_at)
    raw.setdefault("status", fallback_status)
    raw.setdefault("staleness", fallback_staleness)
    raw.setdefault("source_kind", raw.get("sourceKind") or fallback_source_kind)
    redacted, count = _redact(raw)
    return AssistantSourceRef(**redacted), count


def _single_source_ref(
    *,
    source_id: str,
    href: str,
    snapshot_at: str,
    status: str,
    staleness: Dict[str, Any],
    source_kind: str,
) -> Tuple[AssistantSourceRef, int]:
    return _source_ref_from_payload(
        {},
        fallback_source_id=source_id,
        fallback_href=href,
        fallback_snapshot_at=snapshot_at,
        fallback_status=status,
        fallback_staleness=staleness,
        fallback_source_kind=source_kind,
    )


def _payload_meta(payload: Any) -> Dict[str, Any]:
    if isinstance(payload, dict) and isinstance(payload.get("meta"), dict):
        return dict(payload["meta"])
    return {}


def _first_surface(meta: Dict[str, Any], source_id: str) -> Dict[str, Any]:
    surfaces = meta.get("surfaces") if isinstance(meta.get("surfaces"), dict) else {}
    candidates = [
        source_id,
        "events" if source_id == "recent_sse" else source_id,
        "governance_audit_events" if source_id == "audit" else source_id,
    ]
    for candidate in candidates:
        surface = surfaces.get(candidate)
        if isinstance(surface, dict):
            return dict(surface)
    for surface in surfaces.values():
        if isinstance(surface, dict):
            return dict(surface)
    return {}


def _source_snapshot_at(collected: AssistantCollectedSource, payload: Any, snapshot_at: str) -> str:
    meta = _payload_meta(payload)
    return str(collected.snapshot_at or meta.get("snapshot_at") or snapshot_at)


def _source_status(collected: AssistantCollectedSource, payload: Any, source_id: str) -> str:
    if collected.status:
        return str(collected.status)
    meta = _payload_meta(payload)
    surface = _first_surface(meta, source_id)
    return str(surface.get("status") or meta.get("status") or ("ok" if payload is not None else "unavailable"))


def _source_staleness(
    collected: AssistantCollectedSource,
    payload: Any,
    source_id: str,
    status: str,
    snapshot_at: str,
) -> Dict[str, Any]:
    if collected.staleness:
        return dict(collected.staleness)
    meta = _payload_meta(payload)
    surface = _first_surface(meta, source_id)
    staleness = surface.get("staleness") or meta.get("staleness")
    if isinstance(staleness, dict):
        result = dict(staleness)
        result.setdefault("status", "stale" if status in {"degraded", "stale"} else status)
        result.setdefault("last_known_at", snapshot_at)
        return result
    if status in {"degraded", "stale"}:
        return {"status": "stale", "served_from": "cache", "last_known_at": snapshot_at}
    if status in {"unavailable", "missing"}:
        return {"status": "unavailable", "served_from": "unverifiable", "last_known_at": snapshot_at}
    return {"status": "fresh", "served_from": "live", "last_known_at": snapshot_at}


def _extract_items(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("items", "events", "data", "logs"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _assign_payload(
    *,
    backend: AssistantBackendContext,
    internal_debug: AssistantInternalDebugContext,
    source_id: str,
    payload: Any,
) -> None:
    if source_id in BACKEND_PAYLOAD_SOURCES:
        setattr(backend, source_id, payload if isinstance(payload, dict) else {"data": payload})
        return
    if source_id == "recent_sse":
        backend.recent_sse = _extract_items(payload)
        return
    if source_id == "job_logs":
        internal_debug.sanitized_logs = _extract_items(payload)
        return
    if source_id == "health_probes":
        internal_debug.health_probes = _extract_items(payload)
        return
    if source_id == "repo_status" and isinstance(payload, dict):
        internal_debug.repo_status = payload


def _assign_docs_payload(docs_rag: AssistantDocsRagSection, payload: Any) -> None:
    if not isinstance(payload, dict):
        return
    items = payload.get("items")
    if isinstance(items, list):
        docs_rag.items = [item for item in items if isinstance(item, dict)]
    citations = payload.get("citations")
    if isinstance(citations, list):
        docs_rag.citations = [item for item in citations if isinstance(item, dict)]


def _source_access(meta: Dict[str, Any]) -> Dict[str, Any]:
    access = meta.get("access")
    return dict(access) if isinstance(access, dict) else {}


def compose_context_pack(
    *,
    session_id: str,
    request: AssistantContextPackRequest,
    actor: Any,
    collect_source: AssistantSourceCollector,
) -> AssistantContextPack:
    snapshot_at = utc_now()
    frontend = _merged_frontend_context(request)
    frontend_payload, frontend_redacted_count = _redact(
        frontend.model_dump(mode="json", by_alias=False)
    )
    frontend = AssistantFrontendContext(**frontend_payload)
    sources = requested_source_ids(request)
    _enforce_mode_policy(request.mode, sources)

    backend = AssistantBackendContext()
    internal_debug = AssistantInternalDebugContext()
    docs_rag = AssistantDocsRagSection()
    source_refs: List[AssistantSourceRef] = []
    ui_source_refs: List[AssistantSourceRef] = []
    bff_source_refs: List[AssistantSourceRef] = []
    docs_source_refs: List[AssistantSourceRef] = []
    bff_access: Dict[str, Any] = {}
    omitted: List[AssistantOmittedSource] = []
    redacted_fields = frontend_redacted_count
    actor_context = _actor_context(actor)

    for source_id in sources:
        if source_id not in ALLOWLISTED_SOURCES:
            omitted.append(
                AssistantOmittedSource(
                    sourceId=source_id,
                    reason="not_allowlisted",
                    message=f"Source {source_id!r} is not in the assistant context allowlist.",
                )
            )
            continue

        if source_id == "ui":
            ref, count = _single_source_ref(
                source_id="ui",
                href=frontend.route,
                snapshot_at=snapshot_at,
                status="ok",
                staleness={"status": "fresh", "served_from": "frontend", "last_known_at": snapshot_at},
                source_kind="frontend",
            )
            redacted_fields += count
            source_refs.append(ref)
            ui_source_refs.append(ref)
            continue

        collected = _collect_source(collect_source, source_id, request, snapshot_at, actor)
        if collected is None:
            omitted.append(
                AssistantOmittedSource(
                    sourceId=source_id,
                    reason="collector_unavailable",
                    message=f"Source {source_id!r} is allowed but no collector is registered.",
                )
            )
            continue

        payload, count = _redact(collected.payload)
        redacted_fields += count
        source_snapshot_at = _source_snapshot_at(collected, payload, snapshot_at)
        status = _source_status(collected, payload, source_id)
        staleness = _source_staleness(collected, payload, source_id, status, source_snapshot_at)
        meta = _payload_meta(payload)

        if source_id in DOCS_RAG_SOURCES or collected.source_kind == "docs":
            _assign_docs_payload(docs_rag, payload)
        else:
            _assign_payload(
                backend=backend,
                internal_debug=internal_debug,
                source_id=source_id,
                payload=payload,
            )
            if collected.source_kind == "bff":
                access = _source_access(meta)
                if access:
                    bff_access[source_id] = access

        raw_source_refs = collected.source_refs or [{}]
        for raw_ref in raw_source_refs:
            ref, count = _source_ref_from_payload(
                raw_ref,
                fallback_source_id=source_id,
                fallback_href=collected.href or SOURCE_HREFS.get(source_id, f"/bff/{source_id}"),
                fallback_snapshot_at=source_snapshot_at,
                fallback_status=status,
                fallback_staleness=staleness,
                fallback_source_kind=collected.source_kind,
            )
            redacted_fields += count
            source_refs.append(ref)
            if ref.source_kind == "frontend":
                ui_source_refs.append(ref)
            elif ref.source_kind == "docs":
                docs_source_refs.append(ref)
            else:
                bff_source_refs.append(ref)

    return AssistantContextPack(
        contextPackId=f"ctx_{uuid.uuid4().hex[:16]}",
        sessionId=session_id,
        mode=request.mode,
        question=request.question,
        actor=actor_context,
        snapshotAt=snapshot_at,
        frontend=frontend,
        backend=backend,
        internalDebug=internal_debug,
        sources=source_refs,
        sourceRefs=source_refs,
        uiHints=AssistantUiHintSection(
            context=frontend,
            sourceRefs=ui_source_refs,
        ),
        bffReads=AssistantBffReadSection(
            context=backend,
            sourceRefs=bff_source_refs,
            access=bff_access,
        ),
        docsRag=AssistantDocsRagSection(
            items=docs_rag.items,
            citations=docs_rag.citations,
            sourceRefs=docs_source_refs,
        ),
        redaction=AssistantRedactionSummary(redactedFields=redacted_fields),
        omittedSources=omitted,
    )
