"""Default-deny guard for outbound third-party fetches.

Pantheon's source-ingest and research connectors pull from public providers
(Yahoo chart, CoinGecko, TWSE/TPEx/TAIFEX, MOPS, FinMind, SEC/FRED/FINRA,
stooq). On a scheduled tick those become continuous crawling from a single
cloud egress IP, which is what a provider or cloud operator sees as abuse.

Non-production environments therefore deny external egress unless an operator
opts in explicitly. Internal service-to-service calls (loopback, private
ranges, compose service names) are never affected.

Modes, via ``PANTHEON_EXTERNAL_EGRESS``:

``deny``
    Block every external host. Default outside production.
``allowlist``
    Block every external host except those named in
    ``PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS`` (comma-separated; a bare
    registrable domain also matches its subdomains).
``allow``
    No restriction. Default when ``PANTHEON_ENV`` names a production
    environment.
"""

from __future__ import annotations

import ipaddress
import os
from typing import Mapping
from urllib.parse import urlsplit


MODE_ENV_VAR = "PANTHEON_EXTERNAL_EGRESS"
ALLOWED_HOSTS_ENV_VAR = "PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS"

MODE_ALLOW = "allow"
MODE_ALLOWLIST = "allowlist"
MODE_DENY = "deny"
VALID_MODES = (MODE_ALLOW, MODE_ALLOWLIST, MODE_DENY)

_PRODUCTION_ENV_VALUES = {"prod", "production", "live"}
_INTERNAL_HOST_SUFFIXES = (".internal", ".local", ".localdomain", ".svc", ".cluster.local")


class ExternalEgressBlocked(RuntimeError):
    """Raised when policy forbids an outbound request to a third-party host."""

    def __init__(self, url: str, host: str, mode: str, caller: str) -> None:
        self.url = url
        self.host = host
        self.mode = mode
        self.caller = caller
        super().__init__(
            f"External egress to {host!r} is blocked by policy "
            f"(mode={mode}, caller={caller}). Set {MODE_ENV_VAR}=allow, or "
            f"{MODE_ENV_VAR}=allowlist with {host} in {ALLOWED_HOSTS_ENV_VAR}, "
            f"to permit it. url={url}"
        )


def _env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def _clean(value: object) -> str:
    return str(value or "").strip()


def resolve_mode(env: Mapping[str, str] | None = None) -> str:
    """Return the active egress mode.

    An explicit ``PANTHEON_EXTERNAL_EGRESS`` always wins. Otherwise production
    keeps its existing unrestricted behaviour and every other environment —
    dev, staging, local, unset — denies.
    """

    source = _env(env)
    configured = _clean(source.get(MODE_ENV_VAR)).lower()
    if configured:
        if configured not in VALID_MODES:
            raise ValueError(f"{MODE_ENV_VAR} must be one of {', '.join(VALID_MODES)}; got {configured!r}")
        return configured
    environment = _clean(source.get("PANTHEON_ENV")).lower()
    if environment in _PRODUCTION_ENV_VALUES or environment.startswith("prod"):
        return MODE_ALLOW
    return MODE_DENY


def allowed_hosts(env: Mapping[str, str] | None = None) -> frozenset[str]:
    """Return the normalized allowlist entries."""

    raw = _clean(_env(env).get(ALLOWED_HOSTS_ENV_VAR))
    entries = (_clean(entry).lower().lstrip(".") for entry in raw.split(","))
    return frozenset(entry for entry in entries if entry)


def is_internal_host(host: str) -> bool:
    """Return True for hosts that are not third-party network destinations."""

    candidate = _clean(host).lower().strip("[]")
    if not candidate:
        return False
    if candidate == "localhost" or candidate.endswith(".localhost"):
        return True
    if candidate.endswith(_INTERNAL_HOST_SUFFIXES):
        return True
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        # Single-label names are compose/Kubernetes service names, never public.
        return "." not in candidate
    return bool(address.is_loopback or address.is_private or address.is_link_local or address.is_unspecified)


def _host_allowed(host: str, permitted: frozenset[str]) -> bool:
    candidate = host.lower()
    return any(candidate == entry or candidate.endswith(f".{entry}") for entry in permitted)


def external_egress_allowed(url: str, env: Mapping[str, str] | None = None) -> bool:
    """Return True when policy permits an outbound request to ``url``."""

    host = _clean(urlsplit(url).hostname)
    if not host or is_internal_host(host):
        return True
    mode = resolve_mode(env)
    if mode == MODE_ALLOW:
        return True
    if mode == MODE_ALLOWLIST:
        return _host_allowed(host, allowed_hosts(env))
    return False


def guard_external_url(url: str, *, caller: str, env: Mapping[str, str] | None = None) -> str:
    """Return ``url`` when policy permits it, otherwise raise.

    Every connector that reaches a third-party provider must route its request
    through this function so the deny decision cannot be bypassed by adding a
    new endpoint constant.
    """

    if external_egress_allowed(url, env):
        return url
    host = _clean(urlsplit(url).hostname)
    raise ExternalEgressBlocked(url=url, host=host, mode=resolve_mode(env), caller=caller)
