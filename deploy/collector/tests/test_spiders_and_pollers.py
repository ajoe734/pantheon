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
    GdeltRawFeedPoller,
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
    feed_dir = fixtures_dir / "gdelt_feed_recorded"
    transport.register("https://data.gdeltproject.org/gdeltv2/lastupdate.txt",
                       (feed_dir / "masterfilelist-excerpt.txt").read_bytes())
    transport.register("https://data.gdeltproject.org/gdeltv2/20261113110000.export.CSV.zip",
                       (feed_dir / "20261113110000.export.CSV.zip").read_bytes())
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
        warrants_mode="off",  # generic fixture runs: previous behaviour; listing rules have their own tests
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
                           hasher=hasher, transport=transport, warrants_mode="off")


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


def _config_with_enabled(overrides: dict[str, bool]):
    from collector_stack.config import validate_collector_config

    raw = _json_cc.loads(_POLICY_CC.read_text(encoding="utf-8"))
    for sid, enabled in overrides.items():
        raw["sources"][sid]["enabled"] = enabled
    return validate_collector_config(raw, env={})


def test_close_capture_twse_disabled_still_captures_tpex(tmp_path, test_hasher) -> None:
    from collector_stack.runner import SourceDisabledError

    transport, calls = _transport({"twse": True, "tpex": True})
    config = _config_with_enabled(
        {
            "collector-tw-twse-price-history": False,
            "collector-tw-tpex-price-history": True,
        }
    )
    runner = _CollectorRunner_cc(
        config, root_dir=tmp_path, hasher=test_hasher, transport=transport, warrants_mode="off"
    )
    sleeps: list[float] = []
    res = capture_same_day_close(runner, now=_taipei("14:40"), sleep_fn=sleeps.append)
    assert res.sources == {
        "collector-tw-twse-price-history": "disabled",
        "collector-tw-tpex-price-history": "captured",
    }
    assert not res.retry_needed
    assert len(calls) == 1 and "tpex.org.tw" in calls[0]
    assert sleeps == []
    with pytest.raises(SourceDisabledError):
        runner.run_source("collector-tw-twse-price-history", run_id="direct-disabled")


def test_close_capture_tpex_disabled_captures_twse_once_without_retry_loop(
    tmp_path, test_hasher
) -> None:
    full_twse = _json_cc.loads(
        (FIXTURES / "twse_mi_index_full_20261110.json").read_text(encoding="utf-8")
    )
    calls: list[str] = []
    transport = _FixtureTransport_cc()
    transport.register(
        "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX",
        lambda url: (
            calls.append(url)
            or _FetchResponse_cc(
                requested_url=url,
                final_url=url,
                http_status=200,
                body=_json_cc.dumps({**full_twse, "date": "20261112"}).encode("utf-8"),
                headers={},
            )
        ),
    )
    config = _config_with_enabled(
        {
            "collector-tw-twse-price-history": True,
            "collector-tw-tpex-price-history": False,
        }
    )
    runner = _CollectorRunner_cc(
        config, root_dir=tmp_path, hasher=test_hasher, transport=transport, warrants_mode="off"
    )
    sleeps: list[float] = []
    first = capture_same_day_close(runner, now=_taipei("14:40"), sleep_fn=sleeps.append)
    assert first.sources == {
        "collector-tw-twse-price-history": "captured",
        "collector-tw-tpex-price-history": "disabled",
    }
    assert not first.retry_needed
    assert len(calls) == 1 and "twse.com.tw" in calls[0]
    assert sleeps == []
    bundle_items = list((tmp_path / "bundles" / "collector-tw-twse-price-history").rglob("items.jsonl"))
    assert len(bundle_items) == 1
    assert len(bundle_items[0].read_text(encoding="utf-8").splitlines()) == 1389

    second = capture_same_day_close(runner, now=_taipei("14:50"), sleep_fn=sleeps.append)
    assert second.sources == {
        "collector-tw-twse-price-history": "already_captured",
        "collector-tw-tpex-price-history": "disabled",
    }
    assert not second.retry_needed
    assert len(calls) == 1  # no retry loop on later ticks


