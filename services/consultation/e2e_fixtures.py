from __future__ import annotations

import os
import sys
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
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


AUTH_HEADERS = {"Authorization": "Bearer ask006-op:operator,reviewer,admin"}


def idempotency_key(prefix: str = "ask006") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


@dataclass(frozen=True)
class ConsultReviewE2E:
    client: TestClient
    root_dir: Path
    auth_headers: dict[str, str]

    def post_json(
        self,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        expected_status: int,
        idempotent: bool = True,
    ) -> dict[str, Any]:
        headers = dict(self.auth_headers)
        if idempotent:
            headers["Idempotency-Key"] = idempotency_key()
        response = self.client.post(path, json=payload or {}, headers=headers)
        assert response.status_code == expected_status, response.text
        return response.json()

    def get_json(self, path: str, *, expected_status: int = 200) -> dict[str, Any]:
        response = self.client.get(path, headers=self.auth_headers)
        assert response.status_code == expected_status, response.text
        return response.json()

    def create_ask_session(self, *, session_id: str, correlation_id: str) -> dict[str, Any]:
        body = self.post_json(
            "/bff/agora/ask/sessions",
            payload={
                "sessionId": session_id,
                "title": "ASK-006 consult review trigger",
                "contextRefs": [{"type": "correlation", "id": correlation_id}],
            },
            expected_status=201,
        )
        return body["data"]

    def invoke_committee(
        self,
        *,
        session_id: str,
        linked_request_id: str,
    ) -> dict[str, Any]:
        create_body = self.post_json(
            "/bff/agora/committee/sessions",
            payload={
                "sessionId": session_id,
                "title": "ASK-006 management review committee",
                "linkedRequestId": linked_request_id,
                "quorumState": "quorum_met",
                "consensusState": "open",
                "participantRoster": [
                    {"participantId": "persona-risk", "role": "risk_reviewer"},
                    {"participantId": "persona-exec", "role": "execution_reviewer"},
                ],
            },
            expected_status=201,
        )
        assert create_body["data"]["mode"] == "committee"

        open_body = self.post_json(
            f"/bff/agora/committee/sessions/{session_id}/open",
            payload={},
            expected_status=200,
        )
        return open_body["data"]

    def submit_committee_memo(
        self,
        *,
        session_id: str,
        memo_id: str,
        linked_request_id: str,
        correlation_id: str,
    ) -> dict[str, Any]:
        body = self.post_json(
            f"/bff/agora/committee/sessions/{session_id}/memos",
            payload={
                "memoId": memo_id,
                "memoType": "committee_summary",
                "linkedRequestId": linked_request_id,
                "summary": "Committee recommends conditional management review.",
                "recommendations": ["Forward to management review with risk conditions."],
                "evidenceRefs": [{"id": "ev-ask006-risk", "type": "evidence_link"}],
                "correlationId": correlation_id,
            },
            expected_status=201,
        )
        return body["data"]

    def publish_committee_memo(
        self,
        *,
        session_id: str,
        memo_id: str,
        correlation_id: str,
    ) -> dict[str, Any]:
        body = self.post_json(
            f"/bff/agora/committee/sessions/{session_id}/memos/{memo_id}/publish",
            payload={"correlation_id": correlation_id, "priority": "high"},
            expected_status=200,
        )
        return body["data"]

    def ask_events(self) -> list[dict[str, Any]]:
        return [event for _, event in bff_main._sse_buffers["ask"]]


@contextmanager
def consult_review_e2e() -> Iterator[ConsultReviewE2E]:
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
            yield ConsultReviewE2E(
                client=TestClient(bff_main.app),
                root_dir=root_dir,
                auth_headers=dict(AUTH_HEADERS),
            )
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
