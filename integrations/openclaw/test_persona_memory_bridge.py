"""Tests for canonical Memory Plane -> OpenClaw workspace bridge."""
from __future__ import annotations

import json

import pytest

from integrations.openclaw.persona_memory_bridge import (
    MEMORY_CONTEXT_SCHEMA_VERSION,
    materialize_openclaw_memory_context,
    stage_openclaw_memory_writeback_candidate,
)
from services.memory.persona_memory_store import PersonaMemoryEntry, PersonaMemoryStore


PERSONA_ID = "persona-alpha"


def _retrieval_payload():
    return {
        "hits": [
            {
                "type": "persona",
                "relevance_score": 12.0,
                "entry": {
                    "memory_id": "pm-alpha-001",
                    "persona_id": PERSONA_ID,
                    "memory_type": "strategy_lesson",
                    "content": {
                        "summary": "Cut BTC momentum size when funding flips negative.",
                        "tags": ["BTC", "risk"],
                    },
                    "source_event_type": "postmortem_published",
                    "source_event_id": "pm-2026-0705",
                    "written_at": "2026-07-05T00:00:00Z",
                    "write_authority": "incident-svc",
                    "relevance_scope": "persona_private",
                },
            },
            {
                "type": "institutional",
                "relevance_score": 5.0,
                "entry": {
                    "entry_id": "im-001",
                    "knowledge_type": "incident_lesson",
                    "content": {
                        "headline": "Momentum systems need wider buffers during regime transitions.",
                        "body": "Prior incidents showed two-bar lag during regime breaks.",
                        "tags": ["momentum", "regime"],
                    },
                    "source_event_type": "postmortem_published",
                    "source_event_id": "pm-2026-0601",
                    "written_at": "2026-06-01T00:00:00Z",
                    "write_authority": "incident-svc",
                    "scope": "system_wide",
                },
            },
        ],
    }


def test_materializes_canonical_memory_with_traceable_source_ids(tmp_path):
    workspace = tmp_path / "workspace"

    result = materialize_openclaw_memory_context(
        persona_id=PERSONA_ID,
        workspace=str(workspace),
        retrieval_payload=_retrieval_payload(),
        query="risk lessons",
        generated_at="2026-07-05T01:00:00Z",
    )

    assert result.hit_count == 2
    context = json.loads((workspace / "memory" / "context.json").read_text(encoding="utf-8"))
    assert context["schema_version"] == MEMORY_CONTEXT_SCHEMA_VERSION
    assert context["source"] == "canonical_memory_plane"
    assert context["mutation_policy"]["workspace_is_cache"] is True
    assert context["mutation_policy"]["direct_session_writes"] is False
    assert context["hits"][0]["canonical_ref"] == "persona_memory:pm-alpha-001"
    assert context["hits"][1]["canonical_ref"] == "institutional_memory:im-001"

    markdown = (workspace / "MEMORY.md").read_text(encoding="utf-8")
    assert "materialized cache" in markdown
    assert "persona_memory:pm-alpha-001" in markdown
    assert "institutional_memory:im-001" in markdown


def test_rejects_private_persona_memory_from_other_persona(tmp_path):
    payload = _retrieval_payload()
    payload["hits"].insert(
        0,
        {
            "type": "persona",
            "relevance_score": 99.0,
            "entry": {
                "memory_id": "pm-other-001",
                "persona_id": "persona-other",
                "memory_type": "preference",
                "content": {"summary": "Private preference that must not leak."},
                "source_event_type": "operator_feedback",
                "source_event_id": "feedback-1",
                "written_at": "2026-07-05T00:00:00Z",
                "write_authority": "persona-memory-svc",
                "relevance_scope": "persona_private",
            },
        },
    )

    result = materialize_openclaw_memory_context(
        persona_id=PERSONA_ID,
        workspace=str(tmp_path / "workspace"),
        retrieval_payload=payload,
        generated_at="2026-07-05T01:00:00Z",
    )

    context = json.loads((tmp_path / "workspace" / "memory" / "context.json").read_text(encoding="utf-8"))
    assert all(hit["source_id"] != "pm-other-001" for hit in context["hits"])
    assert "Private preference" not in (tmp_path / "workspace" / "MEMORY.md").read_text(encoding="utf-8")
    assert result.rejected_hits == [
        {"type": "persona", "source_id": "pm-other-001", "reason": "persona_scope_mismatch"}
    ]


def test_writeback_candidate_does_not_mutate_canonical_store(tmp_path):
    store_path = tmp_path / "persona_memory_entries.json"
    store = PersonaMemoryStore(path=store_path)
    store.create(
        PersonaMemoryEntry(
            memory_id="pm-existing",
            persona_id=PERSONA_ID,
            memory_type="episodic",
            content={"summary": "Existing canonical memory."},
            source_event_type="session_end",
            source_event_id="session-existing",
            written_at="2026-07-05T00:00:00Z",
            write_authority="persona-memory-svc",
        )
    )

    candidate = stage_openclaw_memory_writeback_candidate(
        workspace=str(tmp_path / "workspace"),
        persona_id=PERSONA_ID,
        summary="Potential new lesson from an OpenClaw turn.",
        source_event_id="openclaw-turn-1",
        generated_at="2026-07-05T01:00:00Z",
        candidate_id="candidate-001",
    )

    assert candidate["status"] == "staged_for_governed_writeback"
    assert candidate["direct_session_writes"] is False
    assert candidate["canonical_write_endpoint"] == "POST /api/memory/writebacks/persona"
    assert (tmp_path / "workspace" / "memory" / "writeback-candidates" / "candidate-001.json").exists()

    reopened = PersonaMemoryStore(path=store_path)
    assert [entry.memory_id for entry in reopened.list(persona_id=PERSONA_ID)] == ["pm-existing"]


def test_cycle_query_comes_from_shortfall_families_and_underlyings_and_fails_closed():
    from integrations.openclaw.persona_memory_bridge import DEFAULT_MEMORY_QUERY, build_cycle_memory_query

    query, tags = build_cycle_memory_query(
        shortfall=["gross_utilisation"], families=["Momentum"], underlyings=["2330", "2317"])
    assert "momentum" in query and "2330" in query and "gross utilisation" in query
    assert query != DEFAULT_MEMORY_QUERY
    assert tags == ["family:momentum", "underlying:2317", "underlying:2330", "shortfall:gross_utilisation"]
    with pytest.raises(ValueError, match="cycle_query_subject_missing"):
        build_cycle_memory_query(shortfall=["gross_utilisation"], families=[], underlyings=[])


def test_materialization_records_query_and_tags_and_sends_tags_to_memory_plane(tmp_path):
    from unittest.mock import patch
    from integrations.openclaw.persona_memory_bridge import materialize_persona_memory_from_api

    seen = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps(_retrieval_payload()).encode()

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        return _Resp()

    with patch("integrations.openclaw.persona_memory_bridge.urllib.request.urlopen", fake_urlopen):
        result = materialize_persona_memory_from_api(
            memory_api_url="http://memory", persona_id=PERSONA_ID, workspace=str(tmp_path / "ws"),
            query="momentum 2330", tags=["family:momentum", "underlying:2330"])
    assert "tags=family%3Amomentum" in seen["url"] and "query=momentum+2330" in seen["url"]
    assert result.to_dict()["retrieval"]["tags"] == ["family:momentum", "underlying:2330"]
    context = json.loads((tmp_path / "ws" / "memory" / "context.json").read_text(encoding="utf-8"))
    assert context["retrieval"]["query"] == "momentum 2330"
    assert context["retrieval"]["tags"] == ["family:momentum", "underlying:2330"]
