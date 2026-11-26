"""Cited evidence list: ``GET /bff/management/evidence?scope=cited``.

Operator decision 2026-11-25 (RC6): the evidence library lists only the
references that decisions, meetings, approvals and research actually cite.
Raw ingest stays in its data source.

Citations come only from recorded fields, read through existing owner reads
with the caller's identity and tenant:

=====================  =========================================================
source                 recorded field (owner read)
=====================  =========================================================
meeting_proposals      proposal ``citations`` (GET /api/consult/meetings/proposals)
meeting_positions      ``rounds[].positions[].citations`` of the 30 newest
                       meetings (GET /api/consult/meetings, then one detail read
                       per meeting, at most 4 in flight)
governance_approvals   decision ``evidence_refs`` (GET /api/governance/approvals)
evolution_proposals    proposal ``evidence_refs`` (GET /api/evolution/proposals)
research_demands       ``retrieval_influence.metadata.cited_item_ids``
                       (GET /api/research/persona-demands)
research_tickets       submission ``evidence_refs`` and
                       ``human_input.evidence_refs`` (GET /api/research/tickets)
persona_cycles         ``persona_cycle.decisions[].cited_case_file_item_ids``
                       (shared OODA owner port)
=====================  =========================================================

A case-file item that was only supplied (never cited) is never listed. A
``facts:meeting_topic:*`` reference is the meeting itself, not evidence.
Private persona memory and trader items are always withheld (the meetings
withholding rule and the case-file projection), other-tenant rows are dropped,
and every listed reference passes ``redact_evidence_refs``. Memory fails
closed: any memory reference (``lessons:*``, ``*:memory_*:*``, bare ``mem-*``)
is withheld and counted unless the recorded case-file item it cites proves the
shared ``persona_and_committee`` scope.

Labels are recorded titles or display names only; the BFF composes no
sentences. A registry artifact or member virtual book is named after the
strategy that the Registry owner records for that artifact (one bounded owner
read per distinct artifact id on the visible page) and links to
``/management/artifacts/{artifact}``; an artifact id is never used as a
strategy id or under ``/management/strategies/``. ``cited_by[].parts`` carries the structured pieces (meeting name,
round, position, kind, persona) for the frontend to translate. Routes point
only at management pages that exist (frontend ``src/App.tsx``); anything else
is ``null``.

Nothing is stored: no new store, writer, index or cross-request cache.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout, wait as futures_wait
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import quote

from services.control_plane.bff.core.owner_reads import (
    remaining_management_read_budget,
    submit_owner_read,
)

log = logging.getLogger(__name__)

MAX_REFERENCES = 500
MAX_ITEM_READS = 20
MEETING_DETAIL_CAP = 30
MEETING_DETAIL_PARALLEL = 4
SOURCE_RESERVE_SECONDS = 0.25

SOURCE_KEYS: Tuple[str, ...] = (
    "meeting_proposals",
    "meeting_positions",
    "governance_approvals",
    "evolution_proposals",
    "research_demands",
    "research_tickets",
    "persona_cycles",
)

# One shared pool per process; each request submits at most one task per source.
_SOURCE_EXECUTOR = ThreadPoolExecutor(max_workers=len(SOURCE_KEYS) + 1, thread_name_prefix="bff-cited-source")
# Meeting details: at most MEETING_DETAIL_PARALLEL owner reads in flight.
_MEETING_DETAIL_EXECUTOR = ThreadPoolExecutor(
    max_workers=MEETING_DETAIL_PARALLEL, thread_name_prefix="bff-cited-meeting-detail"
)
# ko-evi item reads for the visible page.
_ITEM_READ_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="bff-cited-item-read")

_PRIVATE_TOKENS = ("persona_private", "private_memory", "private_user", "user_private")
_TRADER_TOKENS = ("agora", "trader")


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CitedReadContext:
    identity: Any
    tenant_id: str
    is_operator: bool
    caller_user_id: str
    read_store: Any
    snapshot_at: str


@dataclass
class Citation:
    """One recorded citation of ``ref`` by one entity.

    ``label`` is the citing entity's recorded title (or display name) only, or
    ``None``; ``parts`` holds structured pieces for the frontend to render.
    """

    ref: Any
    entity_type: str
    entity_ref: str
    label: Optional[str]
    route_href: Optional[str]
    time: Optional[str]
    item: Optional[Mapping[str, Any]] = None
    parts: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SourceResult:
    citations: List[Citation] = field(default_factory=list)
    read_count: int = 0
    truncated: bool = False
    failed_count: int = 0
    calls: int = 1
    response_bytes: Optional[int] = None
    withheld_count: int = 0
    reason: Optional[str] = None


class SourceDenied(Exception):
    """The owner refused this caller (401/403): shown as ``denied``."""

    def __init__(self, reason: str = "owner_denied") -> None:
        super().__init__(reason)
        self.reason = reason


class SourceUnavailable(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


SourceReader = Callable[[CitedReadContext], SourceResult]


# ---------------------------------------------------------------------------
# Reference parsing
# ---------------------------------------------------------------------------


@dataclass
class ParsedRef:
    ref_id: str
    ref_kind: str
    source_owner: Optional[str]
    source_id: str
    raw: str
    private: bool = False
    trader: bool = False


_GOVERNANCE_REF_OWNERS = {
    "telemetry_summary": "telemetry",
    "drift_report": "telemetry",
    "evaluator_result": "evaluation",
    "critic_finding": "evaluation",
    "audit_log_entry": "audit",
    "committee_memo": "consultation",
    "manual_review_ticket": "governance",
    "service_handoff": "governance",
    "registry_entry": "registry",
    "research_note": "research",
}


def _bytes_of(value: Any) -> int:
    try:
        return len(json.dumps(value, default=str, separators=(",", ":")).encode("utf-8"))
    except Exception:
        return 0


def _flags(*texts: str) -> Tuple[bool, bool]:
    low = " ".join(t.lower() for t in texts if t)
    return any(t in low for t in _PRIVATE_TOKENS), any(t in low for t in _TRADER_TOKENS)


def _parse_string_ref(raw: str, *, kind_hint: Optional[str] = None) -> Optional[ParsedRef]:
    text = str(raw or "").strip()
    if not text:
        return None
    parts = text.split(":", 2)
    if len(parts) == 3 and parts[0] and parts[1] and parts[2] and "://" not in text:
        group, kind, source_id = parts
        if group == "facts" and kind == "meeting_topic":
            return None  # the meeting itself, not evidence
        private, trader = _flags(group, kind)
        if group == "facts" and kind == "registry_entry":
            return ParsedRef(source_id, "registry_artifact", "registry", source_id, text, private, trader)
        if group == "facts" and kind == "strategy_pool":
            return ParsedRef(text, "strategy_pool", "persona", source_id, text, private, trader)
        if group == "facts" and kind == "member_virtual_book":
            return ParsedRef(text, "member_virtual_book", "telemetry", source_id, text, private, trader)
        if kind == "search_excerpt" or group == "evidence":
            rk = "knowledge_object" if source_id.startswith("ko-") else "evidence_item"
            return ParsedRef(text, rk, "search", source_id, text, private, trader)
        if kind.startswith("memory_") or group == "lessons":
            return ParsedRef(text, "memory", "memory", source_id, text, private, trader)
        return ParsedRef(text, kind, None, source_id, text, private, trader)
    private, trader = _flags(text, kind_hint or "")
    if "memory" in str(kind_hint or "").lower() or str(kind_hint or "").lower() == "lessons":
        return ParsedRef(text, "memory", "memory", text, text, private, trader)
    if text.startswith("artifact-"):
        return ParsedRef(text, "registry_artifact", "registry", text, text, private, trader)
    if text.startswith("ko-"):
        return ParsedRef(text, "knowledge_object", "search", text, text, private, trader)
    if text.startswith("evi-") or text.startswith("evref-"):
        return ParsedRef(text, "evidence_item", "source_ingest", text, text, private, trader)
    if text.startswith("mem-"):
        return ParsedRef(text, "memory", "memory", text, text, private, trader)
    if text.startswith("note-"):
        return ParsedRef(text, "research_note", "research", text, text, private, trader)
    kind = str(kind_hint or "").strip() or "reference"
    return ParsedRef(text, kind, _GOVERNANCE_REF_OWNERS.get(kind), text, text, private, trader)


def parse_ref(raw: Any) -> Optional[ParsedRef]:
    """Parse one recorded citation (string or owner ref dict); ``None`` = not evidence."""
    if isinstance(raw, Mapping):
        ref_id = str(
            raw.get("ref_id") or raw.get("artifact_id") or raw.get("item_id") or raw.get("id") or ""
        ).strip()
        kind = str(raw.get("ref_type") or raw.get("kind") or raw.get("evidence_type") or "").strip()
        if not ref_id:
            return None
        parsed = _parse_string_ref(ref_id, kind_hint=kind or None)
        if parsed is None:
            return None
        if kind and parsed.ref_kind in {"reference", kind}:
            parsed.ref_kind = kind
            parsed.source_owner = parsed.source_owner or _GOVERNANCE_REF_OWNERS.get(kind)
        private, trader = _flags(kind)
        parsed.private = parsed.private or private
        parsed.trader = parsed.trader or trader
        return parsed
    return _parse_string_ref(str(raw or ""))


def _item_is_private(item: Optional[Mapping[str, Any]]) -> bool:
    if not isinstance(item, Mapping):
        return False
    from services.control_plane.bff.meetings.client import _is_private_memory_item

    return _is_private_memory_item(item)


def _item_is_trader(item: Optional[Mapping[str, Any]]) -> bool:
    if not isinstance(item, Mapping):
        return False
    from services.control_plane.bff.meetings.client import _is_agora_or_trader_record

    return _is_agora_or_trader_record(item)


_SHARED_MEMORY_SCOPE = "persona_and_committee"


def _is_memory_ref(parsed: ParsedRef, item: Optional[Mapping[str, Any]]) -> bool:
    if parsed.ref_kind == "memory" or parsed.source_owner == "memory":
        return True
    if isinstance(item, Mapping):
        if str(item.get("source_owner") or "").strip().lower() == "memory":
            return True
        if str(item.get("group") or "").strip().lower() == "lessons":
            return True
    return False


def _memory_scope_proven_shared(item: Optional[Mapping[str, Any]]) -> bool:
    """Only a recorded item carrying the committee-shared scope proves a memory ref is shareable."""
    if not isinstance(item, Mapping) or _item_is_private(item):
        return False
    return any(
        str(item.get(key) or "").strip().lower() == _SHARED_MEMORY_SCOPE
        for key in ("relevance_scope", "memory_scope", "visibility_scope")
    )


def _other_tenant(record: Mapping[str, Any], tenant_id: str) -> bool:
    t = str(record.get("tenant_id") or "").strip()
    return bool(t and tenant_id and t != tenant_id)


def _status_code(exc: BaseException) -> Optional[int]:
    for attr in ("status_code", "downstream_status", "code"):
        val = getattr(exc, attr, None)
        if isinstance(val, int):
            return val
    return None


def _raise_mapped(exc: BaseException) -> None:
    if _status_code(exc) in (401, 403):
        raise SourceDenied(getattr(exc, "reason", None) or "owner_denied") from exc
    raise exc


# ---------------------------------------------------------------------------
# Default owner reads (one per source; meeting positions bounded separately)
# ---------------------------------------------------------------------------


def _meetings_client(ctx: CitedReadContext):
    from services.control_plane.bff.meetings.client import MeetingsOwnerClient

    actor = getattr(ctx.identity, "operator_id", None) or ctx.caller_user_id or "viewer"
    return MeetingsOwnerClient(ctx.tenant_id, str(actor))


def _meeting_label(meeting: Mapping[str, Any], names: Any) -> Optional[str]:
    topic = str(meeting.get("topic") or meeting.get("title") or "").strip()
    mid = str(meeting.get("meeting_id") or meeting.get("id") or "").strip()
    if not topic:
        return None
    persona = meeting.get("subject_persona_id") or meeting.get("chair_persona_id")
    if not persona:
        m = re.search(r"(persona-[A-Za-z0-9_-]+)", topic)
        persona = m.group(1) if m else None
    if names is not None and persona:
        try:
            presented = names.present_title(topic, persona_id=persona)
            if presented:
                return presented
        except Exception:
            pass
    return topic


def _meeting_time(meeting: Mapping[str, Any]) -> Optional[str]:
    for k in ("concluded_at", "completed_at", "updated_at", "convened_at", "created_at", "scheduled_at"):
        v = meeting.get(k)
        if v:
            return str(v)
    return None


# Management pages that exist (frontend src/App.tsx, checked 2026-11-25):
# meetings/:id, personas/:id, artifacts/:id, evidence/:id, approvals,
# evolution-journal. strategies/:id takes a strategy id; an artifact id there
# answers 404 (hosted 2026-11-25), so artifacts link to artifacts/:id. No page renders one memory entry, research note,
# committee memo or research ticket, so those links are null.
_APPROVALS_PAGE = "/management/approvals"
_EVOLUTION_JOURNAL_PAGE = "/management/evolution-journal"


def _meeting_href(meeting_id: str) -> str:
    return f"/management/meetings/{quote(meeting_id, safe='')}"


def _persona_href(persona_id: str) -> Optional[str]:
    return f"/management/personas/{quote(persona_id, safe='')}" if persona_id else None


def _artifact_href(artifact_id: str) -> Optional[str]:
    return f"/management/artifacts/{quote(artifact_id, safe='')}" if artifact_id else None


def _evidence_href(ref_id: str) -> Optional[str]:
    return f"/management/evidence/{quote(ref_id, safe='')}" if ref_id else None


def read_meeting_proposals(ctx: CitedReadContext) -> SourceResult:
    from services.control_plane.bff.meetings.client import (
        MeetingsOwnerNotFoundError,
        MeetingsOwnerUnavailableError,
        MeetingsOwnerValidationError,
    )

    try:
        payload = _meetings_client(ctx).list_proposals()
    except (MeetingsOwnerUnavailableError, MeetingsOwnerValidationError, MeetingsOwnerNotFoundError) as exc:
        _raise_mapped(exc)
    items = [p for p in (payload.get("items") or []) if isinstance(p, Mapping)]
    out = SourceResult(read_count=len(items), response_bytes=_bytes_of(payload))
    for prop in items:
        if _other_tenant(prop, ctx.tenant_id):
            out.withheld_count += 1
            continue
        pid = str(prop.get("proposal_id") or prop.get("id") or "").strip()
        mid = str(prop.get("meeting_id") or "").strip()
        title = str(prop.get("title") or prop.get("summary") or "").strip() or None
        kind = str(prop.get("kind") or "").strip() or None
        for cit in prop.get("citations") or []:
            out.citations.append(Citation(
                ref=cit, entity_type="meeting_proposal", entity_ref=pid, label=title,
                route_href=_meeting_href(mid) if mid else None,
                time=str(prop.get("created_at") or prop.get("updated_at") or "") or None,
                parts={"meeting_id": mid or None, "kind": kind},
            ))
    return out


def _meeting_case_items(raw: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
    items: Dict[str, Mapping[str, Any]] = {}
    lists: List[Any] = [raw.get("case_file_items")]
    cf = raw.get("case_file")
    if isinstance(cf, Mapping):
        lists.append(cf.get("items"))
        groups = cf.get("groups")
        if isinstance(groups, Mapping):
            lists.extend(g.get("items") for g in groups.values() if isinstance(g, Mapping))
    for lst in lists:
        for it in lst or []:
            if isinstance(it, Mapping):
                iid = str(it.get("item_id") or it.get("id") or "").strip()
                if iid:
                    items.setdefault(iid, it)
    return items


def meeting_position_citations(raw: Mapping[str, Any], names: Any = None) -> Tuple[List[Citation], int]:
    """Visible position citations of one owner meeting detail, plus withheld count.

    Reuses the meetings withholding rule in its strictest form (as a
    non-operator with no trader identity): private memory and trader items are
    never listed, whatever the caller's role.
    """
    from services.control_plane.bff.meetings.client import (
        _collect_meeting_withheld_and_visible_refs,
        _extract_item_ref_ids,
        _is_agora_or_trader_record,
        _is_private_memory_item,
    )

    _refs, withheld_ids = _collect_meeting_withheld_and_visible_refs(raw, is_operator=False, caller_user_id=None)
    case_items = _meeting_case_items(raw)
    mid = str(raw.get("meeting_id") or raw.get("id") or "").strip()
    label = _meeting_label(raw, names)
    when = _meeting_time(raw)
    out: List[Citation] = []
    withheld = 0
    for rnd in raw.get("rounds") or []:
        if not isinstance(rnd, Mapping):
            continue
        round_no = rnd.get("round")
        rnd_hidden = _is_private_memory_item(rnd) or _is_agora_or_trader_record(rnd)
        for pos in rnd.get("positions") or []:
            if not isinstance(pos, Mapping):
                continue
            hidden = rnd_hidden or _is_private_memory_item(pos) or _is_agora_or_trader_record(pos)
            pos_label = str(pos.get("label") or "").strip() or None
            for cit in pos.get("citations") or []:
                ids = _extract_item_ref_ids(cit) if isinstance(cit, Mapping) else [str(cit or "").strip()]
                ids = [i for i in ids if i]
                if not ids:
                    continue
                item = case_items.get(ids[0])
                if hidden or any(i in withheld_ids for i in ids) or (
                    isinstance(cit, Mapping) and (_is_private_memory_item(cit) or _is_agora_or_trader_record(cit))
                ):
                    withheld += 1
                    continue
                out.append(Citation(
                    ref=cit if isinstance(cit, Mapping) else ids[0],
                    entity_type="meeting_position",
                    entity_ref=f"{mid}#round-{round_no}/{pos_label or ''}",
                    label=label,
                    route_href=_meeting_href(mid) if mid else None,
                    time=str(pos.get("created_at") or rnd.get("completed_at") or "") or when,
                    item=item,
                    parts={"meeting_id": mid or None, "meeting_name": label, "round": round_no,
                           "position": pos_label, "persona_id": pos.get("persona_id")},
                ))
    return out, withheld


def read_meeting_positions(ctx: CitedReadContext) -> SourceResult:
    """Positions live only in meeting detail: list once, then read the newest 30 details."""
    from services.control_plane.bff.core.display_names import current_resolver
    from services.control_plane.bff.meetings.client import (
        MeetingsOwnerNotFoundError,
        MeetingsOwnerUnavailableError,
        MeetingsOwnerValidationError,
    )

    client = _meetings_client(ctx)
    try:
        listing = client.list_meetings(limit=MEETING_DETAIL_CAP)
    except (MeetingsOwnerUnavailableError, MeetingsOwnerValidationError, MeetingsOwnerNotFoundError) as exc:
        _raise_mapped(exc)
    rows = [m for m in (listing.get("items") or []) if isinstance(m, Mapping)]
    rows.sort(key=lambda m: str(_meeting_time(m) or ""), reverse=True)
    selected = rows[:MEETING_DETAIL_CAP]
    truncated = bool(listing.get("next_cursor")) or len(rows) > MEETING_DETAIL_CAP
    ids = [str(m.get("meeting_id") or m.get("id") or "").strip() for m in selected]
    ids = [i for i in ids if i]
    out = SourceResult(read_count=0, truncated=truncated, calls=1, response_bytes=_bytes_of(listing))
    try:
        names = current_resolver(ctx.read_store)
    except Exception:
        names = None

    futures = {
        mid: submit_owner_read(_MEETING_DETAIL_EXECUTOR, (lambda m=mid: client.get_meeting(m)))
        for mid in ids
    }
    # Finish before the outer source wait (which keeps SOURCE_RESERVE_SECONDS).
    budget = remaining_management_read_budget(None, reserve_seconds=2 * SOURCE_RESERVE_SECONDS)
    futures_wait(list(futures.values()), timeout=budget)
    denied = 0
    for mid, fut in futures.items():
        out.calls += 1
        if not fut.done():
            fut.cancel()
            out.failed_count += 1
            continue
        try:
            raw = fut.result()
        except Exception as exc:  # noqa: BLE001 - one failed detail makes the source partial
            if _status_code(exc) in (401, 403):
                denied += 1
            out.failed_count += 1
            continue
        if not isinstance(raw, Mapping) or _other_tenant(raw, ctx.tenant_id):
            out.failed_count += 1
            continue
        out.read_count += 1
        out.response_bytes = (out.response_bytes or 0) + _bytes_of(raw)
        cits, withheld = meeting_position_citations(raw, names)
        out.citations.extend(cits)
        out.withheld_count += withheld
    if ids and out.failed_count == len(ids):
        if denied == len(ids):
            raise SourceDenied("owner_denied")
        raise SourceUnavailable("meeting_detail_reads_failed")
    return out


def _decision_time(rec: Mapping[str, Any]) -> Optional[str]:
    for k in ("decided_at", "resolved_at", "updated_at", "created_at", "submitted_at"):
        v = rec.get(k)
        if v:
            return str(v)
    return None


def read_governance_approvals(ctx: CitedReadContext) -> SourceResult:
    import urllib.error

    from services.control_plane.bff.core import owner_reads

    try:
        records = owner_reads.approval_records() or []
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise SourceDenied(f"governance_owner_http_{exc.code}") from exc
        raise SourceUnavailable(f"governance_owner_http_{exc.code}") from exc
    out = SourceResult(read_count=len(records), response_bytes=_bytes_of(records))
    for rec in records:
        if not isinstance(rec, Mapping):
            continue
        if _other_tenant(rec, ctx.tenant_id):
            out.withheld_count += 1
            continue
        did = str(rec.get("decision_id") or rec.get("id") or "").strip()
        dtype = str(rec.get("decision_type") or rec.get("target_type") or "approval").strip()
        target = str(rec.get("target_id") or "").strip()
        label = str(rec.get("title") or "").strip() or None
        for ref in rec.get("evidence_refs") or []:
            out.citations.append(Citation(
                ref=ref, entity_type="approval", entity_ref=did, label=label,
                route_href=_APPROVALS_PAGE,
                time=_decision_time(rec),
                parts={"decision_type": dtype or None, "target_id": target or None},
            ))
    return out


def read_evolution_proposals(ctx: CitedReadContext) -> SourceResult:
    import os

    from services.control_plane.bff.command_adapters.base import evolution_url
    from services.control_plane.bff.core.owner_reads import read_evolution_records

    store = ctx.read_store
    try:
        if hasattr(store, "list_evolution_decisions") and not os.getenv("PANTHEON_EVOLUTION_API_URL", "").strip():
            records = list(store.list_evolution_decisions() or [])
        else:
            records = read_evolution_records(evolution_url, "/api/evolution/proposals")
    except Exception as exc:  # noqa: BLE001
        _raise_mapped(exc)
    out = SourceResult(read_count=len(records), response_bytes=_bytes_of(records))
    for rec in records:
        if not isinstance(rec, Mapping):
            continue
        if _other_tenant(rec, ctx.tenant_id):
            out.withheld_count += 1
            continue
        pid = str(rec.get("proposal_id") or rec.get("decision_id") or rec.get("id") or "").strip()
        label = str(rec.get("title") or rec.get("summary") or "").strip() or None
        for ref in rec.get("evidence_refs") or []:
            out.citations.append(Citation(
                ref=ref, entity_type="evolution_proposal", entity_ref=pid, label=label,
                route_href=_EVOLUTION_JOURNAL_PAGE,
                time=_decision_time(rec),
                parts={"persona_id": rec.get("persona_id"), "kind": rec.get("proposal_type") or rec.get("kind")},
            ))
    return out


def read_research_demands(ctx: CitedReadContext) -> SourceResult:
    from services.control_plane.bff.core.owner_reads import read_records
    from services.control_plane.bff.research.client import resolve_orchestrator_base_url

    base = resolve_orchestrator_base_url()
    if not base:
        raise SourceUnavailable("research_owner_unconfigured")
    try:
        rows = read_records(lambda p: base.rstrip("/") + p, "/api/research/persona-demands", "demands")
    except Exception as exc:  # noqa: BLE001
        _raise_mapped(exc)
    out = SourceResult(read_count=len(rows), response_bytes=_bytes_of(rows))
    for d in rows:
        if _other_tenant(d, ctx.tenant_id):
            out.withheld_count += 1
            continue
        influence = d.get("retrieval_influence") if isinstance(d.get("retrieval_influence"), Mapping) else {}
        meta = influence.get("metadata") if isinstance(influence.get("metadata"), Mapping) else {}
        cited = meta.get("cited_item_ids") if isinstance(meta.get("cited_item_ids"), list) else []
        did = str(d.get("demand_id") or "").strip()
        persona = str(d.get("persona_id") or "").strip()
        measure = str(d.get("short_measure") or "").strip()
        for cit in cited:
            out.citations.append(Citation(
                ref=cit, entity_type="research_demand", entity_ref=did, label=measure or None,
                route_href=_persona_href(persona),
                time=str(d.get("created_at") or "") or None,
                parts={"persona_id": persona or None, "short_measure": measure or None},
            ))
    return out


def read_research_tickets(ctx: CitedReadContext) -> SourceResult:
    from services.control_plane.bff.research.client import ResearchCommandError, ResearchServiceClient, resolve_orchestrator_base_url

    if not resolve_orchestrator_base_url():
        raise SourceUnavailable("research_owner_unconfigured")
    try:
        page = ResearchServiceClient().list_research_ticket_page(page_size=100)
    except ResearchCommandError as exc:
        _raise_mapped(exc)
    rows = [t for t in (page.get("data") or []) if isinstance(t, Mapping)]
    info = page.get("page_info") if isinstance(page.get("page_info"), Mapping) else {}
    out = SourceResult(read_count=len(rows), truncated=bool(info.get("next_page_token")), response_bytes=_bytes_of(page))
    for t in rows:
        if _other_tenant(t, ctx.tenant_id):
            out.withheld_count += 1
            continue
        tid = str(t.get("ticket_id") or t.get("action_id") or "").strip()
        label = str(t.get("title") or "").strip() or None
        href = None  # no management page renders one research ticket
        human = t.get("human_input") if isinstance(t.get("human_input"), Mapping) else {}
        for ref, when in [
            *((r, t.get("submitted_at")) for r in (t.get("evidence_refs") or [])),
            *((r, human.get("provided_at")) for r in (human.get("evidence_refs") or [])),
        ]:
            if isinstance(ref, Mapping) and _other_tenant(ref, ctx.tenant_id):
                out.withheld_count += 1
                continue
            out.citations.append(Citation(
                ref=ref, entity_type="research_ticket", entity_ref=tid, label=label, route_href=href,
                time=str(when or t.get("updated_at") or "") or None,
                parts={"status": t.get("status")},
            ))
    return out


def persona_cycle_citations(packet: Mapping[str, Any], tenant_id: str, names: Any = None) -> Tuple[List[Citation], int]:
    """Visible decision citations of one OODA packet, plus withheld count.

    Case files are projected with no private persona: private memory of the
    persona is withheld from this management-wide list (the persona's own
    detail view keeps it). A cited id whose item is not visible is dropped.
    """
    from services.control_plane.bff.agora.committee.owner import project_case_file

    cycle = packet.get("persona_cycle") if isinstance(packet.get("persona_cycle"), Mapping) else {}
    cases = cycle.get("case_files") if isinstance(cycle.get("case_files"), Mapping) else {}
    visible: Dict[str, Mapping[str, Any]] = {}
    for cid, case in cases.items():
        view = project_case_file(case, tenant_id=tenant_id, private_persona_ids=frozenset())
        if view.get("status") == "ok" and (view.get("case_file") or {}).get("case_file_id") == cid:
            projected = {
                it["item_id"]
                for g in (view["case_file"].get("groups") or {}).values()
                for it in g.get("items") or []
            }
            # The projection drops scope fields; keep the recorded item (it passed
            # the projection) so memory scope can be proven downstream.
            for g in (case.get("groups") or {}).values():
                for it in (g.get("items") or []) if isinstance(g, Mapping) else []:
                    if isinstance(it, Mapping) and it.get("item_id") in projected:
                        visible.setdefault(str(it["item_id"]), it)
    pid = str(packet.get("packet_id") or "").strip()
    persona = next(iter(packet.get("persona_ids") or []), "") or ""
    pname: Optional[str] = None
    if names is not None and persona:
        try:
            n, missing = names.persona(persona)
            pname = None if missing else n
        except Exception:
            pname = None
    out: List[Citation] = []
    withheld = 0
    for index, d in enumerate(cycle.get("decisions") or []):
        if not isinstance(d, Mapping):
            continue
        kind = str(d.get("kind") or "").strip() or None
        for cid in dict.fromkeys(d.get("cited_case_file_item_ids") or []):
            item = visible.get(str(cid))
            if item is None:
                withheld += 1
                continue
            out.append(Citation(
                ref=str(cid), entity_type="persona_cycle_decision", entity_ref=f"{pid}#decisions/{index}",
                label=None,
                route_href=_persona_href(persona),
                time=str(packet.get("updated_at") or packet.get("created_at") or "") or None,
                item=item,
                parts={"persona_id": persona or None, "persona_name": pname, "kind": kind,
                       "decision_index": index},
            ))
    return out, withheld


def read_persona_cycles(ctx: CitedReadContext) -> SourceResult:
    from services.control_plane.bff.core.display_names import current_resolver
    from services.control_plane.bff.personas.cycle_views import read_cycle_records

    try:
        records = read_cycle_records(ctx.read_store, ctx.tenant_id)
    except AttributeError as exc:
        raise SourceUnavailable("cycle_owner_unconfigured") from exc
    except Exception as exc:  # noqa: BLE001
        _raise_mapped(exc)
    out = SourceResult(read_count=len(records), response_bytes=_bytes_of(records))
    try:
        names = current_resolver(ctx.read_store)
    except Exception:
        names = None
    for packet in records:
        cits, withheld = persona_cycle_citations(packet, ctx.tenant_id, names)
        out.citations.extend(cits)
        out.withheld_count += withheld
    return out


DEFAULT_SOURCE_READERS: Dict[str, SourceReader] = {
    "meeting_proposals": read_meeting_proposals,
    "meeting_positions": read_meeting_positions,
    "governance_approvals": read_governance_approvals,
    "evolution_proposals": read_evolution_proposals,
    "research_demands": read_research_demands,
    "research_tickets": read_research_tickets,
    "persona_cycles": read_persona_cycles,
}


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


def _run_sources(ctx: CitedReadContext, readers: Mapping[str, SourceReader]) -> Dict[str, Tuple[Optional[SourceResult], Dict[str, Any]]]:
    futures = {key: submit_owner_read(_SOURCE_EXECUTOR, (lambda r=reader: r(ctx))) for key, reader in readers.items()}
    budget = remaining_management_read_budget(None, reserve_seconds=SOURCE_RESERVE_SECONDS)
    futures_wait(list(futures.values()), timeout=budget)
    results: Dict[str, Tuple[Optional[SourceResult], Dict[str, Any]]] = {}
    for key, fut in futures.items():
        if not fut.done():
            fut.cancel()
            results[key] = (None, {"status": "failed", "reason": "read_timeout"})
            continue
        try:
            res = fut.result()
        except SourceDenied as exc:
            results[key] = (None, {"status": "denied", "reason": exc.reason})
            continue
        except SourceUnavailable as exc:
            results[key] = (None, {"status": "failed", "reason": exc.reason})
            continue
        except (TimeoutError, FuturesTimeout):
            results[key] = (None, {"status": "failed", "reason": "read_timeout"})
            continue
        except Exception as exc:  # noqa: BLE001 - never echo owner bodies
            if _status_code(exc) in (401, 403):
                results[key] = (None, {"status": "denied", "reason": "owner_denied"})
            else:
                log.warning("cited evidence source %s failed: %s", key, type(exc).__name__)
                results[key] = (None, {"status": "failed", "reason": f"owner_read_error:{type(exc).__name__}"})
            continue
        if not isinstance(res, SourceResult):
            results[key] = (None, {"status": "failed", "reason": "invalid_source_result"})
            continue
        results[key] = (res, {"status": "partial" if res.failed_count else "ok", "reason": res.reason})
    return results


def _ko_evidence_candidate(ko_id: str) -> Optional[str]:
    """``ko-<slug(evidence_item_id)>-<sha256(evidence_item_id)[:12]>`` -> evidence item id, verified."""
    m = re.fullmatch(r"ko-(evi-[a-z0-9-]+)-([0-9a-f]{12})", ko_id)
    if not m:
        return None
    candidate, digest = m.group(1), m.group(2)
    if hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:12] != digest:
        return None
    return candidate


_ARTIFACT_KINDS = frozenset({"registry_artifact", "member_virtual_book"})


def _route_for(parsed: ParsedRef, evidence_ref_id: Optional[str]) -> Optional[str]:
    """A route only when a management page shows this reference; otherwise ``None``."""
    k, sid = parsed.ref_kind, parsed.source_id
    if k in _ARTIFACT_KINDS:
        return _artifact_href(sid)
    if k == "strategy_pool":
        return _persona_href(sid.split("@", 1)[0])
    if evidence_ref_id:
        return _evidence_href(evidence_ref_id)
    if k == "evidence_item" and parsed.source_owner == "source_ingest":
        return _evidence_href(sid)
    return None


_SOURCE_CLASS = {
    "registry_artifact": "registry_record",
    "strategy_pool": "owner_fact",
    "member_virtual_book": "owner_fact",
    "memory": "memory",
    "research_note": "research_note",
    "knowledge_object": "search_excerpt",
    "evidence_item": "evidence",
}


def _name_for(
    parsed: ParsedRef, names: Any, artifact_strategy: Optional[str] = None
) -> Tuple[Optional[str], bool, Dict[str, Any]]:
    """Display name only (no composed sentence) plus structured parts.

    An artifact is named by ``artifact_strategy``: the strategy id that the
    Registry owner records for it. Without that owner answer the row has no
    strategy id and no name (the id text is never parsed into a strategy id).
    """
    k, sid = parsed.ref_kind, parsed.source_id
    parts: Dict[str, Any] = {}
    if k == "strategy_pool":
        persona, _, version = sid.partition("@")
        parts = {"persona_id": persona, "pool_version": version or None}
    elif k in _ARTIFACT_KINDS:
        parts = {"artifact_id": sid, "strategy_id": artifact_strategy or None}
    if names is None:
        return None, True, parts
    try:
        if k in _ARTIFACT_KINDS:
            if not artifact_strategy:
                return None, True, parts
            n, missing = names.strategy(artifact_strategy)
            return (None, True, parts) if missing else (n, False, parts)
        if k == "strategy_pool":
            n, missing = names.persona(parts["persona_id"])
            return (None, True, parts) if missing else (n, False, parts)
        if sid.startswith("persona-"):
            n, missing = names.persona(sid)
            return (None, True, parts) if missing else (n, False, parts)
    except Exception:
        return None, True, parts
    return None, True, parts


# Registry entries visible to every tenant (services/registry/models.py BUILTIN_TENANT).
_REGISTRY_BUILTIN_TENANT = "__builtin__"


def _artifact_strategy(res: Any, artifact_id: str, tenant_id: str) -> Tuple[Optional[str], bool]:
    """``(strategy_id, failed)`` from one Registry artifact detail read.

    The owner port answers ``(detail, status)``; ``status`` other than ``ok``
    is a failed read. The strategy id is the recorded ``strategy_id`` field of
    that artifact only. The read runs with the caller's token and tenant
    header (the Registry owner scopes it); a detail whose ``tenant_id`` or
    ``owner_tenant`` is another tenant, or that names another artifact, is
    treated as failed and never used.
    """
    detail, status = (res if isinstance(res, tuple) and len(res) == 2 else (res, "ok"))
    if str(status or "ok") != "ok":
        return None, True
    if detail is None:
        return None, False  # the owner has no such artifact
    if not isinstance(detail, Mapping) or _other_tenant(detail, tenant_id):
        return None, True
    owner_tenant = str(detail.get("owner_tenant") or "").strip()
    if owner_tenant and tenant_id and owner_tenant not in (tenant_id, _REGISTRY_BUILTIN_TENANT):
        return None, True
    answered = {str(detail.get(k) or "").strip() for k in ("artifact_id", "registry_id", "id")} - {""}
    if answered and artifact_id not in answered:
        return None, True
    strategy_id = str(detail.get("strategy_id") or "").strip()
    return (strategy_id or None), False


def _with_target_name(citation: Dict[str, Any], artifact_names: Mapping[str, str]) -> Dict[str, Any]:
    """Add ``parts.target_display_name`` to an approval whose target artifact was read."""
    parts = citation.get("parts")
    if citation.get("entity_type") != "approval" or not isinstance(parts, Mapping):
        return citation
    name = artifact_names.get(str(parts.get("target_id") or ""))
    if not name:
        return citation
    return {**citation, "parts": {**parts, "target_display_name": name}}


def compose_cited_evidence(
    ctx: CitedReadContext,
    *,
    readers: Optional[Mapping[str, SourceReader]] = None,
    item_reader: Optional[Callable[[str], Any]] = None,
    artifact_reader: Optional[Callable[[str], Any]] = None,
    names: Any = None,
    redact: Optional[Callable[[List[Dict[str, Any]]], Tuple[List[Any], int]]] = None,
    page_token: Optional[str] = None,
    page_size: int = 20,
) -> Dict[str, Any]:
    """Compose the cited list. Never turns a source failure into an empty ok list."""
    readers = dict(readers if readers is not None else DEFAULT_SOURCE_READERS)
    snap = ctx.snapshot_at
    results = _run_sources(ctx, readers)

    rows: Dict[str, Dict[str, Any]] = {}
    sources: Dict[str, Dict[str, Any]] = {}
    excluded = {"meeting_topic": 0, "private_or_trader": 0, "memory_scope_unproven": 0, "unparsed": 0}
    total_calls = 0
    total_bytes = 0
    for key in readers:
        res, state = results[key]
        entry: Dict[str, Any] = {
            "status": state["status"], "reason": state.get("reason"),
            "read_count": res.read_count if res else None,
            "truncated": bool(res.truncated) if res else None,
            "failed_count": res.failed_count if res else None,
            "citation_count": 0, "withheld_count": res.withheld_count if res else 0,
            "calls": res.calls if res else None, "response_bytes": res.response_bytes if res else None,
        }
        sources[key] = entry
        if res is None:
            continue
        total_calls += int(res.calls or 0)
        total_bytes += int(res.response_bytes or 0)
        for cit in res.citations:
            parsed = parse_ref(cit.ref)
            if parsed is None:
                if isinstance(cit.ref, str) and cit.ref.startswith("facts:meeting_topic:"):
                    excluded["meeting_topic"] += 1
                else:
                    excluded["unparsed"] += 1
                continue
            if parsed.private or parsed.trader or _item_is_private(cit.item) or _item_is_trader(cit.item):
                excluded["private_or_trader"] += 1
                entry["withheld_count"] += 1
                continue
            if _is_memory_ref(parsed, cit.item) and not _memory_scope_proven_shared(cit.item):
                excluded["memory_scope_unproven"] += 1
                entry["withheld_count"] += 1
                continue
            entry["citation_count"] += 1
            row = rows.get(parsed.ref_id)
            if row is None:
                row = rows[parsed.ref_id] = {
                    "_parsed": parsed, "_item": cit.item, "cited_as": [], "cited_by": [], "_keys": set(),
                    "_latest": "",
                }
            if parsed.raw not in row["cited_as"]:
                row["cited_as"].append(parsed.raw)
            if row["_item"] is None and cit.item is not None:
                row["_item"] = cit.item
            ckey = (cit.entity_type, cit.entity_ref)
            if ckey in row["_keys"]:
                continue
            row["_keys"].add(ckey)
            row["cited_by"].append({
                "entity_type": cit.entity_type, "entity_ref": cit.entity_ref, "label": cit.label,
                "parts": dict(cit.parts), "route_href": cit.route_href, "time": cit.time, "source": key,
            })
            row["_latest"] = max(row["_latest"], str(cit.time or ""))

    ordered = sorted(rows.values(), key=lambda r: (r["_latest"], r["_parsed"].ref_id), reverse=True)
    reference_total = len(ordered)
    references_truncated = reference_total > MAX_REFERENCES
    ordered = ordered[:MAX_REFERENCES]

    statuses = [s["status"] for s in sources.values()]
    any_ok = any(s in {"ok", "partial"} for s in statuses)
    incomplete = (
        any(s != "ok" for s in statuses)
        or any(bool(s.get("truncated")) for s in sources.values())
        or references_truncated
    )

    # Redaction (reused policy) over the whole cited population.
    redacted_count = 0
    if redact is not None and ordered:
        probes = [{"ref_id": r["_parsed"].ref_id} for r in ordered]
        processed, redacted_count = redact(probes)
        for r, probe, p in zip(ordered, probes, processed):
            if p is not probe:
                r["_redacted"] = dict(p) if isinstance(p, Mapping) else {"reason": "redacted"}

    from .service import _page_slice  # local import: service imports this module lazily

    page_rows, next_token = _page_slice(ordered, page_token, page_size) if ordered else ([], None)

    # Bounded owner reads for the visible page only: ko-evi / evidence item
    # reads and one Registry artifact read per distinct artifact id, sharing
    # one MAX_ITEM_READS budget, one executor and the request read deadline.
    item_reads = {"attempted": 0, "resolved": 0, "failed": 0, "truncated": False}
    resolved_items: Dict[str, Any] = {}
    artifact_strategies: Dict[str, str] = {}
    wanted: List[Tuple[str, str, str]] = []  # (kind, key, target)
    seen_artifacts: set = set()
    for r in page_rows:
        p: ParsedRef = r["_parsed"]
        if r.get("_redacted"):
            continue
        if p.ref_kind in _ARTIFACT_KINDS:
            if artifact_reader is not None and p.source_id and p.source_id not in seen_artifacts:
                seen_artifacts.add(p.source_id)
                wanted.append(("artifact", p.source_id, p.source_id))
            continue
        if item_reader is None:
            continue
        target = None
        if p.ref_kind == "knowledge_object":
            target = _ko_evidence_candidate(p.source_id)
        elif p.ref_kind == "evidence_item":
            target = p.source_id
        if target:
            wanted.append(("item", p.ref_id, target))
    if len(wanted) > MAX_ITEM_READS:
        item_reads["truncated"] = True
        wanted = wanted[:MAX_ITEM_READS]
    futs = []
    for kind, key, target in wanted:
        reader = artifact_reader if kind == "artifact" else item_reader
        futs.append((kind, key, target, submit_owner_read(_ITEM_READ_EXECUTOR, (lambda f=reader, t=target: f(t)))))
    if futs:
        futures_wait([f for *_, f in futs], timeout=remaining_management_read_budget(None, reserve_seconds=0.05))
    for kind, key, target, fut in futs:
        item_reads["attempted"] += 1
        if not fut.done():
            fut.cancel()
            item_reads["failed"] += 1
            continue
        try:
            res = fut.result()
        except Exception:  # noqa: BLE001
            item_reads["failed"] += 1
            continue
        if kind == "artifact":
            strategy_id, failed = _artifact_strategy(res, target, ctx.tenant_id)
            if failed:
                item_reads["failed"] += 1
            elif strategy_id:
                item_reads["resolved"] += 1
                artifact_strategies[target] = strategy_id
            continue
        items = list(getattr(res, "items", None) or []) if not isinstance(res, Mapping) else [res]
        avail = getattr(res, "available", True)
        if not avail:
            item_reads["failed"] += 1
            continue
        match = next((i for i in items if isinstance(i, Mapping)
                      and str(i.get("ref_id") or i.get("id") or "") == target), None)
        if match is not None:
            item_reads["resolved"] += 1
            resolved_items[key] = match

    # Approvals whose target is an artifact answered above carry its strategy name.
    artifact_names: Dict[str, str] = {}
    if names is not None:
        for aid, strategy_id in artifact_strategies.items():
            try:
                n, missing = names.strategy(strategy_id)
            except Exception:  # noqa: BLE001
                continue
            if not missing and n:
                artifact_names[aid] = n

    public: List[Dict[str, Any]] = []
    for r in page_rows:
        p: ParsedRef = r["_parsed"]
        red = r.get("_redacted")
        if red:
            public.append({
                "id": p.ref_id, "ref_id": p.ref_id, "ref_kind": p.ref_kind, "redacted": True,
                "required_capability": red.get("required_capability"), "reason": red.get("reason"),
                "label": p.ref_id, "name_missing": True, "source_owner": p.source_owner,
                "source_class": None, "resolved_link": None, "cited_as": list(r["cited_as"]),
                "cited_by": list(r["cited_by"]), "citation_count": len(r["cited_by"]),
            })
            continue
        ev = resolved_items.get(p.ref_id)
        label, missing, label_parts = _name_for(
            p, names, artifact_strategies.get(p.source_id) if p.ref_kind in _ARTIFACT_KINDS else None
        )
        source_class = _SOURCE_CLASS.get(p.ref_kind, p.ref_kind)
        evidence_ref_id = None
        if ev is not None:
            evidence_ref_id = str(ev.get("ref_id") or ev.get("id") or "")
            title = (ev.get("source_document") or {}).get("title") if isinstance(ev.get("source_document"), Mapping) else None
            title = title or ev.get("title") or (ev.get("display_label") if ev.get("display_label") != evidence_ref_id else None)
            if title:
                label, missing = str(title), False
            source_class = ev.get("source_class") or source_class
        source_owner = p.source_owner
        item = r.get("_item")
        if isinstance(item, Mapping) and item.get("source_owner"):
            source_owner = str(item["source_owner"])
        href = _route_for(p, evidence_ref_id)
        row = {
            "id": p.ref_id, "ref_id": p.ref_id, "ref_kind": p.ref_kind, "source_owner": source_owner,
            "label": label if not missing else p.ref_id, "name_missing": bool(missing),
            "label_parts": label_parts, "source_class": source_class,
            "resolved_link": {"route_href": href, "evidence_ref_id": evidence_ref_id} if href else None,
            "route_href": href, "cited_as": list(r["cited_as"]),
            "cited_by": [_with_target_name(c, artifact_names) for c in r["cited_by"]],
            "citation_count": len(r["cited_by"]), "last_cited_at": r["_latest"] or None, "redacted": False,
        }
        public.append(row)

    if not any_ok:
        status = "unavailable"
    elif incomplete:
        status = "degraded"
    else:
        status = "ok"
    total: Optional[int] = len(ordered) if any_ok else None
    summary = {
        "scope": "cited",
        "complete": not incomplete and any_ok,
        "incomplete": incomplete or not any_ok,
        "reference_total": reference_total if any_ok else None,
        "returned": len(public),
        "references_truncated": references_truncated,
        "max_references": MAX_REFERENCES,
        "redacted_count": redacted_count,
        "excluded": excluded,
        "sources_ok": sum(1 for s in statuses if s == "ok"),
        "sources_not_ok": [k for k, s in sources.items() if s["status"] != "ok"],
    }
    cost = {
        "owner_calls": total_calls,
        "response_bytes": total_bytes,
        "item_reads": item_reads["attempted"],
        "max_item_reads": MAX_ITEM_READS,
        "meeting_detail_cap": MEETING_DETAIL_CAP,
        "meeting_detail_parallel": MEETING_DETAIL_PARALLEL,
    }
    surface = {"status": status, "source": "bff_composed_owner_reads", "snapshot_at": snap}
    if status != "ok":
        surface["reason"] = "source_incomplete" if any_ok else "all_sources_unavailable"
    return {
        "data": {"id": "management-evidence-cited", "scope": "cited", "items": public, "summary": summary,
                 "sources": sources},
        "page_info": {"next_page_token": next_token if any_ok else None, "total": total, "page_size": page_size},
        "meta": {
            "snapshot_at": snap,
            "surfaces": {"management_evidence": dict(surface), "cited_evidence": surface,
                         "cited_evidence_sources": sources},
            "item_reads": item_reads,
            "cost": cost,
            "redacted_evidence_count": redacted_count,
            "policy": "read_only_cited_evidence",
        },
    }
