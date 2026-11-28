"""The read-only acceptance probe requires persisted records and an observed window."""
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("recovery_probe", Path(__file__).parents[1] / "probe_paused_recovery.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def sample(tmp_path):
    status = {"sources": {s: {"status": "complete", "last_run_at": "2026-11-29T01:00:00Z", "last_run_id": "host-1"}
                          for s in module.SOURCES}}
    mapping = {s: "conn-" + s for s in module.SOURCES}
    rows = []
    for source, cid in mapping.items():
        payload = {"connector_id": cid, "ingest_run_id": source, "started_at": "2026-11-29T01:00:00Z",
                   "status": "completed", "normalized_count": 1, "storage_refs": {"records": ["persisted-1"]}}
        rows.extend({"record_type": kind, "payload": payload} for kind in ("ingest_run", "ingest_receipt"))
    journal = tmp_path / "journal.jsonl"
    journal.write_text("".join(json.dumps(row) + "\n" for row in rows))
    dlq = tmp_path / "dlq.jsonl"
    dlq.write_text("")
    return status, journal, dlq, mapping


def check(args, **kwargs):
    return module.probe(*args, "2026-11-28T02:00:00Z", "2026-11-29T02:00:00Z", **kwargs)


def test_probe_passes_with_persisted_source_ids_and_no_growth(tmp_path):
    result = check(sample(tmp_path))
    assert result["status"] == "passed" and result["dlq_growth_per_day"] == 0
    assert result["proofs"]["collector-gdelt"]["ingest_run_ids"] == ["collector-gdelt"]


@pytest.mark.parametrize("change", ["no_receipt", "no_storage", "failed_run", "dlq", "youtube_unknown"])
def test_probe_rejects_unproven_acceptance(tmp_path, change):
    args = sample(tmp_path)
    status, journal, dlq, mapping = args
    records = list(module.rows(journal))
    if change == "no_receipt":
        records = [r for r in records if r["record_type"] != "ingest_receipt"]
    elif change == "no_storage":
        for record in records:
            record["payload"].pop("storage_refs")
    elif change == "failed_run":
        records[0]["payload"]["status"] = "failed"
    elif change == "dlq":
        dlq.write_text(json.dumps({"entry_id": "new", "rejected_at": "2026-11-29T01:00:00Z",
                                  "event": {"payload": {"connector_id": mapping["collector-gdelt"]}}}) + "\n")
    else:
        status["sources"]["collector-youtube-finance"].update(status="paused", paused_reason="no_timer", timer_enabled=False)
    journal.write_text("".join(json.dumps(r) + "\n" for r in records))
    assert check(args)["status"] == "failed"
    if change == "youtube_unknown":
        assert check(args, youtube_pause_evidence={"intentional": True, "actor": "hosted-ops",
                                                  "evidence_ref": "operator-decision-123"})["status"] == "passed"


def test_probe_rejects_short_observation(tmp_path):
    with pytest.raises(ValueError, match="24 hours"):
        module.probe(*sample(tmp_path), "2026-11-29T01:00:00Z", "2026-11-29T02:00:00Z")
