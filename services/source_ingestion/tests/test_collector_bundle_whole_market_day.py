"""COLLECTOR-BUNDLE-WHOLE-MARKET-DAY-READ-20261118: one whole market day in one bundle.

Hosted evidence (collector host, 2026-11-17 TPEx trade date): items.jsonl 16175460 bytes for 13870
rows (about 1166 bytes per row). Every shape here is generated locally with a fixed seed.
"""
from __future__ import annotations

import hashlib
import json
import random
from datetime import datetime, timezone

import pytest

from services.source_ingestion.connectors import collector_bundle as cb
from services.source_ingestion.connectors.base import SourceEvidenceError
from services.source_ingestion.tests.test_collector_bundle_connector import FakeTransport

SOURCE = "collector-tw-tpex-price-history"
DAY = "2026-11-17"
RUN = "run-20261118T001529Z"
HOSTED_ROWS = 13870
SEED = 20261118


def _row_text(rng: random.Random, width: int) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(rng.choice(alphabet) for _ in range(width))


def make_market_day(rows: int = HOSTED_ROWS, *, seed: int = SEED, source: str = SOURCE, day: str = DAY,
                    run_id: str = RUN, with_raw: bool = True, pad: int = 666):
    """A TPEx-shaped complete bundle. Returns (files, index_entry)."""
    rng = random.Random(seed)
    base = f"bundles/{source}/{day}/{run_id}"
    finished = f"{day}T16:15:29Z"
    endpoint = "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes"
    items, lines, files = [], [], {}
    for n in range(rows):
        code = f"{n:06d}"
        item_id = f"tpex-price-history-{day}-{code}"
        body = f"<r>{day}-{code}-{rng.getrandbits(32)}</r>".encode() if with_raw else b""
        sha = hashlib.sha256(body or item_id.encode()).hexdigest()
        items.append({"item_id": item_id, "url": endpoint, "http_status": 200, "content_sha256": sha,
                      "raw_path": f"raw/{sha}.html", "bytes": len(body)})
        close = None if n % 3 == 0 else round(10 + rng.random() * 900, 2)
        lines.append(json.dumps({
            "id": item_id, "url": endpoint, "fetched_at": f"{day}T16:00:00Z", "symbol": code,
            "security_name": f"name {code}", "exchange": "TPEX", "dataset": "tw_price_daily", "trade_date": day,
            "open": close, "high": close, "low": close, "close": close, "volume_shares": rng.randrange(10**6),
            "transactions": rng.randrange(10**4), "official_publication_ref": _row_text(rng, pad),
            "api_endpoint": endpoint,
        }))
        if with_raw:
            files[f"{base}/raw/{sha}.html"] = body
    manifest = {
        "schema_version": "collector_bundle.v1", "source_id": source, "run_id": run_id, "collector_version": "1.0.0",
        "started_at": f"{day}T16:00:00Z", "finished_at": finished, "fetch_policy_hash": "fph",
        "robots_decision": "allowed", "terms_ref": "https://example.test/terms", "attribution": "TPEx",
        "items": items, "status": "complete",
        "bundle_sha256": cb.compute_bundle_sha256([m["content_sha256"] for m in items]),
    }
    files[f"{base}/manifest.json"] = json.dumps(manifest).encode()
    files[f"{base}/items.jsonl"] = "\n".join(lines).encode()
    entry = {"source_id": source, "date": day, "run_id": run_id, "finished_at": finished,
             "bundle_sha256": manifest["bundle_sha256"]}
    files["bundles/index.json"] = json.dumps({"bundles": [entry]}).encode()
    files["status.json"] = json.dumps({"sources": {source: {"status": "ok", "last_run_at": finished}}}).encode()
    return files, entry


class CountingTransport(FakeTransport):
    """Counts bytes fetched per path; honours max_bytes the way the hosted transports do."""

    def __init__(self, files):
        super().__init__(files)
        self.fetched: dict[str, int] = {}

    def read(self, relative_path, *, max_bytes):
        data = self.files[relative_path]
        if len(data) > max_bytes:
            error = cb.PayloadTooLarge(f"Payload exceeded max byte limit ({max_bytes} bytes)")
            error.limit, error.size, error.size_is_exact = max_bytes, len(data), True
            raise error
        self.fetched[relative_path] = self.fetched.get(relative_path, 0) + len(data)
        return data

    def metadata_bytes(self):
        return sum(n for p, n in self.fetched.items() if p.endswith(("manifest.json", "items.jsonl")))


