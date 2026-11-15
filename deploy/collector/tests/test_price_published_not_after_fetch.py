"""Price observation bounds and additive compatibility with hosted readers.

Incident counts/timestamps are recorded; exchange bodies use the checked-in
fixtures, expanded to the recorded shape rather than a claimed live download.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from collector_stack.bundle import BundleWriter, utc_iso
from collector_stack.config import DEFAULT_POLICY_PATH, load_collector_config
from collector_stack.fetchers import FetchResponse, FixtureTransport
from collector_stack.spiders import TpexDailyPriceHistorySpider, TwseDailyPriceHistorySpider


@pytest.fixture(params=[TwseDailyPriceHistorySpider, TpexDailyPriceHistorySpider], ids=["twse", "tpex"])
def price_source(request, fixtures_dir):
    cls = request.param
    fixture = "twse_mi_index_20261110.json" if cls is TwseDailyPriceHistorySpider else "tpex_daily_quotes_20261110.json"
    base = json.loads((fixtures_dir / fixture).read_text())
    policy = load_collector_config(DEFAULT_POLICY_PATH, env={}).get_source(cls.source_id)

    def collect(fetched, counts=(("2026-11-10", 2), ("2026-11-11", 2))):
        transport = FixtureTransport()
        for day, count in counts:
            payload = json.loads(json.dumps(base))
            payload["date"] = day
            table = payload["tables"][0]
            template = table["data"][0]
            table["data"] = [[str(1000 + i), *template[1:]] for i in range(count)]
            url = cls.build_date_url(day)
            transport.register(url, FetchResponse(requested_url=url, final_url=url, http_status=200,
                body=json.dumps(payload).encode(), headers={}))
        spider = cls(policy, transport=transport, sleep_fn=lambda _: None)
        return spider.collect(dates=[day for day, _ in counts], fetched_at=fetched)

    return policy, collect


def instant(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


@pytest.mark.parametrize("fetched,expected", [
    ("2026-11-11T06:16:05Z", "2026-11-11T06:16:05Z"),
    ("2026-11-11T06:40:00Z", "2026-11-11T06:30:00Z"),
    ("2026-11-11T06:30:00Z", "2026-11-11T06:30:00Z"),
    ("2026-11-11T14:16:05+08:00", "2026-11-11T14:16:05+08:00"),
    ("2026-11-10T23:40:00-07:00", "2026-11-11T06:30:00Z"),
    ("2026-11-11T06:29:59.999999Z", "2026-11-11T06:29:59.999999Z"),
    ("2026-11-12T06:16:05Z", "2026-11-11T06:30:00Z"),
    (datetime(2026, 11, 11, 14, 16, 5, tzinfo=timezone(timedelta(hours=8))), "2026-11-11T06:16:05Z"),
    (datetime(2026, 11, 11, 6, 16, 5), "2026-11-11T06:16:05Z"),
])
def test_price_publication_bound_preserves_history(price_source, fetched, expected):
    _, collect = price_source
    items = collect(fetched)
    assert len(items) == 4
    stamp = fetched if isinstance(fetched, str) else utc_iso(fetched)
    for item in items:
        row = item.to_jsonl_dict()
        assert row["fetched_at"] == stamp
        assert instant(row["published_at"]) <= instant(stamp)
        assert row["published_at"] == (expected if row["trade_date"] == "2026-11-11"
                                        else "2026-11-10T06:30:00Z")
        assert row["dataset"] == "tw_price_daily"
        assert json.loads(item.raw_bytes)["record"]["trade_date"] == row["trade_date"]


def test_recorded_two_date_2751_row_shape(price_source):
    _, collect = price_source
    items = collect("2026-11-11T06:16:05Z", (("2026-11-10", 1375), ("2026-11-11", 1376)))
    assert len(items) == 2751
    assert sum(item.published_at == "2026-11-11T06:16:05Z" for item in items) == 1376
    assert sum(item.published_at == "2026-11-10T06:30:00Z" for item in items) == 1375
    assert all(instant(item.published_at) <= instant(item.fetched_at) for item in items)


@pytest.mark.parametrize("revision", ["bee62bc29", "15bee6816", "current"])
@pytest.mark.parametrize("fetched", ["2026-11-11T06:16:05Z", "2026-11-11T06:40:00Z"])
def test_new_bundles_are_admitted_by_hosted_readers(price_source, tmp_path, revision, fetched):
    from services.source_ingestion.connectors import collector_bundle as current

    reader = current
    module_name = None
    if revision != "current":
        code = subprocess.run(["git", "show", f"{revision}:services/source_ingestion/connectors/collector_bundle.py"],
            capture_output=True, text=True, check=True, timeout=10).stdout
        path = tmp_path / f"reader_{revision}.py"
        path.write_text(code)
        module_name = f"services.source_ingestion.connectors.price_bound_{revision}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        reader = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = reader
        spec.loader.exec_module(reader)

    try:
        policy, collect = price_source
        items = collect(fetched)
        root = tmp_path / "producer"
        BundleWriter(root).write_bundle(policy=policy, run_id="recorded-shape", items=items,
            started_at=fetched, finished_at=fetched)
        files = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}

        class Transport(reader.BundleTransport):
            def read(self, relative_path, *, max_bytes):
                return files[relative_path]

        adapter = reader.CollectorBundleAdapter(connector_id=policy.source_id, source_id=policy.source_id,
            source_type="market", dataset="tw_price_daily", cadence_seconds=86400, max_records=100,
            transport=Transport(), raw_root=str(tmp_path / "consumer"), attribution_required=True)
        result = adapter.pull()
        assert len(result.records) == 4
        expected = {item.item_id: item for item in items}
        for record in result.records:
            # Reader validation runs inside pull; also assert exact PIT mapping.
            row = next(item for key, item in expected.items() if f":{key}:" in record.source_id)
            assert record.metadata["event_time"] == row.published_at
            assert record.metadata["available_time"] == row.fetched_at
            assert record.metadata["trade_date"] == row.extra_fields["trade_date"]
        assert len(list((tmp_path / "consumer").rglob("*.json"))) >= 4
    finally:
        if module_name:
            sys.modules.pop(module_name, None)
