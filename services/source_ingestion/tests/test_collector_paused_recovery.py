"""Producer availability is a snapshot, not a synthetic empty ingest."""
import json

import pytest

from services.source_ingestion.connectors.collector_bundle import compute_bundle_sha256, source_offering
from services.source_ingestion.tests.test_collector_bundle_connector import make_bundle, adapter_for
from services.source_ingestion.tests.test_controller_worker import (
    _collector_actual, _collector_reconcile, COLLECTOR_CONNECTOR_ID,
)
from services.source_ingestion.controller_worker import select_collector_pull_connectors


@pytest.mark.parametrize("status,reason", [("paused", "http_429"), ("partial", "parse_failed: invalid numeric value '--'")])
def test_reasoned_state_skips_repeated_scheduled_runs_and_readmits_on_recovery(status, reason):
    actual = _collector_actual()
    item = actual["connectors"][0]
    doc = {"sources": {"source": {"offered": True, "status": status, "reason": reason}}}
    observation = source_offering(doc, "source")
    assert observation == {"offered": False, "collector_status": status, "reason": reason}
    for _ in range(1000):
        item["collector_offering"] = source_offering(doc, "source")
        assert item["collector_offering"] == observation
        assert select_collector_pull_connectors(actual, _collector_reconcile(COLLECTOR_CONNECTOR_ID)) == []
    doc["sources"]["source"].update(status="complete", reason=None)
    item["collector_offering"] = source_offering(doc, "source")
    assert select_collector_pull_connectors(actual, _collector_reconcile(COLLECTOR_CONNECTOR_ID)) == [COLLECTOR_CONNECTOR_ID]


@pytest.mark.parametrize("status", ["paused", "partial"])
def test_unexplained_or_unverified_state_still_runs_fail_closed(status):
    assert source_offering({"sources": {"source": {"offered": True, "status": status}}}, "source")["offered"] is True
    assert source_offering({"sources": {"source": {"status": status, "reason": "429"}}}, "source")["offered"] is None


@pytest.mark.parametrize("status", ["paused", "partial"])
def test_empty_failure_does_not_hold_later_verified_run_forever(tmp_path, status):
    old, first, manifest = make_bundle(status=status)
    base = f"bundles/{first['source_id']}/{first['date']}/{first['run_id']}"
    manifest.update(items=[], bundle_sha256=compute_bundle_sha256([]), paused_reason="http_429")
    old[f"{base}/manifest.json"] = json.dumps(manifest).encode()
    old[f"{base}/items.jsonl"] = b""
    newer, second, _ = make_bundle(run_id="recovered", finished="2026-11-10T03:05:00Z")
    adapter = adapter_for({**old, **newer}, [first, second], tmp_path)
    result = adapter.pull()
    assert len(result.records) == 2
    assert result.next_cursor == "2026-11-10T03:05:00Z#recovered"
    assert result.complete and not result.paused_reasons


def test_nonempty_partial_is_never_discarded_for_newer_run(tmp_path):
    old, first, _ = make_bundle(status="partial")
    newer, second, _ = make_bundle(run_id="recovered", finished="2026-11-10T03:05:00Z")
    result = adapter_for({**old, **newer}, [first, second], tmp_path).pull()
    assert not result.complete
    assert result.next_cursor.startswith("2026-11-10T02:05:00Z#run1")
