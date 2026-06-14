"""
BFF-B3-007: contract tests for GET /bff/management/persona-intent.

The route is a read-only Management aggregate. It composes redacted persona
trace, trainer, and Agora intent summaries without exposing raw transcripts,
message bodies, tool lists, or capability internals.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
from read_store import ReadSurfaceStore

OPERATOR_HEADERS = {"Authorization": "Bearer op-b3-persona-intent:operator"}


def _fresh_client(td: str) -> TestClient:
    bff_main.read_store = ReadSurfaceStore(
        os.path.join(td, "read_surfaces.json"),
        allow_local_snapshot_fallback=True,
    )
    return TestClient(bff_main.app)


def test_persona_intent_composes_redacted_trace_trainer_and_agora_sources() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _fresh_client(td)

            resp = client.get("/bff/management/persona-intent", headers=OPERATOR_HEADERS)

            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["data"] == body["items"]
            assert body["summary"]["persona_trace_count"] >= 1
            assert body["summary"]["trainer_session_count"] >= 1
            assert body["summary"]["agora_session_count"] >= 1
            assert body["summary"]["redacted_item_count"] == body["summary"]["total_items"]
            assert body["meta"]["surfaces"]["management_persona_intent"]["source"] == "bff_composed"
            for surface in [
                "persona_traces",
                "persona_sessions",
                "capability_snapshots",
                "teaching_sessions",
                "agora_sessions",
            ]:
                assert surface in body["meta"]["surfaces"]

            by_type = {item["source_type"]: item for item in body["items"]}
            assert by_type["persona_trace"]["trace"]["trace_id"]
            assert by_type["persona_trace"]["trace"]["capability_summary"]["effective_tool_count"] >= 1
            assert by_type["trainer_session"]["trainer"]["outcome_count"] >= 1
            assert by_type["agora_session"]["agora"]["message_count"] >= 1

            encoded = json.dumps(body)
            assert '"tools_enabled":' not in encoded
            assert '"effective_tools":' not in encoded
            assert '"message_body":' not in encoded
            assert '"messages":' not in encoded
            assert '"content":' not in encoded
        finally:
            bff_main.read_store = original_store


def test_persona_intent_supports_filters_and_pagination() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _fresh_client(td)

            resp = client.get(
                "/bff/management/persona-intent"
                "?source_type=persona_trace&persona_id=persona-alpha&status=active&page_size=1",
                headers=OPERATOR_HEADERS,
            )

            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["page_info"]["page_size"] == 1
            assert body["page_info"]["total"] == 1
            assert len(body["items"]) == 1
            item = body["items"][0]
            assert item["source_type"] == "persona_trace"
            assert item["persona_id"] == "persona-alpha"
            assert item["status"] == "active"
            assert item["redaction"]["policy"] == "management_persona_intent_public_summary"
        finally:
            bff_main.read_store = original_store


def test_persona_intent_requires_read_authentication() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/management/persona-intent")

            assert resp.status_code == 401, resp.text
            assert resp.json()["error"]["code"] == "AUTH_REQUIRED"
        finally:
            bff_main.read_store = original_store
