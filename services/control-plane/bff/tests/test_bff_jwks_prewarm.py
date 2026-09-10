"""AGORA-BFF-JWKS-COLDSTART-20260830: JWKS cache pre-warm at BFF startup."""
from __future__ import annotations

import logging
import os
from unittest.mock import patch


def _prewarm_jwks_cache() -> None:
    """Populate the JWKS cache before /readyz, off the event loop thread."""
    jwks_uri = os.getenv("PANTHEON_BFF_JWKS_URI", "").strip()
    discovery_url = os.getenv("PANTHEON_BFF_OIDC_DISCOVERY_URL", "").strip()
    if not jwks_uri and not discovery_url:
        return
    try:
        import services.runtime_auth_inbound as auth_inbound
        if jwks_uri:
            auth_inbound._fetch_jwks_keys(jwks_uri)
        elif discovery_url:
            auth_inbound._fetch_jwks_keys(str(auth_inbound._fetch_oidc_metadata(discovery_url)["jwks_uri"]).strip())
    except Exception as exc:  # noqa: BLE001 - warm-up must never block startup
        logging.getLogger(__name__).warning("JWKS cache pre-warm failed: %s", exc)


def test_prewarm_noop_when_no_jwks_config(monkeypatch) -> None:
    monkeypatch.delenv("PANTHEON_BFF_JWKS_URI", raising=False)
    monkeypatch.delenv("PANTHEON_BFF_OIDC_DISCOVERY_URL", raising=False)

    with patch("services.runtime_auth_inbound._fetch_jwks_keys") as fetch_keys, patch(
        "services.runtime_auth_inbound._fetch_oidc_metadata"
    ) as fetch_meta:
        _prewarm_jwks_cache()

    fetch_keys.assert_not_called()
    fetch_meta.assert_not_called()


def test_prewarm_fetches_jwks_uri_directly(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_JWKS_URI", "https://idp.example.com/jwks.json")
    monkeypatch.delenv("PANTHEON_BFF_OIDC_DISCOVERY_URL", raising=False)

    with patch("services.runtime_auth_inbound._fetch_jwks_keys") as fetch_keys:
        _prewarm_jwks_cache()

    fetch_keys.assert_called_once_with("https://idp.example.com/jwks.json")


def test_prewarm_resolves_discovery_then_jwks(monkeypatch) -> None:
    monkeypatch.delenv("PANTHEON_BFF_JWKS_URI", raising=False)
    monkeypatch.setenv("PANTHEON_BFF_OIDC_DISCOVERY_URL", "https://idp.example.com/.well-known/openid-configuration")

    with patch(
        "services.runtime_auth_inbound._fetch_oidc_metadata",
        return_value={"jwks_uri": "https://idp.example.com/resolved-jwks.json"},
    ) as fetch_meta, patch("services.runtime_auth_inbound._fetch_jwks_keys") as fetch_keys:
        _prewarm_jwks_cache()

    fetch_meta.assert_called_once_with("https://idp.example.com/.well-known/openid-configuration")
    fetch_keys.assert_called_once_with("https://idp.example.com/resolved-jwks.json")


def test_prewarm_swallows_fetch_failure(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_BFF_JWKS_URI", "https://idp.example.com/jwks.json")
    monkeypatch.delenv("PANTHEON_BFF_OIDC_DISCOVERY_URL", raising=False)

    with patch(
        "services.runtime_auth_inbound._fetch_jwks_keys",
        side_effect=RuntimeError("network unreachable"),
    ):
        _prewarm_jwks_cache()  # must not raise


def test_prewarm_populates_the_real_cache_read_by_request_handling(monkeypatch) -> None:
    """End-to-end: after pre-warm, a request-time fetch is a cache hit, not a network call."""
    import services.runtime_auth_inbound as auth_inbound

    uri = "https://idp.example.com/jwks-e2e.json"
    monkeypatch.setenv("PANTHEON_BFF_JWKS_URI", uri)
    monkeypatch.delenv("PANTHEON_BFF_OIDC_DISCOVERY_URL", raising=False)
    auth_inbound._JWKS_CACHE.pop(uri, None)

    fake_keys = [{"kid": "k1", "kty": "RSA"}]
    with patch("urllib.request.urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value.read.return_value = (
            b'{"keys": [{"kid": "k1", "kty": "RSA"}]}'
        )
        _prewarm_jwks_cache()
        assert urlopen.call_count == 1

        # Request-time call after pre-warm must be served from cache: no second network call.
        result = auth_inbound._fetch_jwks_keys(uri)
        assert result == fake_keys
        assert urlopen.call_count == 1

    auth_inbound._JWKS_CACHE.pop(uri, None)
