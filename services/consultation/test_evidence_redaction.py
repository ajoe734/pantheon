from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[2]
BFF_DIR = REPO_ROOT / "services" / "control-plane" / "bff"
if str(BFF_DIR) not in sys.path:
    sys.path.insert(0, str(BFF_DIR))

import main as bff_main  # noqa: E402
from command_queue import CommandStore  # noqa: E402
from read_store import ReadSurfaceStore  # noqa: E402


AUTH_HEADERS = {"Authorization": "Bearer ask007-op:operator,reviewer,admin"}


SENSITIVE_NEEDLES = {
    "policy_internals",
    "memory_trace",
    "internal_score",
    "persona_internal_state",
    "secret_credentials",
    "secretRef",
    "secret_ref",
    "capability_map_internals",
    "capabilityMap",
    "capability_map",
    "effective_tools",
    "effective_skills",
    "ASK007-POLICY-INTERNALS",
    "ASK007-MEMORY-TRACE",
    "ASK007-SECRET-CREDENTIAL",
    "ASK007-API-KEY",
    "ASK007-CAPABILITY-SECRET",
    "ASK007-CAPABILITY-TOOL",
    "ASK007-CAPABILITY-SKILL",
}


def _assert_review_payload_redacted(payload: Any) -> None:
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=True)
    leaked = sorted(needle for needle in SENSITIVE_NEEDLES if needle in serialized)
    assert leaked == []


def _idempotency_key() -> str:
    return f"ask007-{uuid.uuid4().hex[:16]}"


def _post_json(
    client: TestClient,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    expected_status: int,
) -> dict[str, Any]:
    headers = {**AUTH_HEADERS, "Idempotency-Key": _idempotency_key()}
    response = client.post(path, json=payload or {}, headers=headers)
    assert response.status_code == expected_status, response.text
    return response.json()


def _get_json(client: TestClient, path: str, *, expected_status: int = 200) -> dict[str, Any]:
    response = client.get(path, headers=AUTH_HEADERS)
    assert response.status_code == expected_status, response.text
    return response.json()