def test_close_capture_both_disabled_and_cli_exits_zero(
    tmp_path, test_hasher, capsys: pytest.CaptureFixture[str]
) -> None:
    from collector_stack.cli import main as cli_main

    transport, calls = _transport({"twse": True, "tpex": True})
    raw = _json_cc.loads(_POLICY_CC.read_text(encoding="utf-8"))
    raw["sources"]["collector-tw-twse-price-history"]["enabled"] = False
    raw["sources"]["collector-tw-tpex-price-history"]["enabled"] = False
    policy_path = tmp_path / "fetch-policy.json"
    policy_path.write_text(_json_cc.dumps(raw), encoding="utf-8")
    salt_path = tmp_path / "salt"
    salt_path.write_bytes(b"test-salt-close-capture-32-bytes!")

    runner = _CollectorRunner_cc(
        _load_cc(policy_path),
        root_dir=tmp_path,
        hasher=test_hasher,
        transport=transport,
        warrants_mode="off",
    )
    res = capture_same_day_close(runner, now=_taipei("14:40"), sleep_fn=lambda s: None)
    assert res.sources == {
        "collector-tw-twse-price-history": "disabled",
        "collector-tw-tpex-price-history": "disabled",
    }
    assert not res.retry_needed
    assert calls == []
    assert not (tmp_path / "state" / "same-day-close").exists()

    rc = cli_main(
        [
            "--policy-file",
            str(policy_path),
            "--root-dir",
            str(tmp_path),
            "--salt-file",
            str(salt_path),
            "capture-close",
            "--now",
            "2026-11-12T14:40:00+08:00",
        ]
    )
    assert rc == 0
    cli_out = _json_cc.loads(capsys.readouterr().out)
    assert cli_out == {
        "sources": {
            "collector-tw-tpex-price-history": "disabled",
            "collector-tw-twse-price-history": "disabled",
        },
        "state": "attempted",
        "trade_date": "2026-11-12",
    }


def test_close_capture_listing_unavailable_and_unknown_source_produce_per_source_results(
    tmp_path, test_hasher
) -> None:
    transport, calls = _transport({"twse": True, "tpex": True})
    runner_off = _runner(tmp_path / "unknown", test_hasher, transport)
    unknown_res = capture_same_day_close(
        runner_off,
        now=_taipei("14:40"),
        sources=("collector-unknown-source", "collector-tw-tpex-price-history"),
        sleep_fn=lambda s: None,
    )
    assert unknown_res.sources == {
        "collector-unknown-source": "failed:CollectorConfigError",
        "collector-tw-tpex-price-history": "captured",
    }
    assert unknown_res.retry_needed
    assert len(calls) == 1 and "tpex.org.tw" in calls[0]

    calls.clear()
    runner_listed = _CollectorRunner_cc(
        _load_cc(_POLICY_CC),
        root_dir=tmp_path / "stale-listing",
        hasher=test_hasher,
        transport=transport,
        warrants_mode="listed_only",
    )
    _wl_state(runner_listed.listing_state_path, ["2330"], last_success="2026-11-02")
    stale_res = capture_same_day_close(
        runner_listed,
        now=_taipei("14:40"),
        sleep_fn=lambda s: None,
    )
    assert stale_res.sources == {
        "collector-tw-twse-price-history": "failed:ListingUnavailable",
        "collector-tw-tpex-price-history": "failed:ListingUnavailable",
    }
    assert stale_res.retry_needed
    assert calls == []


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


@pytest.mark.parametrize("sid", ["collector-paper-arxiv", "collector-paper-openalex"])
def test_api_query_override_cannot_bypass_committed_policy(sid):
    from collector_stack.pollers import ArxivPaperPoller, OpenAlexPaperPoller
    cls = {"collector-paper-arxiv": ArxivPaperPoller, "collector-paper-openalex": OpenAlexPaperPoller}[sid]
    transport = FixtureTransport()
    poller = cls(load_collector_config(DEFAULT_POLICY_PATH, env={}).get_source(sid), transport=transport)
    with pytest.raises(CollectorConfigError, match="query URL must match"):
        poller.collect(url=poller.policy.base_urls[0] + "?query=unreviewed")
    assert transport.calls == []


def _gdelt_runner(tmp_path, fixtures_dir, test_hasher, *, index="masterfilelist-excerpt.txt"):
    transport = _build_fixture_transport(fixtures_dir)
    feed = fixtures_dir / "gdelt_feed_recorded"
    transport.register("https://data.gdeltproject.org/gdeltv2/lastupdate.txt", (feed / index).read_bytes())
    transport.register("https://data.gdeltproject.org/gdeltv2/20261113114500.export.CSV.zip",
                       (feed / "20261113114500.export.CSV.zip").read_bytes())
    runner = CollectorRunner(load_collector_config(DEFAULT_POLICY_PATH, env={}),
                             root_dir=tmp_path, hasher=test_hasher, transport=transport)
    return runner, transport


