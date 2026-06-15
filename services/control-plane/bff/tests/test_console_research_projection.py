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

from scripts import project_research_to_bff_surfaces as projector  # noqa: E402

import main as bff_main  # noqa: E402
from read_store import ReadSurfaceStore  # noqa: E402


HEADERS = {"Authorization": "Bearer op-dev:admin:mfa"}


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


@contextmanager
def _projected_bff(monkeypatch) -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        out_dir = Path(td) / "bff-stores"
        monkeypatch.setattr(projector, "_get", _fake_service_payload)
        stores = projector.project("http://research-orchestrator", memory_url="http://memory")
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
