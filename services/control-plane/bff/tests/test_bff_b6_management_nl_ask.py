"""
BFF-B6-001: contract tests for POST /bff/management/nl/ask.

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
"""
from __future__ import annotations

import os
import sys
import tempfile

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
from read_store import ReadSurfaceStore

OPERATOR_HEADERS = {"Authorization": "Bearer op-b6:operator"}
IK = "test-idem-b6-001"


def _fresh_client(td: str) -> TestClient:
    store = ReadSurfaceStore(
        os.path.join(td, "read_surfaces.json"),
        allow_local_snapshot_fallback=True,
    )
    bff_main.read_store = store
    bff_main._MGMT_NL_IDEMPOTENCY.clear()
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
            assert isinstance(data["answer"], str) and data["answer"]
            assert isinstance(data["session_id"], str) and data["session_id"]
            assert isinstance(data["message_id"], str) and data["message_id"]
            assert data["question"] == "What is the current portfolio PnL?"
            assert isinstance(data["sources"], list)
            assert data["confidence"] in {"high", "partial", "unavailable"}
            assert "meta" in body
            assert "surfaces" in body["meta"]
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
    assert "detail" in body


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

            def counting_collect(focus: str, snapshot_at: str):
                call_count["n"] += 1
                return original_collect(focus, snapshot_at)

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

                # Both responses should have the same message_id
                assert resp1.json()["data"]["message_id"] == resp2.json()["data"]["message_id"]
                assert resp2.json()["meta"]["idempotency"]["replayed"] is True
            finally:
                bff_main._mgmt_nl_collect_context = original_collect
        finally:
            bff_main.read_store = original


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
            assert "detail" in body
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
                json={"question": "Session test?", "session_id": supplied_id},
                headers={**OPERATOR_HEADERS, "Idempotency-Key": "ik-sess-001"},
            )
            assert resp.status_code == 202, resp.text
            assert resp.json()["data"]["session_id"] == supplied_id
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