def test_gdelt_real_export_features_and_provenance(tmp_path, fixtures_dir, test_hasher):
    import hashlib
    feed = fixtures_dir / "gdelt_feed_recorded"
    provenance = json.loads((feed / "provenance.json").read_text())
    for recorded in provenance["files"]:
        body = (feed / recorded["path"]).read_bytes()
        assert len(body) == recorded["size"]
        assert hashlib.sha256(body).hexdigest() == recorded["sha256"]
        if recorded["path"].endswith(".zip"):
            assert hashlib.md5(body).hexdigest() == recorded["md5"]
    runner, transport = _gdelt_runner(tmp_path, fixtures_dir, test_hasher)
    outcome = runner.run_source("collector-gdelt", run_id="real-export")
    assert outcome.status == "complete"
    items = [json.loads(line) for line in outcome.bundle.items_path.read_text().splitlines()]
    assert len(items) == 3  # 1197 real rows, three match the committed Taiwan/semiconductor terms
    first = items[0]
    assert first["gdelt_record_id"] == "1332959441"
    assert first["id"] == "gdelt-1332959441"
    assert first["event_time"] == "2026-11-13T00:00:00Z"
    assert first["event_time_precision"] == "day"
    assert first["published_at"] == first["gdelt_date_added"] == "2026-11-13T11:00:00Z"
    assert first["published_at_basis"] == "gdelt_date_added"
    assert first["matched_query_terms"] == ["Taiwan"]
    assert first["metrics"]["event_code"] == "080"
    assert first["metrics"]["goldstein_scale"] == 5.0
    assert first["metrics"]["action_country_code"] == "TW"
    assert first["url"].startswith("https://timesofindia.indiatimes.com/")
    assert first["feed_url"].endswith("20261113110000.export.CSV.zip")
    assert first["attribution"] == runner.config.get_source("collector-gdelt").attribution
    assert len(transport.calls) == 2
    verify_bundle_directory(outcome.bundle.bundle_dir)


def test_gdelt_cache_survives_restart_and_new_snapshot_downloads_once(tmp_path, fixtures_dir, test_hasher):
    runner, transport = _gdelt_runner(tmp_path, fixtures_dir, test_hasher)
    assert runner.run_source("collector-gdelt", run_id="first").status == "complete"
    runner, transport = _gdelt_runner(tmp_path, fixtures_dir, test_hasher)
    again = runner.run_source("collector-gdelt", run_id="repeat")
    assert again.bundle.items_path.read_text() == ""
    assert [call["url"] for call in transport.calls] == ["https://data.gdeltproject.org/gdeltv2/lastupdate.txt"]
    runner, transport = _gdelt_runner(tmp_path, fixtures_dir, test_hasher, index="lastupdate.txt")
    empty = runner.run_source("collector-gdelt", run_id="new-snapshot")
    assert empty.bundle.items_path.read_text() == ""  # real 11:45 snapshot has no matching rows
    assert empty.bundle.manifest["reason"] == "no_matching_records"
    assert len(transport.calls) == 2
    runner, transport = _gdelt_runner(tmp_path, fixtures_dir, test_hasher, index="lastupdate.txt")
    runner.run_source("collector-gdelt", run_id="repeat-empty")
    assert len(transport.calls) == 1


