"""Cross-process acceptance for supervised source reconciliation and execution."""

from __future__ import annotations

import importlib
import json
import multiprocessing
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty
from typing import Any


CONNECTOR_ID = "multiprocess-supervised-notes"
SOURCE_ID = "src-multiprocess-supervised-note-1"
CONTROLLER_TOKEN = "l12-src-001-multiprocess-controller-token-0000000000000000"


def _persona() -> dict[str, Any]:
    return {
        "persona_id": "persona-multiprocess-source",
        "name": "Multi-process Source Acceptance",
        "mandate": "Prove supervised source convergence across workers",
        "lifecycle_state": "research_only",
        "created_at": "2026-07-26T00:00:00Z",
        "required_data_sources": [
            {
                "dataset": "multiprocess_supervised_note",
                "market": "GLOBAL",
                "cadence": "daily",
                "source_class": "live_pull",
                "connector_candidates": [CONNECTOR_ID],
                "policy_gates": [
                    "require_connector_approved",
                    "require_schedule_active",
                ],
            }
        ],
    }


def _worker(
    data_dir: str,
    barrier: Any,
    result_queue: Any,
) -> None:
    """Import an independent service module and race one full controller tick."""

    try:
        os.environ["SOURCE_INGEST_DATA_DIR"] = data_dir
        os.environ["SOURCE_INGEST_EVIDENCE_BACKEND"] = "jsonl"
        os.environ["SOURCE_INGEST_MAX_RECORDS"] = "10"
        os.environ["SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY"] = "1"
        os.environ["SOURCE_INGEST_FRONTIER_MAX_ATTEMPTS"] = "2"
        os.environ["SOURCE_INGEST_FRONTIER_BACKOFF_SECONDS"] = "60"
        sys.modules.pop("services.source_ingestion.main", None)
        module = importlib.import_module("services.source_ingestion.main")

        from fastapi.testclient import TestClient

        from services.source_ingestion.connectors.base import SourceConnector
        from services.source_ingestion.persona_source_reconciler import SourceProvisioningReconciler

        class MultiprocessProvider:
            def connector(self) -> SourceConnector:
                return SourceConnector(
                    connector_id=CONNECTOR_ID,
                    source_type="internal_note",
                    provider="L12-SRC-001 multi-process fixture",
                    license_scope="internal",
                )

            def fetch_config(self) -> dict[str, Any]:
                source_timestamp = (
                    datetime.now(timezone.utc)
                    .replace(microsecond=0)
                    .isoformat()
                    .replace("+00:00", "Z")
                )
                return {
                    "mode": "static_records",
                    "next_watermark": "multiprocess-watermark-1",
                    "records": [
                        {
                            "source_id": SOURCE_ID,
                            "title": "Multi-process supervised source evidence",
                            "content_ref": "memory://l12-src-001/multiprocess/source-1",
                            "metadata": {
                                "body": "Exactly one worker may materialize this record.",
                                "access_scope": ["operator"],
                                "available_time": source_timestamp,
                            },
                        }
                    ],
                }

        def reconciler() -> SourceProvisioningReconciler:
            return SourceProvisioningReconciler(
                manager=module.manager,
                connector_store=module.connector_store,
                schedule_store=module.schedule_config_store,
                provider_factories={
                    CONNECTOR_ID: lambda _connector_id: MultiprocessProvider(),
                },
            )

        module._source_provisioning_reconciler = reconciler
        personas = [_persona()]
        payload = {
            "personas": personas,
            "authoritative_snapshot": True,
            "desired_state_sha256": module._desired_state_digest(personas),
            "source_authority": "test://l12-src-001/multiprocess",
        }
        headers = {"Authorization": f"Bearer {module.controller_token}"}

        barrier.wait(timeout=30)
        with TestClient(module.app) as client:
            reconcile = client.post(
                "/api/source-ingest/persona-source-provisioning/reconcile",
                headers=headers,
                json=payload,
            )
            scheduled = client.post(
                "/api/source-ingest/run-scheduled",
                headers=headers,
                json={"max_concurrency": 1},
            )
        result_queue.put(
            {
                "pid": os.getpid(),
                "reconcile_status": reconcile.status_code,
                "reconcile": reconcile.json(),
                "scheduled_status": scheduled.status_code,
                "scheduled": scheduled.json(),
            }
        )
    except Exception:  # noqa: BLE001 - child errors must reach the parent assertion.
        result_queue.put(
            {
                "pid": os.getpid(),
                "error": traceback.format_exc(),
            }
        )


def _jsonl_records(path: Path, record_type: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        payload
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
        for payload in (json.loads(line),)
        if payload.get("record_type") == record_type
    ]


def test_two_process_workers_create_one_connector_schedule_run_and_source_record(
    tmp_path: Path,
) -> None:
    (tmp_path / "controller_token").write_text(CONTROLLER_TOKEN, encoding="utf-8")
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    result_queue = context.Queue()
    workers = [
        context.Process(
            target=_worker,
            args=(str(tmp_path), barrier, result_queue),
        )
        for _ in range(2)
    ]

    for worker in workers:
        worker.start()
    results = []
    try:
        for _ in workers:
            results.append(result_queue.get(timeout=60))
    except Empty as exc:
        raise AssertionError("multi-process source workers did not return results") from exc
    finally:
        for worker in workers:
            worker.join(timeout=15)
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=5)

    assert all(worker.exitcode == 0 for worker in workers)
    assert all("error" not in result for result in results), results
    assert len({result["pid"] for result in results}) == 2
    assert [result["reconcile_status"] for result in results] == [200, 200]
    assert [result["scheduled_status"] for result in results] == [200, 200]

    reconcile_summaries = [result["reconcile"]["summary"] for result in results]
    assert sorted(
        (summary["mutated"], summary["satisfied"])
        for summary in reconcile_summaries
    ) == [(0, 1), (1, 0)]
    assert sum(
        result["scheduled"]["summary"]["total_ran"]
        for result in results
    ) == 1

    connector_records = _jsonl_records(tmp_path / "connector_config.jsonl", "connector_config")
    schedule_records = _jsonl_records(tmp_path / "connector_schedule.jsonl", "connector_schedule")
    frontier_records = _jsonl_records(tmp_path / "ingest_schedule.jsonl", "crawl_frontier_item")
    run_records = _jsonl_records(tmp_path / "ingest_schedule.jsonl", "ingest_run")
    source_records = _jsonl_records(tmp_path / "source_evidence.jsonl", "source_record")

    assert [record["record_id"] for record in connector_records] == [CONNECTOR_ID]
    assert [record["record_id"] for record in schedule_records] == [CONNECTOR_ID]
    assert len({record["record_id"] for record in frontier_records}) == 1
    assert len({record["record_id"] for record in run_records}) == 1
    assert [record["record_id"] for record in source_records] == [SOURCE_ID]
