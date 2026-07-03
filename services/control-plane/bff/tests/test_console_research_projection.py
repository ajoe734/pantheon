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

from scripts import cleanup_legacy_research_evidence_refs as legacy_cleanup  # noqa: E402
from scripts import project_research_to_bff_surfaces as projector  # noqa: E402

import main as bff_main  # noqa: E402
from read_store import ReadSurfaceStore  # noqa: E402


HEADERS = {"Authorization": "Bearer op-dev:admin:mfa"}
KW03_REF_ID = "evref-123e4567-e89b-12d3-a456-426614174000"


def _fake_service_payload(url: str):
    if url.endswith("/api/research-orchestrator/tasks"):
        return [
            {
                "id": "rtask-console-001",
                "task_id": "rtask-console-001",
                "title": "Console projection research task",
                "objective": "Validate projected console research surfaces from a completed run.",
                "status": "completed",
                "created_by": "research-orchestrator",
                "created_at": "2026-06-15T10:00:00Z",
                "updated_at": "2026-06-15T10:30:00Z",
            }
        ]
    if url.endswith("/api/research-orchestrator/runs"):
        return [
            {
                "id": "rrun-console-001",
                "run_id": "rrun-console-001",
                "task_id": "rtask-console-001",
                "adapter": "stub",
                "requested_mode": "stub",
                "dispatch_mode": "stub",
                "status": "completed",
                "parameters": {
                    "strategy_id": "strategy-console-projection",
                    "strategy_spec_version": "1.0.0",
                    "dataset_version_id": "dataset-console-v1",
                },
                "events": [
                    {
                        "event_type": "run_completed",
                        "summary": "Completed stub research analysis for console projection.",
                    }
                ],
                "created_at": "2026-06-15T10:05:00Z",
                "updated_at": "2026-06-15T10:30:00Z",
                "completed_at": "2026-06-15T10:30:00Z",
            }
        ]
    if url.endswith("/api/research-orchestrator/runs/rrun-console-001/artifacts"):
        return [
            {
                "id": "rart-console-001",
                "artifact_id": "rart-console-001",
                "run_id": "rrun-console-001",
                "task_id": "rtask-console-001",
                "artifact_type": "model_artifact",
                "title": "Console projection model artifact",
                "storage_ref": "object://research/console/model.json",
                "artifact_state": "candidate",
                "metadata": {
                    "metrics": {
                        "sharpe_ratio": 1.37,
                        "max_drawdown": {
                            "value": 0.041,
                            "unit": "ratio",
                            "display_value": "4.1%",
                            "direction": "lower_is_better",
                        },
                    }
                },
                "created_at": "2026-06-15T10:25:00Z",
            }
        ]
    if url.endswith("/api/memory/entries"):
        return {
            "entries": [
                {
                    "id": "mem-console-001",
                    "entry_id": "mem-console-001",
                    "knowledge_type": "research_finding",
                    "content": {
                        "headline": "Console projection memory entry",
                        "body": "The completed research run produced a reusable console projection finding.",
                        "tags": ["research"],
                    },
                    "source_event_type": "research_task_completed",
                    "source_event_id": "rtask-console-001",
                    "written_at": "2026-06-15T10:31:00Z",
                    "write_authority": "research-svc",
                    "scope": "strategy_family",
                    "scope_filter": "strategy-console-projection",
                    "reuse_count": 0,
                }
            ]
        }
    raise AssertionError(f"unexpected URL: {url}")


