"""Tests for start-up configuration validation and safety guards (AC1, AC6, AC7)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from collector_stack.cli import main as cli_main
from collector_stack.config import (
    DEFAULT_POLICY_PATH,
    EXCHANGE_WEBSITE_SOURCE_IDS,
    CollectorConfigError,
    load_collector_config,
    validate_collector_config,
)
from collector_stack.fetchers import FixtureTransport
from collector_stack.privacy import AuthorHasher
from collector_stack.runner import CollectorRunner, SourceDisabledError
from collector_stack.spiders import InstagramPopularTagSpider


def _raw_policy() -> dict:
    return json.loads(DEFAULT_POLICY_PATH.read_text(encoding="utf-8"))


def test_default_policy_loads_and_cli_validates(capsys: pytest.CaptureFixture[str]) -> None:
    config = load_collector_config(DEFAULT_POLICY_PATH, env={})
    assert config.schema_version == "collector_fetch_policy.v1"
    assert config.purpose == "aggregate market attention and sentiment research"
    assert config.auto_pause_consecutive_blocks == 2
    assert config.raw_retention_days == 90

    rc = cli_main(["--policy-file", str(DEFAULT_POLICY_PATH), "validate-config"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "valid"
    assert "collector-tw-cmoney-forum" not in out["disabled_sources"]
    assert "collector-tw-ptt-mirrors" in out["disabled_sources"]
    for sid in EXCHANGE_WEBSITE_SOURCE_IDS:
        src = config.get_source(sid)
        assert src.enabled is True
        assert src.max_connections == 1
        assert src.min_interval_seconds >= 5.0
        assert src.raw_config["backfill_page_granularity"] == "per_date_all_stocks"
        assert src.raw_config["two_year_backfill_requests"] == 488


@pytest.mark.parametrize(
    ("mutator", "expected_match"),
    [
        (lambda d: d.update(stealth_fetching=True), "stealth_fetching=true"),
        (lambda d: d.update(cloudflare_turnstile_bypass=True), "cloudflare_turnstile_bypass=true"),
        (lambda d: d.update(proxy_rotation=True), "proxy_rotation=true"),
        (lambda d: d.update(proxies=["http://10.0.0.1:8080"]), "proxies"),
        (lambda d: d.update(captcha_solving=True), "captcha_solving=true"),
        (lambda d: d.update(captcha_solver="2captcha"), "captcha_solver"),
        (lambda d: d.update(logged_in_sessions=True), "logged_in_sessions=true"),
        (lambda d: d.update(cookie_reuse=True), "cookie_reuse=true"),
        (lambda d: d.update(browser_user_agent_mode="spoof_safari"), "browser_user_agent_mode"),
        (
            lambda d: d["sources"]["collector-tw-threads"].update(stealth=True),
            "stealth=true",
        ),
        (
            lambda d: d["sources"]["collector-tw-threads"].update(fetcher="stealth"),
            "fetcher='stealth'",
        ),
        (
            lambda d: d["sources"]["collector-tw-threads"].update(proxy_url="socks5://127.0.0.1:9050"),
            "proxy_url",
        ),
        (
            lambda d: d["sources"]["collector-tw-threads"].update(solve_captcha=True),
            "solve_captcha=true",
        ),
        (
            lambda d: d["sources"]["collector-tw-threads"].update(logged_in=True),
            "logged_in",
        ),
        (
            lambda d: d["sources"]["collector-tw-threads"].update(cookies={"sessionid": "abc"}),
            "cookies",
        ),
    ],
)
def test_startup_refuses_stealth_proxy_captcha_and_login_configs(
    mutator, expected_match: str
) -> None:
    raw = _raw_policy()
    mutator(raw)
    with pytest.raises(CollectorConfigError, match=expected_match):
        validate_collector_config(raw, env={})


@pytest.mark.parametrize(
    ("env_key", "env_val"),
    [
        ("COLLECTOR_STEALTH", "1"),
        ("COLLECTOR_PROXY_URL", "http://proxy.example:8080"),
        ("HTTPS_PROXY", "http://proxy.example:8080"),
        ("COLLECTOR_CAPTCHA_KEY", "secret"),
        ("COLLECTOR_SESSION_COOKIE", "sid=123"),
    ],
)
def test_startup_refuses_forbidden_environment_variables(env_key: str, env_val: str) -> None:
    raw = _raw_policy()
    with pytest.raises(CollectorConfigError, match=env_key):
        validate_collector_config(raw, env={env_key: env_val})


def test_instagram_only_permits_logged_out_popular_tag_pages(
    test_hasher: AuthorHasher,
) -> None:
    config = load_collector_config(DEFAULT_POLICY_PATH, env={})
    ig_policy = config.get_source("collector-tw-instagram-popular")
    assert ig_policy.logged_in is False
    assert ig_policy.raw_config["popular_tags_only"] is True

    spider = InstagramPopularTagSpider(
        ig_policy, transport=FixtureTransport(), hasher=test_hasher
    )
    for forbidden_url in (
        "https://www.instagram.com/instagram/",
        "https://www.instagram.com/explore/search/keyword/?q=%E5%8F%B0%E8%82%A1",
        "https://www.instagram.com/accounts/login/",
        "https://www.instagram.com/p/ABC123/",
        "https://www.instagram.com/reel/XYZ789/",
    ):
        with pytest.raises(CollectorConfigError):
            spider.collect(url=forbidden_url)


def test_cmoney_enabled_and_ptt_mirrors_disabled_by_default(
    tmp_path: Path, test_hasher: AuthorHasher
) -> None:
    config = load_collector_config(DEFAULT_POLICY_PATH, env={})
    cmoney = config.get_source("collector-tw-cmoney-forum")
    ptt_mirrors = config.get_source("collector-tw-ptt-mirrors")
    assert cmoney.enabled is True
    assert ptt_mirrors.enabled is False

    runner = CollectorRunner(
        config,
        root_dir=tmp_path,
        hasher=test_hasher,
        transport=FixtureTransport(),
    )
    with pytest.raises(SourceDisabledError, match="disabled by default"):
        runner.run_source("collector-tw-ptt-mirrors", run_id="run-disabled-check")


@pytest.mark.parametrize(
    ("source_id", "field_name", "bad_value", "expected_match"),
    [
        ("collector-tw-twse-t86", "max_connections", 2, "max_connections=1"),
        ("collector-tw-twse-t86", "min_interval_seconds", 4.9, "min_interval_seconds must be >= 5.0"),
        ("collector-tw-twse-price-history", "max_connections", 3, "max_connections=1"),
        ("collector-tw-twse-price-history", "min_interval_seconds", 1.0, "min_interval_seconds must be >= 5.0"),
        ("collector-tw-tpex-price-history", "max_connections", 2, "max_connections=1"),
        ("collector-tw-tpex-price-history", "min_interval_seconds", 2.0, "min_interval_seconds must be >= 5.0"),
        (
            "collector-tw-twse-price-history",
            "backfill_page_granularity",
            "per_symbol_monthly",
            "per_date_all_stocks",
        ),
    ],
)
def test_exchange_website_sources_enforce_single_connection_and_five_second_rate_limit(
    source_id: str, field_name: str, bad_value: object, expected_match: str
) -> None:
    raw = _raw_policy()
    raw["sources"][source_id][field_name] = bad_value
    with pytest.raises(CollectorConfigError, match=expected_match):
        validate_collector_config(raw, env={})


def test_dropped_tpex_flows_and_disabled_by_build_sources_are_refused() -> None:
    config = load_collector_config(DEFAULT_POLICY_PATH, env={})
    assert "collector-tw-tpex-flows" not in config.sources
    with pytest.raises(CollectorConfigError, match="dropped per DEV_PLAN"):
        config.get_source("collector-tw-tpex-flows")

    raw = copy.deepcopy(_raw_policy())
    raw["sources"]["collector-yahoo-news"] = copy.deepcopy(raw["sources"]["collector-news-rss"])
    raw["sources"]["collector-yahoo-news"]["source_id"] = "collector-yahoo-news"
    with pytest.raises(CollectorConfigError, match="DISABLED_BY_BUILD"):
        validate_collector_config(raw, env={})


@pytest.mark.parametrize("sid", ["collector-paper-arxiv", "collector-paper-openalex", "collector-gdelt"])
@pytest.mark.parametrize("terms", [None, [], "momentum", ["not-in-fixed-vocabulary"], ["momentum", "momentum"], ['momentum OR site:evil']])
def test_query_terms_fail_closed_at_startup(sid, terms):
    raw = _raw_policy()
    raw["sources"][sid]["query_terms"] = terms
    with pytest.raises(CollectorConfigError, match="fixed vocabulary"):
        validate_collector_config(raw, env={})


def test_missing_vocabulary_and_social_steering_fail_closed(tmp_path, monkeypatch):
    from collector_stack import config as module
    raw = _raw_policy()
    raw["sources"]["collector-tw-threads"]["query_terms"] = ["momentum"]
    with pytest.raises(CollectorConfigError, match="cannot be steered"):
        validate_collector_config(raw, env={})
    monkeypatch.setattr(module, "QUERY_VOCABULARY_PATH", tmp_path / "missing.json")
    with pytest.raises(CollectorConfigError, match="missing or invalid fixed query vocabulary"):
        validate_collector_config(_raw_policy(), env={})


@pytest.mark.parametrize("override", [
    {"base_urls": ["https://api.gdeltproject.org/api/v2/doc/doc"]},
    {"index_url": "https://other.invalid/lastupdate.txt"},
    {"feed_type": "gkg"}, {"cadence_seconds": 900}, {"max_records_per_call": 250},
    {"fetcher": "api"}, {"max_requests_per_run": 5}, {"min_interval_seconds": 0},
])
def test_gdelt_requires_single_official_hourly_raw_feed(override):
    data = _raw_policy()
    data["sources"]["collector-gdelt"].update(override)
    with pytest.raises(CollectorConfigError, match="official hourly export feed"):
        validate_collector_config(data, env={})


@pytest.mark.parametrize('sid', ['collector-tw-twse-index-breadth', 'collector-tw-twse-taiex-history'])
@pytest.mark.parametrize('field,value', [('min_interval_seconds', 1.0), ('max_connections', 2)])
def test_regime_sources_refuse_unsafe_policy_before_fetch(tmp_path, test_hasher, sid, field, value):
    raw = _raw_policy()
    raw['sources'][sid][field] = value
    config = validate_collector_config(raw, env={})
    transport = FixtureTransport()
    runner = CollectorRunner(config, root_dir=tmp_path, hasher=test_hasher, transport=transport)
    with pytest.raises(CollectorConfigError, match='max_connections=1.*min_interval_seconds >= 5.0'):
        runner.run_source(sid, run_id='unsafe')
    assert transport.calls == []
