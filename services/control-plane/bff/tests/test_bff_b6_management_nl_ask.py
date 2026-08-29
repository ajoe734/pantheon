"""
BFF-B6-001/002: contract tests for POST /bff/management/nl/ask.

Acceptance criteria covered:
1. Authenticated POST with `question` returns HTTP 202 with data.answer,
   data.session_id, data.message_id, data.sources, and data.confidence.
2. Anonymous POST returns HTTP 401 typed BFF error envelope.
3. focus=trading_pulse restricts sourced summaries to trading-pulse surface only.
4. Idempotency replay: second request with same Idempotency-Key returns the
   cached result without re-querying management surfaces.
5. Missing `question` field returns HTTP 422 typed BFF error envelope.
6. session_id supplied in body is echoed in data.session_id; omitted
   session_id generates a new one.

BFF-B6-002 audit/evidence grounding fields:
7. Response includes data.audit_ref with target_type/target_id/href.
8. Response includes data.evidence_refs as a list; when evidence is seeded,
   each ref carries an href pointing to /api/v1/knowledge/evidence/{ref_id}.
9. Response includes meta.redacted_evidence_count as a non-negative integer.
10. Completed exchanges expose completed lifecycle fields and publish completion
   SSE events on the ask channel.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
from ports import create_read_surface_ports

OPERATOR_HEADERS = {"Authorization": "Bearer op-b6:operator"}
IK = "test-idem-b6-001"


class _B6NlAskTestStore:
    def __init__(self) -> None:
        self.ports = create_read_surface_ports()
        self.audit_events: list[dict[str, Any]] = []

    def _load_evidence(self) -> dict[str, Any]:
        store_path = os.environ.get("PANTHEON_BFF_EVIDENCE_REF_STORE")
        if store_path and os.path.exists(store_path):
            try:
                with open(store_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def list_evidence_refs(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._load_evidence().values())

    def get_evidence_ref(self, ref_id: Optional[str]) -> Optional[dict[str, Any]]:
        if not ref_id:
            return None
        return self._load_evidence().get(ref_id)

    def record_agora_audit_event(self, event: dict[str, Any]) -> dict[str, Any]:
        self.audit_events.append(event)
        return event

    def list_agora_audit_events(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self.audit_events)

    def dataset_source(self, dataset: str) -> str:
        if dataset == "evidence_refs":
            store_path = os.environ.get("PANTHEON_BFF_EVIDENCE_REF_STORE")
            return "service_backend" if store_path else "missing"
        return self.ports.dataset_source(dataset)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.ports, name)


def _fresh_client(td: str) -> TestClient:
    store = _B6NlAskTestStore()
    bff_main.read_store = store
    bff_main._MGMT_NL_IDEMPOTENCY.clear()
    bff_main._MGMT_AI_AUDIT_EVENTS.clear()
    bff_main._MGMT_AI_CONVERSATION_STORE = bff_main.ManagementAiConversationStore(
        storage_path="off",
        attachment_store=bff_main.ManagementAiAttachmentStore(storage_path="off"),
    )
    bff_main._sse_buffers["ask"].clear()
    return TestClient(bff_main.app)


# ---------------------------------------------------------------------------
# AC#1 — authenticated POST returns 202 with required data fields
# ---------------------------------------------------------------------------

def test_nl_ask_authenticated_returns_202_with_data_fields() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.post(
                "/bff/management/nl/ask",
                json={"question": "What is the current portfolio PnL?"},
                headers={**OPERATOR_HEADERS, "Idempotency-Key": IK},
            )
            assert resp.status_code == 202, resp.text
            body = resp.json()
            assert body["status"] == "accepted"
            data = body["data"]
            assert data["status"] == "completed"
            assert data["lifecycle_status"] == "completed"
            assert "lifecycleStatus" not in data
            assert isinstance(data["answer"], str) and data["answer"]
            assert isinstance(data["session_id"], str) and data["session_id"]
            assert "sessionId" not in data
            assert isinstance(data["message_id"], str) and data["message_id"]
            assert isinstance(data["trace_id"], str) and data["trace_id"]
            assert "traceId" not in data
            assert isinstance(data["provider_status"], dict)
            assert "providerStatus" not in data
            assert isinstance(data["actions"], list)
            assert data["conversation"]["href"].startswith("/bff/management/ai/conversations/")
            assert "trace_id=" not in data["conversation"]["href"]
            assert data["session"]["ttl_seconds"] >= 7 * 24 * 60 * 60
            assert "ttlSeconds" not in data["session"]
            assert data["question"] == "What is the current portfolio PnL?"
            assert isinstance(data["sources"], list)
            assert data["confidence"] in {"high", "partial", "unavailable"}
            assert "meta" in body
            assert body["meta"]["status"] == "completed"
            assert body["meta"]["lifecycle_status"] == "completed"
            assert "lifecycleStatus" not in body["meta"]
            assert "surfaces" in body["meta"]
            sse_events = [event for _, event in bff_main._sse_buffers["ask"]]
            event_types = [event.get("type") for event in sse_events]
            assert "management.nl.ask.accepted" in event_types
            assert "ask.message.completed" in event_types
            assert "management.nl.ask.completed" in event_types
            generic_completed = next(
                event for event in sse_events if event.get("type") == "ask.message.completed"
            )
            assert generic_completed["data"]["status"] == "completed"
            assert generic_completed["data"]["session_id"] == data["session_id"]
            assert generic_completed["data"]["trace_id"] == data["trace_id"]
            assert generic_completed["data"]["provider_status"] == data["provider_status"]
            assert "providerStatus" not in generic_completed["data"]
            domain_completed = next(
                event for event in sse_events if event.get("type") == "management.nl.ask.completed"
            )
            assert domain_completed["data"]["status"] == "completed"
            assert domain_completed["data"]["message_id"] == data["message_id"]
            assert domain_completed["data"]["audit_log"]["href"] == data["audit_log"]["href"]
            assert "auditLog" not in domain_completed["data"]
            assert domain_completed["data"]["conversation"]["href"] == data["conversation"]["href"]
        finally:
            bff_main.read_store = original


def test_nl_ask_dry_run_returns_compact_receipt_without_context_work(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)

            def fail_collect_context(*args, **kwargs):
                raise AssertionError("dry-run must not collect management context")

            monkeypatch.setattr(bff_main, "_mgmt_nl_collect_context", fail_collect_context)
            resp = client.post(
                "/bff/management/nl/ask",
                json={"question": "probe", "focus": "all", "context": "probe-script"},
                headers={
                    **OPERATOR_HEADERS,
                    "Idempotency-Key": "test-idem-b6-dry-run",
                    "X-Dry-Run": "1",
                },
            )
            assert resp.status_code == 202, resp.text
            body = resp.json()
            assert body["data"]["status"] == "accepted"
            assert body["data"]["question"] == "probe"
            assert body["data"]["confidence"] == "dry_run"
            assert body["meta"]["dryRun"] is True
            assert body["meta"]["durable"] is False
            assert body["meta"]["dry_run_mode"] == "compact_receipt"
            assert body["meta"]["idempotency"]["idempotencyKey"] == "test-idem-b6-dry-run"
            assert len(bff_main._MGMT_AI_AUDIT_EVENTS) == 0
            assert len(bff_main._sse_buffers["ask"]) == 0
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# AC#2 — anonymous POST returns 401
# ---------------------------------------------------------------------------

def test_nl_ask_anonymous_returns_401() -> None:
    client = TestClient(bff_main.app, raise_server_exceptions=False)
    resp = client.post(
        "/bff/management/nl/ask",
        json={"question": "Hello?"},
        headers={"Idempotency-Key": "anon-ik-001"},
    )
    assert resp.status_code == 401
    body = resp.json()
    assert "detail" not in body
    assert body["error"]["code"] == "AUTH_REQUIRED"
    assert body["meta"]["correlationId"]


# ---------------------------------------------------------------------------
# AC#3 — focus=trading_pulse restricts sources to trading-pulse surface only
# ---------------------------------------------------------------------------

def test_nl_ask_focus_trading_pulse_restricts_sources() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.post(
                "/bff/management/nl/ask",
                json={"question": "How is trading?", "focus": "trading_pulse"},
                headers={**OPERATOR_HEADERS, "Idempotency-Key": "ik-focus-tp-001"},
            )
            assert resp.status_code == 202, resp.text
            body = resp.json()
            sources = body["data"]["sources"]
            # Only trading_pulse source consulted
            assert "trading_pulse" in sources
            assert "cockpit" not in sources
            assert "portfolio" not in sources
            assert "persona_fleet" not in sources
            # focus echoed in data
            assert body["data"]["focus"] == "trading_pulse"
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# AC#4 — idempotency replay returns cached result
# ---------------------------------------------------------------------------

def test_nl_ask_idempotency_replay_returns_cached() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            call_count = {"n": 0}

            original_collect = bff_main._mgmt_nl_collect_context

            def counting_collect(focus: str, snapshot_at: str, **kwargs):
                call_count["n"] += 1
                return original_collect(focus, snapshot_at, **kwargs)

            bff_main._mgmt_nl_collect_context = counting_collect
            try:
                payload = {"question": "Replay test question?"}
                headers = {**OPERATOR_HEADERS, "Idempotency-Key": "ik-replay-b6-001"}

                resp1 = client.post("/bff/management/nl/ask", json=payload, headers=headers)
                assert resp1.status_code == 202, resp1.text
                first_count = call_count["n"]

                resp2 = client.post("/bff/management/nl/ask", json=payload, headers=headers)
                assert resp2.status_code == 202, resp2.text

                # Context collection should not have been called again on replay
                assert call_count["n"] == first_count, "Management surfaces re-queried on replay"

                assert resp2.json() == resp1.json()
            finally:
                bff_main._mgmt_nl_collect_context = original_collect
        finally:
            bff_main.read_store = original


def test_nl_ask_assistant_transcript_aliases_management_store() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.post(
                "/bff/management/nl/ask",
                json={"question": "Show the durable assistant transcript.", "sessionId": "mgmt-asst-alias-session"},
                headers={**OPERATOR_HEADERS, "Idempotency-Key": "ik-asst-transcript-alias-001"},
            )
            assert resp.status_code == 202, resp.text

            transcript_resp = client.get(
                "/bff/assistant/sessions/mgmt-asst-alias-session/transcript",
                headers=OPERATOR_HEADERS,
            )
            assert transcript_resp.status_code == 200, transcript_resp.text
            turns = transcript_resp.json()["data"]
            assert [turn["role"] for turn in turns] == ["user", "assistant"]
            assert turns[0]["content"] == "Show the durable assistant transcript."
            assert turns[1]["content"] == resp.json()["data"]["answer"]
            assert transcript_resp.json()["meta"]["count"] == 2
        finally:
            bff_main.read_store = original


def test_nl_ask_assistant_transcript_unknown_session_returns_404() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get(
                "/bff/assistant/sessions/local-only-browser-session/transcript",
                headers=OPERATOR_HEADERS,
            )
            assert resp.status_code == 404
        finally:
            bff_main.read_store = original


def test_nl_ask_idempotency_replay_does_not_duplicate_assistant_transcript() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            payload = {
                "question": "Replay should not duplicate transcript turns.",
                "sessionId": "mgmt-asst-idem-session",
            }
            headers = {**OPERATOR_HEADERS, "Idempotency-Key": "ik-asst-transcript-idem-001"}

            resp1 = client.post("/bff/management/nl/ask", json=payload, headers=headers)
            resp2 = client.post("/bff/management/nl/ask", json=payload, headers=headers)
            assert resp1.status_code == 202, resp1.text
            assert resp2.status_code == 202, resp2.text
            assert resp2.json() == resp1.json()

            transcript_resp = client.get(
                "/bff/assistant/sessions/mgmt-asst-idem-session/transcript",
                headers=OPERATOR_HEADERS,
            )
            assert transcript_resp.status_code == 200, transcript_resp.text
            turns = transcript_resp.json()["data"]
            assert [turn["role"] for turn in turns] == ["user", "assistant"]
            assert transcript_resp.json()["meta"]["count"] == 2
        finally:
            bff_main.read_store = original


def test_nl_ask_assistant_transcript_survives_conversation_store_reload() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_read_store = bff_main.read_store
        original_conversation_store = bff_main._MGMT_AI_CONVERSATION_STORE
        store_path = os.path.join(td, "management-ai.json")
        try:
            bff_main.read_store = _B6NlAskTestStore()
            bff_main._MGMT_NL_IDEMPOTENCY.clear()
            bff_main._MGMT_AI_AUDIT_EVENTS.clear()
            bff_main._MGMT_AI_CONVERSATION_STORE = bff_main.ManagementAiConversationStore(
                storage_path=store_path,
                attachment_store=bff_main.ManagementAiAttachmentStore(storage_path="off"),
            )
            client = TestClient(bff_main.app)

            ask_resp = client.post(
                "/bff/management/nl/ask",
                json={"question": "Persist this through a store reload.", "sessionId": "mgmt-asst-reload-session"},
                headers={**OPERATOR_HEADERS, "Idempotency-Key": "ik-asst-transcript-reload-001"},
            )
            assert ask_resp.status_code == 202, ask_resp.text

            bff_main._MGMT_AI_CONVERSATION_STORE = bff_main.ManagementAiConversationStore(
                storage_path=store_path,
                attachment_store=bff_main.ManagementAiAttachmentStore(storage_path="off"),
            )

            transcript_resp = client.get(
                "/bff/assistant/sessions/mgmt-asst-reload-session/transcript",
                headers=OPERATOR_HEADERS,
            )
            assert transcript_resp.status_code == 200, transcript_resp.text
            turns = transcript_resp.json()["data"]
            assert [turn["role"] for turn in turns] == ["user", "assistant"]
            assert turns[0]["content"] == "Persist this through a store reload."
        finally:
            bff_main.read_store = original_read_store
            bff_main._MGMT_AI_CONVERSATION_STORE = original_conversation_store


# ---------------------------------------------------------------------------
# AC#5 — missing question returns 422
# ---------------------------------------------------------------------------

def test_nl_ask_missing_question_returns_422() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.post(
                "/bff/management/nl/ask",
                json={"focus": "cockpit"},
                headers={**OPERATOR_HEADERS, "Idempotency-Key": "ik-no-q-001"},
            )
            assert resp.status_code == 422, resp.text
            body = resp.json()
            assert "detail" not in body
            assert body["error"]["code"] == "VALIDATION_FAILED"
            assert body["meta"]["correlationId"]
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# AC#6 — session_id echoed when supplied; generated when omitted
# ---------------------------------------------------------------------------

def test_nl_ask_session_id_echoed_when_supplied() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            supplied_id = "my-session-b6-xyz"
            resp = client.post(
                "/bff/management/nl/ask",
                json={"question": "Session test?", "sessionId": supplied_id},
                headers={**OPERATOR_HEADERS, "Idempotency-Key": "ik-sess-001"},
            )
            assert resp.status_code == 202, resp.text
            assert resp.json()["data"]["session_id"] == supplied_id
            assert "sessionId" not in resp.json()["data"]
        finally:
            bff_main.read_store = original


def test_nl_ask_session_id_generated_when_omitted() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.post(
                "/bff/management/nl/ask",
                json={"question": "Generate session?"},
                headers={**OPERATOR_HEADERS, "Idempotency-Key": "ik-gen-sess-001"},
            )
            assert resp.status_code == 202, resp.text
            session_id = resp.json()["data"]["session_id"]
            assert isinstance(session_id, str) and session_id
            # Auto-generated IDs use the mgmt-nl- prefix
            assert session_id.startswith("mgmt-nl-")
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# Regression: focus=persona_fleet must populate context, not silently fail
# ---------------------------------------------------------------------------

def test_nl_ask_focus_persona_fleet_populates_context() -> None:
    """
    Regression for BFF-B6-001 review blocker: _mgmt_nl_collect_context was
    calling _project_persona_fleet_payload() without required keyword args,
    causing a TypeError caught as unavailable. After the fix the source must
    appear in data.sources and persona_fleet must be present in summary_context.
    """
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.post(
                "/bff/management/nl/ask",
                json={"question": "How is the persona fleet?", "focus": "persona_fleet"},
                headers={**OPERATOR_HEADERS, "Idempotency-Key": "ik-pf-regression-001"},
            )
            assert resp.status_code == 202, resp.text
            body = resp.json()
            # persona_fleet surface must appear in sources (not silently dropped)
            assert "persona_fleet" in body["data"]["sources"], (
                "persona_fleet missing from sources — TypeError still being caught as unavailable"
            )
            # summary_context must contain a persona_fleet entry (not empty)
            summary_ctx = body["data"].get("summary_context") or {}
            assert "persona_fleet" in summary_ctx, (
                "persona_fleet missing from summary_context"
            )
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# Helpers for BFF-B6-002 audit/evidence grounding tests
# ---------------------------------------------------------------------------

@contextmanager
def _nl_evidence_client() -> Iterator[TestClient]:
    """Client with a seeded evidence_refs store for B6-002 grounding tests."""
    tracked_env = {
        "PANTHEON_BFF_EVIDENCE_REF_STORE": os.environ.get("PANTHEON_BFF_EVIDENCE_REF_STORE"),
    }
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        evidence_store = root / "evidence_refs.json"
        evidence_store.write_text(
            json.dumps(
                {
                    "evref-b6-alert-001": {
                        "ref_id": "evref-b6-alert-001",
                        # Use alert evidence type — operator role has risk.alert.read,
                        # so this ref will not be redacted and will keep its href.
                        "evidence_type": "alert",
                        "link_type": "supporting_evidence",
                        "source_document": {
                            "title": "NL management grounding alert",
                            "source_type": "alert",
                            "source_ref": "alert://mgmt-nl/risk-window",
                            "captured_at": "2026-05-23T10:00:00Z",
                        },
                        "credibility": {"tier": "primary", "verified": True},
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        os.environ["PANTHEON_BFF_EVIDENCE_REF_STORE"] = str(evidence_store)
        original_store = bff_main.read_store
        bff_main._MGMT_NL_IDEMPOTENCY.clear()
        try:
            bff_main.read_store = _B6NlAskTestStore()
            yield TestClient(bff_main.app)
        finally:
            bff_main.read_store = original_store
            for key, value in tracked_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


# ---------------------------------------------------------------------------
# AC#7 — response includes data.audit_ref with required fields
# ---------------------------------------------------------------------------

def test_nl_ask_response_includes_audit_ref() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.post(
                "/bff/management/nl/ask",
                json={"question": "Audit ref test?"},
                headers={**OPERATOR_HEADERS, "Idempotency-Key": "ik-audit-ref-001"},
            )
            assert resp.status_code == 202, resp.text
            body = resp.json()
            assert "auditRef" not in body["data"]
            audit_ref = body["data"].get("audit_ref")
            assert audit_ref is not None, "audit_ref missing from data"
            assert "targetType" not in audit_ref
            assert "targetId" not in audit_ref
            assert audit_ref.get("target_type") == "ManagementNLExchange"
            target_id = audit_ref.get("target_id")
            assert isinstance(target_id, str) and target_id, "audit_ref.target_id must be non-empty string"
            href = audit_ref.get("href")
            assert isinstance(href, str) and "ManagementNLExchange" in href, (
                f"audit_ref.href must reference ManagementNLExchange, got: {href}"
            )
        finally:
            bff_main.read_store = original


# ---------------------------------------------------------------------------
# AC#8 — data.evidence_refs is a list; seeded refs carry /api/v1/knowledge/evidence href
# ---------------------------------------------------------------------------

def test_nl_ask_response_includes_evidence_refs_with_api_href() -> None:
    with _nl_evidence_client() as client:
        resp = client.post(
            "/bff/management/nl/ask",
            json={"question": "Evidence refs test?"},
            headers={**OPERATOR_HEADERS, "Idempotency-Key": "ik-evref-001"},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert "evidenceRefs" not in body["data"]
        evidence_refs = body["data"].get("evidence_refs")
        assert isinstance(evidence_refs, list), "evidence_refs must be a list"
        assert len(evidence_refs) >= 1, "At least one seeded evidence ref must appear"
        ref = evidence_refs[0]
        href = ref.get("href")
        assert isinstance(href, str) and href.startswith("/api/v1/knowledge/evidence/"), (
            f"evidenceRef href must point to /api/v1/knowledge/evidence/, got: {href}"
        )


# ---------------------------------------------------------------------------
# AC#9 — meta.redacted_evidence_count is a non-negative integer
# ---------------------------------------------------------------------------

def test_nl_ask_response_includes_redacted_evidence_count() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.post(
                "/bff/management/nl/ask",
                json={"question": "Redacted count test?"},
                headers={**OPERATOR_HEADERS, "Idempotency-Key": "ik-redact-count-001"},
            )
            assert resp.status_code == 202, resp.text
            body = resp.json()
            meta = body.get("meta", {})
            assert "redactedEvidenceCount" not in meta
            count = meta.get("redacted_evidence_count")
            assert count is not None, "meta.redacted_evidence_count must be present"
            assert isinstance(count, int) and count >= 0, (
                f"meta.redacted_evidence_count must be non-negative int, got: {count!r}"
            )
        finally:
            bff_main.read_store = original