def adapter(root, transport, cap=256):
    return cb.CollectorBundleAdapter(
        connector_id=SOURCE, source_id=SOURCE, source_type="market", dataset="tw_price_daily",
        cadence_seconds=600, max_records=cap, raw_root=str(root), transport=transport)


@pytest.fixture(autouse=True)
def isolated_cache():
    with cb._METADATA_CACHE_LOCK:
        cb._METADATA_CACHE.clear()
    yield
    with cb._METADATA_CACHE_LOCK:
        cb._METADATA_CACHE.clear()


def base_of(entry):
    return f"bundles/{entry['source_id']}/{entry['date']}/{entry['run_id']}"


def test_bounds_are_derived_from_the_measured_day():
    assert cb.MAX_ITEMS_JSONL_BYTES == cb.WHOLE_MARKET_DAY_MAX_ROWS * cb.ITEMS_JSONL_ROW_BYTES_CEILING
    assert cb.MAX_MANIFEST_BYTES == cb.WHOLE_MARKET_DAY_MAX_ROWS * cb.MANIFEST_ITEM_BYTES_CEILING
    assert cb.WHOLE_MARKET_DAY_MAX_ROWS >= 2 * 14000
    assert cb.ITEMS_JSONL_ROW_BYTES_CEILING >= 16175460 / HOSTED_ROWS  # measured hosted bytes per row
    assert cb.MAX_ITEMS_JSONL_BYTES > 2 * 16175460
    assert cb.VERIFIED_METADATA_CACHE_BYTES >= cb.MAX_ITEMS_JSONL_BYTES + cb.MAX_MANIFEST_BYTES


def test_hosted_shape_is_above_the_old_limit_and_is_read_whole():
    files, entry = make_market_day()
    base = base_of(entry)
    items_size, manifest_size = len(files[f"{base}/items.jsonl"]), len(files[f"{base}/manifest.json"])
    assert items_size > 8 * 1024 * 1024 and 15_500_000 < items_size < 16_800_000
    bundle = cb.verify_bundle(CountingTransport(files), expected_source_id=SOURCE, entry=entry, item_window=(0, 0))
    assert len(bundle.items) == HOSTED_ROWS and bundle.source_bytes == items_size + manifest_size


def test_old_limit_reproduces_the_hosted_failure(monkeypatch):
    """With the previous 8 MiB bound on the two files the hosted shape fails exactly as hosted does."""
    files, entry = make_market_day()
    transport = CountingTransport(files)
    with pytest.raises(cb.SourceEvidenceError, match=r"^Payload exceeded max byte limit \(8388608 bytes\)$"):
        transport.read(f"{base_of(entry)}/items.jsonl", max_bytes=cb.MAX_JSON_BYTES)
    monkeypatch.setattr(cb, "MAX_ITEMS_JSONL_BYTES", cb.MAX_JSON_BYTES)
    with pytest.raises(cb.SourceEvidenceError, match=r"items\.jsonl is \d+ bytes, above its read bound of 8388608"):
        cb.verify_bundle(transport, expected_source_id=SOURCE, entry=entry, item_window=(0, 0))


@pytest.mark.parametrize("name,bound,rows_attr", [
    ("items.jsonl", "MAX_ITEMS_JSONL_BYTES", None), ("manifest.json", "MAX_MANIFEST_BYTES", None)])
def test_file_at_the_bound_reads_and_just_above_fails_closed_with_reason(monkeypatch, name, bound, rows_attr):
    files, entry = make_market_day(40, with_raw=False)
    path = f"{base_of(entry)}/{name}"
    size = len(files[path])
    monkeypatch.setattr(cb, bound, size)
    cb.verify_bundle(CountingTransport(files), expected_source_id=SOURCE, entry=entry, item_window=(0, 0))
    monkeypatch.setattr(cb, bound, size - 1)
    with pytest.raises(cb.SourceEvidenceError) as caught:
        cb.verify_bundle(CountingTransport(files), expected_source_id=SOURCE, entry=entry, item_window=(0, 0))
    text = str(caught.value)
    assert path in text and f"{size} bytes" in text and f"bound of {size - 1} bytes" in text


