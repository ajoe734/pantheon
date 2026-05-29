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


BASE_HEADERS = {
    "Authorization": "Bearer analyst-agora:analyst",
    "X-BFF-Api-Version": "2026-05-07",
    "X-Request-Id": "req-write-gap-agora-signal",
}
READ_HEADERS = {
    **BASE_HEADERS,
    "Authorization": "Bearer op-agora:operator",
}


def _seed_read_store(path: Path) -> ReadSurfaceStore:
    path.write_text(
        json.dumps(
            {
                "agora_signals": {},
                "agora_audit_events": {},
                "agora_signal_feedback": {},
            }
        ),
        encoding="utf-8",
    )
    return ReadSurfaceStore(str(path), allow_local_snapshot_fallback=True)


@contextmanager
def _isolated_bff() -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        store_path = Path(td) / "read_surfaces.json"
        bff_main.read_store = _seed_read_store(store_path)
        bff_main._AGORA_CORE_BFF_IDEMPOTENCY.clear()
        bff_main._sse_buffers["signal"].clear()
        bff_main._sse_buffers["inbox"].clear()
        try:
            yield TestClient(bff_main.app)
        finally:
            bff_main.read_store = original_store
            bff_main._AGORA_CORE_BFF_IDEMPOTENCY.clear()
            bff_main._sse_buffers["signal"].clear()
            bff_main._sse_buffers["inbox"].clear()


def test_bff_agora_signal_create_returns_201_persists_and_replays() -> None:
    with _isolated_bff() as client:
        body = {
            "id": "sig-write-gap-001",
            "title": "Opening auction momentum",
            "body": "Review a new opening auction momentum signal.",
            "market": "US",
            "tags": ["auction", "momentum"],
            "linkedPersonaIds": ["persona-paper-owner"],
            "linkedStrategyIds": ["strategy-alpha"],
            "severity": "warn",
        }
        headers = {
            **BASE_HEADERS,
            "Idempotency-Key": "agora-signal-create-001",
            "X-Correlation-Id": "corr-agora-signal-create-001",
        }

        response = client.post("/bff/agora/signals", headers=headers, json=body)
        replay = client.post("/bff/agora/signals", headers=headers, json=body)

        assert response.status_code == 201, response.text
        assert replay.status_code == 201, replay.text
        assert response.headers["X-Correlation-Id"] == "corr-agora-signal-create-001"
        payload = response.json()
        assert payload["data"]["id"] == "sig-write-gap-001"
        assert payload["data"]["status"] == "open"
        assert payload["data"]["reviewStatus"] == "pending_trader_review"
        assert payload["data"]["severity"] == "warn"
        assert payload["meta"]["dryRun"] is False
        assert payload["meta"]["audit"]["evidenceKind"] == "agora.signal.create"
        assert replay.json()["data"]["id"] == payload["data"]["id"]

        detail = client.get("/bff/agora/signals/sig-write-gap-001", headers=READ_HEADERS)
        assert detail.status_code == 200, detail.text
        assert detail.json()["data"]["title"] == "Opening auction momentum"
        assert len(bff_main._sse_buffers["signal"]) == 1
        assert len(bff_main._sse_buffers["inbox"]) == 1


def test_bff_agora_signal_create_dry_run_returns_200_without_persistence() -> None:
    with _isolated_bff() as client:
        body = {
            "id": "sig-write-gap-dry-run",
            "title": "Dry-run signal",
            "body": "Validate the signal create shape without persisting.",
        }
        response = client.post(
            "/bff/agora/signals",
            headers={
                **BASE_HEADERS,
                "Idempotency-Key": "agora-signal-dry-run-001",
                "X-Correlation-Id": "corr-agora-signal-dry-run-001",
                "X-Dry-Run": "1",
            },
            json=body,
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["data"]["id"] == "sig-write-gap-dry-run"
        assert payload["meta"]["dryRun"] is True
        detail = client.get("/bff/agora/signals/sig-write-gap-dry-run", headers=READ_HEADERS)
        assert detail.status_code == 404, detail.text
        assert len(bff_main._sse_buffers["signal"]) == 0
        assert len(bff_main._sse_buffers["inbox"]) == 0


def test_bff_agora_signal_create_rejects_invalid_payload() -> None:
    with _isolated_bff() as client:
        response = client.post(
            "/bff/agora/signals",
            headers={**BASE_HEADERS, "Idempotency-Key": "agora-signal-invalid-001"},
            json={"title": "Missing body", "severity": "critical"},
        )

        assert response.status_code == 422, response.text
        error = response.json()["error"]
        assert error["code"] == "VALIDATION_FAILED"
        assert error["details"]["precondition_failed"] == "body"
