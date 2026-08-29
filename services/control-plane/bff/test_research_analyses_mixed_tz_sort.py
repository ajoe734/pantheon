"""Regression for mixed timezone-aware and naive analysis timestamps."""
from __future__ import annotations

import sys
from pathlib import Path

BFF_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BFF_DIR))

from ports import DefaultResearchKnowledgeSourcePort  # noqa: E402


MIXED = [
    {"id": "a-aware", "analysis_id": "a-aware", "run_at": "2026-01-03T00:00:00Z"},
    {"id": "b-missing", "analysis_id": "b-missing", "run_at": None},
    {"id": "c-naive", "analysis_id": "c-naive", "run_at": "2026-01-02T00:00:00"},
    {"id": "d-aware2", "analysis_id": "d-aware2", "run_at": "2026-01-01T00:00:00+00:00"},
]


def test_list_research_analyses_mixed_tz_does_not_raise() -> None:
    port = DefaultResearchKnowledgeSourcePort(
        research_analyses_store={item["analysis_id"]: item for item in MIXED}
    )

    result = port.list_research_analyses()

    assert len(result) == 4
    assert str(result[0].get("run_at")).startswith("2026-01-03")