def test_transport_that_ignores_max_bytes_is_still_bounded(monkeypatch):
    files, entry = make_market_day(40, with_raw=False)
    monkeypatch.setattr(cb, "MAX_ITEMS_JSONL_BYTES", 1000)
    with pytest.raises(cb.SourceEvidenceError, match=r"items\.jsonl is \d+ bytes, above its read bound of 1000"):
        cb.verify_bundle(FakeTransport(files), expected_source_id=SOURCE, entry=entry, item_window=(0, 0))


def test_http_declared_length_names_the_size_without_reading_the_body():
    class Body:
        headers = {"Content-Length": "70000000"}
        def read(self, n):  # pragma: no cover - must not be reached
            raise AssertionError("body must not be read when the declared size is above the bound")
    from services.source_ingestion.connectors.bounded_reader import PayloadTooLarge, read_bounded_response
    with pytest.raises(PayloadTooLarge) as caught:
        read_bounded_response(Body(), max_bytes=cb.MAX_ITEMS_JSONL_BYTES)
    assert caught.value.size == 70000000 and caught.value.size_is_exact and caught.value.limit == cb.MAX_ITEMS_JSONL_BYTES


def test_just_above_the_bound_at_twice_the_hosted_day_is_refused_with_readable_reason():
    """About 28000 rows fits; a day of 28000 rows at the ceiling row size plus one byte does not."""
    class Huge(FakeTransport):
        def read(self, relative_path, *, max_bytes):
            if relative_path.endswith("items.jsonl"):
                error = cb.PayloadTooLarge("Payload exceeded max byte limit")
                error.limit, error.size, error.size_is_exact = max_bytes, cb.MAX_ITEMS_JSONL_BYTES + 1, True
                raise error
            return super().read(relative_path, max_bytes=max_bytes)
    files, entry = make_market_day(10, with_raw=False)
    with pytest.raises(cb.SourceEvidenceError, match=rf"items\.jsonl is {cb.MAX_ITEMS_JSONL_BYTES + 1} bytes"):
        cb.verify_bundle(Huge(files), expected_source_id=SOURCE, entry=entry, item_window=(0, 0))


def test_twice_the_hosted_day_is_read_within_the_bounds():
    files, entry = make_market_day(2 * HOSTED_ROWS, with_raw=False)
    base = base_of(entry)
    assert len(files[f"{base}/items.jsonl"]) < cb.MAX_ITEMS_JSONL_BYTES
    assert len(files[f"{base}/manifest.json"]) < cb.MAX_MANIFEST_BYTES
    # the manifest alone is above the old 8 MiB at this size
    assert len(files[f"{base}/manifest.json"]) > 8 * 1024 * 1024
    bundle = cb.verify_bundle(CountingTransport(files), expected_source_id=SOURCE, entry=entry, item_window=(0, 0))
    assert len(bundle.items) == 2 * HOSTED_ROWS


@pytest.mark.parametrize("rows", [HOSTED_ROWS, 2 * HOSTED_ROWS])
def test_memory_of_reading_one_whole_day_is_within_the_stated_increase(rows):
    import gc
    import tracemalloc
    files, entry = make_market_day(rows, with_raw=False)
    transport = CountingTransport(files)
    gc.collect()
    tracemalloc.start()
    try:
        bundle = cb.verify_bundle(transport, expected_source_id=SOURCE, entry=entry, item_window=(0, 0))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert len(bundle.items) == rows
    # Stated bound: peak Python allocation while reading and parsing one day <= 6 KiB per row
    # (measured retained about 2.8 KiB per row; peak includes the file bytes held during the parse).
    assert peak <= rows * 6 * 1024, (peak, rows)


