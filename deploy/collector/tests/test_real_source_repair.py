"""Regression tests for failures seen against the real sites on 2026-11-10 (minimized fixtures only)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from collector_stack.config import DEFAULT_POLICY_PATH, CollectorConfigError, load_collector_config, validate_collector_config
from collector_stack.fetchers import FixtureTransport
from collector_stack.pollers import (
    DEFAULT_GEMINI_MODEL,
    CbcMacroPoller,
    GdeltDocPoller,
    gemini_endpoint_url,
)
from collector_stack.runner import CollectorRunner
from collector_stack.spiders import (
    InstagramPopularTagSpider,
    PttStockSpider,
    TwseT86DailySpider,
    recent_weekday_dates,
)

COLLECTOR_DIR = Path(__file__).resolve().parent.parent


def _config():
    return load_collector_config(DEFAULT_POLICY_PATH, env={})


def test_cbc_parses_json_served_as_text_plain_without_hardcoded_date(fixtures_dir: Path) -> None:
    policy = _config().get_source("collector-tw-macro-cbc")
    transport = FixtureTransport()
    transport.register(
        "https://cpx.cbc.gov.tw/API/DataAPI/Get",
        (fixtures_dir / "cbc_bp01d01.json").read_bytes(),
        headers={"content-type": "text/plain; charset=utf-8"},
    )
    poller = CbcMacroPoller(policy, transport=transport)
    items = poller.collect(series_codes=("BP01D01",), fetched_at="2031-01-02T00:00:00Z")
    assert items
    periods = {i.metrics["period"] for i in items}
    assert periods and all(p.startswith("20") for p in periods)
    assert "2026-11-10" not in periods  # period comes from the data, never a default
    assert any("新台幣" in i.text for i in items)


def test_ptt_item_without_author_is_skipped_and_counted(fixtures_dir: Path, test_hasher) -> None:
    policy = _config().get_source("collector-tw-ptt-stock")
    transport = FixtureTransport()
    transport.register("https://www.ptt.cc/bbs/Stock/index.html", (fixtures_dir / "ptt_stock_index.html").read_bytes())
    spider = PttStockSpider(policy, transport=transport, hasher=test_hasher)
    items = spider.collect(fetched_at="2026-11-10T09:00:00Z")
    assert len(items) == 3
    assert spider.skipped_count == 1


def test_instagram_skips_incomplete_node_and_percent_encodes_tag(fixtures_dir: Path, test_hasher) -> None:
    policy = _config().get_source("collector-tw-instagram-popular")
    transport = FixtureTransport()
    transport.register(
        "https://www.instagram.com/explore/tags/",
        (fixtures_dir / "instagram_popular_tag.html").read_bytes(),
        final_url="https://www.instagram.com/explore/tags/%E5%8F%B0%E8%82%A1/",
    )
    spider = InstagramPopularTagSpider(policy, transport=transport, hasher=test_hasher)
    items = spider.collect(tag="台股", fetched_at="2026-11-10T09:00:00Z")
    assert len(items) == 2 and spider.skipped_count == 1
    assert transport.calls[0]["url"] == "https://www.instagram.com/explore/tags/%E5%8F%B0%E8%82%A1/"
    assert all(ord(ch) < 128 for call in transport.calls for ch in call["url"])


def test_gdelt_waits_five_seconds_between_requests_and_query_is_encoded() -> None:
    raw = json.loads(DEFAULT_POLICY_PATH.read_text())
    raw["sources"]["collector-gdelt"]["query_terms"] = ["Taiwan stock", "台股"]
    policy = validate_collector_config(raw, env={}).get_source("collector-gdelt")
    assert policy.min_interval_seconds >= 5.0
    transport = FixtureTransport()
    transport.register("https://api.gdeltproject.org/api/v2/doc/doc", b'{"articles": []}')
    slept: list[float] = []
    poller = GdeltDocPoller(policy, transport=transport)
    poller.fetcher.sleep_fn = slept.append
    poller.collect(fetched_at="2026-11-10T00:00:00Z")
    poller.collect(fetched_at="2026-11-10T00:00:00Z")
    assert slept == [policy.min_interval_seconds]
    assert all(ord(ch) < 128 for call in transport.calls for ch in call["url"])


def test_gdelt_plain_text_rate_limit_body_does_not_crash() -> None:
    policy = _config().get_source("collector-gdelt")
    transport = FixtureTransport()
    transport.register(
        "https://api.gdeltproject.org/api/v2/doc/doc", b"Please limit requests to one every 5 seconds"
    )
    poller = GdeltDocPoller(policy, transport=transport)
    assert poller.collect(fetched_at="2026-11-10T00:00:00Z") == []
    assert poller.skipped_count == 1


def test_default_fetcher_sleep_really_sleeps(monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    calls: list[float] = []
    monkeypatch.setattr(time, "sleep", calls.append)
    policy = _config().get_source("collector-gdelt")
    transport = FixtureTransport()
    transport.register("https://api.gdeltproject.org/api/v2/doc/doc", b'{"articles": []}')
    poller = GdeltDocPoller(policy, transport=transport)
    poller.collect(fetched_at="2026-11-10T00:00:00Z")
    poller.collect(fetched_at="2026-11-10T00:00:00Z")
    assert calls == [5.0]


def test_dgbas_is_disabled_with_recorded_reason() -> None:
    policy = _config().get_source("collector-tw-macro-dgbas")
    assert policy.enabled is False
    assert "Cloudflare" in policy.raw_config["disabled_reason"]


def test_dbnomics_policy_series_are_listed_and_encoded_documented_form() -> None:
    policy = _config().get_source("collector-macro-dbnomics")
    assert policy.raw_config["series_ids"] == ["FED/H15/RIFSPFF_N.D", "NBS/M_A010101/A01010101"]
    assert set(policy.raw_config["dropped_series_ids"]) >= {"BOJ/FM01/STRDCLUCON"}


def test_gemini_model_is_configurable_with_listed_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COLLECTOR_GEMINI_MODEL", raising=False)
    assert gemini_endpoint_url().endswith(f"/models/{DEFAULT_GEMINI_MODEL}:generateContent")
    assert DEFAULT_GEMINI_MODEL == "gemini-2.5-flash"
    monkeypatch.setenv("COLLECTOR_GEMINI_MODEL", "gemini-custom-1")
    assert "/models/gemini-custom-1:" in gemini_endpoint_url()
    assert "gemini-custom-1" in gemini_endpoint_url("gemini-custom-1")
    with pytest.raises(CollectorConfigError):
        gemini_endpoint_url("bad/model?x=1")


def test_no_hardcoded_calendar_dates_in_collection_code() -> None:
    forbidden = re.compile(r"""["'](?:2026[-/]?\d{2}[-/]?\d{2}|2026-\d{2})["']""")
    for path in (COLLECTOR_DIR / "collector_stack").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not forbidden.search(text), f"hard-coded date in {path.name}"


def test_recent_weekday_dates_skips_weekends() -> None:
    assert recent_weekday_dates("2026-11-09T01:00:00Z", count=2) == ["2026-11-09", "2026-11-06"]


def test_t86_default_date_comes_from_run_time(fixtures_dir: Path) -> None:
    policy = _config().get_source("collector-tw-twse-t86")
    transport = FixtureTransport()
    transport.register("https://www.twse.com.tw/rwd/zh/fund/T86", (fixtures_dir / "twse_t86_20261110.json").read_bytes())
    spider = TwseT86DailySpider(policy, transport=transport)
    spider.fetcher.sleep_fn = lambda _s: None
    spider.collect(fetched_at="2027-03-17T09:00:00Z")
    assert "date=20270317" in transport.calls[0]["url"]


def test_status_and_bundles_land_in_one_data_root_served_by_bundle_server(
    tmp_path: Path, fixtures_dir: Path, test_hasher
) -> None:
    transport = FixtureTransport()
    transport.register("https://www.ptt.cc/bbs/Stock/index.html", (fixtures_dir / "ptt_stock_index.html").read_bytes())
    runner = CollectorRunner(_config(), root_dir=tmp_path, hasher=test_hasher, transport=transport)
    outcome = runner.run_source("collector-tw-ptt-stock", run_id="r1")
    assert outcome.skipped_count == 1
    status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert status["sources"]["collector-tw-ptt-stock"]["skipped_items"] == 1
    assert (tmp_path / "bundles" / "status.json").is_file()


def test_compose_mounts_data_root_whole_and_lock_is_cpu_only() -> None:
    compose = (COLLECTOR_DIR / "docker-compose.yml").read_text(encoding="utf-8")
    assert re.search(r"-\s+\$\{COLLECTOR_DATA_ROOT:-[^}]+\}:/data\s*$", compose, re.M)
    assert "/data/bundles" not in compose
    lock = "\n".join(
        l for l in (COLLECTOR_DIR / "requirements.lock").read_text(encoding="utf-8").lower().splitlines()
        if not l.lstrip().startswith("#")
    )
    assert "torch==" in lock and "+cpu" in lock
    for banned in ("nvidia", "triton", "cuda"):
        assert banned not in lock
    for line in lock.splitlines():
        if line and not line.startswith(("#", " ")):
            assert "==" in line, f"unpinned lock line: {line}"
    dockerfile = (COLLECTOR_DIR / "Dockerfile").read_text(encoding="utf-8")
    assert "requirements.lock" in dockerfile and "--no-deps" in dockerfile and "whl/cpu" in dockerfile


def test_ptt_pinned_posts_listed_twice_are_deduplicated(fixtures_dir: Path, test_hasher, tmp_path: Path) -> None:
    page = (fixtures_dir / "ptt_stock_index.html").read_text(encoding="utf-8")
    transport = FixtureTransport()
    transport.register("https://www.ptt.cc/bbs/Stock/index.html", page + page)  # every entry twice
    runner = CollectorRunner(_config(), root_dir=tmp_path, hasher=test_hasher, transport=transport)
    outcome = runner.run_source("collector-tw-ptt-stock", run_id="dup")
    assert outcome.status == "complete"
    assert len(outcome.bundle.manifest["items"]) == 3


@pytest.mark.parametrize(
    ("now_utc", "expected"),
    [
        ("2026-11-11T02:50:00Z", "2026-11-10"),  # 10:50 Taiwan, before 15:00 -> previous trading day
        ("2026-11-11T07:00:00Z", "2026-11-11"),  # 15:00 Taiwan -> today
        ("2026-11-09T03:00:00Z", "2026-11-06"),  # Monday morning -> Friday
        ("2026-11-14T08:00:00Z", "2026-11-13"),  # Saturday -> Friday
    ],
)
def test_t86_targets_latest_published_trading_day(now_utc: str, expected: str) -> None:
    from collector_stack.spiders import latest_published_trading_date

    assert latest_published_trading_date(now_utc) == expected


class _RecordingTransport(FixtureTransport):
    def request(self, url, **kwargs):  # type: ignore[override]
        from collector_stack.fetchers import FetchResponse

        self.calls.append({"url": url})
        return FetchResponse(requested_url=url, final_url=url, http_status=200, body=b"{}")


_NETWORK_SOURCES = [
    "collector-tw-threads",
    "collector-tw-cmoney-forum",
    "collector-tw-instagram-popular",
    "collector-tw-twse-t86",
    "collector-tw-macro-ndc",
    "collector-tw-ptt-stock",
    "collector-tw-macro-cbc",
    "collector-gdelt",
    "collector-macro-dbnomics",
    "collector-paper-arxiv",
    "collector-paper-openalex",
    "collector-paper-nber-rss",
    "collector-news-rss",
]


@pytest.mark.parametrize("source_id", _NETWORK_SOURCES)
def test_cli_run_path_issues_at_least_one_request(
    source_id: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from collector_stack import cli
    from collector_stack import runner as runner_module

    transport = _RecordingTransport()
    monkeypatch.setattr(runner_module, "LiveScraplingTransport", lambda: transport)
    salt = tmp_path / "salt"
    salt.write_bytes(b"cli-path-test-salt-0123456789abcdef-0123456789")
    try:
        cli.main(["--root-dir", str(tmp_path), "--salt-file", str(salt), "run", "--source", source_id])
    except Exception:  # an empty fake body may be unparseable; the assertion is about requests
        pass
    assert len(transport.calls) >= 1, f"{source_id} issued no request through the CLI runner path"


def test_zero_items_is_not_complete_and_records_reason(tmp_path: Path, test_hasher) -> None:
    transport = FixtureTransport()
    transport.register("https://www.ptt.cc/bbs/Stock/index.html", "<html></html>")
    runner = CollectorRunner(_config(), root_dir=tmp_path, hasher=test_hasher, transport=transport)
    outcome = runner.run_source("collector-tw-ptt-stock", run_id="empty")
    assert outcome.status == "partial"
    assert outcome.bundle.manifest["reason"] == "parse_zero"
    status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert status["sources"]["collector-tw-ptt-stock"]["reason"] == "parse_zero"


def test_zero_requests_is_not_complete_and_records_no_targets(tmp_path: Path, test_hasher) -> None:
    runner = CollectorRunner(_config(), root_dir=tmp_path, hasher=test_hasher, transport=FixtureTransport())
    outcome = runner.run_source("collector-tw-twse-t86", run_id="none", collect_kwargs={"urls": []})
    assert outcome.status == "partial"
    assert outcome.bundle.manifest["reason"] == "no_targets"


def test_http_transport_passes_json_and_rss_through_byte_identical(monkeypatch) -> None:
    pytest.importorskip("httpx")
    import sys
    import types

    from collector_stack.fetchers import LiveScraplingTransport

    payloads = {
        "/j": (b'{"a": [1, 2], "t": "\xe4\xb8\xad"}', "text/plain"),
        "/r": (b'<?xml version="1.0"?><rss version="2.0"><channel><item><title>x</title></item></channel></rss>', "application/xml"),
    }

    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        body, ctype = payloads[request.url.path]
        return httpx.Response(200, content=body, headers={"content-type": ctype})

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    stub = types.ModuleType("scrapling.fetchers")
    stub.Fetcher = object
    monkeypatch.setitem(sys.modules, "scrapling", types.ModuleType("scrapling"))
    monkeypatch.setitem(sys.modules, "scrapling.fetchers", stub)
    for path, (body, _) in payloads.items():
        resp = LiveScraplingTransport().request(f"http://example.test{path}", fetcher_kind="http")
        assert resp.body == body


def test_browser_fetch_waits_for_selector_not_network_idle() -> None:
    src = (COLLECTOR_DIR / "collector_stack" / "fetchers.py").read_text(encoding="utf-8")
    assert "network_idle=False" in src and "network_idle=True" not in src
    assert 'wait_selector' in src


def test_parse_error_writes_partial_bundle_with_parse_failed_and_url(tmp_path: Path, test_hasher) -> None:
    transport = FixtureTransport()
    transport.register("https://www.ptt.cc/bbs/Stock/index.html", "<html></html>")
    runner = CollectorRunner(_config(), root_dir=tmp_path, hasher=test_hasher, transport=transport)

    def boom(self, **kw):
        self.fetcher.fetch("https://www.ptt.cc/bbs/Stock/index.html")
        raise ValueError("mismatched tag")

    import collector_stack.runner as r
    from collector_stack.spiders import PttStockSpider as P

    orig = P.collect
    P.collect = boom
    try:
        outcome = runner.run_source("collector-tw-ptt-stock", run_id="pf")
    finally:
        P.collect = orig
    assert outcome.status == "partial"
    assert outcome.bundle.manifest["reason"] == "parse_failed"
    assert outcome.bundle.manifest["source_url"] == "https://www.ptt.cc/bbs/Stock/index.html"
    status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert status["sources"]["collector-tw-ptt-stock"]["reason"] == "parse_failed"
