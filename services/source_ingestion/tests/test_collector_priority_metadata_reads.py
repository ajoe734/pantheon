"""COLLECTOR-PRIORITY-METADATA-REVERIFY-20261119: deterministic file-read counts per pull."""
from __future__ import annotations

import hashlib
import json

import pytest

from services.source_ingestion.connectors import collector_bundle as cb
from services.source_ingestion.connectors.collector_bundle import CollectorBundleAdapter, compute_bundle_sha256
from services.source_ingestion.tests.test_collector_bundle_connector import (
    FETCHED, HOST, _TWSE_ROW, _CountingTransport,
)

SOURCE = "collector-tw-tpex-price-history"
LATEST = "2026-11-18"
HISTORY = "2024-12-05"


@pytest.fixture(autouse=True)
def _clean_memos():
    for memo in (cb._METADATA_CACHE, cb._PRIORITY_TRADE_DATE_MEMO, cb._PRIORITY_DELIVERED_MEMO):
        memo.clear()
    yield
    for memo in (cb._METADATA_CACHE, cb._PRIORITY_TRADE_DATE_MEMO, cb._PRIORITY_DELIVERED_MEMO):
        memo.clear()


def _run(run_id, entry_day, trade_day, n=3, tag="a", finished=None):
    finished = finished or f"{entry_day}T{abs(hash(run_id)) % 20:02d}:00:00Z"
    items, rows, raw = [], [], {}
    for i in range(n):
        body = f"<r>{tag}-{trade_day}-{i}</r>".encode()
        sha = hashlib.sha256(body).hexdigest()
        items.append({"item_id": f"i{i}", "url": f"https://example.test/{i}", "http_status": 200,
                      "content_sha256": sha, "raw_path": f"raw/{sha}.html", "bytes": len(body)})
        rows.append({"id": f"i{i}", "url": f"https://example.test/{i}", "fetched_at": "2026-11-19T00:00:00Z",
                     **{**_TWSE_ROW, "exchange": "TPEX", "symbol": f"{1000 + i}", "trade_date": trade_day}})
        raw[sha] = body
    manifest = {"schema_version": "collector_bundle.v1", "source_id": SOURCE, "run_id": run_id,
                "collector_version": "1.0.0", "started_at": finished, "finished_at": finished,
                "fetch_policy_hash": "fph", "robots_decision": "allowed", "terms_ref": "https://example.test/terms",
                "attribution": "TPEx", "items": items, "status": "complete",
                "bundle_sha256": compute_bundle_sha256([m["content_sha256"] for m in items])}
    base = f"bundles/{SOURCE}/{entry_day}/{run_id}"
    files = {f"{base}/manifest.json": json.dumps(manifest).encode(),
             f"{base}/items.jsonl": "\n".join(json.dumps(r) for r in rows).encode()}
    files.update({f"{base}/raw/{s}.html": b for s, b in raw.items()})
    entry = {"source_id": SOURCE, "date": entry_day, "run_id": run_id, "finished_at": finished,
             "bundle_sha256": manifest["bundle_sha256"]}
    return files, entry


class World:
    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.files: dict[str, bytes] = {}
        self.entries: list[dict] = []
        self.transport = _CountingTransport(self.files)

    def add(self, *args, **kw):
        files, entry = _run(*args, **kw)
        self.files.update(files)
        self.entries = [e for e in self.entries if e["run_id"] != entry["run_id"]] + [entry]
        self.files["bundles/index.json"] = json.dumps({"bundles": self.entries}).encode()
        return entry

    def adapter(self, transport=None):
        return CollectorBundleAdapter(
            connector_id="conn-t", raw_root=str(self.tmp / "store"), transport=transport or self.transport,
            host=HOST, source_id=SOURCE, source_type="market", attribution_required=True,
            dataset="tw_price_daily", max_records=1000, cadence_seconds=1800)

    def pull(self, transport=None):
        self.transport.reads.clear()
        adapter = self.adapter(transport)
        result = adapter.pull(cursor=adapter.load_resume_cursor())
        adapter.save_resume_cursor(result)
        return result

    def reads(self, name):
        return [r for r in self.transport.reads if r.endswith(name)]

    def count(self, run_id, name):
        return sum(1 for r in self.reads(name) if f"/{run_id}/" in r)


def _hosted_shape(tmp_path):
    w = World(tmp_path)
    for rid in ("run-a", "run-b", "close-2026-11-18-1500", "run-c"):
        w.add(rid, LATEST, LATEST, tag=rid)
    for rid in ("run-old1", "run-old2"):  # fetched on the latest date, carry the previous session
        w.add(rid, LATEST, "2026-11-17", tag=rid)
    w.add(f"backfill-{HISTORY}", "2026-11-01", HISTORY, n=8, tag="hist")
    return w


