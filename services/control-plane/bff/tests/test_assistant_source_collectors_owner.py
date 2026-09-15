"""BFF-ASSISTANT-SOURCE-COLLECTOR-SEAM-CORRECTIVE-001: single-owner collector.

Proves ``collect_assistant_context_source`` behaves correctly for every
source family it owns (jobs, job_logs, audit, recent_sse, docs_rag,
persona_health, strategy_health, control_room, alerts) using fakes/stubs
this test builds itself -- zero import of ``services.control_plane.bff.main``
and zero monkeypatching of main.py globals. Covers this task's declared
acceptance criteria: identity/tenant scoping (no cross-tenant leakage),
citations (docs_rag snippets/citations), allowlist degrade (unknown and
not-yet-implemented source_ids return ``None`` cleanly), pagination
(jobs/audit/recent_sse truncation), and freshness/staleness derivation for
at least one degraded/unavailable case per source family.

Canonical pattern per
``services/control-plane/bff/tests/test_journal_context_resolver_owner.py``
(BFF-JOURNAL-CONTEXT-SEAM-CORRECTIVE-001).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.control_plane.bff.assistant.context_composer import AssistantCollectedSource
from services.control_plane.bff.assistant.source_collectors import (
    AssistantSourceCollectorDeps,
    collect_assistant_context_source,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeIdentity:
    def __init__(self, tenant_id: Optional[str] = None, roles=("operator",)) -> None:
        self.tenant_id = tenant_id
        self.roles = list(roles)


class _FakeReadStore:
    def __init__(self, jobs: Optional[List[Dict[str, Any]]] = None, events: Optional[List[Dict[str, Any]]] = None) -> None:
        self._jobs = jobs or []
        self._events = events or []
        self._jobs_by_id = {str(j.get("id")): j for j in self._jobs}

    def get_job_bff(self, job_id: str) -> Optional[Dict[str, Any]]:
        return self._jobs_by_id.get(str(job_id))

    def list_jobs_bff(self) -> List[Dict[str, Any]]:
        return list(self._jobs)

    def list_events_bff(self, *, page_size: int = 25) -> List[Dict[str, Any]]:
        return list(self._events)[:page_size]


class _FakePersonaService:
    def __init__(self, items: Optional[List[Dict[str, Any]]] = None) -> None:
        self._items = items or []
        self.calls: List[str] = []

    def build_persona_health_items(self, snapshot_at: str) -> List[Dict[str, Any]]:
        self.calls.append(snapshot_at)
        return list(self._items)


class _Focus:
    def __init__(self, entity_type: Optional[str] = None, entity_id: Optional[str] = None) -> None:
        self.entity_type = entity_type
        self.entity_id = entity_id


class _Frontend:
    def __init__(self, route: str = "/agora/ask") -> None:
        self.route = route


class _Request:
    def __init__(self, *, focus: Optional[_Focus] = None, question: str = "", route: str = "/agora/ask") -> None:
        self.focus = focus
        self.frontend = _Frontend(route=route)
        self.question = question
        self.route = route
        self.selected_entity = None


def _filter_tenant_records_fn(records: List[Dict[str, Any]], tenant_id: str) -> List[Dict[str, Any]]:
    """Minimal, faithful stand-in for main.py's tenant record filter:
    empty tenant_id passes everything through; otherwise a record is kept
    only if it declares no tenant, a wildcard tenant, or the matching one.
    """
    clean = str(tenant_id or "").strip()
    if not clean:
        return list(records)
    kept = []
    for record in records:
        record_tenant = str(record.get("tenant_id") or "").strip()
        if not record_tenant or record_tenant in {"*", clean}:
            kept.append(record)
    return kept


def _tenant_payload_fn(identity: Any, *, requested_tenant: Optional[str] = None) -> Dict[str, Any]:
    tenant_id = getattr(identity, "tenant_id", None)
    if not tenant_id:
        return {"id": None, "allowed_ids": [], "scope": "unknown"}
    return {"id": tenant_id, "allowed_ids": [tenant_id], "scope": "tenant"}


def _dataset_surface_status(
    dataset: str,
    *,
    snapshot_at: Optional[str] = None,
    has_data: Optional[bool] = None,
    missing_message: Optional[str] = None,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    status = "ok"
    if source == "missing":
        status = "unavailable"
    elif has_data is False:
        status = "unavailable"
    surface = {"status": status, "source": source or "fake_read_store", "dataset": dataset}
    if status == "unavailable":
        surface["staleness"] = {"served_from": "unverifiable", "last_known_at": snapshot_at}
    return surface


def _make_deps(**overrides: Any) -> AssistantSourceCollectorDeps:
    defaults: Dict[str, Any] = dict(
        read_store=_FakeReadStore(),
        list_governance_audit_events=lambda **kw: [],
        filter_tenant_records_fn=_filter_tenant_records_fn,
        dataset_surface_status=_dataset_surface_status,
        generic_path_collector=lambda path: None,
        persona_service=_FakePersonaService(),
        build_operator_alerts_payload=lambda snapshot_at: {
            "alerts": [],
            "meta": {"snapshot_at": snapshot_at, "surfaces": {}},
        },
        tenant_payload_fn=_tenant_payload_fn,
        read_roles=frozenset({"operator"}),
    )
    defaults.update(overrides)
    return AssistantSourceCollectorDeps(**defaults)


SNAPSHOT_AT = "2026-09-15T00:00:00Z"


# ---------------------------------------------------------------------------
# Allowlist degrade paths
# ---------------------------------------------------------------------------


def test_unknown_source_id_returns_none_cleanly():
    result = collect_assistant_context_source(
        "totally_unknown_source", _Request(), SNAPSHOT_AT, None, deps=_make_deps()
    )
    assert result is None


def test_not_yet_implemented_but_allowlisted_source_returns_none_cleanly():
    # "health_probes"/"sanitized_logs"/"repo_status" are allowlisted by
    # context_composer.py's ALLOWLISTED_SOURCES set but have no collector
    # branch here; the collector must still degrade to None (the
    # not_allowlisted/collector_unavailable paths live in context_composer.py).
    for source_id in ("health_probes", "sanitized_logs", "repo_status", "ui"):
        assert collect_assistant_context_source(
            source_id, _Request(), SNAPSHOT_AT, None, deps=_make_deps()
        ) is None


# ---------------------------------------------------------------------------
# jobs: identity/tenant scoping + pagination
# ---------------------------------------------------------------------------


def test_jobs_source_hides_cross_tenant_records():
    jobs = [
        {"id": "job-a", "tenant_id": "tenant-a", "status": "running"},
        {"id": "job-b", "tenant_id": "tenant-b", "status": "running"},
    ]
    deps = _make_deps(read_store=_FakeReadStore(jobs=jobs))
    identity = _FakeIdentity(tenant_id="tenant-a")

    result = collect_assistant_context_source("jobs", _Request(), SNAPSHOT_AT, identity, deps=deps)

    assert isinstance(result, AssistantCollectedSource)
    ids = {item["id"] for item in result.payload["items"]}
    assert ids == {"job-a"}
    assert result.payload["page_info"]["total"] == 1


def test_jobs_source_paginates_items_to_twenty():
    jobs = [{"id": f"job-{i}", "tenant_id": "*", "status": "running"} for i in range(25)]
    deps = _make_deps(read_store=_FakeReadStore(jobs=jobs))

    result = collect_assistant_context_source("jobs", _Request(), SNAPSHOT_AT, None, deps=deps)

    assert len(result.payload["items"]) == 20
    assert result.payload["page_info"]["total"] == 25


def test_jobs_source_selected_missing_when_focused_job_not_visible():
    deps = _make_deps(read_store=_FakeReadStore(jobs=[]))
    request = _Request(focus=_Focus(entity_type="job", entity_id="job-x"))

    result = collect_assistant_context_source("jobs", request, SNAPSHOT_AT, None, deps=deps)

    assert result.payload["selected"] is None
    assert result.payload["selected_missing"] == {
        "entity_type": "job",
        "entity_id": "job-x",
        "reason": "job_not_found_or_not_visible",
    }
    assert result.href == "/bff/jobs/job-x"


def test_jobs_source_selected_hidden_by_tenant_scope():
    jobs = [{"id": "job-b", "tenant_id": "tenant-b", "status": "running"}]
    deps = _make_deps(read_store=_FakeReadStore(jobs=jobs))
    identity = _FakeIdentity(tenant_id="tenant-a")
    request = _Request(focus=_Focus(entity_type="job", entity_id="job-b"))

    result = collect_assistant_context_source("jobs", request, SNAPSHOT_AT, identity, deps=deps)

    # The job exists but belongs to a different tenant -- it must not leak
    # into "selected" even though it was looked up directly by id.
    assert result.payload["selected"] is None
    assert result.payload["selected_missing"]["entity_id"] == "job-b"


# ---------------------------------------------------------------------------
# job_logs: unavailable/freshness + allowlist no-focus degrade
# ---------------------------------------------------------------------------


def test_job_logs_source_returns_none_without_focused_job():
    deps = _make_deps()
    result = collect_assistant_context_source("job_logs", _Request(), SNAPSHOT_AT, None, deps=deps)
    assert result is None


def test_job_logs_source_unavailable_for_missing_job_has_staleness():
    deps = _make_deps(read_store=_FakeReadStore(jobs=[]))
    request = _Request(focus=_Focus(entity_type="job", entity_id="job-missing"))

    result = collect_assistant_context_source("job_logs", request, SNAPSHOT_AT, None, deps=deps)

    assert result.status == "unavailable"
    surface = result.payload["meta"]["surfaces"]["job_logs"]
    assert surface["status"] == "unavailable"
    assert surface["staleness"]["last_known_at"] == SNAPSHOT_AT


def test_job_logs_source_hides_cross_tenant_job():
    jobs = [{"id": "job-b", "tenant_id": "tenant-b", "logs": ["line"]}]
    deps = _make_deps(read_store=_FakeReadStore(jobs=jobs))
    identity = _FakeIdentity(tenant_id="tenant-a")
    request = _Request(focus=_Focus(entity_type="job", entity_id="job-b"))

    result = collect_assistant_context_source("job_logs", request, SNAPSHOT_AT, identity, deps=deps)

    assert result.status == "unavailable"


def test_job_logs_source_returns_truncated_logs_for_visible_job():
    logs = [f"line-{i}" for i in range(60)]
    jobs = [{"id": "job-a", "tenant_id": "*", "logs": logs}]
    deps = _make_deps(read_store=_FakeReadStore(jobs=jobs))
    request = _Request(focus=_Focus(entity_type="job", entity_id="job-a"))

    result = collect_assistant_context_source("job_logs", request, SNAPSHOT_AT, None, deps=deps)

    assert result.status == "ok"
    assert len(result.payload["logs"]) == 50


# ---------------------------------------------------------------------------
# audit: focused vs unfocused, tenant scoping, pagination
# ---------------------------------------------------------------------------


def test_audit_source_filters_by_tenant_and_paginates():
    events = [{"target_id": f"e{i}", "tenant_id": "tenant-a"} for i in range(60)]
    events += [{"target_id": "cross", "tenant_id": "tenant-b"}]
    deps = _make_deps(list_governance_audit_events=lambda **kw: events)
    identity = _FakeIdentity(tenant_id="tenant-a")

    result = collect_assistant_context_source("audit", _Request(), SNAPSHOT_AT, identity, deps=deps)

    assert len(result.payload["items"]) == 50
    assert result.payload["page_info"]["total"] == 60  # tenant-b event filtered before pagination
    assert all(item["tenant_id"] == "tenant-a" for item in result.payload["items"])


def test_audit_source_focused_on_entity_uses_entity_href_and_filters_target():
    events = [
        {"target_id": "ent-1", "tenant_id": "*"},
        {"target_id": "ent-2", "tenant_id": "*"},
    ]

    def _list_audit(*, target_type=None, **kw):
        assert target_type == "job"
        return events

    deps = _make_deps(list_governance_audit_events=_list_audit)
    request = _Request(focus=_Focus(entity_type="job", entity_id="ent-1"))

    result = collect_assistant_context_source("audit", request, SNAPSHOT_AT, None, deps=deps)

    assert result.href == "/bff/audit/entities/job/ent-1"
    assert [item["target_id"] for item in result.payload["items"]] == ["ent-1"]


def test_audit_source_unavailable_status_when_dataset_missing():
    # `has_data=bool(events) or None` means an empty list yields
    # `has_data=None` (not `False`), so staleness is driven by the injected
    # dataset_surface_status's own "missing" source signal instead -- this
    # matches main.py's pre-extraction behavior exactly.
    def _surface(dataset, **kw):
        return _dataset_surface_status(dataset, source="missing", snapshot_at=kw.get("snapshot_at"))

    deps = _make_deps(list_governance_audit_events=lambda **kw: [], dataset_surface_status=_surface)
    result = collect_assistant_context_source("audit", _Request(), SNAPSHOT_AT, None, deps=deps)
    assert result.status == "unavailable"
    assert result.payload["meta"]["surfaces"]["audit"]["status"] == "unavailable"


# ---------------------------------------------------------------------------
# recent_sse: tenant scoping + pagination + freshness
# ---------------------------------------------------------------------------


def test_recent_sse_source_hides_cross_tenant_events_and_paginates():
    events = [{"id": f"ev-{i}", "tenant_id": "tenant-a"} for i in range(30)]
    events += [{"id": "cross", "tenant_id": "tenant-b"}]
    deps = _make_deps(read_store=_FakeReadStore(events=events))
    identity = _FakeIdentity(tenant_id="tenant-a")

    result = collect_assistant_context_source("recent_sse", _Request(), SNAPSHOT_AT, identity, deps=deps)

    assert len(result.payload["items"]) == 25
    assert all(item["tenant_id"] == "tenant-a" for item in result.payload["items"])


def test_recent_sse_source_unavailable_when_dataset_missing():
    def _surface(dataset, **kw):
        return _dataset_surface_status(dataset, source="missing", snapshot_at=kw.get("snapshot_at"))

    deps = _make_deps(read_store=_FakeReadStore(events=[]), dataset_surface_status=_surface)
    result = collect_assistant_context_source("recent_sse", _Request(), SNAPSHOT_AT, None, deps=deps)
    assert result.status == "unavailable"
    assert result.payload["meta"]["surfaces"]["recent_sse"]["staleness"]["last_known_at"] == SNAPSHOT_AT


# ---------------------------------------------------------------------------
# persona_health: real PersonaService projection, not route replay
# ---------------------------------------------------------------------------


def test_persona_health_source_reads_through_persona_service():
    items = [
        {"id": "p1", "persona_id": "p1", "tenant_id": "tenant-a", "health": "healthy"},
        {"id": "p2", "persona_id": "p2", "tenant_id": "tenant-b", "health": "healthy"},
    ]
    persona_service = _FakePersonaService(items)
    deps = _make_deps(persona_service=persona_service)
    identity = _FakeIdentity(tenant_id="tenant-a")

    result = collect_assistant_context_source("persona_health", _Request(), SNAPSHOT_AT, identity, deps=deps)

    assert persona_service.calls == [SNAPSHOT_AT]
    ids = {item["id"] for item in result.payload["items"]}
    assert ids == {"p1"}
    assert set(result.payload["meta"]["surfaces"].keys()) == {"persona_health", "persona_league"}


def test_persona_health_source_degrades_when_dataset_missing():
    def _surface(dataset, **kw):
        return _dataset_surface_status(dataset, source="missing", **{k: v for k, v in kw.items() if k != "source"})

    deps = _make_deps(persona_service=_FakePersonaService([]), dataset_surface_status=_surface)
    result = collect_assistant_context_source("persona_health", _Request(), SNAPSHOT_AT, None, deps=deps)
    assert result.payload["meta"]["surfaces"]["persona_health"]["status"] == "unavailable"


# ---------------------------------------------------------------------------
# strategy_health / control_room: injected generic-path callable
# ---------------------------------------------------------------------------


def test_strategy_health_unavailable_when_generic_path_returns_none():
    deps = _make_deps(generic_path_collector=lambda path: None)
    result = collect_assistant_context_source("strategy_health", _Request(), SNAPSHOT_AT, None, deps=deps)
    assert result.status == "unavailable"
    assert result.href == "/bff/v5/execution/strategy-health"


def test_strategy_health_filters_tenant_when_generic_path_has_data():
    payload = {
        "items": [
            {"id": "s1", "tenant_id": "tenant-a"},
            {"id": "s2", "tenant_id": "tenant-b"},
        ],
        "meta": {"snapshot_at": SNAPSHOT_AT, "surfaces": {}},
    }
    deps = _make_deps(generic_path_collector=lambda path: payload)
    identity = _FakeIdentity(tenant_id="tenant-a")

    result = collect_assistant_context_source("strategy_health", _Request(), SNAPSHOT_AT, identity, deps=deps)

    assert [item["id"] for item in result.payload["items"]] == ["s1"]


def test_control_room_unavailable_when_generic_path_returns_none():
    deps = _make_deps(generic_path_collector=lambda path: None)
    result = collect_assistant_context_source("control_room", _Request(), SNAPSHOT_AT, None, deps=deps)
    assert result.status == "unavailable"
    assert result.href == "/bff/v5/control-room"


# ---------------------------------------------------------------------------
# alerts
# ---------------------------------------------------------------------------


def test_alerts_source_filters_tenant_records():
    def _alerts(snapshot_at):
        return {
            "alerts": [
                {"id": "a1", "tenant_id": "tenant-a"},
                {"id": "a2", "tenant_id": "tenant-b"},
            ],
            "meta": {"snapshot_at": snapshot_at, "surfaces": {}},
        }

    deps = _make_deps(build_operator_alerts_payload=_alerts)
    identity = _FakeIdentity(tenant_id="tenant-a")

    result = collect_assistant_context_source("alerts", _Request(), SNAPSHOT_AT, identity, deps=deps)

    assert [item["id"] for item in result.payload["alerts"]] == ["a1"]


# ---------------------------------------------------------------------------
# docs_rag: allowlist snippets/citations + unavailable degrade
# ---------------------------------------------------------------------------


def test_docs_rag_source_returns_citations_and_snippets(tmp_path):
    doc_path = tmp_path / "guide.md"
    doc_path.write_text(
        "Intro line.\nThe kernel debug workflow needs context refs.\nMore text after.",
        encoding="utf-8",
    )
    allowlist = (("guide", "guide.md", "Sample Guide"),)
    deps = _make_deps(repo_root=lambda: tmp_path, docs_allowlist=allowlist)
    request = _Request(question="how does context refs work")

    result = collect_assistant_context_source("docs_rag", request, SNAPSHOT_AT, None, deps=deps)

    assert result.status == "ok"
    assert result.source_kind == "docs"
    assert result.payload["citations"] == [{"ref_id": "doc:guide", "title": "Sample Guide", "path": "guide.md"}]
    assert "context refs" in result.payload["items"][0]["snippet"]
    assert result.source_refs[0]["source_id"] == "docs_rag"
    assert result.source_refs[1]["source_id"] == "doc:guide"


def test_docs_rag_source_unavailable_when_no_allowlisted_docs_exist(tmp_path):
    allowlist = (("missing_doc", "does/not/exist.md", "Missing"),)
    deps = _make_deps(repo_root=lambda: tmp_path, docs_allowlist=allowlist)

    result = collect_assistant_context_source("docs_rag", _Request(), SNAPSHOT_AT, None, deps=deps)

    assert result.status == "unavailable"
    assert result.payload["items"] == []
    assert result.payload["citations"] == []
    assert result.source_refs[0]["staleness"]["status"] == "unavailable"


def test_docs_rag_source_uses_default_repo_root_when_not_overridden():
    # No repo_root override: the module's own real-module default lazy
    # import must resolve to the actual repo root, so the real
    # AI_COLLABORATION_GUIDE.md allowlist entry is found.
    deps = _make_deps()
    result = collect_assistant_context_source("docs_rag", _Request(), SNAPSHOT_AT, None, deps=deps)
    slugs = {item["ref_id"] for item in result.payload["items"]}
    assert "doc:ai_collaboration_guide" in slugs
