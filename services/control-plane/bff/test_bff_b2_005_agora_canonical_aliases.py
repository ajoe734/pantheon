from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from read_store import ReadSurfaceStore


HEADERS = {"Authorization": "Bearer op-bff-b2-005:operator"}


def _seed_read_store(path: Path) -> ReadSurfaceStore:
    path.write_text(
        json.dumps(
            {
                "agora_watchlist": {
                    "AAPL": {
                        "id": "watch-AAPL",
                        "symbol": "AAPL",
                        "return1dPct": 2.6,
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
        ),
        encoding="utf-8",
    )
    return ReadSurfaceStore(str(path), allow_local_snapshot_fallback=True)


@contextmanager
def _isolated_bff() -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        bff_main.read_store = _seed_read_store(Path(td) / "read_surfaces.json")
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
