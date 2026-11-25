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
    GdeltRawFeedPoller,
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


def test_gdelt_index_and_download_keep_five_second_interval(tmp_path, fixtures_dir, monkeypatch):
    import time
    slept = []
    monkeypatch.setattr(time, "sleep", slept.append)
    policy = _config().get_source("collector-gdelt")
    transport = FixtureTransport()
    feed = fixtures_dir / "gdelt_feed_recorded"
    transport.register("https://data.gdeltproject.org/gdeltv2/lastupdate.txt",
                       (feed / "masterfilelist-excerpt.txt").read_bytes())
    transport.register("https://data.gdeltproject.org/gdeltv2/20261113110000.export.CSV.zip",
                       (feed / "20261113110000.export.CSV.zip").read_bytes())
    poller = GdeltRawFeedPoller(policy, transport=transport, cache_dir=tmp_path)
    assert len(poller.collect()) == 3
    assert slept == [5.0]
    assert all(call["fetcher_kind"] == "http" for call in transport.calls)


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


def test_cna_finance_and_securities_recorded_feeds_parse_and_exclude_aall(
    fixtures_dir: Path, tmp_path: Path, test_hasher
) -> None:
    from collector_stack.bundle import verify_bundle_directory
    from collector_stack.pollers import RssHubNewsPoller

    cna_aie_xml = (fixtures_dir / "rsshub_cna.xml").read_bytes()
    cna_aie_text = cna_aie_xml.decode("utf-8")
    assert "<title>產經 | 中央社 CNA</title>" in cna_aie_text
    assert "<link>https://www.cna.com.tw/list/aie.aspx</link>" in cna_aie_text
    assert "/cna/aall" not in cna_aie_text
    assert "list/aall.aspx" not in cna_aie_text

    cna_asc_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss xmlns:atom="http://www.w3.org/2005/Atom" xmlns:dc="http://purl.org/dc/elements/1.1/" version="2.0">'
        "<channel>"
        "<title>證券 | 中央社 CNA</title>"
        "<link>https://www.cna.com.tw/list/asc.aspx</link>"
        '<atom:link href="http://rsshub:1200/cna/asc" rel="self" type="application/rss+xml"></atom:link>'
        "<description>最新股市動態、國內外債市、債券、基金、期貨等資訊，都在中央社證券新聞。 - Powered by RSSHub</description>"
        "<generator>RSSHub</generator>"
        "<lastBuildDate>Wed, 25 Nov 2026 07:01:54 GMT</lastBuildDate>"
        "<item>"
        "<title>建通預估明年獲利續成長 銅價成最大變數</title>"
        "<description>&lt;p&gt;（中央社記者曾仁凱台北25日電）建通精密（2460）今天舉行法人說明會，總經理林育民表示，隨著公司新型銅材業務持續擴張，明年相關營收有機會超越老本行端子事業。&lt;/p&gt;</description>"
        "<link>https://www.cna.com.tw/news/afe/202611250156.aspx</link>"
        '<guid isPermaLink="false">https://www.cna.com.tw/news/afe/202611250156.aspx</guid>'
        "<pubDate>Wed, 25 Nov 2026 06:37:00 GMT</pubDate>"
        "<dc:creator>曾仁凱</dc:creator>"
        "<category>建通精密</category>"
        "<category>證券</category>"
        "</item>"
        "</channel>"
        "</rss>"
    ).encode("utf-8")

    policy = _config().get_source("collector-news-rss")
    cna_routes = tuple(r for r in policy.raw_config["routes"] if r.startswith("/cna/"))
    assert cna_routes == ("/cna/aie", "/cna/asc")

    transport = FixtureTransport(
        {
            "http://rsshub:1200/cna/aie": cna_aie_xml,
            "http://rsshub:1200/cna/asc": cna_asc_xml,
        }
    )
    sleeps: list[float] = []
    poller = RssHubNewsPoller(policy, transport=transport)
    poller.fetcher.sleep_fn = sleeps.append
    items = poller.collect(routes=cna_routes, fetched_at="2026-11-25T07:05:00Z")
    assert len(items) == 2
    assert sleeps == [1.0]
    assert [c["url"] for c in transport.calls] == [
        "http://rsshub:1200/cna/aie",
        "http://rsshub:1200/cna/asc",
    ]
    by_route = {i.metrics["route"]: i for i in items}
    assert set(by_route) == {"/cna/aie", "/cna/asc"}
    assert by_route["/cna/aie"].item_id == "news-cna-202611250147-aspx"
    assert by_route["/cna/aie"].published_at == "2026-11-25T06:08:00Z"
    assert "1519" in by_route["/cna/aie"].extra_fields["symbols"]
    assert by_route["/cna/asc"].item_id == "news-cna-202611250156-aspx"
    assert by_route["/cna/asc"].published_at == "2026-11-25T06:37:00Z"
    assert "2460" in by_route["/cna/asc"].extra_fields["symbols"]

    runner = CollectorRunner(_config(), root_dir=tmp_path, hasher=test_hasher, transport=transport)
    outcome = runner.run_source(
        "collector-news-rss",
        run_id="cna-finance-securities",
        started_at="2026-11-25T07:05:00Z",
        finished_at="2026-11-25T07:05:05Z",
        collect_kwargs={"routes": cna_routes},
    )
    assert outcome.status == "complete"
    manifest = verify_bundle_directory(outcome.bundle.bundle_dir)
    assert len(manifest["items"]) == 2


