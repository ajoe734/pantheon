"""Offline fixture tests for every spider and poller in SD §5 except dropped TPEx flows (AC2)."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from collector_stack.bundle import verify_bundle_directory
from collector_stack.config import (
    DEFAULT_POLICY_PATH,
    CollectorConfigError,
    load_collector_config,
)
from collector_stack.fetchers import FixtureTransport
from collector_stack.pollers import (
    GdeltDocPoller,
    RssHubNewsPoller,
    YouTubeDataApiPoller,
)
from collector_stack.privacy import AuthorHasher
from collector_stack.runner import CollectorRunner
from collector_stack.spiders import (
    TPEX_DAILY_QUOTES_WEBSITE_URL_TEMPLATE,
    TWO_YEAR_BACKFILL_TRADING_DAYS,
    TWSE_MI_INDEX_WEBSITE_URL_TEMPLATE,
    TWSE_T86_WEBSITE_URL_TEMPLATE,
    TpexDailyPriceHistorySpider,
    TwseDailyPriceHistorySpider,
    TwseT86DailySpider,
    estimate_two_year_backfill_requests,
)


def _build_ndc_zip_bytes(csv_bytes: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.csv", b"name,schema,description\n")
        zf.writestr("景氣指標與燈號.csv", csv_bytes)
    return buf.getvalue()


def _build_fixture_transport(fixtures_dir: Path) -> FixtureTransport:
    ndc_zip = _build_ndc_zip_bytes((fixtures_dir / "ndc_6099.csv").read_bytes())
    transport = FixtureTransport()

    # §5.1 Macro and economic data
    transport.register(
        "https://cpx.cbc.gov.tw/API/DataAPI/Get",
        (fixtures_dir / "cbc_bp01d01.json").read_bytes(),
    )
    transport.register(
        "https://data.gov.tw/api/v2/rest/dataset/6099",
        (fixtures_dir / "ndc_dataset_6099.json").read_bytes(),
    )
    transport.register(
        "https://ws.ndc.gov.tw/Download.ashx",
        ndc_zip,
    )
    transport.register(
        "https://www.stat.gov.tw/Point.aspx",
        (fixtures_dir / "dgbas_macro.html").read_bytes(),
    )
    transport.register(
        "https://openapi.twse.com.tw/v1/opendata/t187ap05_L",
        (fixtures_dir / "twse_t187ap05_l.json").read_bytes(),
    )
    transport.register(
        "https://www.twse.com.tw/rwd/zh/fund/T86",
        (fixtures_dir / "twse_t86_20261110.json").read_bytes(),
    )
    transport.register(
        "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX",
        (fixtures_dir / "twse_mi_index_20261110.json").read_bytes(),
    )
    transport.register(
        "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes",
        (fixtures_dir / "tpex_daily_quotes_20261110.json").read_bytes(),
    )
    transport.register(
        "https://api.gdeltproject.org/api/v2/doc/doc",
        (fixtures_dir / "gdelt_doc.json").read_bytes(),
    )
    transport.register(
        "https://api.db.nomics.world/v22/series/",
        (fixtures_dir / "dbnomics_series.json").read_bytes(),
    )

    # §5.2 Papers and research
    transport.register(
        "https://export.arxiv.org/api/query",
        (fixtures_dir / "arxiv_qfin.xml").read_bytes(),
    )
    transport.register(
        "https://api.openalex.org/works",
        (fixtures_dir / "openalex_works.json").read_bytes(),
    )
    transport.register(
        "http://rsshub:1200/nber/papers",
        (fixtures_dir / "rsshub_nber.xml").read_bytes(),
    )

    # §5.3 Community and social
    transport.register(
        "https://www.threads.com/search",
        (fixtures_dir / "threads_search.html").read_bytes(),
    )
    transport.register(
        "https://www.instagram.com/explore/tags/",
        (fixtures_dir / "instagram_popular_tag.html").read_bytes(),
        final_url="https://www.instagram.com/popular/%E5%8F%B0%E8%82%A1/?utm_source=explore_tag",
    )
    transport.register(
        "https://www.cmoney.tw/forum/stock/",
        (fixtures_dir / "cmoney_forum_2330.html").read_bytes(),
    )
    transport.register(
        "https://www.ptt.cc/bbs/Stock/index.html",
        (fixtures_dir / "ptt_stock_index.html").read_bytes(),
    )
    transport.register(
        "https://rss.disp.cc/Stock.xml",
        (fixtures_dir / "ptt_mirror_disp_stock.xml").read_bytes(),
    )
    transport.register(
        "https://www.googleapis.com/youtube/v3/search",
        (fixtures_dir / "youtube_search.json").read_bytes(),
    )
    transport.register(
        "https://www.googleapis.com/youtube/v3/videos",
        (fixtures_dir / "youtube_videos.json").read_bytes(),
    )
    transport.register(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
        (fixtures_dir / "gemini_video_summary.json").read_bytes(),
    )

    # §5.5 News via RSSHub
    transport.register(
        "http://rsshub:1200/cna/aall",
        (fixtures_dir / "rsshub_cna.xml").read_bytes(),
    )
    return transport


@pytest.mark.parametrize(
    ("source_id", "min_expected_items", "require_attribution"),
    [
        # §5.1 Macro and economic data (TPEx flows dropped per Dev Plan §2)
        ("collector-tw-macro-cbc", 2, True),
        ("collector-tw-macro-ndc", 2, True),
        ("collector-tw-macro-dgbas", 3, True),
        ("collector-tw-twse-openapi", 2, True),
        ("collector-tw-twse-t86", 2, True),
        ("collector-tw-twse-price-history", 2, True),
        ("collector-tw-tpex-price-history", 2, True),
        ("collector-gdelt", 1, True),
        ("collector-macro-dbnomics", 2, True),
        # §5.2 Papers and research
        ("collector-paper-arxiv", 1, False),
        ("collector-paper-openalex", 1, False),
        ("collector-paper-nber-rss", 1, False),
        ("collector-papers", 3, False),
        # §5.3 Community and social
        ("collector-tw-threads", 2, False),
        ("collector-tw-instagram-popular", 2, False),
        ("collector-tw-cmoney-forum", 1, False),
        ("collector-tw-ptt-stock", 1, False),
        ("collector-tw-ptt-mirrors", 1, False),
        ("collector-youtube-finance", 1, False),
        ("collector-youtube-gemini-notes", 1, False),
        # §5.4 Books and curated knowledge
        ("collector-library-books", 2, False),
        # §5.5 News via RSSHub
        ("collector-news-rss", 1, False),
    ],
)
def test_every_section_5_source_runs_on_captured_fixture_with_no_network_call(
    tmp_path: Path,
    fixtures_dir: Path,
    test_hasher: AuthorHasher,
    source_id: str,
    min_expected_items: int,
    require_attribution: bool,
) -> None:
    config = load_collector_config(DEFAULT_POLICY_PATH, env={})
    library_dir = tmp_path / "library" / "books"
    library_dir.mkdir(parents=True, exist_ok=True)
    (library_dir / "quant_factors_handbook.pdf").write_bytes(
        (fixtures_dir / "sample_book.md").read_bytes()
    )

    transport = _build_fixture_transport(fixtures_dir)
    runner = CollectorRunner(
        config,
        root_dir=tmp_path,
        library_dir=library_dir,
        hasher=test_hasher,
        transport=transport,
    )

    outcome = runner.run_source(
        source_id,
        run_id=f"fixture-{source_id}",
        started_at="2026-11-10T09:00:00Z",
        finished_at="2026-11-10T09:00:10Z",
        allow_disabled=True,
        collect_kwargs=(
            {"video_id": "fixture_vid_001"} if source_id == "collector-youtube-gemini-notes" else None
        ),
    )
    assert outcome.status == "complete"
    manifest = verify_bundle_directory(outcome.bundle.bundle_dir)
    assert manifest["source_id"] == source_id
    assert len(manifest["items"]) >= min_expected_items
    if require_attribution:
        assert len(manifest["attribution"].strip()) > 0

    lines = [
        json.loads(line)
        for line in outcome.bundle.items_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) == len(manifest["items"])

    # Social sources must have 64-char HMAC hashed_author and no display names
    if source_id in {
        "collector-tw-threads",
        "collector-tw-instagram-popular",
        "collector-tw-cmoney-forum",
        "collector-tw-ptt-stock",
        "collector-tw-ptt-mirrors",
        "collector-youtube-finance",
    }:
        for row in lines:
            assert len(row["hashed_author"]) == 64

    # ArXiv poller must have filtered out the hep-th entry and kept only q-fin.PM/stat.ML
    if source_id == "collector-paper-arxiv":
        assert len(lines) == 1
        assert "2611.01234v1" in lines[0]["id"]
        assert lines[0]["metrics"]["redistribution_allowed"] is False

    # Book library poller must carry page_start / page_end and redistribution_allowed=False
    if source_id == "collector-library-books":
        assert lines[0]["metrics"]["page_start"] == 1
        assert lines[0]["metrics"]["page_end"] == 18
        assert lines[1]["metrics"]["page_start"] == 19
        assert lines[1]["metrics"]["page_end"] == 42
        assert lines[0]["metrics"]["redistribution_allowed"] is False


def test_source_specific_guardrails_gdelt_youtube_rsshub_and_books(
    tmp_path: Path, fixtures_dir: Path, test_hasher: AuthorHasher
) -> None:
    config = load_collector_config(DEFAULT_POLICY_PATH, env={})
    transport = _build_fixture_transport(fixtures_dir)

    # GDELT refuses > 250 records per call
    gdelt = GdeltDocPoller(config.get_source("collector-gdelt"), transport=transport)
    with pytest.raises(CollectorConfigError, match="250"):
        gdelt.collect(max_records=500)

    # YouTube refuses captions.download
    yt = YouTubeDataApiPoller(
        config.get_source("collector-youtube-finance"),
        transport=transport,
        hasher=test_hasher,
    )
    with pytest.raises(CollectorConfigError, match="captions.download"):
        yt.collect(attempt_captions_download=True)

    # RSSHub news refuses yahoo and cnyes/anue as DISABLED_BY_BUILD
    news = RssHubNewsPoller(config.get_source("collector-news-rss"), transport=transport)
    for bad_route in ("/yahoo/news", "/cnyes/news", "/anue/headline", "/ptt/Stock"):
        with pytest.raises(CollectorConfigError):
            news.collect(routes=[bad_route])


def test_exchange_website_spiders_rate_limit_single_connection_all_stock_pages_and_two_year_counts(
    tmp_path: Path, fixtures_dir: Path, test_hasher: AuthorHasher
) -> None:
    config = load_collector_config(DEFAULT_POLICY_PATH, env={})
    transport = _build_fixture_transport(fixtures_dir)

    backfill_summary = estimate_two_year_backfill_requests()
    assert backfill_summary["trading_days_per_year"] == 244
    assert backfill_summary["trading_days_total"] == TWO_YEAR_BACKFILL_TRADING_DAYS == 488
    assert backfill_summary["price_history_requests_for_two_years"] == 976
    assert backfill_summary["all_exchange_website_requests_for_two_years"] == 1464
    assert (
        backfill_summary["endpoints"]["collector-tw-twse-t86"]["endpoint_template"]
        == TWSE_T86_WEBSITE_URL_TEMPLATE
    )
    assert (
        backfill_summary["endpoints"]["collector-tw-twse-price-history"]["endpoint_template"]
        == TWSE_MI_INDEX_WEBSITE_URL_TEMPLATE
    )
    assert (
        backfill_summary["endpoints"]["collector-tw-tpex-price-history"]["endpoint_template"]
        == TPEX_DAILY_QUOTES_WEBSITE_URL_TEMPLATE
    )
    for sid in (
        "collector-tw-twse-t86",
        "collector-tw-twse-price-history",
        "collector-tw-tpex-price-history",
    ):
        ep = backfill_summary["endpoints"][sid]
        assert ep["page_granularity"] == "per_date_all_stocks"
        assert ep["requests_for_two_years"] == 488
        assert ep["max_connections"] == 1
        assert ep["min_interval_seconds"] == 5.0

    # Verify >= 5.0s sleep between consecutive requests and Connection: close single-connection header
    t86_sleeps: list[float] = []
    t86_spider = TwseT86DailySpider(
        config.get_source("collector-tw-twse-t86"),
        transport=transport,
        sleep_fn=t86_sleeps.append,
    )
    t86_items = t86_spider.collect(dates=("2026-11-10", "2026-11-09"))
    assert len(t86_items) >= 2
    assert t86_sleeps == [5.0]
    assert transport.calls[-1]["headers"]["Connection"] == "close"

    twse_hist_sleeps: list[float] = []
    twse_hist_spider = TwseDailyPriceHistorySpider(
        config.get_source("collector-tw-twse-price-history"),
        transport=transport,
        sleep_fn=twse_hist_sleeps.append,
    )
    twse_hist_items = twse_hist_spider.collect(dates=("2026-11-10", "2026-11-09"))
    assert len(twse_hist_items) >= 2
    assert twse_hist_sleeps == [5.0]
    assert transport.calls[-1]["headers"]["Connection"] == "close"

    tpex_hist_sleeps: list[float] = []
    tpex_hist_spider = TpexDailyPriceHistorySpider(
        config.get_source("collector-tw-tpex-price-history"),
        transport=transport,
        sleep_fn=tpex_hist_sleeps.append,
    )
    tpex_hist_items = tpex_hist_spider.collect(dates=("2026-11-10", "2026-11-09"))
    assert len(tpex_hist_items) >= 2
    assert tpex_hist_sleeps == [5.0]
    assert transport.calls[-1]["headers"]["Connection"] == "close"

    # Per-stock queries must be rejected so history backfill always uses per-date all-stock pages
    with pytest.raises(CollectorConfigError, match="per-date all-stock"):
        t86_spider.collect(
            urls=["https://www.twse.com.tw/rwd/zh/fund/T86?date=20261110&stockNo=2330&response=json"]
        )
    with pytest.raises(CollectorConfigError, match="per-date all-stock"):
        twse_hist_spider.collect(
            urls=["https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?date=20261110&stockNo=2330&response=json"]
        )
    with pytest.raises(CollectorConfigError, match="per-date all-stock"):
        tpex_hist_spider.collect(
            urls=["https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date=2026/11/10&id=6488&response=json"]
        )


# --- TW-SAME-DAY-CLOSE-VIA-COLLECTOR-20261112: same-day close capture ---
import json as _json_cc
from datetime import datetime as _datetime_cc, timezone as _timezone_cc
from pathlib import Path as _Path_cc

from collector_stack.runner import CLOSE_CAPTURE_SOURCES, capture_same_day_close
from collector_stack.config import DEFAULT_POLICY_PATH as _POLICY_CC, load_collector_config as _load_cc
from collector_stack.fetchers import FetchResponse as _FetchResponse_cc, FixtureTransport as _FixtureTransport_cc
from collector_stack.runner import CollectorRunner as _CollectorRunner_cc

FIXTURES = _Path_cc(__file__).parent / "fixtures"
NO_DATA = {"stat": "很抱歉，沒有符合條件的資料!"}
SYSTEMD = _Path_cc(__file__).resolve().parent.parent / "systemd"


def _taipei(hhmm: str, day: str = "2026-11-12") -> _datetime_cc:
    h, m = map(int, hhmm.split(":"))
    y, mo, d = map(int, day.split("-"))
    return _datetime_cc(y, mo, d, h - 8, m, tzinfo=_timezone_cc.utc)


def _transport(published: dict[str, bool]) -> tuple[_FixtureTransport_cc, list[str]]:
    twse = _json_cc.loads((FIXTURES / "twse_mi_index_20261110.json").read_text(encoding="utf-8"))
    tpex = _json_cc.loads((FIXTURES / "tpex_daily_quotes_20261110.json").read_text(encoding="utf-8"))
    calls: list[str] = []

    def route(base: dict, key: str, compact: bool):
        def _r(url: str) -> FetchResponse:
            calls.append(url)
            raw = url.split("date=")[1].split("&")[0]
            payload = {**base, "date": raw} if published[key] else dict(NO_DATA)
            return _FetchResponse_cc(requested_url=url, final_url=url, http_status=200,
                                 body=_json_cc.dumps(payload).encode("utf-8"), headers={})
        return _r

    t = _FixtureTransport_cc()
    t.register("https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX", route(twse, "twse", True))
    t.register("https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes", route(tpex, "tpex", False))
    return t, calls


def _runner(tmp_path, hasher, transport) -> _CollectorRunner_cc:
    return _CollectorRunner_cc(_load_cc(_POLICY_CC), root_dir=tmp_path,
                           hasher=hasher, transport=transport)


def test_no_request_before_1440_or_after_1700_or_on_non_trading_days(tmp_path, test_hasher) -> None:
    transport, calls = _transport({"twse": True, "tpex": True})
    runner = _runner(tmp_path, test_hasher, transport)
    assert capture_same_day_close(runner, now=_taipei("14:39"), sleep_fn=lambda s: None).state == "outside_window"
    assert capture_same_day_close(runner, now=_taipei("17:01"), sleep_fn=lambda s: None).state == "outside_window"
    assert capture_same_day_close(runner, now=_taipei("15:00", "2026-11-14"), sleep_fn=lambda s: None).state == "not_trading_day"
    assert calls == []


def test_first_attempt_captures_today_once_then_skips(tmp_path, test_hasher) -> None:
    transport, calls = _transport({"twse": True, "tpex": True})
    runner = _runner(tmp_path, test_hasher, transport)
    sleeps: list[float] = []
    first = capture_same_day_close(runner, now=_taipei("14:40"), sleep_fn=sleeps.append)
    assert first.sources == {s: "captured" for s in CLOSE_CAPTURE_SOURCES}
    assert not first.retry_needed
    assert len(calls) == 2 and "20261112" in calls[0] and "2026/11/12" in calls[1].replace("-", "/")
    assert sleeps == [5.0]  # one connection: >= 5 s between the two sources
    again = capture_same_day_close(runner, now=_taipei("14:50"), sleep_fn=sleeps.append)
    assert set(again.sources.values()) == {"already_captured"}
    assert len(calls) == 2


def test_unpublished_page_is_retried_until_published(tmp_path, test_hasher) -> None:
    published = {"twse": True, "tpex": False}
    transport, calls = _transport(published)
    runner = _runner(tmp_path, test_hasher, transport)
    first = capture_same_day_close(runner, now=_taipei("14:40"), sleep_fn=lambda s: None)
    assert first.sources["collector-tw-twse-price-history"] == "captured"
    assert first.sources["collector-tw-tpex-price-history"].startswith("retry:")
    assert first.retry_needed
    published["tpex"] = True
    second = capture_same_day_close(runner, now=_taipei("14:50"), sleep_fn=lambda s: None)
    assert second.sources["collector-tw-twse-price-history"] == "already_captured"
    assert second.sources["collector-tw-tpex-price-history"] == "captured"
    assert len(calls) == 3  # twse once, tpex twice


def test_timer_covers_window_in_taipei_time() -> None:
    timer = (SYSTEMD / "pantheon-collector-close.timer").read_text(encoding="utf-8")
    assert "Asia/Taipei" in timer and "14:40" in timer and "17:00:00" in timer and "Mon..Fri" in timer
    service = (SYSTEMD / "pantheon-collector-close.service").read_text(encoding="utf-8")
    assert "capture-close" in service and "SuccessExitStatus=2" in service


def test_rsshub_news_pubdate_is_emitted_as_canonical_utc_iso_and_raw_bytes_keep_the_literal(
    tmp_path: Path,
) -> None:
    config = load_collector_config(DEFAULT_POLICY_PATH, env={})
    base = config.get_source("collector-news-rss").base_urls[0].rstrip("/")

    def item(guid: str, pub: str) -> str:
        return f"<item><guid>{guid}</guid><title>t</title><link>https://x.test/{guid}</link><pubDate>{pub}</pubDate></item>"

    xml = (
        "<rss><channel>"
        + item("a", "Wed, 11 Nov 2026 04:37:00 GMT")
        + item("b", "Wed, 11 Nov 2026 12:37:00 +0800")
        + item("c", "2026-11-11T04:37:00Z")
        + item("d", "Wed, 11 Nov 2026 04:37:00")
        + "</channel></rss>"
    )
    transport = FixtureTransport()
    transport.register(f"{base}/cna/aall", xml.encode())
    news = RssHubNewsPoller(config.get_source("collector-news-rss"), transport=transport)
    got = {i.item_id: i for i in news.collect(fetched_at="2026-11-12T00:00:00Z")}
    assert got["news-cna-a"].published_at == "2026-11-11T04:37:00Z"
    assert got["news-cna-b"].published_at == "2026-11-11T04:37:00Z"
    assert got["news-cna-c"].published_at == "2026-11-11T04:37:00Z"
    # no provable zone: left as-is so the verified reader keeps failing truthfully
    assert got["news-cna-d"].published_at == "Wed, 11 Nov 2026 04:37:00"
    assert b"Wed, 11 Nov 2026 04:37:00 GMT" in got["news-cna-a"].raw_bytes


def test_rss_pub_iso_rejects_malformed_offsets_and_trailing_text_unchanged() -> None:
    from collector_stack.pollers import _rss_pub_iso

    for bad in ("Tue, 10 Nov 2026 01:00:00 +080", "Tue, 10 Nov 2026 01:00:00 +1260",
                "Tue, 10 Nov 2026 01:00:00 GMT trailing", "Wed, 10 Nov 2026 01:00:00 GMT",
                "Tue, 10 Nov 2026 01:00:00 -0000"):
        assert _rss_pub_iso(bad) == bad
    assert _rss_pub_iso("Tue, 10 Nov 2026 09:00:00 +0800") == "2026-11-10T01:00:00Z"
    assert _rss_pub_iso("Tue, 10 Nov 2026 01:00:00 GMT") == "2026-11-10T01:00:00Z"


@pytest.mark.parametrize(("sid", "fixture", "parameter"), [
    ("collector-paper-arxiv", "arxiv_query_recorded.xml", "search_query"),
    ("collector-paper-openalex", "openalex_search_recorded.json", "search"),
])
def test_recorded_query_shapes_through_real_runner(tmp_path, fixtures_dir, test_hasher, sid, fixture, parameter):
    from urllib.parse import parse_qs, urlparse
    from collector_stack.pollers import _policy_query
    config = load_collector_config(DEFAULT_POLICY_PATH, env={})
    transport = FixtureTransport()
    policy = config.get_source(sid)
    metadata_path = Path(__file__).resolve().parents[3] / "docs/deployment/evidence/COLLECTOR-DEMAND-QUERY-PLAN-R2-20261112/recorded-responses.json"
    metadata = next(r for r in json.loads(metadata_path.read_text()) if r["file"] == fixture)
    recorded = (fixtures_dir / fixture).read_bytes()
    import hashlib
    assert hashlib.sha256(recorded).hexdigest() == metadata["sha256"]
    transport.register(metadata["url"], recorded)
    runner = CollectorRunner(config, root_dir=tmp_path, hasher=test_hasher, transport=transport)
    outcome = runner.run_source(sid, run_id="query-shape-" + sid, started_at="2026-11-13T09:00:00Z", allow_disabled=True)
    assert outcome.status == "complete"
    query = parse_qs(urlparse(transport.calls[0]["url"]).query)[parameter][0]
    assert query == _policy_query(policy, arxiv=(sid == "collector-paper-arxiv"))
    assert outcome.bundle.items_path.read_text().strip()


def test_gdelt_recorded_changed_query_exact_recorded_parameters(fixtures_dir):
    """Authentic recording: HTTP 200 ``{}`` for the changed query with maxrecords=10/timespan=1month.

    This is NOT the production default (250/3months, which was rate-limited and has no
    recording) and proves zero articles only; it checks the poller issues the recorded
    query shape and tolerates the real empty-object response.
    """
    import hashlib
    from urllib.parse import parse_qs, urlparse
    from collector_stack.pollers import _policy_query
    metadata_path = Path(__file__).resolve().parents[3] / "docs/deployment/evidence/COLLECTOR-DEMAND-QUERY-PLAN-R2-20261112/recorded-responses.json"
    metadata = next(r for r in json.loads(metadata_path.read_text()) if r["file"] == "gdelt_query_recorded.json")
    recorded = (fixtures_dir / "gdelt_query_recorded.json").read_bytes()
    assert hashlib.sha256(recorded).hexdigest() == metadata["sha256"] and recorded == b"{}"
    recorded_q = parse_qs(urlparse(metadata["url"]).query)
    assert recorded_q["maxrecords"] == ["10"] and recorded_q["timespan"] == ["1month"]
    policy = load_collector_config(DEFAULT_POLICY_PATH, env={}).get_source("collector-gdelt")
    assert recorded_q["query"] == [_policy_query(policy)]
    transport = FixtureTransport()
    poller = GdeltDocPoller(policy, transport=transport)
    # The poller's urlencode yields '+' for spaces; the recording used %20 (same query).
    transport.register(metadata["url"].replace("%20", "+"), recorded)
    assert poller.collect(max_records=10, timespan="1month") == []
    called = parse_qs(urlparse(transport.calls[0]["url"]).query)
    assert called == recorded_q


@pytest.mark.parametrize("sid", ["collector-paper-arxiv", "collector-paper-openalex", "collector-gdelt"])
def test_api_query_override_cannot_bypass_committed_policy(sid):
    from collector_stack.pollers import ArxivPaperPoller, OpenAlexPaperPoller
    cls = {"collector-paper-arxiv": ArxivPaperPoller, "collector-paper-openalex": OpenAlexPaperPoller, "collector-gdelt": GdeltDocPoller}[sid]
    transport = FixtureTransport()
    poller = cls(load_collector_config(DEFAULT_POLICY_PATH, env={}).get_source(sid), transport=transport)
    with pytest.raises(CollectorConfigError, match="query URL must match"):
        poller.collect(url=poller.policy.base_urls[0] + "?query=unreviewed")
    assert transport.calls == []
