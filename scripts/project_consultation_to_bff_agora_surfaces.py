#!/usr/bin/env python3
"""Project real consultation service output into BFF Agora read-surface stores.

The consultation service remains the producer for requests, transcripts, memos,
and gate handoffs. This script only materializes read-model projections that the
operator BFF already knows how to serve under /bff/agora/*.

No rows are invented: every projected record is derived from a consultation
request, transcript, memo, or handoff returned by the service or replayed from
the consultation store.

Usage:
    CONSULTATION_URL=http://consultation-svc:8096 OUT_DIR=/data/bff \
        python3 scripts/project_consultation_to_bff_agora_surfaces.py

    CONSULTATION_DATA_DIR=/data/consultation OUT_DIR=/data/bff \
        python3 scripts/project_consultation_to_bff_agora_surfaces.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any


StoreMap = dict[str, dict[str, dict[str, Any]]]

DATASET_FILENAMES = {
    "agora_signals": "agora_signals.json",
    "agora_sessions": "agora_sessions.json",
    "agora_handoffs": "agora_handoffs.json",
    "agora_training_examples": "agora_training_examples.json",
    "research_tickets": "research_tickets.json",
    "research_notes": "research_notes.json",
    "insight_cards": "insight_cards.json",
    "decision_journal_entries": "decision_journal_entries.json",
    "postmortems": "postmortems.json",
}

TERMINAL_REQUEST_STATUSES = {"published", "cancelled", "failed"}


def _get(url: str) -> Any:
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            return json.loads(response.read())
    except Exception as exc:  # noqa: BLE001
        print(f"  warn: GET {url} failed: {exc}", file=sys.stderr)
        return None


def _items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        for key in (
            "items",
            "data",
            "requests",
            "memos",
            "transcripts",
            "handoffs",
            "entries",
        ):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return [item for item in payload.values() if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _model_to_data(model: Any) -> dict[str, Any]:
    if isinstance(model, dict):
        return json.loads(json.dumps(model))
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    if hasattr(model, "dict"):
        return model.dict()
    return json.loads(model.json())


def _first_text(*values: Any) -> str | None:
    for value in values:
        if value in (None, ""):
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _record_id(record: dict[str, Any], *keys: str) -> str:
    return _first_text(*(record.get(key) for key in keys)) or ""


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _slug_ref(prefix: str, raw: str) -> str:
    clean = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in raw.strip())
    return f"{prefix}-{clean}" if clean else ""


def _actor_id(actor: Any) -> str | None:
    actor_data = _mapping(actor)
    return _first_text(actor_data.get("actor_id"), actor_data.get("id"), actor)


def _actor_type(actor: Any) -> str:
    actor_data = _mapping(actor)
    return _first_text(actor_data.get("actor_type"), actor_data.get("type"), "participant") or "participant"


def _status_value(record: dict[str, Any]) -> str:
    value = record.get("status")
    if isinstance(value, dict):
        value = value.get("value")
    return str(value or "").strip().lower()


def _request_timestamp(req: dict[str, Any]) -> str | None:
    return _first_text(
        req.get("completed_at"),
        req.get("canceled_at"),
        req.get("updated_at"),
        req.get("created_at"),
    )


def _memo_timestamp(memo: dict[str, Any]) -> str | None:
    return _first_text(memo.get("published_at"), memo.get("updated_at"), memo.get("created_at"))


def _request_title(req: dict[str, Any]) -> str:
    metadata = _mapping(req.get("metadata"))
    consultation = _mapping(metadata.get("consultation"))
    return _first_text(
        consultation.get("title"),
        metadata.get("title"),
        req.get("task"),
        req.get("consultation_type"),
        req.get("target_id"),
        req.get("request_id"),
    ) or "Consultation request"


def _request_body(req: dict[str, Any]) -> str:
    metadata = _mapping(req.get("metadata"))
    consultation = _mapping(metadata.get("consultation"))
    return _first_text(
        consultation.get("summary"),
        consultation.get("reason"),
        metadata.get("summary"),
        req.get("task"),
        f"Consultation request for {req.get('target_type')}:{req.get('target_id')}",
    ) or "Consultation request"


def _priority(req: dict[str, Any]) -> str:
    return str(req.get("priority") or "normal").strip().lower() or "normal"


def _severity(req: dict[str, Any]) -> str:
    return {
        "urgent": "critical",
        "high": "high",
        "normal": "medium",
        "low": "low",
    }.get(_priority(req), "info")


def _ticket_status(req: dict[str, Any]) -> str:
    return {
        "draft": "open",
        "submitted": "open",
        "assigned": "in_progress",
        "in_progress": "in_progress",
        "memo_pending": "in_progress",
        "published": "closed",
        "cancelled": "archived",
        "failed": "closed",
    }.get(_status_value(req), "open")


def _signal_review_status(req: dict[str, Any]) -> str:
    return {
        "published": "approved",
        "cancelled": "rejected",
        "failed": "rejected",
    }.get(_status_value(req), "pending_trader_review")


def _linked_strategy_ids(req: dict[str, Any]) -> list[str]:
    target_type = str(req.get("target_type") or "").strip().lower()
    target_id = _first_text(req.get("target_id"))
    metadata = _mapping(req.get("metadata"))
    explicit = _list(metadata.get("linked_strategy_ids") or metadata.get("linkedStrategyIds"))
    values = [str(value) for value in explicit if str(value).strip()]
    if target_id and target_type in {"strategy", "strategy_spec", "allocation_policy"}:
        values.append(target_id)
    return sorted(set(values))


def _linked_persona_ids(req: dict[str, Any]) -> list[str]:
    metadata = _mapping(req.get("metadata"))
    values = [str(value) for value in _list(metadata.get("linked_persona_ids") or metadata.get("linkedPersonaIds"))]
    from_persona = _first_text(req.get("from_persona_id"))
    if from_persona:
        values.append(from_persona)
    requested_by = _actor_id(req.get("requested_by"))
    if requested_by and _actor_type(req.get("requested_by")) == "persona":
        values.append(requested_by)
    return sorted({value for value in values if value.strip()})


def _events_by_request(transcripts: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for transcript in transcripts:
        request_id = _record_id(transcript, "request_id", "session_id", "id")
        if not request_id:
            continue
        grouped.setdefault(request_id, [])
        for event in _list(transcript.get("events")):
            if isinstance(event, dict):
                grouped[request_id].append(event)
    return grouped


def _memos_by_request(memos: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for memo in memos:
        request_id = _record_id(memo, "request_id")
        if request_id:
            grouped.setdefault(request_id, []).append(memo)
    return grouped


def _handoffs_by_request(handoffs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for handoff in handoffs:
        request_id = _record_id(handoff, "request_id")
        if request_id:
            grouped.setdefault(request_id, []).append(handoff)
    return grouped


def _message_content(event: dict[str, Any]) -> str:
    content = event.get("content")
    if isinstance(content, dict):
        return _first_text(
            content.get("text"),
            content.get("body"),
            content.get("summary"),
            content.get("message"),
            json.dumps(content, sort_keys=True),
        ) or ""
    return _first_text(content) or ""


def _project_signal(req: dict[str, Any], memos: list[dict[str, Any]]) -> dict[str, Any] | None:
    request_id = _record_id(req, "request_id", "id")
    if not request_id:
        return None
    timestamp = _request_timestamp(req)
    signal_id = _slug_ref("sig", request_id)
    latest_memo = memos[-1] if memos else {}
    return {
        "id": signal_id,
        "signal_id": signal_id,
        "title": _request_title(req),
        "body": _first_text(latest_memo.get("summary"), _request_body(req)),
        "description": _request_body(req),
        "severity": _severity(req),
        "status": "open" if _status_value(req) not in TERMINAL_REQUEST_STATUSES else "closed",
        "reviewStatus": _signal_review_status(req),
        "linkedPersonaIds": _linked_persona_ids(req),
        "linkedStrategyIds": _linked_strategy_ids(req),
        "source_ref": f"consultation_request:{request_id}",
        "target": {"type": req.get("target_type"), "id": req.get("target_id")},
        "createdAt": req.get("created_at") or timestamp,
        "updatedAt": timestamp or req.get("created_at"),
        "createdBy": _actor_id(req.get("requested_by")) or "consultation-svc",
        "authorId": _actor_id(req.get("requested_by")) or "consultation-svc",
        "canonicalWriteAuthority": "consultation-svc",
    }


def _project_session(
    req: dict[str, Any],
    *,
    events: list[dict[str, Any]],
    memos: list[dict[str, Any]],
) -> dict[str, Any] | None:
    request_id = _record_id(req, "request_id", "id")
    if not request_id:
        return None
    metadata = _mapping(req.get("metadata"))
    consultation = _mapping(metadata.get("consultation"))
    session_id = _first_text(req.get("linked_session_id"), consultation.get("session_id"), request_id) or request_id
    timestamp = _request_timestamp(req)
    participants: dict[str, dict[str, str]] = {}
    requester = _actor_id(req.get("requested_by"))
    if requester:
        participants[requester] = {"type": _actor_type(req.get("requested_by")), "id": requester}
    for memo in memos:
        author_ref = _first_text(memo.get("author_ref"))
        if author_ref:
            participants[author_ref] = {"type": str(memo.get("author_type") or "participant"), "id": author_ref}
    for event in events:
        actor = event.get("actor")
        actor_ref = _actor_id(actor)
        if actor_ref:
            participants[actor_ref] = {"type": _actor_type(actor), "id": actor_ref}

    messages = []
    for event in sorted(events, key=lambda item: int(item.get("sequence_no") or 0)):
        event_id = _record_id(event, "event_id", "id")
        if not event_id:
            continue
        actor = event.get("actor")
        messages.append(
            {
                "id": event_id,
                "sessionId": session_id,
                "sender": {"type": _actor_type(actor), "id": _actor_id(actor) or "unknown"},
                "role": "assistant" if _actor_type(actor) in {"persona", "committee", "system"} else "user",
                "content": _message_content(event),
                "language": "zh-TW",
                "attachments": [],
                "citations": [],
                "annotations": [{"type": "consultation_event", "eventType": event.get("event_type")}],
                "createdAt": event.get("event_time") or timestamp,
            }
        )

    mode = _first_text(consultation.get("agora_mode"), consultation.get("session_type"), "committee")
    return {
        "id": session_id,
        "sessionId": session_id,
        "title": _request_title(req),
        "mode": mode,
        "status": "closed" if _status_value(req) in TERMINAL_REQUEST_STATUSES else "active",
        "participants": list(participants.values()),
        "contextRefs": _list(req.get("context_refs")),
        "messages": messages,
        "targetEntity": {"type": req.get("target_type"), "id": req.get("target_id")},
        "linkedRequestId": request_id,
        "memoIds": [_record_id(memo, "memo_id", "id") for memo in memos if _record_id(memo, "memo_id", "id")],
        "createdBy": requester or "consultation-svc",
        "createdAt": req.get("created_at") or timestamp,
        "updatedAt": timestamp or req.get("created_at"),
        "canonicalWriteAuthority": "consultation-svc",
    }


def _project_ticket(req: dict[str, Any]) -> dict[str, Any] | None:
    request_id = _record_id(req, "request_id", "id")
    if not request_id:
        return None
    ticket_id = _slug_ref("consult-ticket", request_id)
    status = _ticket_status(req)
    timestamp = _request_timestamp(req)
    return {
        "id": ticket_id,
        "ticket_id": ticket_id,
        "title": _request_title(req),
        "description": _request_body(req),
        "status": status,
        "priority": _priority(req),
        "owner": "consultation-svc",
        "created_at": req.get("created_at") or timestamp,
        "updated_at": timestamp or req.get("created_at"),
        "closed_at": timestamp if status == "closed" else None,
        "archived_at": timestamp if status == "archived" else None,
        "lifecycle_history": [
            {
                "from_status": None,
                "to_status": status,
                "transitioned_at": timestamp or req.get("created_at"),
                "transitioned_by": "consultation-svc",
            }
        ],
        "linked_experiments": [],
        "linked_artifacts": [],
        "source_ref": f"consultation_request:{request_id}",
    }


def _project_request_insight(req: dict[str, Any]) -> dict[str, Any] | None:
    request_id = _record_id(req, "request_id", "id")
    if not request_id:
        return None
    timestamp = _request_timestamp(req)
    insight_id = _slug_ref("insight", request_id)
    return {
        "id": insight_id,
        "insight_id": insight_id,
        "summary": _request_title(req),
        "scope": str(req.get("target_type") or "consultation"),
        "status": "active",
        "tags": ["consultation", str(req.get("request_type") or "request")],
        "source_ref": f"consultation_request:{request_id}",
        "supporting_evidence_refs": list(req.get("evidence_refs") or []),
        "linked_sources": [{"type": "consultation_request", "id": request_id}],
        "aggregation_provenance": {"aggregated_at": timestamp},
        "created_at": req.get("created_at") or timestamp,
        "updated_at": timestamp or req.get("created_at"),
        "route_href": f"/agora/signals/{_slug_ref('sig', request_id)}",
    }


def _project_memo_insight(memo: dict[str, Any], request: dict[str, Any] | None) -> dict[str, Any] | None:
    memo_id = _record_id(memo, "memo_id", "id")
    if not memo_id:
        return None
    timestamp = _memo_timestamp(memo)
    request_id = _record_id(memo, "request_id")
    return {
        "id": _slug_ref("insight", memo_id),
        "insight_id": _slug_ref("insight", memo_id),
        "summary": _first_text(memo.get("summary"), _request_title(request or {}), memo_id),
        "scope": str((request or {}).get("target_type") or memo.get("target_type") or "consultation"),
        "status": "active" if _status_value(memo) == "published" else "classified",
        "tags": ["consultation", str(memo.get("memo_type") or "memo")],
        "source_ref": f"consultation_memo:{memo_id}",
        "supporting_evidence_refs": [
            ref_id
            for finding in _list(memo.get("findings"))
            if isinstance(finding, dict)
            for ref_id in _list(finding.get("evidence_refs"))
        ],
        "linked_sources": [{"type": "consultation_request", "id": request_id}],
        "aggregation_provenance": {"aggregated_at": timestamp},
        "created_at": memo.get("created_at") or timestamp,
        "updated_at": timestamp or memo.get("created_at"),
        "route_href": f"/agora/journal/{_slug_ref('journal', memo_id)}",
    }


def _project_note_from_transcript(req: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any] | None:
    request_id = _record_id(req, "request_id", "id")
    if not request_id or not events:
        return None
    note_id = _slug_ref("note", request_id)
    body = "\n".join(
        f"{_actor_id(event.get('actor')) or 'participant'}: {_message_content(event)}"
        for event in sorted(events, key=lambda item: int(item.get("sequence_no") or 0))
    )
    timestamp = events[-1].get("event_time") or _request_timestamp(req)
    return {
        "id": note_id,
        "note_id": note_id,
        "title": _request_title(req),
        "body": body,
        "tags": ["consultation", "transcript"],
        "linked_ticket_id": _slug_ref("consult-ticket", request_id),
        "created_at": req.get("created_at") or timestamp,
        "updated_at": timestamp,
        "route_href": f"/agora/notes/{note_id}",
    }


def _project_journal_entry(memo: dict[str, Any], request: dict[str, Any] | None) -> dict[str, Any] | None:
    memo_id = _record_id(memo, "memo_id", "id")
    if not memo_id:
        return None
    timestamp = _memo_timestamp(memo)
    return {
        "id": _slug_ref("journal", memo_id),
        "entry_id": _slug_ref("journal", memo_id),
        "title": _first_text(_request_title(request or {}), memo.get("memo_type"), memo_id),
        "body": _first_text(memo.get("summary"), "Consultation memo recorded."),
        "tags": ["consultation", str(memo.get("memo_type") or "memo")],
        "linkedStrategyIds": _linked_strategy_ids(request or {}),
        "linkedPersonaIds": _linked_persona_ids(request or {}),
        "visibility": "private",
        "createdAt": memo.get("created_at") or timestamp,
        "updatedAt": timestamp or memo.get("created_at"),
        "version": 1,
        "source_ref": f"consultation_memo:{memo_id}",
    }


def _project_handoff(handoff: dict[str, Any], request: dict[str, Any] | None) -> dict[str, Any] | None:
    handoff_id = _record_id(handoff, "handoff_id", "id")
    if not handoff_id:
        return None
    request_id = _record_id(handoff, "request_id")
    timestamp = _first_text(handoff.get("sent_at"), handoff.get("created_at"), _request_timestamp(request or {}))
    return {
        "id": handoff_id,
        "handoffId": handoff_id,
        "handoffType": str(handoff.get("target_gate") or "consultation_gate_handoff"),
        "status": str(handoff.get("status") or "pending"),
        "source": {
            "app": "consultation",
            "route": f"/api/consult/requests/{request_id}/handoffs",
            "entity": {"type": "consult_gate_handoff", "id": handoff_id},
        },
        "destination": {
            "app": "management",
            "route": f"/governance/{handoff.get('target_gate') or 'review'}",
            "queue": str(handoff.get("target_gate") or "governance"),
        },
        "priority": _priority(request or {}),
        "slaDueAt": timestamp,
        "rerouteCount": 0,
        "payload": {
            "requestId": request_id,
            "memoIds": list(handoff.get("memo_ids") or []),
            "evidenceRefs": list(handoff.get("evidence_refs") or []),
            "auditRefs": list(handoff.get("audit_refs") or []),
        },
        "createdBy": {"type": "service", "id": "consultation-svc"},
        "createdAt": handoff.get("created_at") or timestamp,
        "updatedAt": timestamp,
        "canonicalWriteAuthority": "consultation-svc",
    }


def _project_training_example(req: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any] | None:
    request_id = _record_id(req, "request_id", "id")
    if not request_id or len(events) < 2:
        return None
    ordered = sorted(events, key=lambda item: int(item.get("sequence_no") or 0))
    first = ordered[0]
    last = ordered[-1]
    timestamp = last.get("event_time") or _request_timestamp(req)
    example_id = _slug_ref("trn-agora", request_id)
    return {
        "id": example_id,
        "trainingExampleId": example_id,
        "source": "consultation_transcript",
        "personaId": _actor_id(last.get("actor")),
        "input": {
            "requestId": request_id,
            "message": _message_content(first),
            "target": {"type": req.get("target_type"), "id": req.get("target_id")},
        },
        "expected": {
            "message": _message_content(last),
            "memoIds": [],
        },
        "labels": ["consultation", str(req.get("request_type") or "request")],
        "status": "published" if _status_value(req) == "published" else "draft",
        "createdBy": "consultation-svc",
        "createdAt": req.get("created_at") or timestamp,
        "updatedAt": timestamp,
    }


def _project_postmortem(req: dict[str, Any], memos: list[dict[str, Any]], handoffs: list[dict[str, Any]]) -> dict[str, Any] | None:
    request_id = _record_id(req, "request_id", "id")
    if not request_id:
        return None
    request_type = str(req.get("request_type") or "").strip().lower()
    status = _status_value(req)
    if request_type != "incident" and status not in TERMINAL_REQUEST_STATUSES:
        return None
    timestamp = _request_timestamp(req)
    memo_summaries = [_first_text(memo.get("summary")) for memo in memos if _first_text(memo.get("summary"))]
    return {
        "id": _slug_ref("pm", request_id),
        "postmortem_id": _slug_ref("pm", request_id),
        "title": _request_title(req),
        "incident_id": req.get("target_id") if request_type == "incident" else None,
        "summary": _first_text(*memo_summaries, _request_body(req)),
        "status": "completed" if status == "published" else status or "recorded",
        "root_cause": "consultation_review",
        "action_items": [
            {
                "id": _slug_ref("handoff-action", _record_id(handoff, "handoff_id", "id")),
                "title": f"Follow consultation handoff {_record_id(handoff, 'handoff_id', 'id')}",
                "status": str(handoff.get("status") or "pending"),
            }
            for handoff in handoffs
            if _record_id(handoff, "handoff_id", "id")
        ],
        "created_at": req.get("created_at") or timestamp,
        "updated_at": timestamp,
        "source_ref": f"consultation_request:{request_id}",
    }


def _load_from_service(consultation_url: str) -> dict[str, list[dict[str, Any]]]:
    base = consultation_url.rstrip("/")
    return {
        "requests": _items(_get(f"{base}/api/consult/requests")),
        "memos": _items(_get(f"{base}/api/consult/memos")),
        "transcripts": _items(_get(f"{base}/api/consult/transcripts")),
        "handoffs": _items(_get(f"{base}/api/consult/handoffs")),
    }


def _load_from_data_dir(data_dir: str | os.PathLike[str]) -> dict[str, list[dict[str, Any]]]:
    from services.consultation.store import ConsultationStore

    store = ConsultationStore(str(data_dir))
    return {
        "requests": [_model_to_data(item) for item in store.list_requests()],
        "memos": [_model_to_data(item) for item in store.list_memos()],
        "transcripts": [_model_to_data(item) for item in store.list_transcripts()],
        "handoffs": [_model_to_data(item) for item in store.list_handoffs()],
    }


def project(
    consultation_url: str | None = None,
    *,
    data_dir: str | os.PathLike[str] | None = None,
) -> StoreMap:
    source = (
        _load_from_service(consultation_url)
        if consultation_url
        else _load_from_data_dir(data_dir)
        if data_dir
        else {"requests": [], "memos": [], "transcripts": [], "handoffs": []}
    )
    requests = source["requests"]
    memos = source["memos"]
    transcripts = source["transcripts"]
    handoffs = source["handoffs"]

    requests_by_id = {_record_id(req, "request_id", "id"): req for req in requests if _record_id(req, "request_id", "id")}
    events_by_request = _events_by_request(transcripts)
    memos_by_request = _memos_by_request(memos)
    handoffs_by_request = _handoffs_by_request(handoffs)

    stores: StoreMap = {dataset: {} for dataset in DATASET_FILENAMES}

    for req in requests:
        request_id = _record_id(req, "request_id", "id")
        if not request_id:
            continue
        request_memos = memos_by_request.get(request_id, [])
        request_events = events_by_request.get(request_id, [])
        request_handoffs = handoffs_by_request.get(request_id, [])

        signal = _project_signal(req, request_memos)
        if signal:
            stores["agora_signals"][str(signal["signal_id"])] = signal

        session = _project_session(req, events=request_events, memos=request_memos)
        if session:
            stores["agora_sessions"][str(session["sessionId"])] = session

        ticket = _project_ticket(req)
        if ticket:
            stores["research_tickets"][str(ticket["ticket_id"])] = ticket

        request_insight = _project_request_insight(req)
        if request_insight:
            stores["insight_cards"][str(request_insight["insight_id"])] = request_insight

        note = _project_note_from_transcript(req, request_events)
        if note:
            stores["research_notes"][str(note["note_id"])] = note

        training_example = _project_training_example(req, request_events)
        if training_example:
            stores["agora_training_examples"][str(training_example["trainingExampleId"])] = training_example

        postmortem = _project_postmortem(req, request_memos, request_handoffs)
        if postmortem:
            stores["postmortems"][str(postmortem["postmortem_id"])] = postmortem

    for memo in memos:
        request_id = _record_id(memo, "request_id")
        req = requests_by_id.get(request_id)
        insight = _project_memo_insight(memo, req)
        if insight:
            stores["insight_cards"][str(insight["insight_id"])] = insight
        journal = _project_journal_entry(memo, req)
        if journal:
            stores["decision_journal_entries"][str(journal["entry_id"])] = journal

    for handoff in handoffs:
        request_id = _record_id(handoff, "request_id")
        projected = _project_handoff(handoff, requests_by_id.get(request_id))
        if projected:
            stores["agora_handoffs"][str(projected["handoffId"])] = projected

    return stores


def write_projection(stores: StoreMap, out_dir: str | os.PathLike[str]) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for dataset, filename in DATASET_FILENAMES.items():
        payload = stores.get(dataset, {})
        (out / filename).write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def main() -> int:
    consultation_url = os.environ.get("CONSULTATION_URL", "").strip() or None
    data_dir = os.environ.get("CONSULTATION_DATA_DIR", "").strip() or None
    if consultation_url is None and data_dir is None:
        consultation_url = "http://consultation-svc:8096"
    out_dir = os.environ.get("OUT_DIR", "/data/bff")
    stores = project(consultation_url, data_dir=data_dir)
    write_projection(stores, out_dir)
    print(
        "projected "
        f"{len(stores['agora_signals'])} signals, "
        f"{len(stores['agora_sessions'])} sessions, "
        f"{len(stores['agora_handoffs'])} handoffs, "
        f"{len(stores['agora_training_examples'])} training examples, "
        f"{len(stores['research_tickets'])} inbox research tasks, "
        f"{len(stores['research_notes'])} notes, "
        f"{len(stores['insight_cards'])} insights, "
        f"{len(stores['decision_journal_entries'])} journal entries, "
        f"{len(stores['postmortems'])} postmortems -> {out_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
