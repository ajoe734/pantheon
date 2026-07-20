import pytest

from services.external_egress import (
    ExternalEgressBlocked,
    allowed_hosts,
    external_egress_allowed,
    guard_external_url,
    is_internal_host,
    resolve_mode,
)


def test_non_production_defaults_to_deny():
    assert resolve_mode({}) == "deny"
    assert resolve_mode({"PANTHEON_ENV": "dev"}) == "deny"
    assert resolve_mode({"PANTHEON_ENV": "staging-live"}) == "deny"


def test_production_keeps_unrestricted_egress():
    assert resolve_mode({"PANTHEON_ENV": "prod"}) == "allow"
    assert resolve_mode({"PANTHEON_ENV": "production"}) == "allow"


def test_explicit_mode_overrides_environment():
    assert resolve_mode({"PANTHEON_ENV": "prod", "PANTHEON_EXTERNAL_EGRESS": "deny"}) == "deny"
    assert resolve_mode({"PANTHEON_ENV": "dev", "PANTHEON_EXTERNAL_EGRESS": "allow"}) == "allow"


def test_invalid_mode_is_rejected():
    with pytest.raises(ValueError):
        resolve_mode({"PANTHEON_EXTERNAL_EGRESS": "off"})


@pytest.mark.parametrize(
    "host",
    ["localhost", "127.0.0.1", "::1", "10.50.0.21", "192.168.1.4", "source-ingest", "postgres", "vm.internal"],
)
def test_internal_hosts_are_recognized(host):
    assert is_internal_host(host) is True


@pytest.mark.parametrize("host", ["query1.finance.yahoo.com", "api.coingecko.com", "8.8.8.8"])
def test_third_party_hosts_are_not_internal(host):
    assert is_internal_host(host) is False


def test_internal_traffic_is_never_blocked():
    env = {"PANTHEON_ENV": "dev"}
    assert external_egress_allowed("http://source-ingest:8097/api/source-ingest/run-scheduled", env)
    assert external_egress_allowed("http://127.0.0.1:8097/readyz", env)
    guard_external_url("http://postgres:5432/", caller="test", env=env)


def test_dev_blocks_third_party_fetch():
    env = {"PANTHEON_ENV": "dev"}
    with pytest.raises(ExternalEgressBlocked) as excinfo:
        guard_external_url(
            "https://query1.finance.yahoo.com/v8/finance/chart/SPY?interval=1d",
            caller="source_ingest.us_public",
            env=env,
        )
    error = excinfo.value
    assert error.host == "query1.finance.yahoo.com"
    assert error.mode == "deny"
    assert "PANTHEON_EXTERNAL_EGRESS" in str(error)


def test_allowlist_admits_only_named_domains():
    env = {
        "PANTHEON_ENV": "dev",
        "PANTHEON_EXTERNAL_EGRESS": "allowlist",
        "PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS": "twse.com.tw, api.finmindtrade.com",
    }
    assert allowed_hosts(env) == frozenset({"twse.com.tw", "api.finmindtrade.com"})
    # Bare registrable domain also admits its subdomains.
    guard_external_url("https://openapi.twse.com.tw/v1/exchangeReport", caller="test", env=env)
    guard_external_url("https://api.finmindtrade.com/api/v4/data", caller="test", env=env)
    with pytest.raises(ExternalEgressBlocked):
        guard_external_url("https://api.coingecko.com/api/v3/coins", caller="test", env=env)


def test_allowlist_does_not_match_suffix_lookalike_domains():
    env = {
        "PANTHEON_EXTERNAL_EGRESS": "allowlist",
        "PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS": "twse.com.tw",
    }
    with pytest.raises(ExternalEgressBlocked):
        guard_external_url("https://eviltwse.com.tw/data", caller="test", env=env)


def test_allow_mode_permits_third_party_fetch():
    env = {"PANTHEON_ENV": "dev", "PANTHEON_EXTERNAL_EGRESS": "allow"}
    assert guard_external_url("https://api.coingecko.com/api/v3/coins", caller="test", env=env)


def test_connector_fetch_helpers_are_guarded(monkeypatch):
    """Every third-party connector must deny by default, not just the module."""
    from services.source_ingestion import configured
    from services.source_ingestion.connectors import (
        crypto_coingecko,
        finmind_taiwan,
        taiwan_official,
        us_public,
    )

    monkeypatch.delenv("PANTHEON_EXTERNAL_EGRESS", raising=False)
    monkeypatch.setenv("PANTHEON_ENV", "dev")

    with pytest.raises(ExternalEgressBlocked):
        crypto_coingecko._request_json("https://api.coingecko.com/api/v3/coins/markets")
    with pytest.raises(ExternalEgressBlocked):
        us_public._request_json("https://data.sec.gov/submissions/CIK0000320193.json", user_agent="test")
    with pytest.raises(ExternalEgressBlocked):
        us_public._request_text("https://stooq.com/q/d/l/?s=spy.us")
    with pytest.raises(ExternalEgressBlocked):
        configured._assert_robots_allowed("https://stooq.com/q/d/l/", ("https://stooq.com/",), 5.0)
    assert taiwan_official.TWSE_OPENAPI_BASE_URL.startswith("https://")
    assert finmind_taiwan.FINMIND_BASE_URL.startswith("https://")