def _fake_service_payload_with_kw03_evidence(url: str):
    payload = _fake_service_payload(url)
    if url.endswith("/api/research-orchestrator/runs/rrun-console-001/artifacts"):
        artifact = dict(payload[0])
        metadata = dict(artifact.get("metadata") or {})
        metadata["kw03_evidence_refs"] = [
            {
                "ref_id": KW03_REF_ID,
                "source_document": {
                    "title": "Console projection source note",
                    "source_type": "research_note",
                    "source_ref": "note-console-001",
                    "captured_at": "2026-06-15T10:20:00Z",
                },
                "link_type": "supporting_evidence",
                "credibility": {
                    "tier": "primary",
                    "verified": True,
                    "last_verified_at": "2026-06-15T10:21:00Z",
                    "verification_method": "source_ingest_verification",
                },
                "linked_object_summary": {
                    "entity_type": "artifact",
                    "entity_ref": "rart-console-001",
                    "display_label": "Console projection model artifact",
                    "route_href": "/research/artifacts/rart-console-001",
                },
                "resolved_link": {
                    "availability": "available",
                    "route_href": "/knowledge/notes/note-console-001",
                    "display_label": "Console projection source note",
                    "open_in_new_tab": False,
                },
            }
        ]
        artifact["metadata"] = metadata
        return [artifact]
    return payload


def _projected_stores(monkeypatch, payload_func=_fake_service_payload):
    monkeypatch.setattr(projector, "_get", payload_func)
    return projector.project("http://research-orchestrator", memory_url="http://memory")


@contextmanager
def _projected_bff(monkeypatch) -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        out_dir = Path(td) / "bff-stores"
        stores = _projected_stores(monkeypatch)
        projector.write_projection(stores, out_dir)

        store_env = {
            "PANTHEON_BFF_RESEARCH_TICKET_STORE": out_dir / "research_tickets.json",
            "PANTHEON_BFF_RESEARCH_ANALYSIS_STORE": out_dir / "research_analyses.json",
            "PANTHEON_BFF_RESEARCH_NOTES_STORE": out_dir / "research_notes.json",
            "PANTHEON_BFF_EVIDENCE_REF_STORE": out_dir / "evidence_refs.json",
            "PANTHEON_BFF_INSIGHT_CARD_STORE": out_dir / "insight_cards.json",
            "PANTHEON_BFF_STRATEGY_SPEC_STORE": out_dir / "strategy_specs.json",
            "PANTHEON_BFF_INSTITUTIONAL_MEMORY_STORE": out_dir / "institutional_memory_entries.json",
        }
        for key, value in store_env.items():
            monkeypatch.setenv(key, str(value))
        monkeypatch.delenv("PANTHEON_MEMORY_API_URL", raising=False)

        original_store = bff_main.read_store
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=False,
        )
        try:
            yield TestClient(bff_main.app)
        finally:
            bff_main.read_store = original_store


def test_projector_does_not_promote_artifact_only_runs_to_evidence(monkeypatch) -> None:
    stores = _projected_stores(monkeypatch)

    assert stores["research_artifacts"]["rart-console-001"]["artifact_id"] == "rart-console-001"
    assert stores["evidence_refs"] == {}
    assert stores["projection_diagnostics"]["evidence_refs"]["projected"] == 0
    assert stores["projection_diagnostics"]["evidence_refs"]["skip_reasons"]["no_canonical_evidence_payload"] == 1

    insight = stores["insight_cards"]["insight-rrun-console-001"]
    assert insight["supporting_evidence_refs"] == []
    linked_types = {item["type"] for item in insight["linked_sources"]}
    assert {"research_run", "research_analysis", "research_artifact"}.issubset(linked_types)


def test_projector_accepts_complete_kw03_evidence_only(monkeypatch) -> None:
    stores = _projected_stores(monkeypatch, _fake_service_payload_with_kw03_evidence)

    assert set(stores["evidence_refs"]) == {KW03_REF_ID}
    evidence = stores["evidence_refs"][KW03_REF_ID]
    assert evidence["ref_id"] == KW03_REF_ID
    assert evidence["source_document"]["source_type"] == "research_note"
    assert evidence["source_document"]["source_ref"] == "note-console-001"
    assert evidence["link_type"] == "supporting_evidence"
    assert evidence["credibility"]["tier"] == "primary"
    assert evidence["resolved_link"]["availability"] == "available"
    assert all(not ref_id.startswith("evref-rart-") for ref_id in stores["evidence_refs"])

    insight = stores["insight_cards"]["insight-rrun-console-001"]
    assert insight["supporting_evidence_refs"] == [KW03_REF_ID]