def test_gdelt_bundle_write_failure_replays_verified_cache(tmp_path, fixtures_dir, test_hasher, monkeypatch):
    runner, _ = _gdelt_runner(tmp_path, fixtures_dir, test_hasher)
    def fail(**kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(runner.bundle_writer, "write_bundle", fail)
    with pytest.raises(OSError, match="disk full"):
        runner.run_source("collector-gdelt", run_id="disk-failed")
    runner, transport = _gdelt_runner(tmp_path, fixtures_dir, test_hasher)
    outcome = runner.run_source("collector-gdelt", run_id="recovered")
    assert outcome.status == "complete"
    assert len(outcome.bundle.items_path.read_text().splitlines()) == 3
    assert len(transport.calls) == 1  # cached download reused, unbundled records replayed


@pytest.mark.parametrize("failure", ["size", "md5", "http", "network", "bad_zip", "bad_columns", "bad_date", "bad_index", "foreign_url", "access"])
def test_gdelt_failures_record_reason_and_auto_pause(tmp_path, fixtures_dir, test_hasher, failure):
    import hashlib
    from collector_stack.runner import SourcePausedError
    feed = fixtures_dir / "gdelt_feed_recorded"
    raw_index = (feed / "masterfilelist-excerpt.txt").read_text()
    size, md5, url = raw_index.splitlines()[0].split()
    body = (feed / "20261113110000.export.CSV.zip").read_bytes()
    for attempt in range(2):
        runner, transport = _gdelt_runner(tmp_path, fixtures_dir, test_hasher)
        index = raw_index
        payload = body
        status = 200
        if failure == "size":
            payload = body[:-1]
        elif failure == "md5":
            index = raw_index.replace(md5, "0" * 32)
        elif failure in ("http", "access"):
            status = 500 if failure == "http" else 429
        elif failure == "bad_index":
            index = "unparseable manifest"
        elif failure == "foreign_url":
            index = raw_index.replace("http://data.gdeltproject.org/", "https://other.invalid/")
        elif failure in ("bad_zip", "bad_columns", "bad_date"):
            if failure == "bad_zip":
                payload = b"not a zip"
            else:
                with zipfile.ZipFile(io.BytesIO(body)) as archive:
                    csv_body = archive.read(archive.namelist()[0])
                if failure == "bad_columns":
                    csv_body = csv_body.replace(b"\t", b" ", 1)
                else:
                    csv_body = csv_body.replace(b"20261113", b"20260230", 1)
                output = io.BytesIO()
                with zipfile.ZipFile(output, "w") as archive:
                    archive.writestr("20261113110000.export.CSV", csv_body)
                payload = output.getvalue()
            index = raw_index.replace(size, str(len(payload)), 1).replace(md5, hashlib.md5(payload).hexdigest(), 1)
        transport.register("https://data.gdeltproject.org/gdeltv2/lastupdate.txt", index)
        target = "https://data.gdeltproject.org/gdeltv2/20261113110000.export.CSV.zip"
        if failure == "network":
            def fail(_url):
                raise TimeoutError("download timed out")
            transport.register(target, fail)
        else:
            transport.register(target, payload, http_status=status)
        outcome = runner.run_source("collector-gdelt", run_id=f"{failure}-{attempt}")
        assert outcome.status == ("partial" if attempt == 0 else "paused")
        assert outcome.bundle.manifest["paused_reason"]
        assert not list((tmp_path / "state" / "gdelt-feed").glob("*.bundled"))
    with pytest.raises(SourcePausedError):
        runner.run_source("collector-gdelt", run_id="must-not-fetch")


def test_gdelt_filter_uses_policy_and_never_accepts_query_override(tmp_path, fixtures_dir, test_hasher):
    from dataclasses import replace
    runner, transport = _gdelt_runner(tmp_path, fixtures_dir, test_hasher)
    policy = runner.config.get_source("collector-gdelt")
    raw = dict(policy.raw_config, query_terms=["semiconductor"])
    poller = GdeltRawFeedPoller(replace(policy, raw_config=raw), transport=transport, cache_dir=tmp_path / "cache")
    with pytest.raises(TypeError):
        poller.collect(query="unreviewed")
    assert not transport.calls
    items = poller.collect()
    assert items == []  # the three Taiwan rows don't match a semiconductor-only policy
    assert poller.empty_reason == "no_matching_records"


def test_gdelt_reviewed_vocabulary_change_reuses_cache(tmp_path, fixtures_dir, test_hasher):
    from collector_stack.config import validate_collector_config
    runner, _ = _gdelt_runner(tmp_path, fixtures_dir, test_hasher)
    runner.run_source("collector-gdelt", run_id="original-policy")
    runner, transport = _gdelt_runner(tmp_path, fixtures_dir, test_hasher)
    raw = json.loads(DEFAULT_POLICY_PATH.read_text())
    raw["sources"]["collector-gdelt"]["query_terms"] = ["Taiwan"]
    runner.config = validate_collector_config(raw, env={})
    changed = runner.run_source("collector-gdelt", run_id="reviewed-policy")
    assert len(changed.bundle.items_path.read_text().splitlines()) == 3
    assert len(transport.calls) == 1


def test_gdelt_rejects_corrupt_existing_cache(tmp_path, fixtures_dir, test_hasher):
    runner, _ = _gdelt_runner(tmp_path, fixtures_dir, test_hasher)
    runner.run_source("collector-gdelt", run_id="good-cache")
    cached = tmp_path / "state" / "gdelt-feed" / "20261113110000.export.CSV.zip"
    cached.write_bytes(b"truncated cache")
    runner, transport = _gdelt_runner(tmp_path, fixtures_dir, test_hasher)
    corrupt = runner.run_source("collector-gdelt", run_id="corrupt-cache")
    assert corrupt.status == "partial"
    assert "gdelt_integrity_mismatch" in corrupt.bundle.manifest["paused_reason"]
    assert len(transport.calls) == 1
    assert not cached.exists()
    runner, transport = _gdelt_runner(tmp_path, fixtures_dir, test_hasher)
    runner.run_source("collector-gdelt", run_id="redownload-cache")
    assert len(transport.calls) == 2
    assert cached.is_file()


@pytest.mark.parametrize(
    'class_name,fixture,dates,count',
    [
        ('TwseIndexBreadthSpider', 'twse_mi_index_full_20261110.json', ['2026-11-10'], 57),
        ('TwseTaiexHistorySpider', 'twse_mi_5mins_hist_201201.json', ['2012-01'], 15),
    ],
)
def test_regime_complete_official_pages(fixtures_dir, class_name, fixture, dates, count):
    from collector_stack import spiders
    cls = getattr(spiders, class_name)
    body = (fixtures_dir / fixture).read_bytes()
    payload = json.loads(body)
    url = cls.build_date_url(dates[0])
    transport = FixtureTransport({url: body})
    spider = cls(load_collector_config(env={}).get_source(cls.source_id), transport=transport)
    stamp = '2026-11-14T04:00:00Z'
    items = spider.collect(dates=dates, fetched_at=stamp)
    assert len(items) == count and len(transport.calls) == 1
    assert all(i.url == url and i.fetched_at == stamp for i in items)
    if class_name == 'TwseIndexBreadthSpider':
        indices = [i.extra_fields for i in items[:-1]]
        assert {r['index_name'] for r in indices} == {r[0] for r in payload['tables'][0]['data']}
        taiex = next(r for r in indices if r['index_name'] == '發行量加權股價指數')
        assert (taiex['close'], taiex['change_points'], taiex['change_percent']) == (42711.92, 1050.22, 2.52)
        food = next(r for r in indices if r['index_name'] == '食品類指數')
        assert (food['change_points'], food['change_percent']) == (-20.37, -1.08)
        breadth = items[-1].extra_fields
        assert breadth['whole_market'] == dict(advancers=10250, limit_up=150, decliners=3702,
                                               limit_down=137, unchanged=694, untraded=22388, no_comparison=4701)
        assert breadth['stocks'] == dict(advancers=526, limit_up=30, decliners=491,
                                         limit_down=2, unchanged=68, untraded=2, no_comparison=0)
        assert json.loads(items[1].raw_bytes)['data'][0] == payload['tables'][0]['data'][1]
        assert json.loads(items[-1].raw_bytes)['data'] == payload['tables'][7]['data']
        assert all(i.extra_fields['dataset'] != 'tw_price_daily' for i in items)
        # Table order and unrelated table availability are not schema identity.
        payload['tables'].reverse()
        transport.register(url, json.dumps(payload, ensure_ascii=False))
        spider = cls(load_collector_config(env={}).get_source(cls.source_id), transport=transport)
        assert [i.item_id for i in spider.collect(dates=dates)] == [i.item_id for i in items]
    else:
        first = items[0].extra_fields
        assert first['trade_date'] == '2012-01-02'
        assert (first['open'], first['high'], first['low'], first['close']) == (7071.35, 7073.48, 6948.67, 6952.21)
        assert {i.extra_fields['trade_date'] for i in items} == {
            spiders._normalize_iso_date(row[0]) for row in payload['data']
        }
        assert [json.loads(i.raw_bytes)['data'][0] for i in items] == payload['data']


@pytest.mark.parametrize('fault', ['missing_index', 'missing_breadth', 'fields', 'breadth_labels',
                                  'breadth_value', 'last_index_value', 'date', 'stat', 'duplicate'])
def test_regime_index_schema_failure_records_reason_and_no_partial_rows(
    tmp_path, fixtures_dir, test_hasher, fault
):
    from collector_stack.spiders import TwseIndexBreadthSpider
    p = json.loads((fixtures_dir / 'twse_mi_index_full_20261110.json').read_bytes())
    if fault == 'missing_index':
        p['tables'][0] = {}
    elif fault == 'missing_breadth':
        p['tables'][7] = {}
    elif fault == 'fields':
        p['tables'][7]['fields'][1] = 'changed'
    elif fault == 'breadth_labels':
        p['tables'][7]['data'].pop()
    elif fault == 'breadth_value':
        p['tables'][7]['data'][-1][-1] = 'unknown'
    elif fault == 'last_index_value':
        p['tables'][0]['data'][-1][1] = '--'
    elif fault == 'date':
        p['date'] = '20261109'
    elif fault == 'stat':
        p['stat'] = 'not OK'
    else:
        p['tables'][0]['data'].append(p['tables'][0]['data'][0])
    url = TwseIndexBreadthSpider.build_date_url('2026-11-10')
    runner = CollectorRunner(load_collector_config(env={}), root_dir=tmp_path, hasher=test_hasher,
                             transport=FixtureTransport({url: json.dumps(p, ensure_ascii=False)}))
    outcome = runner.run_source(TwseIndexBreadthSpider.source_id, run_id='invalid',
                                collect_kwargs={'dates': ['2026-11-10']})
    assert outcome.status == 'partial'
    assert outcome.bundle.manifest['reason'].startswith('parse_failed: ')
    assert len(outcome.bundle.manifest['reason']) > len('parse_failed: ')
    assert outcome.bundle.items_path.read_bytes() == b''
    assert outcome.bundle.manifest['source_url'] == url


@pytest.mark.parametrize('fault', ['fields', 'last_value', 'duplicate', 'wrong_month', 'ohlc', 'date'])
def test_regime_month_schema_failure_is_atomic(tmp_path, fixtures_dir, test_hasher, fault):
    from collector_stack.spiders import TwseTaiexHistorySpider
    p = json.loads((fixtures_dir / 'twse_mi_5mins_hist_201201.json').read_bytes())
    if fault == 'fields':
        p['fields'][-1] = 'changed'
    elif fault == 'last_value':
        p['data'][-1][-1] = '--'
    elif fault == 'duplicate':
        p['data'].append(p['data'][0])
    elif fault == 'wrong_month':
        p['data'][-1][0] = '101/02/01'
    elif fault == 'ohlc':
        p['data'][-1][2], p['data'][-1][3] = p['data'][-1][3], p['data'][-1][2]
    else:
        p['date'] = '20120201'
    url = TwseTaiexHistorySpider.build_date_url('2012-01')
    runner = CollectorRunner(load_collector_config(env={}), root_dir=tmp_path, hasher=test_hasher,
                             transport=FixtureTransport({url: json.dumps(p, ensure_ascii=False)}))
    outcome = runner.run_source(TwseTaiexHistorySpider.source_id, run_id='invalid-month',
                                collect_kwargs={'dates': ['2012-01']})
    assert outcome.status == 'partial' and outcome.bundle.manifest['reason'].startswith('parse_failed: ')
    assert outcome.bundle.items_path.read_bytes() == b''


# --- TAIFEX Taiwan VIX (fixtures are real files recorded from taifex.com.tw on 2026-11-14) -------------
_VIX_DAILY_PAGE = ("<input onClick=\"window.open('https://www.taifex.com.tw/file/taifex/Dailydownload/vix/log2data/"
                   "202611new.txt')\" value=\"下載\" title=\"202611new.txt\">")
_VIX_DAY_PAGE = "<input onClick=\"window.open('getVixData?filesname=20261113')\" value=\"下載\" title=\"20261113(txt)\">"
_VIX_ZIP_NAME = "202610歷史紀錄波動率指數_新VIX.zip"
_VIX_ZIP_PAGE = ("<input onClick=\"window.open('https://www.taifex.com.tw/file/taifex/Dailydownload/vix/logdata/"
                 + _VIX_ZIP_NAME + "')\">")


def _vix_transport(fixtures_dir, *, daily=None, day=None, member='tvix_20261001', zip_body=None):
    from urllib.parse import quote
    from collector_stack.spiders import TaifexVixSpider as S
    day_body = (fixtures_dir / 'taifex_vix_intraday_sample.txt').read_bytes()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr(member, day_body.replace(b'20261113', b'20261001'))
    return FixtureTransport({
        S.DAILY_PAGE: _VIX_DAILY_PAGE, S.INTRADAY_PAGE: _VIX_DAY_PAGE, S.INTRADAY_3M_PAGE: _VIX_ZIP_PAGE,
        S.DAILY_URL.format(yyyymm='202611'): daily if daily is not None
        else (fixtures_dir / 'taifex_vix_daily_sample.txt').read_bytes(),
        S.DAY_URL.format(yyyymmdd='20261113'): day if day is not None else day_body,
        S.FILE_BASE + 'logdata/' + quote(_VIX_ZIP_NAME): zip_body if zip_body is not None else buf.getvalue(),
    })


def _vix_runner(tmp_path, test_hasher, transport):
    return CollectorRunner(load_collector_config(env={}), root_dir=tmp_path, hasher=test_hasher, transport=transport)


def test_taifex_vix_archives_raw_files_and_parsed_rows(tmp_path, fixtures_dir, test_hasher):
    transport = _vix_transport(fixtures_dir)
    outcome = _vix_runner(tmp_path, test_hasher, transport).run_source('collector-tw-taifex-vix', run_id='vix1')
    assert outcome.status == 'complete'
    items = [json.loads(line) for line in outcome.bundle.items_path.read_text().splitlines()]
    by = {i['dataset']: i for i in items if i['dataset'] != 'tw_vix_intraday'}
    daily = by['tw_vix_daily']
    assert daily['row_count'] == 10 and daily['first_date'] == '2026-11-02' and daily['last_date'] == '2026-11-13'
    assert daily['rows'][0] == {'trade_date': '2026-11-02', 'time': '13:45:00.00', 'time_raw': '13450000',
                                'close': 31.3, 'last_1min_avg': 31.28}
    days = {i['trade_date']: i for i in items if i['dataset'] == 'tw_vix_intraday'}
    assert set(days) == {'2026-10-01', '2026-11-13'}
    cur = days['2026-11-13']
    assert cur['delivery'] == 'daily_file' and cur['complete_session'] and cur['last_1min_avg'] == 37.84
    assert cur['rows'][0]['time'] == '09:00:00.00' and cur['rows'][0]['value'] == 39.65
    assert cur['rows'][-1]['time'] == '13:45:00.00' and cur['row_count'] == len(cur['rows']) == 1141
    assert days['2026-10-01']['delivery'] == 'monthly_zip'
    assert by['tw_vix_intraday_archive']['member_days'] == ['20261001']
    exts = sorted(p.suffix for p in (outcome.bundle.bundle_dir / 'raw').iterdir())
    assert exts == ['.txt', '.txt', '.txt', '.zip']
    raw_daily = next(p for p in (outcome.bundle.bundle_dir / 'raw').iterdir() if p.stat().st_size == 482)
    assert raw_daily.read_bytes() == (fixtures_dir / 'taifex_vix_daily_sample.txt').read_bytes()


def test_taifex_vix_is_incremental_and_idempotent(tmp_path, fixtures_dir, test_hasher):
    runner = _vix_runner(tmp_path, test_hasher, _vix_transport(fixtures_dir))
    first = runner.run_source('collector-tw-taifex-vix', run_id='vix1')
    second_transport = _vix_transport(fixtures_dir)
    runner2 = _vix_runner(tmp_path, test_hasher, second_transport)
    second = runner2.run_source('collector-tw-taifex-vix', run_id='vix2')
    ids1 = {json.loads(l)['id'] for l in first.bundle.items_path.read_text().splitlines()}
    ids2 = {json.loads(l)['id'] for l in second.bundle.items_path.read_text().splitlines()}
    # Only the still-growing current-month daily file is fetched again, with an unchanged content-derived id.
    assert ids2 <= ids1 and len(ids2) == 1 and next(iter(ids2)).startswith('taifex-vix-daily-202611-')
    fetched = [c['url'] for c in second_transport.calls]
    assert not any('getVixData' in u or u.endswith('.zip') or '%' in u for u in fetched)
    assert not any('20261113' in u for u in fetched)


def _vix_fault_cases():
    day = lambda f: (lambda b: f(b))
    return {
        'html_instead_of_file': ('day', b'<!DOCTYPE HTML><html></html>', 'HTML'),
        'changed_header': ('day', None, 'changed header'),
        'bad_value': ('day', None, 'bad VIX value'),
        'wrong_date': ('day', None, 'malformed row'),
        'daily_bad_row': ('daily', None, 'malformed row'),
        'bad_zip': ('zip', b'not a zip', 'not a zip'),
        'bad_member': ('member', None, 'unexpected member'),
    }


@pytest.mark.parametrize('fault', list(_vix_fault_cases()))
def test_taifex_vix_format_change_fails_run_without_partial_rows(tmp_path, fixtures_dir, test_hasher, fault):
    kind, body, reason = _vix_fault_cases()[fault]
    day = (fixtures_dir / 'taifex_vix_intraday_sample.txt').read_bytes()
    daily = (fixtures_dir / 'taifex_vix_daily_sample.txt').read_bytes()
    kwargs = {}
    if fault == 'changed_header':
        kwargs['day'] = day.replace('臺指選擇權波動率指數'.encode('cp950'), 'VIX'.encode('cp950'), 1)
    elif fault == 'bad_value':
        kwargs['day'] = day.replace(b'39.65', b'N/A', 1)
    elif fault == 'wrong_date':
        kwargs['day'] = day.replace(b'20261113\t9001500', b'20261112\t9001500', 1)
    elif fault == 'daily_bad_row':
        kwargs['daily'] = daily.replace(b'31.30\t\t31.28', b'31.30', 1)
    elif kind == 'day':
        kwargs['day'] = body
    elif kind == 'zip':
        kwargs['zip_body'] = body
    else:
        kwargs['member'] = 'tvix_bogus'
    transport = _vix_transport(fixtures_dir, **kwargs)
    runner = _vix_runner(tmp_path, test_hasher, transport)
    outcome = runner.run_source('collector-tw-taifex-vix', run_id='vix-bad')
    assert outcome.status == 'partial'
    assert outcome.bundle.manifest['reason'].startswith('parse_failed: ') and reason in outcome.bundle.manifest['reason']
    assert outcome.bundle.items_path.read_bytes() == b''
    assert not (tmp_path / 'state' / 'taifex-vix' / 'ledger.json').exists()


def test_taifex_vix_missing_download_links_fail_closed(tmp_path, fixtures_dir, test_hasher):
    from collector_stack.spiders import TaifexVixSpider as S
    transport = _vix_transport(fixtures_dir)
    transport.register(S.DAILY_PAGE, '<html>rendered dynamically</html>')
    outcome = _vix_runner(tmp_path, test_hasher, transport).run_source('collector-tw-taifex-vix', run_id='vix-nolink')
    assert outcome.status == 'partial' and 'no download links' in outcome.bundle.manifest['reason']
    assert outcome.bundle.items_path.read_bytes() == b''


# ---------------------------------------------------------------------------
# SOURCE-WARRANT-LISTING-EXPIRY-20261117: the exchange listing decides what is collected
# ---------------------------------------------------------------------------
from collector_stack.config import CollectorConfigError as _CCE_wl  # noqa: E402

_TWSE_PH = "collector-tw-twse-price-history"


def _wl_state(path, symbols, *, last_success="2026-11-10"):
    from services.source_ingestion import security_listing as sl

    st = sl.ListingState(last_success_date=last_success)
    for sym in symbols:
        st.allowlist[sym] = {"venue": "TWSE", "category": "股票", "cfi": "ESVUFR", "is_warrant": False,
                             "first_seen": last_success, "last_seen": last_success}
    st.save(path)


def _wl_runner(tmp_path, test_hasher, fixtures_dir, **kw):
    base = _json_cc.loads((fixtures_dir / "twse_mi_index_20261110.json").read_text(encoding="utf-8"))
    t = _FixtureTransport_cc()
    t.register("https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX",
               lambda url: _FetchResponse_cc(requested_url=url, final_url=url, http_status=200,
                                             body=_json_cc.dumps(base).encode("utf-8"), headers={}))
    return CollectorRunner(_load_cc(_POLICY_CC), root_dir=tmp_path, hasher=test_hasher, transport=t, **kw)


def _wl_symbols(outcome):
    rows = [_json_cc.loads(x) for x in outcome.bundle.items_path.read_text().splitlines() if x.strip()]
    return sorted({r["symbol"] for r in rows})


def test_listed_only_collects_listed_and_skips_unlisted(tmp_path, test_hasher, fixtures_dir):
    runner = _wl_runner(tmp_path, test_hasher, fixtures_dir)
    _wl_state(runner.listing_state_path, ["2330"])
    out = runner.run_source(_TWSE_PH, run_id="wl1", started_at="2026-11-10T09:00:00Z",
                            finished_at="2026-11-10T09:00:05Z", allow_disabled=True,
                            collect_kwargs={"dates": ["2026-11-10"]})
    assert out.status == "complete"
    assert _wl_symbols(out) == ["2330"]


def test_listing_older_than_three_trading_days_stops_price_collection_and_uninitialised_is_shadow(tmp_path, test_hasher, fixtures_dir):
    from services.source_ingestion.security_listing import ListingUnavailable

    runner = _wl_runner(tmp_path, test_hasher, fixtures_dir)
    # never initialised on this host: rollout shadow, unfiltered (the daily refresh initialises it)
    out = runner.run_source(_TWSE_PH, run_id="wl2", started_at="2026-11-10T09:00:00Z",
                            finished_at="2026-11-10T09:00:05Z", allow_disabled=True, collect_kwargs={"dates": ["2026-11-10"]})
    assert _wl_symbols(out) == ["2317", "2330"]
    runner.transport.calls.clear()
    _wl_state(runner.listing_state_path, ["2330"], last_success="2026-11-02")  # 6 trading days old
    with pytest.raises(ListingUnavailable):
        runner.run_source(_TWSE_PH, run_id="wl3", started_at="2026-11-10T09:00:00Z",
                          finished_at="2026-11-10T09:00:05Z", allow_disabled=True, collect_kwargs={"dates": ["2026-11-10"]})
    assert runner.transport.calls == []  # nothing was fetched


def test_rollback_mode_off_restores_previous_behaviour(tmp_path, test_hasher, fixtures_dir):
    runner = _wl_runner(tmp_path, test_hasher, fixtures_dir, warrants_mode="off")
    out = runner.run_source(_TWSE_PH, run_id="wl4", started_at="2026-11-10T09:00:00Z",
                            finished_at="2026-11-10T09:00:05Z", allow_disabled=True,
                            collect_kwargs={"dates": ["2026-11-10"]})
    assert _wl_symbols(out) == ["2317", "2330"]  # no listing file needed, nothing filtered
    with pytest.raises(_CCE_wl):
        _wl_runner(tmp_path, test_hasher, fixtures_dir, warrants_mode="bogus")


def test_spider_counts_skipped_not_listed_and_t86_suspension_accept_filter(tmp_path, test_hasher, fixtures_dir):
    runner = _wl_runner(tmp_path, test_hasher, fixtures_dir, warrants_mode="off")
    spider = runner.build_collector(runner.config.get_source(_TWSE_PH))
    spider.collect(dates=["2026-11-10"], symbols_filter=["2330"], fetched_at="2026-11-10T09:00:00Z")
    assert spider.skipped_not_listed_count == 1
    import inspect
    from collector_stack.spiders import TwTradingSuspensionSpider, TwseT86DailySpider
    for cls in (TwTradingSuspensionSpider, TwseT86DailySpider):
        assert "symbols_filter" in inspect.signature(cls.collect).parameters
        assert not hasattr(cls, "WARRANT_SYMBOL_RE")
