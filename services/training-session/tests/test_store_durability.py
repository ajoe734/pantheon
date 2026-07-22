from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


SERVICE_DIR = Path(__file__).resolve().parents[1]
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from store import TrainingSessionStore  # noqa: E402


def test_shared_store_keeps_all_concurrent_job_updates(tmp_path: Path) -> None:
    stores = [TrainingSessionStore(tmp_path) for _ in range(8)]

    def write_job(index: int) -> None:
        stores[index % len(stores)].put_preview_job(
            f"job-{index:03d}",
            {"status": "queued", "sequence": index},
        )

    with ThreadPoolExecutor(max_workers=len(stores)) as executor:
        list(executor.map(write_job, range(80)))

    jobs = TrainingSessionStore(tmp_path).list_preview_jobs()
    assert len(jobs) == 80
    assert {job["sequence"] for job in jobs} == set(range(80))


def test_event_append_is_durable_and_complete_across_store_instances(tmp_path: Path) -> None:
    stores = [TrainingSessionStore(tmp_path) for _ in range(8)]

    def append_event(index: int) -> None:
        stores[index % len(stores)].append_event(
            {
                "event_id": f"event-{index:03d}",
                "session_id": "session-001",
                "sequence_number": index + 1,
            }
        )

    with ThreadPoolExecutor(max_workers=len(stores)) as executor:
        list(executor.map(append_event, range(80)))

    events = TrainingSessionStore(tmp_path).list_event_log("session-001")
    assert len(events) == 80
    assert {event["event_id"] for event in events} == {f"event-{index:03d}" for index in range(80)}