def test_news_rss_scheduled_run_deduplicates_articles_across_routes_and_respects_request_limit(
    fixtures_dir: Path, tmp_path: Path, test_hasher
) -> None:
    from collector_stack.bundle import verify_bundle_directory

    config = _config()
    policy = config.get_source("collector-news-rss")
    assert policy.raw_config["routes"] == [
        "/cna/aie",
        "/cna/asc",
        "/udn/news/breakingnews/6",
        "/udn/news/breakingnews/11",
    ]

    # /cna/aie and /cna/asc both serve 202611250147.aspx; /cna/asc also serves 202611250156.aspx.
    # /udn/news/breakingnews/6 and /11 both serve story 8381234; /11 also serves story 8381299.
    cna_aie_xml = (fixtures_dir / "rsshub_cna.xml").read_bytes()
    cna_asc_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel><title>證券 | 中央社 CNA</title>'
        "<item><title>華城今年業績看創新高</title>"
        "<description>華城（1519）受惠電力設備需求。</description>"
        "<link>https://www.cna.com.tw/news/afe/202611250147.aspx</link>"
        "<guid>https://www.cna.com.tw/news/afe/202611250147.aspx</guid>"
        "<pubDate>Wed, 25 Nov 2026 06:08:00 GMT</pubDate></item>"
        "<item><title>建通預估明年獲利續成長 銅價成最大變數</title>"
        "<description>建通精密（2460）今日舉行法說會。</description>"
        "<link>https://www.cna.com.tw/news/afe/202611250156.aspx</link>"
        "<guid>https://www.cna.com.tw/news/afe/202611250156.aspx</guid>"
        "<pubDate>Wed, 25 Nov 2026 06:37:00 GMT</pubDate></item>"
        "</channel></rss>"
    ).encode("utf-8")
    udn_6_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel><title>即時新聞 - 財經 - 聯合新聞網</title>'
        "<item><title>台積電2330先進封裝擴產</title>"
        "<description>台積電（2330）CoWoS產能持續擴充。</description>"
        "<link>https://udn.com/news/story/7240/8381234</link>"
        "<guid>https://udn.com/news/story/7240/8381234</guid>"
        "<pubDate>Wed, 25 Nov 2026 06:40:00 GMT</pubDate></item>"
        "</channel></rss>"
    ).encode("utf-8")
    udn_11_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel><title>即時新聞 - 股市 - 聯合新聞網</title>'
        "<item><title>台積電2330先進封裝擴產</title>"
        "<description>台積電（2330）CoWoS產能持續擴充。</description>"
        "<link>https://udn.com/news/story/7240/8381234</link>"
        "<guid>https://udn.com/news/story/7240/8381234</guid>"
        "<pubDate>Wed, 25 Nov 2026 06:40:00 GMT</pubDate></item>"
        "<item><title>鴻海2317伺服器出貨增溫</title>"
        "<description>鴻海（2317）AI伺服器第四季動能強勁。</description>"
        "<link>https://udn.com/news/story/7251/8381299</link>"
        "<guid>https://udn.com/news/story/7251/8381299</guid>"
        "<pubDate>Wed, 25 Nov 2026 06:50:00 GMT</pubDate></item>"
        "</channel></rss>"
    ).encode("utf-8")

    transport = FixtureTransport(
        {
            "http://rsshub:1200/cna/aie": cna_aie_xml,
            "http://rsshub:1200/cna/asc": cna_asc_xml,
            "http://rsshub:1200/udn/news/breakingnews/6": udn_6_xml,
            "http://rsshub:1200/udn/news/breakingnews/11": udn_11_xml,
        }
    )
    runner = CollectorRunner(config, root_dir=tmp_path, hasher=test_hasher, transport=transport)
    outcome = runner.run_source(
        "collector-news-rss",
        run_id="four-routes-dedupe",
        started_at="2026-11-25T07:15:00Z",
        finished_at="2026-11-25T07:15:08Z",
    )
    assert outcome.status == "complete"
    assert len(transport.calls) == 4 <= policy.max_requests_per_run == 10
    assert [c["url"] for c in transport.calls] == [
        "http://rsshub:1200/cna/aie",
        "http://rsshub:1200/cna/asc",
        "http://rsshub:1200/udn/news/breakingnews/6",
        "http://rsshub:1200/udn/news/breakingnews/11",
    ]
    manifest = verify_bundle_directory(outcome.bundle.bundle_dir)
    assert "route_errors" not in manifest
    assert [m["item_id"] for m in manifest["items"]] == [
        "news-cna-202611250147-aspx",
        "news-cna-202611250156-aspx",
        "news-udn-8381234",
        "news-udn-8381299",
    ]


