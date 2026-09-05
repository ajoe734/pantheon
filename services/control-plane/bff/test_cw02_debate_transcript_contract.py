from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi.testclient import TestClient


from services.control_plane.bff import main as bff_main


OPERATOR_AUTH = "Bearer test-operator:operator"
REVIEWER_AUTH = "Bearer test-reviewer:reviewer"

_SESSION_ID = "cs-20260419-081"
_TRANSCRIPT_URL = f"/api/v1/consultations/{_SESSION_ID}/transcript"


def _default_transcript_record() -> Dict[str, Any]:
    return {
        "transcript_id": "tr-cs-20260419-081",
        "session_id": _SESSION_ID,
        "linked_request_id": "cr-20260419-014",
        "events": [
            {
                "transcript_id": "tr-cs-20260419-081",
                "session_id": _SESSION_ID,
                "event_id": "evt-tr-cs-20260419-081-001",
                "sequence_no": 1,
                "parent_event_id": None,
                "event_type": "message",
                "event_time": "2026-04-19T17:06:30Z",
                "ingest_time": "2026-04-19T17:06:31Z",
                "actor": {
                    "actor_type": "persona",
                    "actor_id": "persona-alpha",
                    "display_name": "Alpha Trader",
                    "role": "requester",
                },
                "content": {
                    "format": "markdown",
                    "text": "Requesting risk review for macro regime shift scenario before approving live deployment.",
                },
                "evidence_refs": [],
                "visibility": "committee",
                "redaction": {"is_redacted": False, "reason": None},
                "meta": {"source": "consultation-service", "hash": None},
            },
            {
                "transcript_id": "tr-cs-20260419-081",
                "session_id": _SESSION_ID,
                "event_id": "evt-tr-cs-20260419-081-002",
                "sequence_no": 2,
                "parent_event_id": "evt-tr-cs-20260419-081-001",
                "event_type": "evidence_attachment",
                "event_time": "2026-04-19T17:07:00Z",
                "ingest_time": "2026-04-19T17:07:01Z",
                "actor": {
                    "actor_type": "persona",
                    "actor_id": "p-macro-observer",
                    "display_name": "Macro Observer",
                    "role": "committee_participant",
                },
                "content": {"format": "plaintext", "text": None},
                "evidence_refs": ["telemetry-vol-spike-20260419"],
                "visibility": "committee",
                "redaction": {"is_redacted": False, "reason": None},
                "meta": {"source": "consultation-service", "hash": None},
            },
            {
                "transcript_id": "tr-cs-20260419-081",
                "session_id": _SESSION_ID,
                "event_id": "evt-tr-cs-20260419-081-003",
                "sequence_no": 3,
                "parent_event_id": "evt-tr-cs-20260419-081-001",
                "event_type": "outcome_signal",
                "event_time": "2026-04-19T17:08:30Z",
                "ingest_time": "2026-04-19T17:08:31Z",
                "actor": {
                    "actor_type": "persona",
                    "actor_id": "p-execution-lead",
                    "display_name": "Execution Lead",
                    "role": "committee_participant",
                },
                "content": {
                    "format": "markdown",
                    "text": "Conditional approval — deployment must reduce capital allocation by 20% pending regime confirmation.",
                },
                "evidence_refs": ["dp-20260419-014"],
                "visibility": "committee",
                "redaction": {"is_redacted": False, "reason": None},
                "meta": {"source": "consultation-service", "hash": None},
            },
        ],
    }


