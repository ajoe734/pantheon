"""Regression: KeywordRetriever.retrieve must not raise when matches tie on
score and their updated_at values mix tz-aware and naive datetimes.

Verification campaign 2026-06-14, round 33, finding F19. The sort key
`(item.score, item.updated_at or datetime.min)` compared an aware indexed_at
(parsed from "...Z") against the naive datetime.min floor on a score tie →
"TypeError: can't compare offset-naive and offset-aware datetimes".
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from services.search.retriever import KeywordRetriever


def _doc(indexed_at):
    # retrieve() only reads these attributes, so duck-typing is sufficient.
    return SimpleNamespace(
        knowledge_object=SimpleNamespace(title="title"),
        search_text="alpha beta gamma",
        relevance_score=0.5,
        indexed_at=indexed_at,
    )


def test_retrieve_mixed_tz_tiebreak_does_not_raise():
    docs = [
        _doc(datetime(2026, 1, 1, tzinfo=timezone.utc)),  # aware
        _doc(None),                                        # -> datetime.min (naive)
        _doc(datetime(2026, 1, 2)),                        # naive
    ]
    # All three match "alpha" with identical score -> the datetime tie-breaker
    # is exercised; must not raise.
    result = KeywordRetriever().retrieve("alpha", docs, top_k=10)
    assert len(result) == 3
