"""TJ-E2E-011 materializer capacity/rebuild baseline benchmark.

Measures how long ``JourneyMaterializer.rebuild()`` takes over a synthetic
event set, so operators have a recorded baseline to compare against during a
disaster-rebuild drill or capacity review. This does not touch any shared
store or network service; it only builds in-memory synthetic events and
times the pure materializer.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from services.trade_journey.materializer import STAGES, JourneyMaterializer


def _synthetic_events(*, journeys: int, stages_per_journey: int, start: datetime) -> list[dict[str, Any]]:
    stages_per_journey = max(1, min(stages_per_journey, len(STAGES)))
    events: list[dict[str, Any]] = []
    for journey_index in range(journeys):
        journey_id = f"journey-{journey_index:06d}"
        for stage_index in range(stages_per_journey):
            occurred_at = start + timedelta(seconds=journey_index * stages_per_journey + stage_index)
            events.append({
                "event_id": f"{journey_id}-e{stage_index}",
                "journey_id": journey_id,
                "tenant_id": "tenant-baseline",
                "environment": "paper",
                "occurred_at": occurred_at.isoformat().replace("+00:00", "Z"),
                "source": "capacity-baseline",
                "stage": STAGES[stage_index],
                "stage_status": "succeeded",
            })
    return events


def measure_rebuild_baseline(
    *,
    journeys: int = 500,
    stages_per_journey: int = 10,
    start: datetime | None = None,
) -> dict[str, Any]:
    start = start or datetime(2026, 7, 12, tzinfo=timezone.utc)
    events = _synthetic_events(journeys=journeys, stages_per_journey=stages_per_journey, start=start)
    materializer = JourneyMaterializer()

    began = time.perf_counter()
    materializer.rebuild(events)
    elapsed_seconds = time.perf_counter() - began

    return {
        "journeys": journeys,
        "stages_per_journey": stages_per_journey,
        "event_count": len(events),
        "elapsed_seconds": elapsed_seconds,
        "events_per_second": (len(events) / elapsed_seconds) if elapsed_seconds > 0 else None,
        "journeys_per_second": (journeys / elapsed_seconds) if elapsed_seconds > 0 else None,
        "rebuild_status": materializer.rebuild_status,
        "revision": materializer.revision,
        "projection_count": len(materializer.projections),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure JourneyMaterializer rebuild throughput")
    parser.add_argument("--journeys", type=int, default=500)
    parser.add_argument("--stages-per-journey", type=int, default=10)
    args = parser.parse_args(argv)

    result = measure_rebuild_baseline(journeys=args.journeys, stages_per_journey=args.stages_per_journey)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