def test_truncated_and_altered_files_are_refused():
    files, entry = make_market_day(60, with_raw=False)
    base = base_of(entry)
    good_items = files[f"{base}/items.jsonl"]
    files[f"{base}/items.jsonl"] = good_items[: len(good_items) // 2]
    with pytest.raises(cb.SourceEvidenceError):
        cb.verify_bundle(FakeTransport(files), expected_source_id=SOURCE, entry=entry, item_window=(0, 0))
    files[f"{base}/items.jsonl"] = b"\n".join(good_items.split(b"\n")[:-1])  # a whole row dropped
    with pytest.raises(cb.SourceEvidenceError, match="do not match manifest.items"):
        cb.verify_bundle(FakeTransport(files), expected_source_id=SOURCE, entry=entry, item_window=(0, 0))
    files[f"{base}/items.jsonl"] = good_items
    manifest = json.loads(files[f"{base}/manifest.json"])
    manifest["items"][3]["content_sha256"] = "0" * 64  # altered manifest: bundle hash no longer agrees
    files[f"{base}/manifest.json"] = json.dumps(manifest).encode()
    with pytest.raises(cb.SourceEvidenceError, match="bundle_sha256 mismatch"):
        cb.verify_bundle(FakeTransport(files), expected_source_id=SOURCE, entry=entry, item_window=(0, 0))


def test_altered_or_truncated_raw_file_is_refused_for_ingested_item():
    files, entry = make_market_day(20)
    base = base_of(entry)
    manifest = json.loads(files[f"{base}/manifest.json"])
    victim = manifest["items"][5]
    files[f"{base}/{victim['raw_path']}"] = files[f"{base}/{victim['raw_path']}"][:-1]
    with pytest.raises(cb.SourceEvidenceError, match="raw hash mismatch"):
        cb.verify_bundle(FakeTransport(files), expected_source_id=SOURCE, entry=entry, item_window=(0, 20))


def deliver_day(root, transport, cap, *, limit_ticks=50):
    """Pull until the day is delivered, then pull once more to prove nothing is re-delivered.

    Returns (ticks, delivered, after_done_records). Stops on the first tick that returns no records once
    the priority day was reported done, or when no priority key remains (the product's state after the
    day). The tick limit is only a safety bound; reaching it fails the test.
    """
    a = adapter(root, transport, cap)
    ticks, delivered, done_seen = 0, 0, False
    while ticks < limit_ticks:
        ticks += 1
        result = a.pull()
        a.save_resume_cursor(result)
        delivered += len(result.records)
        done_seen = done_seen or bool(result.priority_done)
        if not result.records and (done_seen or result.priority_key is None):
            return ticks, delivered, len(result.records)
    raise AssertionError(f"day not delivered within {limit_ticks} ticks (delivered {delivered})")


def test_hosted_shape_day_is_delivered_over_ticks_with_linear_bytes(tmp_path):
    files, entry = make_market_day()
    base = base_of(entry)
    transport = CountingTransport(files)
    ticks, delivered, after_done = deliver_day(tmp_path, transport, cap=5000)
    assert delivered == HOSTED_ROWS and ticks >= 3
    assert after_done == 0  # the tick after the day is done re-delivers nothing
    metadata_size = len(files[f"{base}/items.jsonl"]) + len(files[f"{base}/manifest.json"])
    # Acceptance: bytes fetched for the day's metadata files <= twice their size, whatever the tick count.
    assert transport.metadata_bytes() <= 2 * metadata_size
    assert transport.metadata_bytes() == metadata_size  # one read, then the verified cache
    assert not (tmp_path / "state").exists() or not list((tmp_path / "state").rglob("*items.jsonl*"))


def test_cursor_format_is_unchanged_for_the_previous_release(tmp_path):
    files, entry = make_market_day(600, with_raw=True)
    a = adapter(tmp_path, CountingTransport(files), cap=250)
    result = a.pull()
    a.save_resume_cursor(result)
    cursor = a.load_resume_cursor()
    assert result.priority_key and (cursor is None or cb._PARTIAL_CURSOR_MARK in cursor or cursor == result.next_cursor)
    saved = json.loads(a._resume_cursor_path().read_text()) if a._resume_cursor_path().exists() else {}
    assert set(saved) <= {"source_id", "cursor"}
