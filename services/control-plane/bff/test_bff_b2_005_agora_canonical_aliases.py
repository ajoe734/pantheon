from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from fastapi.testclient import TestClient


from services.control_plane.bff import main as bff_main
from services.control_plane.bff.ports import ReadSurfacePorts, create_in_memory_read_surface_ports


HEADERS = {"Authorization": "Bearer op-bff-b2-005:operator"}


class AgoraAliasesTestReadPorts(ReadSurfacePorts):
    def __init__(self, data: dict | None = None) -> None:
        super().__init__()
        self._data = data or {}

    def dataset_source(self, dataset: str) -> str:
        return "local_snapshot" if self._data else "missing"

    def dataset_surface_status(self, dataset: str, *, snapshot_at: str, **kwargs: Any) -> dict[str, Any]:
        return {"status": "degraded", "source": "local_snapshot", "snapshot_at": snapshot_at}

    def list_agora_watchlist(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._data.get("agora_watchlist", {}).values())

    def list_agora_signals(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._data.get("agora_signals", {}).values())

    def get_agora_signal(self, signal_id: str | None) -> dict[str, Any] | None:
        return self._data.get("agora_signals", {}).get(str(signal_id or ""))

    def list_agora_sessions(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._data.get("agora_sessions", {}).values())

    def get_agora_session(self, session_id: str | None) -> dict[str, Any] | None:
        return self._data.get("agora_sessions", {}).get(str(session_id or ""))

    def list_agora_messages(self, session_id: str | None, **kwargs: Any) -> list[dict[str, Any]]:
        sess = self.get_agora_session(session_id)
        return list(sess.get("messages", [])) if sess else []

    def list_agora_session_messages(self, session_id: str | None, **kwargs: Any) -> list[dict[str, Any]]:
        return self.list_agora_messages(session_id, **kwargs)

    def list_agora_notes(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._data.get("research_notes", {}).values())

    def list_research_notes(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.list_agora_notes(**kwargs)

    def list_decision_journal_entries(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._data.get("decision_journal_entries", {}).values())

    def list_research_tickets(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._data.get("research_tickets", {}).values())

    def list_postmortems(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._data.get("postmortems", {}).values())

    def list_agora_postmortems(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.list_postmortems(**kwargs)

    def list_agora_handoffs(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._data.get("agora_handoffs", {}).values())

    def get_agora_handoff(self, handoff_id: str | None) -> dict[str, Any] | None:
        return self._data.get("agora_handoffs", {}).get(str(handoff_id or ""))

    def list_insight_cards(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._data.get("insight_cards", {}).values())

    def list_agora_insights(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.list_insight_cards(**kwargs)

    def _read_dataset_records(self, dataset: str) -> list[dict[str, Any]]:
        if dataset in ("insight_cards", "insights"):
            return self.list_insight_cards()
        if dataset == "agora_signals":
            return self.list_agora_signals()
        if dataset == "agora_sessions":
            return self.list_agora_sessions()
        if dataset == "research_tickets":
            return self.list_research_tickets()
        if dataset == "postmortems":
            return self.list_postmortems()
        if dataset == "research_notes":
            return self.list_agora_notes()
        if dataset == "decision_journal_entries":
            return self.list_decision_journal_entries()
        if dataset == "agora_handoffs":
            return self.list_agora_handoffs()
        if dataset == "agora_watchlist":
            return self.list_agora_watchlist()
        raw = self._data.get(dataset, {})
        return list(raw.values()) if isinstance(raw, dict) else list(raw)


def _seed_read_store() -> AgoraAliasesTestReadPorts:
    data = {
        "agora_watchlist": {
            "AAPL": {
                "id": "watch-AAPL",
                "symbol": "AAPL",
                "return1dPct": 2.6,
            }
        },
        "agora_signals": {
            "sig-b2-005": {
                "id": "sig-b2-005",
                "signal_id": "sig-b2-005",
                "title": "B2 signal",
                "reviewStatus": "pending_trader_review",
                "createdAt": "2026-05-23T06:04:00Z",
                "updatedAt": "2026-05-23T06:04:00Z",
            }
        },
        "insight_cards": {
            "ins-b2-005": {
                "id": "ins-b2-005",
                "insight_id": "ins-b2-005",
                "summary": "Inbox insight for B2 acceptance.",
                "created_at": "2026-05-23T06:05:00Z",
                "updated_at": "2026-05-23T06:05:00Z",
            }
        },
        "agora_sessions": {
            "sess-b2-005": {
                "id": "sess-b2-005",
                "sessionId": "sess-b2-005",
                "title": "Canonical alias session",
                "status": "active",
                "createdAt": "2026-05-23T06:00:00Z",
                "updatedAt": "2026-05-23T06:00:00Z",
            },
            "ask-b2-005": {
                "id": "ask-b2-005",
                "sessionId": "ask-b2-005",
                "title": "Quick ask alias session",
                "mode": "quick_ask",
                "status": "active",
                "createdAt": "2026-05-23T06:06:00Z",
                "updatedAt": "2026-05-23T06:06:00Z",
            }
        },
        "research_notes": {
            "note-b2-005": {
                "id": "note-b2-005",
                "note_id": "note-b2-005",
                "title": "Market note",
                "body": "Alias route should share the notes read model.",
                "created_at": "2026-05-23T06:00:00Z",
                "updated_at": "2026-05-23T06:00:00Z",
            }
        },
        "decision_journal_entries": {
            "journal-b2-005": {
                "id": "journal-b2-005",
                "entry_id": "journal-b2-005",
                "title": "Alias journal",
                "body": "Decision journal alias coverage.",
                "created_at": "2026-05-23T06:00:00Z",
                "updated_at": "2026-05-23T06:00:00Z",
            }
        },
        "postmortems": {
            "pm-b2-005": {
                "id": "pm-b2-005",
                "postmortem_id": "pm-b2-005",
                "title": "Agora B2 postmortem",
                "status": "published",
                "created_at": "2026-05-23T06:03:00Z",
                "updated_at": "2026-05-23T06:03:00Z",
            }
        },
        "research_tickets": {
            "rt-b2-005": {
                "ticket_id": "rt-b2-005",
                "title": "Alias research task",
                "description": "Research task alias coverage.",
                "status": "new",
                "priority": "normal",
                "owner": "research",
                "created_at": "2026-05-23T06:00:00Z",
                "updated_at": "2026-05-23T06:00:00Z",
            }
        },
        "agora_handoffs": {
            "handoff-b2-005": {
                "id": "handoff-b2-005",
                "handoffId": "handoff-b2-005",
                "handoffType": "consult_memo_to_management_review",
                "status": "submitted",
                "priority": "normal",
                "createdAt": "2026-05-23T06:00:00Z",
                "updatedAt": "2026-05-23T06:00:00Z",
            }
        },
    }
    return AgoraAliasesTestReadPorts(data)


@contextmanager
def _isolated_bff() -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        bff_main.read_store = _seed_read_store()
        try:
            yield TestClient(bff_main.app)
        finally:
            bff_main.read_store = original_store


def _records(payload: dict) -> list[dict]:
    for key in ("items", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    raise AssertionError(f"payload has no list records: {payload}")


def _record_ids(payload: dict, key: str) -> list[str]:
    return [str(item.get(key) or "") for item in _records(payload)]


def test_bff_b2_005_agora_aliases_share_canonical_read_surfaces() -> None:
    cases = [
        ("/bff/agora/markets", "/bff/agora/watchlist", "agora_watchlist", "symbol", "AAPL"),
        (
            "/bff/agora/committee-sessions",
            "/bff/agora/sessions",
            "agora_session_list",
            "sessionId",
            "sess-b2-005",
        ),
        ("/bff/agora/market-notes", "/bff/agora/notes", "agora_note_list", "id", "note-b2-005"),
        (
            "/bff/agora/decision-journal",
            "/bff/agora/journal",
            "agora_journal_list",
            "id",
            "journal-b2-005",
        ),
        (
            "/bff/agora/research-tasks",
            "/bff/research/tasks",
            "research_task_list",
            "ticket_id",
            "rt-b2-005",
        ),
        (
            "/bff/agora/incoming",
            "/bff/agora/handoffs",
            "agora_handoff_list",
            "handoffId",
            "handoff-b2-005",
        ),
    ]

    with _isolated_bff() as client:
        for alias_path, canonical_path, surface_key, id_key, expected_id in cases:
            alias_response = client.get(alias_path, headers=HEADERS)
            canonical_response = client.get(canonical_path, headers=HEADERS)

            assert alias_response.status_code == 200, alias_response.text
            assert canonical_response.status_code == 200, canonical_response.text
            alias_payload = alias_response.json()
            canonical_payload = canonical_response.json()
            assert _record_ids(alias_payload, id_key) == _record_ids(canonical_payload, id_key)
            assert expected_id in _record_ids(alias_payload, id_key)
            assert surface_key in alias_payload["meta"]["surfaces"]
            alias_surface = alias_payload["meta"]["surfaces"][surface_key]
            canonical_surface = canonical_payload["meta"]["surfaces"][surface_key]
            assert alias_surface["status"] == canonical_surface["status"]
            assert alias_surface["source"] == canonical_surface["source"]


def test_bff_b2_005_agora_core_routes_return_envelopes_and_composite_inbox() -> None:
    with _isolated_bff() as client:
        cases = [
            ("/bff/agora/signals", "agora_signal_list", "signal_id", "sig-b2-005"),
            ("/bff/agora/journal", "agora_journal_list", "id", "journal-b2-005"),
            ("/bff/agora/postmortems", "agora_postmortems", "postmortem_id", "pm-b2-005"),
            ("/bff/agora/ask/sessions", "agora_ask_sessions", "sessionId", "ask-b2-005"),
        ]

        for path, surface_key, id_key, expected_id in cases:
            response = client.get(path, headers=HEADERS)

            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["data"] == payload["items"]
            assert expected_id in _record_ids(payload, id_key)
            assert payload["page_info"]["next_page_token"] is None
            surface = payload["meta"]["surfaces"][surface_key]
            assert surface["source"] in {"local_snapshot", "bff_local"}
            assert surface["status"] in {"degraded", "ok"}

        detail_response = client.get("/bff/agora/ask/sessions/ask-b2-005", headers=HEADERS)
        assert detail_response.status_code == 200, detail_response.text
        detail_payload = detail_response.json()
        assert detail_payload["data"]["sessionId"] == "ask-b2-005"
        assert "agora_ask_session_detail" in detail_payload["meta"]["surfaces"]

        inbox_response = client.get("/bff/agora/inbox", headers=HEADERS)
        assert inbox_response.status_code == 200, inbox_response.text
        inbox_payload = inbox_response.json()
        assert inbox_payload["data"] == inbox_payload["items"]
        items = inbox_payload["items"]
        assert any(
            item.get("inboxType") == "insight"
            and (item.get("id") or item.get("insight_id")) == "ins-b2-005"
            for item in items
        )
        assert any(
            item.get("inboxType") == "signal" and item.get("signal_id") == "sig-b2-005"
            for item in items
        )
        assert any(
            item.get("inboxType") == "research_task" and item.get("ticket_id") == "rt-b2-005"
            for item in items
        )
        counts = inbox_payload["meta"]["composition"]["itemCounts"]
        assert counts["insight"] >= 1
        assert counts["signal"] >= 1
        assert counts["research_task"] >= 1
        for surface_key in (
            "agora_inbox",
            "agora_inbox_insights",
            "agora_inbox_signals",
            "agora_inbox_research_tasks",
        ):
            assert surface_key in inbox_payload["meta"]["surfaces"]
