from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from read_store import ReadSurfaceStore


OPERATOR_TOKEN = "Bearer op-2:operator"


def _seeded_store(path: Path) -> ReadSurfaceStore:
    path.write_text(
        json.dumps(
            {
                "decision_journal_entries": {
                    "journal-001": {
                        "id": "journal-001",
                        "title": "Original decision",
                        "body": "Initial body",
                        "tags": ["risk.review"],
                        "linkedStrategyIds": ["strategy-alpha"],
                        "linkedPersonaIds": ["persona-alpha"],
                        "visibility": "private",
                        "createdAt": "2026-05-07T12:00:00Z",
                        "updatedAt": "2026-05-07T12:00:00Z",
                        "version": 1,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return ReadSurfaceStore(str(path), allow_local_snapshot_fallback=False)


def _headers(key: str, *, content_type: str = "application/merge-patch+json") -> dict[str, str]:
    return {
        "Authorization": OPERATOR_TOKEN,
        "Idempotency-Key": key,
        "X-Correlation-Id": "corr-journal-001",
        "Content-Type": content_type,
    }


def test_agora_journal_patch_rejects_non_merge_patch_content_type() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        bff_main.read_store = _seeded_store(Path(td) / "read_surfaces.json")
        client = TestClient(bff_main.app)

        try:
            response = client.patch(
                "/bff/agora/journal/journal-001",
                headers=_headers("idem-journal-content-type", content_type="application/json"),
                json={"title": "Updated decision"},
            )

            assert response.status_code == 415, response.text
            detail = response.json()["detail"]
            assert detail["error"]["code"] == "INVALID_REQUEST"
            assert detail["error"]["details"]["precondition_failed"] == "content_type"
        finally:
            bff_main.read_store = original_store


def test_agora_journal_patch_rejects_body_idempotency_key() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        bff_main.read_store = _seeded_store(Path(td) / "read_surfaces.json")
        client = TestClient(bff_main.app)

        try:
            response = client.patch(
                "/bff/agora/journal/journal-001",
                headers=_headers("idem-journal-body-idempotency"),
                json={
                    "title": "Updated decision",
                    "idempotencyKey": "must-not-be-here",
                },
            )

            assert response.status_code == 400, response.text
            detail = response.json()["detail"]
            assert detail["error"]["code"] == "INVALID_REQUEST"
            assert detail["error"]["details"]["precondition_failed"] == "body_idempotency_key"
        finally:
            bff_main.read_store = original_store


def test_agora_journal_patch_returns_data_and_audit_diff() -> None:
    with tempfile.TemporaryDirectory() as td:
        store_path = Path(td) / "read_surfaces.json"
        original_store = bff_main.read_store
        bff_main.read_store = _seeded_store(store_path)
        client = TestClient(bff_main.app)

        try:
            response = client.patch(
                "/bff/agora/journal/journal-001",
                headers=_headers("idem-journal-success"),
                json={
                    "title": "Approved rollout notes",
                    "body": "The committee approved the paper rollout.",
                    "tags": ["paper.rollout", "committee-review"],
                    "linkedStrategyIds": ["strategy-alpha", "strategy-beta"],
                    "visibility": "team",
                },
            )

            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["status"] == "completed"
            assert payload["data"]["id"] == "journal-001"
            assert payload["data"]["title"] == "Approved rollout notes"
            assert payload["data"]["linkedStrategyIds"] == ["strategy-alpha", "strategy-beta"]
            assert payload["data"]["canonicalWriteAuthority"] == "agora_journal_service"
            assert payload["data"]["persistenceMode"] == "bff_local_dev_store"

            audit = payload["meta"]["audit"]
            assert audit["action"] == "agora.journal.merge_patch"
            assert audit["correlationId"] == "corr-journal-001"
            assert audit["degraded"] is True
            assert audit["diff"]["before"]["title"] == "Original decision"
            assert audit["diff"]["after"]["title"] == "Approved rollout notes"
            assert set(audit["diff"]["changedFields"]) >= {
                "title",
                "body",
                "tags",
                "linkedStrategyIds",
                "visibility",
            }

            stored = json.loads(store_path.read_text(encoding="utf-8"))
            audit_records = stored["agora_journal_audit_events"]
            assert len(audit_records) == 1
            stored_audit = next(iter(audit_records.values()))
            assert stored_audit["diff"]["before"]["body"] == "Initial body"
            assert stored_audit["diff"]["after"]["body"] == "The committee approved the paper rollout."
        finally:
            bff_main.read_store = original_store


def test_agora_journal_patch_idempotency_conflict_rejected() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        bff_main.read_store = _seeded_store(Path(td) / "read_surfaces.json")
        client = TestClient(bff_main.app)

        try:
            headers = _headers("idem-journal-conflict")
            first = client.patch(
                "/bff/agora/journal/journal-001",
                headers=headers,
                json={"title": "First patch"},
            )
            second = client.patch(
                "/bff/agora/journal/journal-001",
                headers=headers,
                json={"title": "Different patch"},
            )

            assert first.status_code == 200, first.text
            assert second.status_code == 409, second.text
            detail = second.json()["detail"]
            assert detail["error"]["code"] == "IDEMPOTENCY_CONFLICT"
            assert detail["error"]["details"]["precondition_failed"] == "idempotency_conflict"
        finally:
            bff_main.read_store = original_store
