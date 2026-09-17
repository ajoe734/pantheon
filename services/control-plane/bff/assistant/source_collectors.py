"""Assistant context source collectors.

BFF-ASSISTANT-SOURCE-COLLECTOR-SEAM-CORRECTIVE-001: single-owner home for
real, typed assistant-context source collection. Extracted from
``main.py`` following the exact seam pattern established by
BFF-JOURNAL-CONTEXT-SEAM-CORRECTIVE-001 (see
``services/control-plane/bff/agora/interaction/context_resolver.py``):
main.py's former inline collector bodies were deleted in the same change
that introduced this module, and now bind a thin
``_assistant_collect_source`` wrapper at the composition root that closes
over the real runtime collaborators and calls
``collect_assistant_context_source`` here. This module is the *only*
implementation of assistant source collection -- no forwarding wrapper, no
second copy, no fallback duplicate ACL may be added elsewhere.

Every collaborator this module needs (the read store, persona-health
projection, audit event listing, job accessors, docs repo root,
tenant/access-meta helpers, and the generic route-replay callable still
used for ``control_room``/``strategy_health``) is an explicit, injected
dependency carried on :class:`AssistantSourceCollectorDeps`. The module has
zero import-time dependency on ``main.py`` globals, so it can be exercised
directly with fakes/stubs -- see
``services/control-plane/bff/tests/test_assistant_source_collectors_owner.py``.

``persona_health`` reads through the real ``PersonaService`` projection
(``persona_service.build_persona_health_items``) rather than the generic
``_sem_final_generic_list_for_path`` route-replay shortcut main.py used to
depend on for it; ``control_room`` and ``strategy_health`` still go through
the injected generic-path callable because no narrower typed port exists
for them yet (out of scope for this task).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Tuple

from fastapi import HTTPException

from .context_composer import AssistantCollectedSource
from ..models import OperatorIdentity

# Canonical allowlist of repo docs the docs_rag source is permitted to read
# and cite. This is a static, self-contained constant -- it does not depend
# on any main.py runtime state, so it lives directly on this module rather
# than being injected.
_ASSISTANT_DOCS_RAG_ALLOWLIST: Tuple[Tuple[str, str, str], ...] = (
    (
        "existing_architecture_plan",
        "docs/04/pantheon_assistant_kernel_user_2026-05-31/EXISTING_ARCHITECTURE_INTEGRATION_PLAN_2026-06-03.md",
        "Pantheon Management Assistant existing architecture integration plan",
    ),
    (
        "existing_architecture_tasks",
        "docs/04/pantheon_assistant_kernel_user_2026-05-31/EXISTING_ARCHITECTURE_EXECUTION_TASKS_2026-06-03.md",
        "Existing architecture assistant integration execution tasks",
    ),
    (
        "ai_collaboration_guide",
        "AI_COLLABORATION_GUIDE.md",
        "Pantheon AI collaboration and repository workflow guide",
    ),
)


def _default_repo_root() -> Path:
    # services/control-plane/bff/assistant/source_collectors.py -> repo root
    # is four parents up, mirroring loop_inventory.py's canonical
    # `_REPO_ROOT = Path(__file__).resolve().parents[3]` computed one
    # directory shallower (bff/loop_inventory.py).
    return Path(__file__).resolve().parents[4]


def _default_tenant_payload_fn(identity: Any, *, requested_tenant: Optional[str] = None) -> Dict[str, Any]:
    from ..auth.policy import bff_me_tenant_payload

    return bff_me_tenant_payload(identity, requested_tenant=requested_tenant)


def _default_read_roles() -> FrozenSet[str]:
    from ..auth.policy import _READ_ROLES

    return _READ_ROLES


def _default_utc_now() -> str:
    from ..models import utc_now

    return utc_now()


@dataclass
class AssistantSourceCollectorDeps:
    """Explicit collaborators for :func:`collect_assistant_context_source`.

    Fields with no sensible standalone default (they are pure closures over
    main.py's composed runtime state -- the read store, the merged audit
    projector, the dataset-source registry, the generic route-replay
    dispatcher, the app-scoped PersonaService singleton, and the alerts
    builder) are required. Fields with a clearly reusable canonical import
    (tenant payload resolution, read roles, repo root, utc_now) get a lazy
    real-module default, mirroring ``context_resolver.py``'s
    ``if callable(x): x() else: from ... import real_thing`` judgement.
    """

    read_store: Any
    list_governance_audit_events: Callable[..., List[Dict[str, Any]]]
    filter_tenant_records_fn: Callable[[List[Dict[str, Any]], Optional[str]], List[Dict[str, Any]]]
    dataset_surface_status: Callable[..., Dict[str, Any]]
    generic_path_collector: Callable[[str], Optional[Dict[str, Any]]]
    persona_service: Any
    build_operator_alerts_payload: Callable[[str], Dict[str, Any]]
    get_job: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None
    list_jobs: Optional[Callable[[], List[Dict[str, Any]]]] = None
    tenant_payload_fn: Optional[Callable[..., Dict[str, Any]]] = None
    read_roles: Optional[FrozenSet[str]] = None
    repo_root: Optional[Callable[[], Path]] = None
    utc_now: Optional[Callable[[], str]] = None
    docs_allowlist: Tuple[Tuple[str, str, str], ...] = field(default_factory=lambda: _ASSISTANT_DOCS_RAG_ALLOWLIST)

    def __post_init__(self) -> None:
        if self.get_job is None:
            self.get_job = lambda job_id: self.read_store.get_job_bff(job_id)
        if self.list_jobs is None:
            def _list_jobs() -> List[Dict[str, Any]]:
                jobs = self.read_store.list_jobs_bff()
                return sorted(
                    jobs,
                    key=lambda j: str(j.get("created_at") or j.get("submitted_at") or ""),
                    reverse=True,
                )

            self.list_jobs = _list_jobs
        if self.tenant_payload_fn is None:
            self.tenant_payload_fn = _default_tenant_payload_fn
        if self.read_roles is None:
            self.read_roles = _default_read_roles()
        if self.repo_root is None:
            self.repo_root = _default_repo_root
        if self.utc_now is None:
            self.utc_now = _default_utc_now


def _assistant_focus_entity(request: Any) -> Tuple[Optional[str], Optional[str]]:
    focus = getattr(request, "focus", None)
    if focus is not None:
        entity_type = str(getattr(focus, "entity_type", "") or "").strip()
        entity_id = str(getattr(focus, "entity_id", "") or "").strip()
        if entity_type and entity_id:
            return entity_type, entity_id

    selected = getattr(request, "selected_entity", None)
    if selected is None:
        frontend = getattr(request, "frontend", None)
        selected = getattr(frontend, "selected_entity", None) if frontend is not None else None
    if isinstance(selected, dict):
        entity_type = str(
            selected.get("entity_type")
            or selected.get("entityType")
            or selected.get("type")
            or ""
        ).strip()
        entity_id = str(
            selected.get("entity_id")
            or selected.get("entityId")
            or selected.get("id")
            or ""
        ).strip()
        if entity_type and entity_id:
            return entity_type, entity_id
    return None, None


def _assistant_source_access_meta(
    identity: Optional[OperatorIdentity],
    *,
    deps: AssistantSourceCollectorDeps,
) -> Dict[str, Any]:
    roles = list(getattr(identity, "roles", []) or [])
    tenant: Dict[str, Any] = {
        "id": None,
        "allowed_ids": [],
        "scope": "unknown",
    }
    if identity is not None:
        try:
            tenant = deps.tenant_payload_fn(identity, requested_tenant=None)
        except HTTPException:
            tenant = {
                "id": None,
                "allowed_ids": [],
                "scope": "denied",
            }
    return {
        "rbac": {
            "enforced": True,
            "required_roles": sorted(deps.read_roles),
            "actor_roles": roles,
        },
        "tenant": {
            "enforced": True,
            "tenant_id": tenant.get("id"),
            "allowed_tenants": list(tenant.get("allowed_ids") or []),
            "scope": tenant.get("scope") or "unknown",
        },
    }


def _assistant_attach_access_meta(
    payload: Any,
    *,
    source_id: str,
    identity: Optional[OperatorIdentity],
    snapshot_at: str,
    deps: AssistantSourceCollectorDeps,
) -> Dict[str, Any]:
    result = dict(payload) if isinstance(payload, dict) else {"data": payload}
    meta = dict(result.get("meta") if isinstance(result.get("meta"), dict) else {})
    meta.setdefault("snapshot_at", snapshot_at)
    meta.setdefault("surfaces", {source_id: {"status": "ok", "source": "bff_read"}})
    meta["access"] = _assistant_source_access_meta(identity, deps=deps)
    result["meta"] = meta
    return result


def _assistant_tenant_scope(
    identity: Optional[OperatorIdentity],
    *,
    deps: AssistantSourceCollectorDeps,
) -> Dict[str, Any]:
    return _assistant_source_access_meta(identity, deps=deps).get("tenant", {})


def _assistant_filter_tenant_records(
    records: List[Dict[str, Any]],
    identity: Optional[OperatorIdentity],
    *,
    deps: AssistantSourceCollectorDeps,
) -> List[Dict[str, Any]]:
    tenant = _assistant_tenant_scope(identity, deps=deps)
    if tenant.get("scope") == "global":
        return [record for record in records if isinstance(record, dict)]
    return deps.filter_tenant_records_fn(
        [record for record in records if isinstance(record, dict)],
        str(tenant.get("tenant_id") or ""),
    )


def _assistant_filter_payload_tenant(
    payload: Any,
    identity: Optional[OperatorIdentity],
    *,
    deps: AssistantSourceCollectorDeps,
) -> Any:
    if not isinstance(payload, dict):
        return payload
    result = dict(payload)
    for key in ("items", "alerts", "events", "data"):
        value = result.get(key)
        if isinstance(value, list):
            result[key] = _assistant_filter_tenant_records(value, identity, deps=deps)
    return result


def _assistant_unavailable_source(
    source_id: str,
    *,
    href: str,
    snapshot_at: str,
    dataset: str,
    identity: Optional[OperatorIdentity] = None,
    deps: AssistantSourceCollectorDeps,
) -> AssistantCollectedSource:
    surface = deps.dataset_surface_status(dataset, snapshot_at=snapshot_at, source="missing")
    return AssistantCollectedSource(
        source_id=source_id,
        href=href,
        payload=_assistant_attach_access_meta(
            {
                "data": None,
                "meta": {
                    "snapshot_at": snapshot_at,
                    "surfaces": {source_id: surface},
                },
            },
            source_id=source_id,
            identity=identity,
            snapshot_at=snapshot_at,
            deps=deps,
        ),
        status=str(surface.get("status") or "unavailable"),
    )


def _assistant_collect_jobs_source(
    request: Any,
    snapshot_at: str,
    identity: Optional[OperatorIdentity] = None,
    *,
    deps: AssistantSourceCollectorDeps,
) -> AssistantCollectedSource:
    entity_type, entity_id = _assistant_focus_entity(request)
    selected_job = None
    href = "/bff/jobs"
    if entity_type and entity_type.lower() in {"job", "jobs"} and entity_id:
        raw_job = deps.get_job(entity_id)
        if isinstance(raw_job, dict):
            selected_job = next(
                iter(_assistant_filter_tenant_records([raw_job], identity, deps=deps)), None
            )
        href = f"/bff/jobs/{entity_id}"

    jobs = _assistant_filter_tenant_records(deps.list_jobs(), identity, deps=deps)
    surface = deps.dataset_surface_status(
        "jobs",
        snapshot_at=snapshot_at,
        has_data=bool(jobs) or bool(selected_job) or None,
    )
    payload: Dict[str, Any] = {
        "items": jobs[:20],
        "selected": selected_job,
        "page_info": {"next_page_token": None, "total": len(jobs)},
        "meta": {
            "snapshot_at": snapshot_at,
            "surfaces": {"jobs": surface},
        },
    }
    if entity_id and selected_job is None:
        payload["selected_missing"] = {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "reason": "job_not_found_or_not_visible",
        }
    return AssistantCollectedSource(
        source_id="jobs",
        href=href,
        payload=_assistant_attach_access_meta(
            payload,
            source_id="jobs",
            identity=identity,
            snapshot_at=snapshot_at,
            deps=deps,
        ),
        status=str(surface.get("status") or "ok"),
    )


def _assistant_collect_job_logs_source(
    request: Any,
    snapshot_at: str,
    identity: Optional[OperatorIdentity] = None,
    *,
    deps: AssistantSourceCollectorDeps,
) -> Optional[AssistantCollectedSource]:
    entity_type, entity_id = _assistant_focus_entity(request)
    if not entity_id or (entity_type and entity_type.lower() not in {"job", "jobs"}):
        return None
    job = deps.get_job(entity_id)
    if isinstance(job, dict):
        job = next(iter(_assistant_filter_tenant_records([job], identity, deps=deps)), None)
    if job is None:
        return _assistant_unavailable_source(
            "job_logs",
            href=f"/bff/jobs/{entity_id}/logs",
            snapshot_at=snapshot_at,
            dataset="jobs",
            identity=identity,
            deps=deps,
        )
    logs = list(job.get("logs") or [])
    surface = deps.dataset_surface_status("jobs", snapshot_at=snapshot_at, has_data=True)
    return AssistantCollectedSource(
        source_id="job_logs",
        href=f"/bff/jobs/{entity_id}/logs",
        payload=_assistant_attach_access_meta(
            {
                "job_id": entity_id,
                "logs": logs[:50],
                "meta": {
                    "snapshot_at": snapshot_at,
                    "surfaces": {"job_logs": surface},
                },
            },
            source_id="job_logs",
            identity=identity,
            snapshot_at=snapshot_at,
            deps=deps,
        ),
        status=str(surface.get("status") or "ok"),
    )


def _assistant_collect_audit_source(
    request: Any,
    snapshot_at: str,
    identity: Optional[OperatorIdentity] = None,
    *,
    deps: AssistantSourceCollectorDeps,
) -> AssistantCollectedSource:
    entity_type, entity_id = _assistant_focus_entity(request)
    href = "/bff/audit"
    if entity_type and entity_id:
        events = [
            event
            for event in deps.list_governance_audit_events(target_type=entity_type)
            if str(event.get("target_id") or event.get("entity_id") or "") == entity_id
        ]
        href = f"/bff/audit/entities/{entity_type}/{entity_id}"
    else:
        events = deps.list_governance_audit_events()
    events = _assistant_filter_tenant_records(events, identity, deps=deps)
    surface = deps.dataset_surface_status(
        "governance_audit_events",
        snapshot_at=snapshot_at,
        has_data=bool(events) or None,
    )
    return AssistantCollectedSource(
        source_id="audit",
        href=href,
        payload=_assistant_attach_access_meta(
            {
                "items": events[:50],
                "page_info": {"next_page_token": None, "total": len(events)},
                "meta": {
                    "snapshot_at": snapshot_at,
                    "surfaces": {"audit": surface},
                },
            },
            source_id="audit",
            identity=identity,
            snapshot_at=snapshot_at,
            deps=deps,
        ),
        status=str(surface.get("status") or "ok"),
    )


def _assistant_collect_recent_sse_source(
    _request: Any,
    snapshot_at: str,
    identity: Optional[OperatorIdentity] = None,
    *,
    deps: AssistantSourceCollectorDeps,
) -> AssistantCollectedSource:
    events = _assistant_filter_tenant_records(
        deps.read_store.list_events_bff(page_size=25), identity, deps=deps
    )
    surface = deps.dataset_surface_status(
        "governance_audit_events",
        snapshot_at=snapshot_at,
        has_data=bool(events) or None,
    )
    return AssistantCollectedSource(
        source_id="recent_sse",
        href="/bff/events",
        payload=_assistant_attach_access_meta(
            {
                "items": events[:25],
                "page_info": {"next_page_token": None},
                "meta": {
                    "snapshot_at": snapshot_at,
                    "surfaces": {"recent_sse": surface},
                },
            },
            source_id="recent_sse",
            identity=identity,
            snapshot_at=snapshot_at,
            deps=deps,
        ),
        status=str(surface.get("status") or "ok"),
    )


def _assistant_doc_query_terms(request: Any) -> List[str]:
    values: List[str] = []
    for value in (
        getattr(request, "question", None),
        getattr(request, "route", None),
    ):
        if value:
            values.extend(str(value).lower().split())
    frontend = getattr(request, "frontend", None)
    if frontend is not None and getattr(frontend, "route", None):
        values.extend(str(frontend.route).lower().split("/"))
    return [value.strip(".,:;()[]{}").lower() for value in values if len(value.strip(".,:;()[]{}")) > 3]


def _assistant_doc_snippet(text: str, terms: List[str], *, limit: int = 900) -> str:
    compact = " ".join(line.strip() for line in text.splitlines() if line.strip())
    lower = compact.lower()
    start = 0
    for term in terms:
        found = lower.find(term)
        if found >= 0:
            start = max(0, found - 160)
            break
    return compact[start:start + limit]


def _assistant_collect_docs_rag_source(
    request: Any,
    snapshot_at: str,
    identity: Optional[OperatorIdentity] = None,
    *,
    deps: AssistantSourceCollectorDeps,
) -> AssistantCollectedSource:
    root = deps.repo_root()
    terms = _assistant_doc_query_terms(request)
    items: List[Dict[str, Any]] = []
    citations: List[Dict[str, Any]] = []
    source_refs: List[Dict[str, Any]] = []

    for slug, relative_path, title in deps.docs_allowlist:
        path = root / relative_path
        if not path.exists() or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        ref_id = f"doc:{slug}"
        snippet = _assistant_doc_snippet(text, terms)
        citation = {
            "ref_id": ref_id,
            "title": title,
            "path": relative_path,
        }
        items.append({
            "ref_id": ref_id,
            "title": title,
            "path": relative_path,
            "snippet": snippet,
        })
        citations.append(citation)
        source_refs.append({
            "source_id": ref_id,
            "href": relative_path,
            "snapshot_at": snapshot_at,
            "status": "ok",
            "staleness": {
                "status": "fresh",
                "served_from": "repo_doc_allowlist",
                "last_known_at": snapshot_at,
            },
            "source_kind": "docs",
        })

    status = "ok" if items else "unavailable"
    surface = {
        "status": status,
        "source": "repo_doc_allowlist",
    }
    source_refs.insert(0, {
        "source_id": "docs_rag",
        "href": "docs://assistant/context",
        "snapshot_at": snapshot_at,
        "status": status,
        "staleness": {
            "status": "fresh" if items else "unavailable",
            "served_from": "repo_doc_allowlist",
            "last_known_at": snapshot_at,
        },
        "source_kind": "docs",
    })
    return AssistantCollectedSource(
        source_id="docs_rag",
        href="docs://assistant/context",
        payload={
            "items": items,
            "citations": citations,
            "meta": {
                "snapshot_at": snapshot_at,
                "surfaces": {"docs_rag": surface},
                "access": {
                    **_assistant_source_access_meta(identity, deps=deps),
                    "corpus": "repo_doc_allowlist",
                },
            },
        },
        status=status,
        source_kind="docs",
        source_refs=source_refs,
    )


def _assistant_collect_persona_health_source(
    request: Any,
    snapshot_at: str,
    identity: Optional[OperatorIdentity] = None,
    *,
    deps: AssistantSourceCollectorDeps,
) -> AssistantCollectedSource:
    """Real typed persona_health collector.

    Reads via the single app-scoped ``PersonaService.build_persona_health_items``
    projection (the sole implementation of the execution persona-health
    projection) instead of the generic route-replay shortcut, closing the
    identity/tenant/pagination/freshness gap this task's acceptance
    criteria targets.
    """
    persona_surface = deps.dataset_surface_status("personas", snapshot_at=snapshot_at)
    league_surface = deps.dataset_surface_status("persona_league", snapshot_at=snapshot_at)
    health_items = deps.persona_service.build_persona_health_items(snapshot_at)
    payload: Dict[str, Any] = {
        "data": health_items,
        "items": health_items,
        "page_info": {"next_page_token": None, "total": len(health_items)},
        "meta": {
            "snapshot_at": snapshot_at,
            "surfaces": {
                "persona_health": persona_surface,
                "persona_league": league_surface,
            },
        },
    }
    payload = _assistant_filter_payload_tenant(payload, identity, deps=deps)
    return AssistantCollectedSource(
        source_id="persona_health",
        href="/bff/v5/execution/persona-health",
        payload=_assistant_attach_access_meta(
            payload,
            source_id="persona_health",
            identity=identity,
            snapshot_at=snapshot_at,
            deps=deps,
        ),
    )


def collect_assistant_context_source(
    source_id: str,
    request: Any,
    snapshot_at: str,
    identity: Optional[OperatorIdentity] = None,
    *,
    deps: AssistantSourceCollectorDeps,
) -> Optional[AssistantCollectedSource]:
    """Single owner for real typed assistant context source collection.

    Preserves the exact per-``source_id`` payload shapes, meta.surfaces
    keys, access meta, staleness/status derivation, docs allowlist snippet
    extraction, and jobs/job_logs/audit/recent_sse tenant filtering that
    main.py's pre-extraction inline collectors produced. Returns ``None``
    for any unrecognized ``source_id`` (the caller in
    ``context_composer.py`` is responsible for the
    not-allowlisted/collector-unavailable degrade paths).
    """
    if source_id == "control_room":
        payload = deps.generic_path_collector("/bff/v5/control-room")
        if payload is None:
            return _assistant_unavailable_source(
                source_id,
                href="/bff/v5/control-room",
                snapshot_at=snapshot_at,
                dataset="incidents",
                identity=identity,
                deps=deps,
            )
        payload = _assistant_filter_payload_tenant(payload, identity, deps=deps)
        return AssistantCollectedSource(
            source_id=source_id,
            href="/bff/v5/control-room",
            payload=_assistant_attach_access_meta(
                payload,
                source_id=source_id,
                identity=identity,
                snapshot_at=snapshot_at,
                deps=deps,
            ),
        )
    if source_id == "jobs":
        return _assistant_collect_jobs_source(request, snapshot_at, identity, deps=deps)
    if source_id == "alerts":
        payload = _assistant_filter_payload_tenant(
            deps.build_operator_alerts_payload(snapshot_at), identity, deps=deps
        )
        return AssistantCollectedSource(
            source_id=source_id,
            href="/bff/alerts",
            payload=_assistant_attach_access_meta(
                payload,
                source_id=source_id,
                identity=identity,
                snapshot_at=snapshot_at,
                deps=deps,
            ),
        )
    if source_id == "audit":
        return _assistant_collect_audit_source(request, snapshot_at, identity, deps=deps)
    if source_id == "recent_sse":
        return _assistant_collect_recent_sse_source(request, snapshot_at, identity, deps=deps)
    if source_id == "persona_health":
        return _assistant_collect_persona_health_source(request, snapshot_at, identity, deps=deps)
    if source_id == "strategy_health":
        payload = deps.generic_path_collector("/bff/v5/execution/strategy-health")
        if payload is None:
            return _assistant_unavailable_source(
                source_id,
                href="/bff/v5/execution/strategy-health",
                snapshot_at=snapshot_at,
                dataset="strategy_specs",
                identity=identity,
                deps=deps,
            )
        payload = _assistant_filter_payload_tenant(payload, identity, deps=deps)
        return AssistantCollectedSource(
            source_id=source_id,
            href="/bff/v5/execution/strategy-health",
            payload=_assistant_attach_access_meta(
                payload,
                source_id=source_id,
                identity=identity,
                snapshot_at=snapshot_at,
                deps=deps,
            ),
        )
    if source_id == "job_logs":
        return _assistant_collect_job_logs_source(request, snapshot_at, identity, deps=deps)
    if source_id == "docs_rag":
        return _assistant_collect_docs_rag_source(request, snapshot_at, identity, deps=deps)
    return None