def test_cleanup_removes_legacy_artifact_evidence_and_stale_insight_refs() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "evidence_refs.json").write_text(
            """
{
  "evref-rart-20260615-002": {
    "id": "evref-rart-20260615-002",
    "ref_id": "evref-rart-20260615-002",
    "credibility": {
      "tier": "producer_record",
      "verification_method": "research_orchestrator_projection"
    }
  },
  "evref-123e4567-e89b-12d3-a456-426614174000": {
    "id": "evref-123e4567-e89b-12d3-a456-426614174000",
    "ref_id": "evref-123e4567-e89b-12d3-a456-426614174000",
    "credibility": {"tier": "primary"}
  }
}
""".strip(),
            encoding="utf-8",
        )
        (root / "insight_cards.json").write_text(
            """
{
  "insight-rrun-20260615-002": {
    "insight_id": "insight-rrun-20260615-002",
    "supporting_evidence_refs": [
      "evref-rart-20260615-002",
      "evref-123e4567-e89b-12d3-a456-426614174000"
    ]
  }
}
""".strip(),
            encoding="utf-8",
        )

        result = legacy_cleanup.cleanup(root)

        assert result["removed_evidence_ref_ids"] == ["evref-rart-20260615-002"]
        assert result["insight_cards_touched"] == 1
        assert len(result["backup_paths"]) == 2
        assert all(Path(path).exists() for path in result["backup_paths"])
        evidence_refs = legacy_cleanup._read_json(root / "evidence_refs.json")
        insight_cards = legacy_cleanup._read_json(root / "insight_cards.json")
        assert set(evidence_refs) == {KW03_REF_ID}
        assert insight_cards["insight-rrun-20260615-002"]["supporting_evidence_refs"] == [KW03_REF_ID]


def test_cleanup_removes_insight_only_legacy_refs_after_evidence_was_deleted() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "evidence_refs.json").write_text("{}", encoding="utf-8")
        (root / "insight_cards.json").write_text(
            """
{
  "insight-rrun-20260615-001": {
    "insight_id": "insight-rrun-20260615-001",
    "supporting_evidence_refs": ["evref-rart-20260615-001"]
  }
}
""".strip(),
            encoding="utf-8",
        )

        result = legacy_cleanup.cleanup(root)

        assert result["removed_evidence_ref_ids"] == []
        assert result["removed_insight_ref_ids"] == ["evref-rart-20260615-001"]
        assert result["insight_cards_touched"] == 1
        insight_cards = legacy_cleanup._read_json(root / "insight_cards.json")
        assert insight_cards["insight-rrun-20260615-001"]["supporting_evidence_refs"] == []


def test_projected_research_console_surfaces_return_ok_counts(monkeypatch) -> None:
    with _projected_bff(monkeypatch) as client:
        knowledge = client.get("/bff/knowledge", headers=HEADERS)
        assert knowledge.status_code == 200, knowledge.text
        knowledge_body = knowledge.json()
        assert knowledge_body["page_info"]["total"] > 0
        assert knowledge_body["meta"]["surfaces"]["knowledge_inbox"]["status"] == "ok"

        analyses = client.get("/bff/research-analyses", headers=HEADERS)
        assert analyses.status_code == 200, analyses.text
        analyses_body = analyses.json()
        assert analyses_body["page_info"]["total"] > 0
        assert analyses_body["meta"]["surfaces"]["research_analyses"]["status"] == "ok"
        assert analyses_body["items"][0]["analysis_id"] == "analysis-rrun-console-001"

        tasks = client.get("/bff/research/tasks", headers=HEADERS)
        assert tasks.status_code == 200, tasks.text
        tasks_body = tasks.json()
        assert tasks_body["page_info"]["total"] > 0
        assert tasks_body["meta"]["surfaces"]["research_task_list"]["status"] == "ok"
        assert tasks_body["items"][0]["ticket_id"] == "rtask-console-001"
