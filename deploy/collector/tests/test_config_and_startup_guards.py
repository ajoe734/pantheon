"""Tests for start-up configuration validation and safety guards (AC1, AC6, AC7)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from collector_stack.cli import main as cli_main
from collector_stack.config import (
    DEFAULT_POLICY_PATH,
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
    assert "collector-tw-cmoney-forum" in out["disabled_sources"]
    assert "collector-tw-ptt-mirrors" in out["disabled_sources"]


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


def test_cmoney_and_ptt_mirrors_are_present_and_disabled_by_default(
    tmp_path: Path, test_hasher: AuthorHasher
) -> None:
    config = load_collector_config(DEFAULT_POLICY_PATH, env={})
    cmoney = config.get_source("collector-tw-cmoney-forum")
    ptt_mirrors = config.get_source("collector-tw-ptt-mirrors")
    assert cmoney.enabled is False
    assert ptt_mirrors.enabled is False

    runner = CollectorRunner(
        config,
        root_dir=tmp_path,
        hasher=test_hasher,
        transport=FixtureTransport(),
    )
    for disabled_id in ("collector-tw-cmoney-forum", "collector-tw-ptt-mirrors"):
        with pytest.raises(SourceDisabledError, match="disabled by default"):
            runner.run_source(disabled_id, run_id="run-disabled-check")


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