class _TranscriptReadStore:
    """CW-02 in-memory consult-transcript read double.

    Holds an in-memory `consult_transcripts` dataset (mirroring the retired
    legacy BFF read surface's default fixture) that tests may mutate directly via
    `._data` + `._save()`, and additionally consults
    PANTHEON_BFF_CONSULT_TRANSCRIPT_STORE for a service-store override, again
    mirroring the original local-snapshot vs service-store provenance split.
    """

    def __init__(self, path: str, allow_local_snapshot_fallback: bool = True) -> None:
        self._path = path
        self._data: Dict[str, Any] = {
            "consult_transcripts": {_SESSION_ID: _default_transcript_record()},
        }

    def _save(self) -> None:
        # No real file-backed persistence is required for the double; tests
        # mutate self._data directly and call _save() to signal "commit".
        try:
            with open(self._path, "w", encoding="utf-8") as handle:
                json.dump(self._data, handle)
        except OSError:
            pass

    def _transcript_store_env_path(self) -> Optional[str]:
        raw = os.environ.get("PANTHEON_BFF_CONSULT_TRANSCRIPT_STORE", "").strip()
        return raw or None

    def _service_transcript_records(self) -> Optional[Dict[str, Dict[str, Any]]]:
        path = self._transcript_store_env_path()
        if not path or not Path(path).exists():
            return None
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    def dataset_source(self, dataset: str) -> str:
        if dataset != "consult_transcripts":
            return "missing"
        if self._service_transcript_records() is not None:
            return "service_store"
        return "local_snapshot"

    def get_consult_transcript(
        self,
        session_id: Optional[str],
        *,
        from_sequence_no: Optional[int] = None,
        page_size: int = 50,
        page_token: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not session_id:
            return None

        service_records = self._service_transcript_records()
        transcripts = service_records if service_records is not None else self._data.get("consult_transcripts", {})
        record = transcripts.get(session_id)
        if record is None and session_id != _SESSION_ID:
            return None

        if record is None:
            surface_state = "unavailable"
            events: List[Dict[str, Any]] = []
            transcript_id = f"tr-{session_id}"
            linked_request_id = None
        else:
            transcript_id = str(record.get("transcript_id") or f"tr-{session_id}")
            linked_request_id = record.get("linked_request_id")
            raw_events = list(record.get("events") or [])
            raw_events.sort(key=lambda e: int(e.get("sequence_no") or 0))

            full_seqs = [int(e.get("sequence_no") or 0) for e in raw_events]
            has_gap = any(
                full_seqs[i + 1] != full_seqs[i] + 1
                for i in range(len(full_seqs) - 1)
            )
            surface_state = "degraded" if has_gap else "ok"

            if from_sequence_no is not None:
                raw_events = [e for e in raw_events if int(e.get("sequence_no") or 0) >= from_sequence_no]
            events = raw_events

        offset = 0
        if page_token:
            try:
                offset = int(page_token)
            except (ValueError, TypeError):
                offset = 0

        page_events = events[offset: offset + page_size]
        next_offset = offset + page_size
        next_page_token = str(next_offset) if next_offset < len(events) else None

        now = "2026-04-19T17:30:00Z"
        return {
            "object_ref": {"type": "ConsultTranscript", "id": transcript_id},
            "transcript_id": transcript_id,
            "session_id": session_id,
            "linked_request_id": linked_request_id,
            "events": page_events,
            "page_info": {
                "next_page_token": next_page_token,
                "page_size": page_size,
                "total": len(events),
            },
            "meta": {
                "snapshot_at": now,
                "staleness": {
                    "served_from": self.dataset_source("consult_transcripts") if record is not None else "unavailable",
                    "last_known_at": now,
                },
                "surfaces": {
                    "transcript": {"state": surface_state},
                },
            },
        }


@contextmanager
def _seeded_client():
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        bff_main.read_store = _TranscriptReadStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        client = TestClient(bff_main.app)
        try:
            yield client
        finally:
            bff_main.read_store = original_store


def test_cw02_transcript_returns_required_envelope() -> None:
    with _seeded_client() as client:
        response = client.get(_TRANSCRIPT_URL, headers={"Authorization": OPERATOR_AUTH})
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["object_ref"]["type"] == "ConsultTranscript"
        assert payload["object_ref"]["id"] == "tr-cs-20260419-081"
        assert payload["transcript_id"] == "tr-cs-20260419-081"
        assert payload["session_id"] == _SESSION_ID
        assert payload["linked_request_id"] == "cr-20260419-014"
        assert "events" in payload
        assert "page_info" in payload
        assert "next_page_token" in payload["page_info"]
        assert "page_size" in payload["page_info"]
        assert "total" in payload["page_info"]
        assert "meta" in payload
        assert "snapshot_at" in payload["meta"]
        assert "staleness" in payload["meta"]
        assert "surfaces" in payload["meta"]
        assert "transcript" in payload["meta"]["surfaces"]
        assert "state" in payload["meta"]["surfaces"]["transcript"]


def test_cw02_transcript_events_ordered_by_sequence_no() -> None:
    with _seeded_client() as client:
        response = client.get(_TRANSCRIPT_URL, headers={"Authorization": OPERATOR_AUTH})
        assert response.status_code == 200, response.text

        events = response.json()["events"]
        assert len(events) == 3
        seqs = [e["sequence_no"] for e in events]
        assert seqs == sorted(seqs), "events must be ordered ascending by sequence_no"
        assert seqs == [1, 2, 3]


def test_cw02_transcript_event_fields_complete() -> None:
    with _seeded_client() as client:
        response = client.get(_TRANSCRIPT_URL, headers={"Authorization": OPERATOR_AUTH})
        assert response.status_code == 200, response.text

        event = response.json()["events"][0]
        assert event["transcript_id"] == "tr-cs-20260419-081"
        assert event["session_id"] == _SESSION_ID
        assert "event_id" in event
        assert "sequence_no" in event
        assert "parent_event_id" in event
        assert "event_type" in event
        assert "event_time" in event
        assert "ingest_time" in event
        assert "actor" in event
        assert "actor_type" in event["actor"]
        assert "actor_id" in event["actor"]
        assert "role" in event["actor"]
        assert "content" in event
        assert "format" in event["content"]
        assert "evidence_refs" in event
        assert "visibility" in event
        assert "redaction" in event
        assert "is_redacted" in event["redaction"]
        assert "meta" in event


def test_cw02_transcript_actor_identity_from_canonical_source() -> None:
    with _seeded_client() as client:
        response = client.get(_TRANSCRIPT_URL, headers={"Authorization": OPERATOR_AUTH})
        assert response.status_code == 200, response.text

        events = response.json()["events"]
        first = events[0]
        assert first["actor"]["actor_type"] == "persona"
        assert first["actor"]["actor_id"] == "persona-alpha"
        assert first["actor"]["role"] == "requester"

        second = events[1]
        assert second["actor"]["actor_id"] == "p-macro-observer"
        assert second["actor"]["role"] == "committee_participant"


def test_cw02_transcript_from_sequence_no_filter() -> None:
    with _seeded_client() as client:
        response = client.get(
            _TRANSCRIPT_URL + "?from_sequence_no=2",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        events = payload["events"]
        assert all(e["sequence_no"] >= 2 for e in events)
        assert len(events) == 2
        assert payload["page_info"]["total"] == 2


def test_cw02_transcript_surface_state_ok_for_contiguous_events() -> None:
    with _seeded_client() as client:
        response = client.get(_TRANSCRIPT_URL, headers={"Authorization": OPERATOR_AUTH})
        assert response.status_code == 200, response.text

        state = response.json()["meta"]["surfaces"]["transcript"]["state"]
        assert state == "ok"


def test_cw02_transcript_surface_state_degraded_for_gap() -> None:
    with _seeded_client() as client:
        # introduce a sequence gap by removing sequence_no 2
        store = bff_main.read_store
        transcript = store._data["consult_transcripts"][_SESSION_ID]
        transcript["events"] = [
            e for e in transcript["events"] if e["sequence_no"] != 2
        ]
        store._save()

        response = client.get(_TRANSCRIPT_URL, headers={"Authorization": OPERATOR_AUTH})
        assert response.status_code == 200, response.text

        state = response.json()["meta"]["surfaces"]["transcript"]["state"]
        assert state == "degraded"


def test_cw02_transcript_unavailable_state_for_missing_session() -> None:
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/consultations/does-not-exist/transcript",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 404, response.text


def test_cw02_transcript_pagination_page_size() -> None:
    with _seeded_client() as client:
        response = client.get(
            _TRANSCRIPT_URL + "?page_size=1",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert len(payload["events"]) == 1
        assert payload["page_info"]["page_size"] == 1
        assert payload["page_info"]["total"] == 3
        assert payload["page_info"]["next_page_token"] is not None


def test_cw02_transcript_pagination_second_page() -> None:
    with _seeded_client() as client:
        first = client.get(
            _TRANSCRIPT_URL + "?page_size=1",
            headers={"Authorization": OPERATOR_AUTH},
        )
        token = first.json()["page_info"]["next_page_token"]

        second = client.get(
            _TRANSCRIPT_URL + f"?page_size=1&page_token={token}",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert second.status_code == 200, second.text

        payload = second.json()
        assert len(payload["events"]) == 1
        assert payload["events"][0]["sequence_no"] == 2


def test_cw02_transcript_reviewer_can_read() -> None:
    with _seeded_client() as client:
        response = client.get(_TRANSCRIPT_URL, headers={"Authorization": REVIEWER_AUTH})
        assert response.status_code == 200, response.text


def test_cw02_transcript_unauthenticated_is_rejected() -> None:
    with _seeded_client() as client:
        response = client.get(_TRANSCRIPT_URL)
        assert response.status_code in {401, 403}, response.text


def test_cw02_transcript_surface_state_degraded_when_gap_hidden_by_filter() -> None:
    """Gap detection must use the full stream even when from_sequence_no skips past the gap."""
    with _seeded_client() as client:
        store = bff_main.read_store
        transcript = store._data["consult_transcripts"][_SESSION_ID]
        transcript["events"] = [
            e for e in transcript["events"] if e["sequence_no"] != 2
        ]
        store._save()

        response = client.get(
            _TRANSCRIPT_URL + "?from_sequence_no=3",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text
        state = response.json()["meta"]["surfaces"]["transcript"]["state"]
        assert state == "degraded", "gap in full stream must surface as degraded even when filtered slice looks contiguous"


def test_cw02_transcript_served_from_local_snapshot() -> None:
    with _seeded_client() as client:
        response = client.get(_TRANSCRIPT_URL, headers={"Authorization": OPERATOR_AUTH})
        assert response.status_code == 200, response.text
        served_from = response.json()["meta"]["staleness"]["served_from"]
        assert served_from == "local_snapshot"


def test_cw02_transcript_served_from_service_store() -> None:
    seed_record = {
        "transcript_id": "tr-cs-20260419-081",
        "session_id": _SESSION_ID,
        "linked_request_id": "cr-20260419-014",
        "events": [
            {
                "transcript_id": "tr-cs-20260419-081",
                "session_id": _SESSION_ID,
                "event_id": "evt-001",
                "sequence_no": 1,
                "parent_event_id": None,
                "event_type": "message",
                "event_time": "2026-04-19T08:10:00Z",
                "ingest_time": "2026-04-19T08:10:01Z",
                "actor": {"actor_type": "persona", "actor_id": "persona-alpha", "display_name": None, "role": "requester"},
                "content": {"format": "markdown", "text": "hello"},
                "evidence_refs": [],
                "visibility": "committee",
                "redaction": {"is_redacted": False, "reason": None},
                "meta": {"source": "committee-engine", "hash": None},
            }
        ],
    }

    with tempfile.TemporaryDirectory() as td:
        transcript_path = os.path.join(td, "consult_transcripts.json")
        with open(transcript_path, "w") as fh:
            json.dump({_SESSION_ID: seed_record}, fh)

        original_env = os.environ.get("PANTHEON_BFF_CONSULT_TRANSCRIPT_STORE")
        os.environ["PANTHEON_BFF_CONSULT_TRANSCRIPT_STORE"] = transcript_path

        original_store = bff_main.read_store
        bff_main.read_store = _TranscriptReadStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        client = TestClient(bff_main.app)
        try:
            response = client.get(_TRANSCRIPT_URL, headers={"Authorization": OPERATOR_AUTH})
            assert response.status_code == 200, response.text
            served_from = response.json()["meta"]["staleness"]["served_from"]
            assert served_from == "service_store"
        finally:
            bff_main.read_store = original_store
            if original_env is None:
                os.environ.pop("PANTHEON_BFF_CONSULT_TRANSCRIPT_STORE", None)
            else:
                os.environ["PANTHEON_BFF_CONSULT_TRANSCRIPT_STORE"] = original_env
