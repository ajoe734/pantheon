"""Same-day close capture timing, once-per-day ledger, retry, and CLI/timer wiring."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from collector_stack.close_capture import CLOSE_CAPTURE_SOURCES, capture_same_day_close
from collector_stack.config import DEFAULT_POLICY_PATH, load_collector_config
from collector_stack.fetchers import FetchResponse, FixtureTransport
from collector_stack.runner import CollectorRunner

FIXTURES = Path(__file__).parent / "fixtures"
NO_DATA = {"stat": "很抱歉，沒有符合條件的資料!"}
SYSTEMD = Path(__file__).resolve().parent.parent / "systemd"


def _taipei(hhmm: str, day: str = "2026-11-12") -> datetime:
    h, m = map(int, hhmm.split(":"))
    y, mo, d = map(int, day.split("-"))
    return datetime(y, mo, d, h - 8, m, tzinfo=timezone.utc)


def _transport(published: dict[str, bool]) -> tuple[FixtureTransport, list[str]]:
    twse = json.loads((FIXTURES / "twse_mi_index_20261110.json").read_text(encoding="utf-8"))
    tpex = json.loads((FIXTURES / "tpex_daily_quotes_20261110.json").read_text(encoding="utf-8"))
    calls: list[str] = []

    def route(base: dict, key: str, compact: bool):
        def _r(url: str) -> FetchResponse:
            calls.append(url)
            raw = url.split("date=")[1].split("&")[0]
            payload = {**base, "date": raw} if published[key] else dict(NO_DATA)
            return FetchResponse(requested_url=url, final_url=url, http_status=200,
                                 body=json.dumps(payload).encode("utf-8"), headers={})
        return _r

    t = FixtureTransport()
    t.register("https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX", route(twse, "twse", True))
    t.register("https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes", route(tpex, "tpex", False))
    return t, calls


def _runner(tmp_path, hasher, transport) -> CollectorRunner:
    return CollectorRunner(load_collector_config(DEFAULT_POLICY_PATH), root_dir=tmp_path,
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
