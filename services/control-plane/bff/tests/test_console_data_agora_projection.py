from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[4]
BFF_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(BFF_ROOT))

from scripts import project_consultation_to_bff_agora_surfaces as projector  # noqa: E402

import main as bff_main  # noqa: E402
from ports import create_in_memory_read_surface_ports  # noqa: E402


HEADERS = {"Authorization": "Bearer op-dev:admin:mfa"}


def _fake_consultation_payload(url: str):
    if url.endswith("/api/consult/requests"):
        return [
            {
                "request_id": "cr-agora-console-001",
                "request_type": "incident",
                "requested_by": {"actor_type": "persona", "actor_id": "persona-risk"},
                "from_persona_id": "persona-risk",
                "target_type": "incident",
                "target_id": "incident-agora-001",
                "task": "Review the live-safety consultation for the Agora console.",
                "consultation_type": "incident_response",
                "context_refs": [{"type": "runtime", "id": "runtime-us-equity-paper"}],
                "evidence_refs": ["ev-consult-001"],
                "priority": "high",
                "status": "published",
                "linked_session_id": "agora-consult-session-001",
                "metadata": {
                    "consultation": {
                        "title": "Agora console incident consultation",
                        "summary": "Risk persona asked the committee to review a paper-runtime incident.",
                        "agora_mode": "committee",
                    }
                },
                "trace_id": "trace-agora-console-001",
                "created_at": "2026-06-15T10:00:00Z",
                "completed_at": "2026-06-15T10:40:00Z",
            }
        ]
    if url.endswith("/api/consult/memos"):
        return [
            {
                "memo_id": "mem-agora-console-001",
                "request_id": "cr-agora-console-001",
                "memo_type": "committee_summary",
                "author_type": "committee",
                "author_ref": "committee-risk",
                "target_type": "incident",
                "target_id": "incident-agora-001",
                "summary": "The committee approved guarded paper remediation with telemetry watch.",
                "findings": [
                    {
                        "severity": "high",
                        "category": "runtime_safety",
                        "claim": "Paper remediation is safe under current fail-closed posture.",
                        "evidence_refs": ["ev-consult-001"],
                        "recommendation": "approve_with_conditions",
                    }
                ],
                "recommendation": "approve_with_conditions",
                "confidence": 0.84,
                "status": "published",
                "trace_id": "trace-agora-console-001",
                "created_at": "2026-06-15T10:25:00Z",
                "published_at": "2026-06-15T10:35:00Z",
            }
        ]
    if url.endswith("/api/consult/transcripts"):
        return [
            {
                "transcript_id": "tr-cr-agora-console-001",
                "session_id": "cr-agora-console-001",
                "request_id": "cr-agora-console-001",
                "created_at": "2026-06-15T10:05:00Z",
                "events": [
                    {
                        "event_id": "evt-agora-console-001",
                        "session_id": "cr-agora-console-001",
                        "sequence_no": 1,
                        "event_type": "operator_question",
                        "event_time": "2026-06-15T10:06:00Z",
                        "actor": {"actor_type": "human", "actor_id": "op-dev"},
                        "content": {"text": "Should we continue paper remediation?"},
                        "evidence_refs": ["ev-consult-001"],
                    },
                    {
                        "event_id": "evt-agora-console-002",
                        "session_id": "cr-agora-console-001",
                        "sequence_no": 2,
                        "event_type": "committee_response",
                        "event_time": "2026-06-15T10:20:00Z",
                        "actor": {"actor_type": "committee", "actor_id": "committee-risk"},
                        "content": {"text": "Continue in paper only and keep live authority disabled."},
                        "evidence_refs": ["ev-consult-001"],
                    },
                ],
            }
        ]
    if url.endswith("/api/consult/handoffs"):
        return [
            {
                "handoff_id": "gh-agora-console-001",
                "request_id": "cr-agora-console-001",
                "target_gate": "governance_review",
                "memo_ids": ["mem-agora-console-001"],
                "evidence_refs": ["ev-consult-001"],
                "audit_refs": ["aud-agora-console-001"],
                "trace_id": "trace-agora-console-001",
                "status": "pending",
                "created_at": "2026-06-15T10:36:00Z",
            }
        ]
    raise AssertionError(f"unexpected URL: {url}")