def test_news_rss_per_route_http_error_or_invalid_xml_is_isolated_while_all_routes_failed_is_partial(
    fixtures_dir: Path, tmp_path: Path, test_hasher
) -> None:
    from collector_stack.bundle import verify_bundle_directory
    from collector_stack.fetchers import FetchResponse

    config = _config()
    cna_aie_xml = (fixtures_dir / "rsshub_cna.xml").read_bytes()
    udn_11_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel><title>UDN 股市</title>'
        "<item><title>鴻海2317伺服器出貨增溫</title>"
        "<description>鴻海（2317）AI伺服器第四季動能強勁。</description>"
        "<link>https://udn.com/news/story/7251/8381299</link>"
        "<guid>https://udn.com/news/story/7251/8381299</guid>"
        "<pubDate>Wed, 25 Nov 2026 06:50:00 GMT</pubDate></item>"
        "</channel></rss>"
    ).encode("utf-8")

    # 1) Partial route failures: /cna/asc returns HTTP 503, /udn/news/breakingnews/6 returns invalid XML,
    #    while /cna/aie and /udn/news/breakingnews/11 succeed with HTTP 200 valid RSS.
    partial_transport = FixtureTransport(
        {
            "http://rsshub:1200/cna/aie": cna_aie_xml,
            "http://rsshub:1200/cna/asc": FetchResponse(
                requested_url="http://rsshub:1200/cna/asc",
                final_url="http://rsshub:1200/cna/asc",
                http_status=503,
                body=b"RSSHub upstream 503 error",
            ),
            "http://rsshub:1200/udn/news/breakingnews/6": FetchResponse(
                requested_url="http://rsshub:1200/udn/news/breakingnews/6",
                final_url="http://rsshub:1200/udn/news/breakingnews/6",
                http_status=200,
                body=b"<rss><channel><item><title>unclosed",
            ),
            "http://rsshub:1200/udn/news/breakingnews/11": udn_11_xml,
        }
    )
    runner = CollectorRunner(
        config, root_dir=tmp_path / "isolated", hasher=test_hasher, transport=partial_transport
    )
    outcome = runner.run_source("collector-news-rss", run_id="isolated-errors")
    assert outcome.status == "complete"
    assert outcome.state.consecutive_blocks == 0
    assert outcome.state.paused is False
    manifest = verify_bundle_directory(outcome.bundle.bundle_dir)
    assert [m["item_id"] for m in manifest["items"]] == [
        "news-cna-202611250147-aspx",
        "news-udn-8381299",
    ]
    assert manifest["route_errors"]["/cna/asc"] == "http_503"
    assert manifest["route_errors"]["/udn/news/breakingnews/6"].startswith("invalid_xml:")
    status_doc = json.loads((tmp_path / "isolated" / "status.json").read_text(encoding="utf-8"))
    assert status_doc["sources"]["collector-news-rss"]["route_errors"] == manifest["route_errors"]

    # 2) Every route failed (HTTP 503/404 and invalid XML): run is a failure (status="partial", reason="fetch_failed").
    all_fail_transport = FixtureTransport(
        {
            "http://rsshub:1200/cna/aie": FetchResponse(
                requested_url="http://rsshub:1200/cna/aie",
                final_url="http://rsshub:1200/cna/aie",
                http_status=503,
                body=b"503 Service Unavailable",
            ),
            "http://rsshub:1200/cna/asc": FetchResponse(
                requested_url="http://rsshub:1200/cna/asc",
                final_url="http://rsshub:1200/cna/asc",
                http_status=404,
                body=b"404 Not Found",
            ),
            "http://rsshub:1200/udn/news/breakingnews/6": FetchResponse(
                requested_url="http://rsshub:1200/udn/news/breakingnews/6",
                final_url="http://rsshub:1200/udn/news/breakingnews/6",
                http_status=200,
                body=b"not xml at all <<<",
            ),
            "http://rsshub:1200/udn/news/breakingnews/11": FetchResponse(
                requested_url="http://rsshub:1200/udn/news/breakingnews/11",
                final_url="http://rsshub:1200/udn/news/breakingnews/11",
                http_status=200,
                body=b"<html><body>error page</body></html>",
            ),
        }
    )
    fail_runner = CollectorRunner(
        config, root_dir=tmp_path / "all-failed", hasher=test_hasher, transport=all_fail_transport
    )
    fail_outcome = fail_runner.run_source("collector-news-rss", run_id="all-routes-failed")
    assert fail_outcome.status == "partial"
    assert fail_outcome.state.last_status == "partial"
    fail_manifest = verify_bundle_directory(fail_outcome.bundle.bundle_dir)
    assert fail_manifest["status"] == "partial"
    assert fail_manifest["reason"] == "fetch_failed"
    assert fail_manifest["items"] == []
    assert set(fail_manifest["route_errors"]) == {
        "/cna/aie",
        "/cna/asc",
        "/udn/news/breakingnews/6",
        "/udn/news/breakingnews/11",
    }
    assert fail_manifest["route_errors"]["/cna/aie"] == "http_503"
    assert fail_manifest["route_errors"]["/cna/asc"] == "http_404"
    assert fail_manifest["route_errors"]["/udn/news/breakingnews/6"].startswith("invalid_xml:")
    assert fail_manifest["route_errors"]["/udn/news/breakingnews/11"] == "invalid_xml:unexpected_root_html"