@contextmanager
def _consult_review_client() -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        root_dir = Path(td)
        original_store = bff_main.read_store
        original_command_store = bff_main.command_store
        original_env = {
            key: os.environ.get(key)
            for key in (
                "PANTHEON_BFF_CONSULTATION_DATA_DIR",
                "PANTHEON_CONSULTATION_DATA_DIR",
                "CONSULTATION_DATA_DIR",
                "PANTHEON_BFF_AUTH_STUB",
                "PANTHEON_BFF_AUTH_MODE",
            )
        }
        bff_main.read_store = ReadSurfaceStore(
            str(root_dir / "read_surfaces.json"),
            allow_local_snapshot_fallback=False,
        )
        bff_main.command_store = CommandStore(str(root_dir / "commands.jsonl"))
        bff_main._AGORA_CORE_BFF_IDEMPOTENCY.clear()
        bff_main._sse_buffers["ask"].clear()
        bff_main._sse_subscribers["ask"].clear()
        for key in (
            "PANTHEON_BFF_CONSULTATION_DATA_DIR",
            "PANTHEON_CONSULTATION_DATA_DIR",
            "CONSULTATION_DATA_DIR",
        ):
            os.environ.pop(key, None)
        os.environ["PANTHEON_BFF_AUTH_STUB"] = "true"
        os.environ["PANTHEON_BFF_AUTH_MODE"] = "permissive"

        try:
            yield TestClient(bff_main.app)
        finally:
            bff_main.read_store = original_store
            bff_main.command_store = original_command_store
            bff_main._AGORA_CORE_BFF_IDEMPOTENCY.clear()
            bff_main._sse_buffers["ask"].clear()
            bff_main._sse_subscribers["ask"].clear()
            for key, value in original_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def test_ask_007_publish_redacts_persona_internal_evidence_before_review_queue() -> None:
    correlation_id = "corr-ask007-redaction"
    ask_session_id = "ask-007-root-session"
    committee_session_id = "committee-ask007-review"
    memo_id = "memo-ask007-redacted-review"

    with _consult_review_client() as client:
        ask_session = _post_json(
            client,
            "/bff/agora/ask/sessions",
            payload={
                "sessionId": ask_session_id,
                "title": "ASK-007 consult redaction root session",
                "contextRefs": [{"type": "correlation", "id": correlation_id}],
            },
            expected_status=201,
        )["data"]
        assert ask_session["sessionId"] == ask_session_id
        assert ask_session["mode"] == "quick_ask"

        committee = _post_json(
            client,
            "/bff/agora/committee/sessions",
            payload={
                "sessionId": committee_session_id,
                "title": "ASK-007 persona consult redaction review",
                "linkedRequestId": ask_session_id,
                "quorumState": "quorum_met",
                "consensusState": "open",
                "participantRoster": [
                    {"participantId": "persona-risk-ask007", "role": "risk_reviewer"},
                    {"participantId": "persona-exec-ask007", "role": "execution_reviewer"},
                ],
            },
            expected_status=201,
        )["data"]
        assert committee["mode"] == "committee"

        opened_committee = _post_json(
            client,
            f"/bff/agora/committee/sessions/{committee_session_id}/open",
            payload={},
            expected_status=200,
        )["data"]
        assert opened_committee["status"] == "open"

        draft_body = _post_json(
            client,
            f"/bff/agora/committee/sessions/{committee_session_id}/memos",
            payload={
                "memoId": memo_id,
                "memoType": "committee_summary",
                "linkedRequestId": ask_session_id,
                "correlationId": correlation_id,
                "authorRef": {
                    "type": "persona",
                    "id": "persona-risk-ask007",
                    "role": "risk_reviewer",
                    "policy_internals": {"decision_tree": "ASK007-POLICY-INTERNALS"},
                    "memory_trace": ["ASK007-MEMORY-TRACE"],
                    "internal_score": 0.93,
                    "secret_credentials": {"api_key": "ASK007-SECRET-CREDENTIAL"},
                    "capability_map_internals": {
                        "effective_tools": ["ASK007-CAPABILITY-TOOL"],
                        "effective_skills": ["ASK007-CAPABILITY-SKILL"],
                    },
                },
                "summary": "Persona risk review is ready for management review.",
                "recommendations": ["Forward to management review with redacted evidence."],
                "evidenceRefs": [
                    {
                        "id": "ev-ask007-persona-state",
                        "type": "persona",
                        "description": "Persona risk finding",
                        "persona_internal_state": {
                            "policy_internals": "ASK007-POLICY-INTERNALS",
                            "memory_trace": ["ASK007-MEMORY-TRACE"],
                            "internal_score": 0.81,
                        },
                    },
                    {
                        "id": "ev-ask007-secret-credential",
                        "type": "artifact",
                        "description": "Credential-backed artifact reference",
                        "secret_credentials": {"api_key": "ASK007-API-KEY"},
                        "secretRef": "env://ASK007-CAPABILITY-SECRET",
                    },
                    {
                        "id": "ev-ask007-capability-map",
                        "type": "policy",
                        "description": "Capability-gated policy evidence",
                        "capability_map_internals": {
                            "effective_tools": ["ASK007-CAPABILITY-TOOL"],
                            "effective_skills": ["ASK007-CAPABILITY-SKILL"],
                        },
                    },
                ],
            },
            expected_status=201,
        )
        draft_memo = draft_body["data"]
        assert draft_memo["author_ref"] == {
            "type": "persona",
            "id": "persona-risk-ask007",
            "role": "risk_reviewer",
        }

        published_body = _post_json(
            client,
            f"/bff/agora/committee/sessions/{committee_session_id}/memos/{memo_id}/publish",
            payload={"correlationId": correlation_id, "priority": "high"},
            expected_status=200,
        )
        published_memo = published_body["data"]
        assert published_memo["status"] == "published"
        assert published_memo["session_to_memo_mapping"]["evidence_refs"] == [
            "ev-ask007-persona-state",
            "ev-ask007-secret-credential",
            "ev-ask007-capability-map",
        ]

        memo_detail = _get_json(
            client,
            f"/bff/agora/committee/sessions/{committee_session_id}/memos/{memo_id}"
        )["data"]
        handoffs = _get_json(
            client,
            "/bff/agora/handoffs?handoffType=consult_memo_to_management_review"
        )["items"]

        assert len(handoffs) == 1
        assert handoffs[0]["destination"]["queue"] == "consult_memo_review"
        assert handoffs[0]["payload"]["memoId"] == memo_id

        for review_payload in (draft_memo, published_memo, memo_detail, handoffs[0]):
            _assert_review_payload_redacted(review_payload)