@contextmanager
def _projected_agora_bff(monkeypatch) -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        out_dir = Path(td) / "bff-stores"
        monkeypatch.setattr(projector, "_get", _fake_consultation_payload)
        stores = projector.project("http://consultation-svc:8096")
        projector.write_projection(stores, out_dir)

        store_env = {
            "PANTHEON_BFF_AGORA_SIGNAL_STORE": out_dir / "agora_signals.json",
            "PANTHEON_BFF_AGORA_SESSION_STORE": out_dir / "agora_sessions.json",
            "PANTHEON_BFF_AGORA_HANDOFF_STORE": out_dir / "agora_handoffs.json",
            "PANTHEON_BFF_AGORA_TRAINING_EXAMPLE_STORE": out_dir / "agora_training_examples.json",
            "PANTHEON_BFF_RESEARCH_TICKET_STORE": out_dir / "research_tickets.json",
            "PANTHEON_BFF_RESEARCH_NOTES_STORE": out_dir / "research_notes.json",
            "PANTHEON_BFF_INSIGHT_CARD_STORE": out_dir / "insight_cards.json",
            "PANTHEON_BFF_DECISION_JOURNAL_STORE": out_dir / "decision_journal_entries.json",
            "PANTHEON_BFF_POSTMORTEM_STORE": out_dir / "postmortems.json",
        }
        for key, value in store_env.items():
            monkeypatch.setenv(key, str(value))

        original_store = bff_main.read_store
        ports = create_in_memory_read_surface_ports(
            research_knowledge_source_kwargs={
                "research_tickets_store": stores["research_tickets"],
                "research_notes_store": stores["research_notes"],
                "insight_cards_store": stores["insight_cards"],
            },
            lifecycle_telemetry_governance_kwargs={
                "postmortems": stores["postmortems"],
            },
        )
        # Agora's projected records have their own typed contracts which are
        # intentionally narrower than the generic consultation/read models.
        # Bind only those test-owned read projections to the actual ports
        # container; no product store compatibility layer is involved.
        ports.list_agora_signals = lambda **_kwargs: list(stores["agora_signals"].values())
        ports.list_agora_sessions = lambda **_kwargs: list(stores["agora_sessions"].values())
        ports.list_agora_insights = lambda **_kwargs: list(stores["insight_cards"].values())
        ports.list_agora_notes = lambda **_kwargs: list(stores["research_notes"].values())
        ports.list_agora_handoffs = lambda **_kwargs: list(stores["agora_handoffs"].values())
        ports.list_agora_training_examples = lambda **_kwargs: list(stores["agora_training_examples"].values())
        ports.list_decision_journal_entries = lambda **_kwargs: list(stores["decision_journal_entries"].values())
        ports._read_dataset_records = lambda dataset: list(stores.get(dataset, {}).values())
        ports.dataset_source = lambda _dataset: "test_projection"
        bff_main.read_store = ports
        try:
            yield TestClient(bff_main.app)
        finally:
            bff_main.read_store = original_store


def _assert_ok_list(client: TestClient, path: str, surface_key: str) -> dict:
    response = client.get(path, headers=HEADERS)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["items"]) > 0
    assert payload["meta"]["surfaces"][surface_key]["status"] == "ok"
    return payload


def test_projected_consultation_agora_surfaces_return_ok_counts(monkeypatch) -> None:
    with _projected_agora_bff(monkeypatch) as client:
        signals = _assert_ok_list(client, "/bff/agora/signals", "agora_signal_list")
        assert signals["items"][0]["signal_id"] == "sig-cr-agora-console-001"

        sessions = _assert_ok_list(client, "/bff/agora/sessions", "agora_session_list")
        assert sessions["items"][0]["sessionId"] == "agora-consult-session-001"

        insights = _assert_ok_list(client, "/bff/agora/insights", "agora_insight_list")
        assert {item["source_ref"] for item in insights["items"]} >= {
            "consultation_request:cr-agora-console-001",
            "consultation_memo:mem-agora-console-001",
        }

        notes = _assert_ok_list(client, "/bff/agora/notes", "agora_note_list")
        assert notes["items"][0]["note_id"] == "note-cr-agora-console-001"

        journal = _assert_ok_list(client, "/bff/agora/journal", "agora_journal_list")
        assert journal["items"][0]["id"] == "journal-mem-agora-console-001"

        handoffs = _assert_ok_list(client, "/bff/agora/handoffs", "agora_handoff_list")
        assert handoffs["items"][0]["handoffId"] == "gh-agora-console-001"

        training = _assert_ok_list(client, "/bff/agora/training-examples", "agora_training_example_list")
        assert training["items"][0]["trainingExampleId"] == "trn-agora-cr-agora-console-001"

        postmortems = _assert_ok_list(client, "/bff/agora/postmortems", "agora_postmortems")
        assert postmortems["items"][0]["postmortem_id"] == "pm-cr-agora-console-001"

        inbox = client.get("/bff/agora/inbox", headers=HEADERS)
        assert inbox.status_code == 200, inbox.text
        inbox_payload = inbox.json()
        assert len(inbox_payload["items"]) >= 3
        assert inbox_payload["meta"]["surfaces"]["agora_inbox_signals"]["status"] == "ok"
        assert inbox_payload["meta"]["surfaces"]["agora_inbox_insights"]["status"] == "ok"
        assert inbox_payload["meta"]["surfaces"]["agora_inbox_research_tasks"]["status"] == "ok"
