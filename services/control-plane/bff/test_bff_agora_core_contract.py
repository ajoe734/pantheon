from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from datetime import datetime, timezone
from typing import Any, Iterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services.control_plane.bff.agora.router import create_agora_router
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.models import CommandType, OperatorIdentity
from services.control_plane.bff.ports import ReadSurfacePorts, create_in_memory_read_surface_ports


OPERATOR_TOKEN = "Bearer op-agora:operator"
HEADERS = {"Authorization": OPERATOR_TOKEN}


def _extract_identity(authorization: str | None) -> OperatorIdentity:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    raw = authorization[len("Bearer "):].strip()
    parts = raw.split(":")
    operator_id = parts[0] if parts else "op"
    roles = parts[1].split(",") if len(parts) > 1 else []
    return OperatorIdentity(operator_id=operator_id, roles=roles, claims={})


def _require_role(identity: OperatorIdentity) -> None:
    if not identity or not identity.roles:
        raise HTTPException(status_code=403, detail="Forbidden")


def _bff_error(
    status_code: int,
    code: Any,
    message: str,
    reason: str = "",
    precondition_failed: Optional[str] = None,
    suggestion: Optional[str] = None,
    details_extra: Optional[dict[str, Any]] = None,
    **kwargs: Any,
) -> HTTPException:
    val = code.value if hasattr(code, "value") else str(code)
    details: dict[str, Any] = {
        "reason": reason or message,
        "precondition_failed": precondition_failed,
        "suggestion": suggestion,
    }
    if details_extra:
        details.update(details_extra)
    return HTTPException(
        status_code=status_code,
        detail={
            "error": {
                "code": val,
                "message": message,
                "details": details,
            }
        },
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error(resp):
    body = resp.json()
    if isinstance(body.get("error"), dict):
        return body["error"]
    detail = body.get("detail")
    if isinstance(detail, dict) and isinstance(detail.get("error"), dict):
        return detail["error"]
    raise AssertionError(f"response did not contain BFF error envelope: {body}")


class AgoraCoreTestReadPorts(ReadSurfacePorts):
    def __init__(self, data: dict | None = None) -> None:
        super().__init__()
        self._data = data or {}

    def list_agora_signals(self, *, review_status: str | None = None, **kwargs: Any) -> list[dict[str, Any]]:
        items = list(self._data.get("agora_signals", {}).values())
        if review_status:
            items = [item for item in items if str(item.get("reviewStatus") or item.get("review_status") or "").strip() == review_status]
        return items

    def get_agora_signal(self, signal_id: str | None) -> dict[str, Any] | None:
        return self._data.get("agora_signals", {}).get(str(signal_id or ""))

    def patch_agora_signal(self, signal_id: str, *, review_status: str | None = None, **kwargs: Any) -> dict[str, Any] | None:
        sig = self._data.get("agora_signals", {}).get(signal_id)
        if sig:
            if review_status is not None:
                sig["reviewStatus"] = review_status
                sig["review_status"] = review_status
            sig.update(kwargs)
            return sig
        return None

    def list_agora_watchlist(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._data.get("agora_watchlist", {}).values())

    def list_agora_sessions(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._data.get("agora_sessions", {}).values())

    def list_agora_messages(self, session_id: str | None, **kwargs: Any) -> list[dict[str, Any]]:
        sess = self._data.get("agora_sessions", {}).get(str(session_id or ""))
        if sess and "messages" in sess:
            return list(sess["messages"])
        return []

    def list_agora_session_messages(self, session_id: str | None, **kwargs: Any) -> list[dict[str, Any]]:
        return self.list_agora_messages(session_id, **kwargs)

    def get_agora_session(self, session_id: str | None) -> dict[str, Any] | None:
        return self._data.get("agora_sessions", {}).get(str(session_id or ""))

    def get_agora_message(self, message_id: str | None) -> dict[str, Any] | None:
        clean_id = str(message_id or "")
        for sess in self._data.get("agora_sessions", {}).values():
            for msg in sess.get("messages", []):
                if msg.get("id") == clean_id or msg.get("message_id") == clean_id:
                    return msg
        return {"id": clean_id, "session_id": "sess-001"} if clean_id == "msg-001" else None

    def list_research_tickets(self, *, statuses: list[str] | None = None, owner: str | None = None, include_fixture_pack: bool = False, **kwargs: Any) -> list[dict[str, Any]]:
        tickets = list(self._data.get("research_tickets", {}).values())
        if statuses:
            tickets = [t for t in tickets if str(t.get("status") or "") in statuses]
        if owner:
            tickets = [t for t in tickets if str(t.get("owner") or "") == owner]
        return tickets

    def get_research_ticket(self, ticket_id: str | None) -> dict[str, Any] | None:
        return self._data.get("research_tickets", {}).get(str(ticket_id or ""))

    def list_decision_journal_entries(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._data.get("decision_journal_entries", {}).values())

    def create_decision_journal_entry(self, *, title: str, body: str, actor_id: str | None = None, **kwargs: Any) -> dict[str, Any]:
        entry = {"id": "dje-001", "title": title, "body": body, "author": actor_id or "op-agora", "canonicalWriteAuthority": "agora_journal_service"}
        self._data.setdefault("decision_journal_entries", {})[entry["id"]] = entry
        return entry

    def list_insight_cards(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._data.get("insight_cards", {}).values())

    def get_insight_card(self, card_id: str | None) -> dict[str, Any] | None:
        return self._data.get("insight_cards", {}).get(str(card_id or ""))

    def list_agora_insights(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.list_insight_cards(**kwargs)

    def get_agora_insight(self, card_id: str | None) -> dict[str, Any] | None:
        return self.get_insight_card(card_id)

    def create_agora_insight(self, *, summary: str, tags: list[str] | None = None, **kwargs: Any) -> dict[str, Any]:
        ins_id = "ins-002"
        ins = {"id": ins_id, "insight_id": ins_id, "summary": summary, "tags": tags or []}
        self._data.setdefault("insight_cards", {})[ins_id] = ins
        return ins

    def list_institutional_memory_entries(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._data.get("institutional_memory_entries", {}).values())

    def get_institutional_memory_entry(self, entry_id: str | None) -> dict[str, Any] | None:
        return self._data.get("institutional_memory_entries", {}).get(str(entry_id or ""))

    def list_agora_memory_entries(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.list_institutional_memory_entries(**kwargs)

    def get_agora_memory_entry(self, entry_id: str | None) -> dict[str, Any] | None:
        return self.get_institutional_memory_entry(entry_id)

    def list_agora_training_examples(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._data.get("agora_training_examples", {}).values())

    def create_agora_training_example(self, *, input: Any = None, expected: Any = None, labels: list[str] | None = None, **kwargs: Any) -> dict[str, Any]:
        ex_id = "trn-agora-001"
        ex = {"trainingExampleId": ex_id, "id": ex_id, "input": input or kwargs.get("input_data"), "expected": expected, "labels": labels or []}
        self._data.setdefault("agora_training_examples", {})[ex_id] = ex
        return ex

    def list_agora_notes(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._data.get("research_notes", {}).values())

    def create_agora_note(self, *, title: str, body: str, actor_id: str | None = None, **kwargs: Any) -> dict[str, Any]:
        note = {"id": "note-001", "title": title, "body": body, "created_by": actor_id or "op-agora"}
        self._data.setdefault("research_notes", {})[note["id"]] = note
        return note

    def record_agora_audit_event(self, event: dict[str, Any] | None = None, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return event or kwargs

    def record_agora_signal_feedback(self, signal_id: str | None = None, *args: Any, **kwargs: Any) -> dict[str, Any]:
        sid = signal_id or kwargs.get("signal_id") or kwargs.get("signalId") or ""
        fb_id = f"fb-{sid}"
        res = {
            "signal_id": sid,
            "signalId": sid,
            "feedback_id": fb_id,
            "feedbackId": fb_id,
            "id": fb_id,
            "decision": kwargs.get("decision", ""),
            "confidence": kwargs.get("confidence", 0),
            "reason": kwargs.get("reason", ""),
            "actor_id": kwargs.get("actor_id", "op-agora"),
            "created_at": kwargs.get("created_at", "2026-05-08T09:00:00Z"),
        }
        if args and isinstance(args[0], dict):
            res.update(args[0])
            if "signal_id" in args[0]:
                res["signalId"] = args[0]["signal_id"]
        sig = self._data.get("agora_signals", {}).get(sid)
        if sig and res.get("decision"):
            sig["reviewStatus"] = res["decision"]
            sig["review_status"] = res["decision"]
        return res

    def create_agora_feedback(self, *, signal_id: str, actor_id: str | None = None, decision: str = "", confidence: int = 0, reason: str = "", created_at: str | None = None, **kwargs: Any) -> dict[str, Any]:
        return {
            "feedback_id": f"fb-{signal_id}",
            "signal_id": signal_id,
            "actor_id": actor_id or "op-agora",
            "decision": decision,
            "confidence": confidence,
            "reason": reason,
            "created_at": created_at or "2026-05-08T09:00:00Z",
        }

    def dataset_source(self, dataset: str) -> str:
        return "in_memory"

    def dataset_surface_status(self, dataset: str, *, snapshot_at: str, **kwargs: Any) -> dict[str, Any]:
        return {"status": "ok", "source": "in_memory", "snapshot_at": snapshot_at}


def _seed_read_store() -> AgoraCoreTestReadPorts:
    data = {
        "agora_signals": {
            "sig-001": {
                "id": "sig-001",
                "signal_id": "sig-001",
                "title": "Opening auction momentum",
                "reviewStatus": "pending_trader_review",
                "conviction": 0.71,
                "alpha": "auction-momentum",
                "updatedAt": "2026-05-08T09:00:00Z",
            }
        },
        "agora_watchlist": {
            "AAPL": {
                "id": "watch-AAPL",
                "symbol": "AAPL",
                "return1dPct": 2.6,
            }
        },
        "agora_sessions": {
            "sess-001": {
                "id": "sess-001",
                "sessionId": "sess-001",
                "title": "Signal review",
                "status": "active",
                "messages": [
                    {
                        "id": "msg-001",
                        "sessionId": "sess-001",
                        "sender": {"type": "operator", "id": "op-agora"},
                        "role": "user",
                        "content": "Explain signal sig-001",
                        "language": "en-US",
                        "attachments": [],
                        "citations": [],
                        "annotations": [],
                        "createdAt": "2026-05-08T09:01:00Z",
                    }
                ],
                "createdAt": "2026-05-08T09:00:00Z",
                "updatedAt": "2026-05-08T09:01:00Z",
            }
        },
        "research_notes": {},
        "decision_journal_entries": {},
        "insight_cards": {
            "ins-001": {
                "id": "ins-001",
                "insight_id": "ins-001",
                "summary": "Auction momentum needs risk review",
                "scope": "strategy",
                "status": "classified",
                "confidence": {"score": 0.82},
                "tags": ["signal"],
                "source_ref": "agora:sig-001",
                "supporting_evidence_refs": [],
                "linked_sources": [],
                "aggregation_provenance": {"aggregated_at": "2026-05-08T09:02:00Z"},
                "created_at": "2026-05-08T09:02:00Z",
                "updated_at": "2026-05-08T09:02:00Z",
            }
        },
        "institutional_memory_entries": {
            "mem-001": {
                "id": "mem-001",
                "entry_id": "mem-001",
                "headline": "Auction slippage memory",
                "body": "Opening auction slippage should constrain momentum rollout.",
                "scope": {"type": "strategy", "id": "strategy-alpha"},
                "tags": ["risk"],
                "created_at": "2026-05-08T08:00:00Z",
                "updated_at": "2026-05-08T08:00:00Z",
            }
        },
        "agora_training_examples": {},
        "research_tickets": {
            "rt-001": {
                "ticket_id": "rt-001",
                "title": "Review signal feedback",
                "description": "Decide whether sig-001 should become a research ticket.",
                "status": "new",
                "priority": "normal",
                "owner": "research",
                "created_at": "2026-05-08T08:30:00Z",
                "updated_at": "2026-05-08T08:30:00Z",
            }
        },
    }
    return AgoraCoreTestReadPorts(data)


@contextmanager
def _isolated_agora_bff() -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        read_store = _seed_read_store()
        cmd_store = CommandStore(os.path.join(td, "commands.jsonl"))
        idempotency_store: dict[str, dict[str, Any]] = {}

        app = FastAPI(title="Agora Core Test App")

        @app.exception_handler(HTTPException)
        def _http_exception_handler(request: Any, exc: HTTPException) -> Any:
            detail = exc.detail
            if isinstance(detail, dict) and "error" in detail:
                content = detail
            elif isinstance(detail, dict):
                content = {"error": detail}
            else:
                content = {"error": {"message": str(detail), "code": "HTTP_ERROR"}}
            return JSONResponse(status_code=exc.status_code, content=content)

        app.include_router(
            create_agora_router(
                extract_identity=_extract_identity,
                require_read_role=_require_role,
                require_write_role=_require_role,
                require_operator_role=_require_role,
                require_journal_write_role=_require_role,
                require_agora_signal_write_role=_require_role,
                require_agora_bulk_feedback_role=_require_role,
                bff_error=_bff_error,
                utc_now=_utc_now,
                get_read_store=lambda: read_store,
                get_command_store=lambda: cmd_store,
                idempotency_store=idempotency_store,
                sync_servant_agent=lambda p: dict(p),
            )
        )
        yield TestClient(app, raise_server_exceptions=False)



def _assert_command(payload: dict, command: CommandType) -> None:
    assert payload["status"] == "accepted"
    assert payload["data"]["command"] == command.value
    assert payload["data"]["receipt"]["command"] == command.value
    assert payload["data"]["receipt"]["status"] == "accepted"


def test_agora_daily_signals_sessions_and_research_task_reads() -> None:
    with _isolated_agora_bff() as client:
        daily = client.get("/bff/agora/daily", headers=HEADERS)
        assert daily.status_code == 200, daily.text
        daily_payload = daily.json()
        assert daily_payload["data"]["kpis"]["watchlistMoveCount"] >= 1
        assert daily_payload["data"]["kpis"]["signalReviewQueue"] >= 1
        assert daily_payload["data"]["kpis"]["researchQuestionCount"] >= 1

        signals = client.get("/bff/agora/signals", headers=HEADERS)
        assert signals.status_code == 200, signals.text
        assert signals.json()["items"][0]["id"] == "sig-001"

        signal = client.get("/bff/agora/signals/sig-001", headers=HEADERS)
        assert signal.status_code == 200, signal.text
        assert signal.json()["data"]["signal_id"] == "sig-001"

        watchlist = client.get("/bff/agora/watchlist", headers=HEADERS)
        assert watchlist.status_code == 200, watchlist.text
        assert watchlist.json()["items"][0]["symbol"] == "AAPL"

        sessions = client.get("/bff/agora/sessions", headers=HEADERS)
        assert sessions.status_code == 200, sessions.text
        assert sessions.json()["items"][0]["sessionId"] == "sess-001"

        messages = client.get("/bff/agora/sessions/sess-001/messages", headers=HEADERS)
        assert messages.status_code == 200, messages.text
        assert messages.json()["items"][0]["id"] == "msg-001"

        tasks = client.get("/bff/research/tasks?status=new", headers=HEADERS)
        assert tasks.status_code == 200, tasks.text
        assert tasks.json()["items"][0]["ticket_id"] == "rt-001"


def test_agora_signal_feedback_validates_records_and_replays() -> None:
    with _isolated_agora_bff() as client:
        rejected = client.post(
            "/bff/agora/signals/sig-001/feedback",
            headers={**HEADERS, "Idempotency-Key": "agora-feedback-invalid"},
            json={"decision": "disagree", "confidence": 5},
        )
        assert rejected.status_code == 422, rejected.text
        assert _error(rejected)["details"]["precondition_failed"] == "signal_feedback.reason"

        body = {"decision": "disagree", "confidence": 5, "reason": "Auction slippage risk is elevated"}
        accepted = client.post(
            "/bff/agora/signals/sig-001/feedback",
            headers={**HEADERS, "Idempotency-Key": "agora-feedback-001"},
            json=body,
        )
        replay = client.post(
            "/bff/agora/signals/sig-001/feedback",
            headers={**HEADERS, "Idempotency-Key": "agora-feedback-001"},
            json=body,
        )

        assert accepted.status_code == 200, accepted.text
        assert replay.status_code == 200, replay.text
        payload = accepted.json()
        assert payload["status"] == "completed"
        assert payload["data"]["feedback"]["signalId"] == "sig-001"
        assert payload["data"]["signal"]["reviewStatus"] == "disagree"
        assert payload["meta"]["command"]["command"] == CommandType.AGORA_SIGNAL_FEEDBACK.value
        assert replay.json()["data"]["feedback"]["feedbackId"] == payload["data"]["feedback"]["feedbackId"]


def test_agora_note_journal_insight_and_training_creation() -> None:
    with _isolated_agora_bff() as client:
        note = client.post(
            "/bff/agora/notes",
            headers={**HEADERS, "Idempotency-Key": "agora-note-001"},
            json={"title": "Desk note", "body": "Signal reviewed by the trader.", "tags": ["signal"]},
        )
        assert note.status_code == 201, note.text
        assert note.json()["data"]["title"] == "Desk note"

        journal = client.post(
            "/bff/agora/journal",
            headers={**HEADERS, "Idempotency-Key": "agora-journal-001"},
            json={
                "title": "Delay promotion",
                "decision": "Keep sig-001 in paper observation.",
                "rationale": "Auction slippage risk remains elevated.",
                "tags": ["paper.rollout"],
                "linkedStrategyIds": ["strategy-alpha"],
            },
        )
        assert journal.status_code == 201, journal.text
        assert journal.json()["data"]["canonicalWriteAuthority"] == "agora_journal_service"

        journal_list = client.get("/bff/agora/journal", headers=HEADERS)
        assert journal_list.status_code == 200, journal_list.text
        assert journal_list.json()["items"][0]["title"] == "Delay promotion"

        insight = client.post(
            "/bff/agora/insights",
            headers={**HEADERS, "Idempotency-Key": "agora-insight-001"},
            json={"summary": "Auction risk should gate strategy promotion", "tags": ["risk"]},
        )
        assert insight.status_code == 201, insight.text
        assert insight.json()["data"]["summary"] == "Auction risk should gate strategy promotion"

        training = client.post(
            "/bff/agora/training-examples",
            headers={**HEADERS, "Idempotency-Key": "agora-training-001"},
            json={"input": {"signal": "sig-001"}, "expected": {"decision": "defer"}, "labels": ["risk"]},
        )
        assert training.status_code == 201, training.text
        assert training.json()["data"]["trainingExampleId"].startswith("trn-agora-")


def test_agora_action_routes_emit_command_envelopes() -> None:
    with _isolated_agora_bff() as client:
        message_action = client.post(
            "/bff/agora/messages/msg-001/actions/create-research-task",
            headers={**HEADERS, "Idempotency-Key": "agora-message-action-001"},
            json={"reason": "Escalate message to research"},
        )
        assert message_action.status_code == 202, message_action.text
        _assert_command(message_action.json(), CommandType.AGORA_MESSAGE_ACTION)

        insight_action = client.post(
            "/bff/agora/insights/ins-001/actions/convert-to-training",
            headers={**HEADERS, "Idempotency-Key": "agora-insight-action-001"},
            json={"reason": "Useful training example"},
        )
        assert insight_action.status_code == 202, insight_action.text
        _assert_command(insight_action.json(), CommandType.AGORA_INSIGHT_ACTION)

        memory_action = client.post(
            "/bff/agora/memory/mem-001/actions/merge",
            headers={**HEADERS, "Idempotency-Key": "agora-memory-action-001"},
            json={"reason": "Merge duplicate memory"},
        )
        assert memory_action.status_code == 202, memory_action.text
        _assert_command(memory_action.json(), CommandType.AGORA_MEMORY_ACTION)

        quarantine = client.post(
            "/bff/memory/mem-001/actions/quarantine",
            headers={**HEADERS, "Idempotency-Key": "agora-memory-quarantine-001"},
            json={"reason": "Sensitive memory review"},
        )
        assert quarantine.status_code == 202, quarantine.text
        _assert_command(quarantine.json(), CommandType.AGORA_MEMORY_ACTION)

        attach = client.post(
            "/bff/insights/ins-001/actions/attach-strategy",
            headers={**HEADERS, "Idempotency-Key": "agora-insight-attach-001"},
            json={"strategyId": "strategy-alpha"},
        )
        assert attach.status_code == 202, attach.text
        _assert_command(attach.json(), CommandType.AGORA_INSIGHT_ACTION)


def test_agora_core_error_envelopes_for_missing_objects() -> None:
    with _isolated_agora_bff() as client:
        signal = client.get("/bff/agora/signals/no-such-signal", headers=HEADERS)
        assert signal.status_code == 404
        assert _error(signal)["code"] == "RESOURCE_NOT_FOUND"

        memory = client.post(
            "/bff/memory/no-such-memory/actions/quarantine",
            headers={**HEADERS, "Idempotency-Key": "agora-missing-memory"},
            json={"reason": "test"},
        )
        assert memory.status_code == 404
        assert _error(memory)["details"]["precondition_failed"] == "memory_id"
