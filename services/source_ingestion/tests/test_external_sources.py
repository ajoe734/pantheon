"""PIT validation error is diagnosable: it names the source/record id and both timestamps, bounded."""
from __future__ import annotations

import pytest

from services.source_ingestion.connectors.base import SourceRecord, SourceType
from services.source_ingestion.external_sources import SourceEvidenceError, validate_external_source_record


def _record(source_id: str) -> SourceRecord:
    return SourceRecord(
        source_id=source_id, connector_id="conn-x", source_type=SourceType.MARKET, title="t", content_ref="https://e.test/x",
        metadata={"license_scope": "research", "entitlement_tags": ["market-research"], "access_scope": ["research"],
                  "event_time": "2026-11-17T06:30:00Z", "available_time": "2026-11-17T05:45:00Z"},
        trace_id="t",
    )


def test_pit_error_names_ids_and_times_and_is_bounded():
    with pytest.raises(SourceEvidenceError) as err:
        validate_external_source_record(_record("collector:s:" + "x" * 500), force=True)
    text = str(err.value)
    assert "available_time must be >= event_time for PIT external source records" in text
    assert "2026-11-17T06:30:00Z" in text and "2026-11-17T05:45:00Z" in text
    assert "conn-x" in text and len(text) < 500