def _settle(w, pulls=4):
    for _ in range(pulls):
        w.pull()


def test_unchanged_index_costs_no_metadata_reads(tmp_path):
    w = _hosted_shape(tmp_path)
    _settle(w)
    first = w.pull()
    assert not first.records or first.priority_key is None
    # priority path: index only; candidates untouched
    for rid in ("run-a", "run-b", "close-2026-11-18-1500", "run-c", "run-old1", "run-old2"):
        assert w.count(rid, "manifest.json") == 0 and w.count(rid, "items.jsonl") == 0
    assert len(w.reads("index.json")) <= 2  # priority + ordered sweep


def test_new_run_is_verified_and_delivered_as_today(tmp_path):
    w = _hosted_shape(tmp_path)
    _settle(w)
    w.add("run-new", LATEST, LATEST, n=4, tag="new", finished="2026-11-18T23:00:00Z")
    w.transport.reads.clear()
    result = w.pull()
    assert w.count("run-new", "manifest.json") >= 1 and w.count("run-new", "items.jsonl") >= 1
    assert result.priority_key and any(r.source_id for r in result.records)


def test_changed_bundle_sha_of_existing_run_is_verified_and_correction_delivered(tmp_path):
    w = _hosted_shape(tmp_path)
    _settle(w)
    w.add("run-c", LATEST, LATEST, tag="corrected", finished="2026-11-18T23:30:00Z")
    result = w.pull()
    assert w.count("run-c", "manifest.json") >= 1
    assert result.priority_key
    latest = [r for r in result.records if r.metadata["trade_date"] == LATEST]
    assert len(latest) == 3  # corrected content of every identity of run-c


def test_restart_verifies_as_today(tmp_path):
    w = _hosted_shape(tmp_path)
    _settle(w)
    cb._METADATA_CACHE.clear()
    cb._PRIORITY_DELIVERED_MEMO.clear()
    cb._PRIORITY_TRADE_DATE_MEMO.clear()
    w.pull()
    assert w.count("run-a", "manifest.json") >= 1 and w.count("run-c", "manifest.json") >= 1


def test_previous_session_candidates_not_reverified_once_known(tmp_path):
    w = _hosted_shape(tmp_path)
    _settle(w)
    w.add("run-new", LATEST, LATEST, n=4, tag="new", finished="2026-11-18T23:00:00Z")
    w.pull()
    for rid in ("run-old1", "run-old2"):
        assert w.count(rid, "manifest.json") == 0 and w.count(rid, "items.jsonl") == 0


def test_history_bundle_metadata_is_read_once_across_pulls(tmp_path):
    w = _hosted_shape(tmp_path)
    history = f"backfill-{HISTORY}"
    reads = 0
    for _ in range(4):
        w.pull()
        reads += w.count(history, "manifest.json")
    w.pull()
    reads += w.count(history, "manifest.json")
    assert reads == 1
    assert sum(1 for r in w.transport.reads if r.endswith("items.jsonl") and f"/{history}/" in r) == 0


def test_pending_session_is_not_memoised(tmp_path):
    w = _hosted_shape(tmp_path)
    adapter = w.adapter()
    result = adapter.pull(cursor=None, max_records=2)
    assert not result.priority_done
    assert not cb._PRIORITY_DELIVERED_MEMO
    # a second pull with the session still pending takes the full path again (no early None)
    again = w.adapter().pull(cursor=None, max_records=2)
    assert again.priority_key and not again.priority_done


def test_memos_are_bounded_by_count(tmp_path):
    for i in range(cb.PRIORITY_TRADE_DATE_MEMO_ENTRIES + 20):
        with cb._METADATA_CACHE_LOCK:
            cb._PRIORITY_TRADE_DATE_MEMO[("s", str(i), "d")] = "2026-11-18"
    w = _hosted_shape(tmp_path)
    _settle(w, 2)
    assert len(cb._PRIORITY_TRADE_DATE_MEMO) <= cb.PRIORITY_TRADE_DATE_MEMO_ENTRIES
    assert len(cb._PRIORITY_DELIVERED_MEMO) <= cb.PRIORITY_DELIVERED_MEMO_ENTRIES


def test_state_file_format_is_unchanged(tmp_path):
    w = _hosted_shape(tmp_path)
    _settle(w, 2)
    doc = json.loads(w.adapter()._priority_key_path().read_text())
    assert set(doc) <= {"source_id", "key", "delivered", "done", "held", "trade_date", "items", "updated_at",
                        "complete", "priority_key"} | set(doc)
    # No memo field is persisted: the old release reads this file unchanged.
    assert not any("memo" in k or "fingerprint" in k for k in doc)
