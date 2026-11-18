"""COLLECTOR-LISTING-DAILY-REFRESH-20261118: refresh-listing CLI, idempotence, status.json, timer, fail closed."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from collector_stack import cli as cli_mod
from collector_stack import runner as runner_mod
from collector_stack.config import DEFAULT_POLICY_PATH, load_collector_config
from collector_stack.fetchers import FetchResponse, FixtureTransport
from collector_stack.privacy import AuthorHasher
from collector_stack.runner import LISTING_FILTERED_SOURCES, LISTING_SOURCE_ID, CollectorRunner

FIX = Path(__file__).parent / "fixtures" / "security_listing"
SYSTEMD = Path(__file__).resolve().parents[1] / "systemd"
SL = runner_mod.load_security_listing_module()
DAY = "2026-11-17"  # Tuesday


def _transport(status: int = 200, tpex_ok: bool = True) -> FixtureTransport:
    t = FixtureTransport()
    twse = (FIX / "twse_isin_strmode2_excerpt.html").read_text(encoding="utf-8").encode("big5hkscs", errors="replace")
    tpex = (FIX / "tpex_isin_strmode4_excerpt.html").read_text(encoding="utf-8").encode("big5hkscs", errors="replace")
    t.register(SL.TWSE_LISTED_URL, twse)
    t.register(SL.TPEX_URL, tpex if tpex_ok else b"<html>broken</html>", http_status=status)
    return t


@pytest.fixture()
def hasher(tmp_path: Path) -> AuthorHasher:
    salt = tmp_path / "salt"
    salt.write_bytes(b"host-local-test-salt-9f8e7d6c5b4a3210-never-committed")
    return AuthorHasher.from_file(salt)


def _runner(tmp_path: Path, hasher: AuthorHasher, transport: FixtureTransport, **kw) -> CollectorRunner:
    cfg = load_collector_config(DEFAULT_POLICY_PATH)
    return CollectorRunner(cfg, root_dir=tmp_path / "data", hasher=hasher, transport=transport, **kw)


def _status(root: Path) -> dict:
    return json.loads((root / "status.json").read_text(encoding="utf-8"))["sources"][LISTING_SOURCE_ID]


def test_refresh_publishes_and_records_status(tmp_path: Path, hasher: AuthorHasher) -> None:
    t = _transport()
    r = _runner(tmp_path, hasher, t)
    res = r.refresh_listing_for_day(DAY, sleep_fn=lambda s: None)
    assert res["ok"] and res["listing_date"] == DAY and res["published"]["ok"]
    pub = r.root_dir / SL.BUNDLE_STATE_PATH
    assert pub.read_bytes() == r.listing_state_path.read_bytes()
    st = _status(r.root_dir)
    assert st["status"] == "ok" and st["last_run_id"] == f"listing-{DAY}"
    # collector's own policy-checked transport + contact identity, one connection, both pages only
    assert [c["url"] for c in t.calls] == [SL.TWSE_LISTED_URL, SL.TPEX_URL]
    assert all(c["headers"]["User-Agent"] == r.config.contact_user_agent for c in t.calls)
    assert all("cookie" not in {k.lower() for k in c["headers"]} for c in t.calls)


def test_same_day_is_idempotent_no_second_fetch(tmp_path: Path, hasher: AuthorHasher) -> None:
    t = _transport()
    r = _runner(tmp_path, hasher, t)
    r.refresh_listing_for_day(DAY, sleep_fn=lambda s: None)
    n = len(t.calls)
    before = (r.root_dir / SL.BUNDLE_STATE_PATH).read_bytes()
    res = r.refresh_listing_for_day(DAY, sleep_fn=lambda s: None)
    assert res["skipped"] == "already_refreshed" and len(t.calls) == n
    assert (r.root_dir / SL.BUNDLE_STATE_PATH).read_bytes() == before


def test_same_day_missing_publish_is_republished_without_fetch(tmp_path: Path, hasher: AuthorHasher) -> None:
    t = _transport()
    r = _runner(tmp_path, hasher, t)
    r.refresh_listing_for_day(DAY, sleep_fn=lambda s: None)
    (r.root_dir / SL.BUNDLE_STATE_PATH).unlink()
    n = len(t.calls)
    res = r.refresh_listing_for_day(DAY, sleep_fn=lambda s: None)
    assert res["ok"] and res["republished"] and len(t.calls) == n
    assert (r.root_dir / SL.BUNDLE_STATE_PATH).is_file()


def test_failure_fails_closed_keeps_previous_and_records(tmp_path: Path, hasher: AuthorHasher) -> None:
    r = _runner(tmp_path, hasher, _transport())
    r.refresh_listing_for_day(DAY, sleep_fn=lambda s: None)
    good = r.listing_state_path.read_bytes()
    r2 = _runner(tmp_path, hasher, _transport(tpex_ok=False))
    res = r2.refresh_listing_for_day("2026-11-18", sleep_fn=lambda s: None)
    assert res["ok"] is False
    assert r2.listing_state_path.read_bytes() == good
    st = _status(r2.root_dir)
    assert st["status"] == "failed" and st["reason"]


def test_http_error_is_failure(tmp_path: Path, hasher: AuthorHasher) -> None:
    r = _runner(tmp_path, hasher, _transport(status=503))
    res = r.refresh_listing_for_day(DAY, sleep_fn=lambda s: None)
    assert res["ok"] is False and not r.listing_state_path.exists()


def test_non_trading_day_skips_without_fetch(tmp_path: Path, hasher: AuthorHasher) -> None:
    t = _transport()
    r = _runner(tmp_path, hasher, t)
    res = r.refresh_listing_for_day("2026-11-21", sleep_fn=lambda s: None)  # Saturday
    assert res["skipped"] == "not_a_trading_day" and t.calls == []


def test_first_filter_day_rule(tmp_path: Path, hasher: AuthorHasher) -> None:
    r = _runner(tmp_path, hasher, _transport())
    assert r.listing_symbols(DAY) is None  # never initialised: unfiltered
    r.refresh_listing_for_day(DAY, sleep_fn=lambda s: None)
    allowed = r.listing_symbols(DAY)
    assert allowed and "1101" in allowed  # filtered from the first collection after the first success
    assert LISTING_FILTERED_SOURCES == {
        "collector-tw-twse-t86", "collector-tw-twse-price-history",
        "collector-tw-tpex-price-history", "collector-tw-trading-suspension",
    }
    off = _runner(tmp_path, hasher, _transport(), warrants_mode="off")
    assert off.warrants_mode == "off"


def test_cli_command_exit_codes(tmp_path: Path, hasher: AuthorHasher, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    salt = tmp_path / "salt"
    salt.write_bytes(b"host-local-test-salt-9f8e7d6c5b4a3210-never-committed")
    monkeypatch.setattr(runner_mod, "LiveScraplingTransport", lambda: _transport())
    argv = ["--policy-file", str(DEFAULT_POLICY_PATH), "--root-dir", str(tmp_path / "d"), "--salt-file", str(salt)]
    monkeypatch.setattr("time.sleep", lambda s: None)
    assert cli_mod.main(argv + ["refresh-listing", "--date", DAY]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["published"]["path"] == "bundles/security-listing.jsonl"
    assert cli_mod.main(argv + ["refresh-listing", "--date", DAY]) == 0
    assert json.loads(capsys.readouterr().out)["skipped"] == "already_refreshed"
    monkeypatch.setattr(runner_mod, "LiveScraplingTransport", lambda: _transport(tpex_ok=False))
    assert cli_mod.main(argv + ["refresh-listing", "--date", "2026-11-18"]) == 2


def test_timer_runs_before_0530z_and_service_calls_command() -> None:
    timer = (SYSTEMD / "pantheon-collector-listing.timer").read_text(encoding="utf-8")
    svc = (SYSTEMD / "pantheon-collector-listing.service").read_text(encoding="utf-8")
    times = re.findall(r"OnCalendar=Mon\.\.Fri \*-\*-\* (\d\d):(\d\d):00 Asia/Taipei", timer)
    assert times and all((int(h), int(m)) < (13, 30) for h, m in times)  # 13:30 Taipei == 05:30Z
    assert "Unit=pantheon-collector-listing.service" in timer and "WantedBy=timers.target" in timer
    assert "refresh-listing" in svc and "--root-dir /data" in svc and "Type=oneshot" in svc

