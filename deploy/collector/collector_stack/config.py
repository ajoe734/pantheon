"""Collector stack configuration and start-up policy enforcement (SD §6.2, §6.3)."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

DEFAULT_POLICY_PATH = Path(__file__).resolve().parent.parent / "fetch-policy.json"

ALLOWED_SCHEMA_VERSIONS = frozenset({"collector_fetch_policy.v1"})
ALLOWED_FETCHERS = frozenset({"http", "browser", "api"})
ALLOWED_ROBOTS_DECISIONS = frozenset(
    {"allowed", "exempted_by_operator_2026-11-10", "not_applicable_api"}
)
OPERATOR_ROBOTS_EXEMPT_SOURCES = frozenset(
    {"collector-tw-threads", "collector-tw-instagram-popular"}
)
DISABLED_BY_BUILD_TOKENS = frozenset({"yahoo", "cnyes", "anue"})
DROPPED_SOURCE_IDS = frozenset({"collector-tw-tpex-flows"})

FORBIDDEN_GLOBAL_BOOL_FLAGS = (
    "stealth_fetching",
    "stealth",
    "headless_stealth",
    "cloudflare_turnstile_bypass",
    "cloudflare_bypass",
    "turnstile_bypass",
    "spoof_fingerprint",
    "proxy_rotation",
    "use_proxy",
    "captcha_solving",
    "solve_captcha",
    "logged_in_sessions",
    "logged_in",
    "allow_login",
    "cookie_reuse",
)

FORBIDDEN_NONEMPTY_KEYS = (
    "proxies",
    "proxy_url",
    "residential_proxy",
    "captcha_solver",
    "captcha_api_key",
    "captcha_provider",
    "cookies",
    "session_cookie",
    "auth_cookie",
    "cookie_jar",
    "login_username",
    "login_password",
    "account_credentials",
)

FORBIDDEN_ENV_VARS = (
    "COLLECTOR_STEALTH",
    "COLLECTOR_PROXY_URL",
    "COLLECTOR_PROXIES",
    "COLLECTOR_CAPTCHA_KEY",
    "COLLECTOR_CAPTCHA_SOLVER",
    "COLLECTOR_SESSION_COOKIE",
    "COLLECTOR_LOGIN_USER",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


class CollectorConfigError(ValueError):
    """Raised when the collector configuration violates start-up safety policies."""


@dataclass(frozen=True)
class SourceFetchPolicy:
    source_id: str
    section_ref: str
    description: str
    base_urls: tuple[str, ...]
    cadence: str
    cadence_seconds: int
    max_requests_per_run: int
    min_interval_seconds: float
    fetcher: str
    respect_robots: bool
    robots_decision: str
    terms_ref: str
    attribution_required: bool
    attribution: str
    logged_in: bool
    enabled: bool
    raw_config: Mapping[str, Any] = field(default_factory=dict)

    @property
    def policy_hash(self) -> str:
        canonical = json.dumps(dict(self.raw_config), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def validate_target_url(self, url: str) -> None:
        parsed = urlparse(url)
        lowered = url.lower()
        for token in DISABLED_BY_BUILD_TOKENS:
            if token in lowered:
                raise CollectorConfigError(
                    f"source {self.source_id} target URL {url!r} references DISABLED_BY_BUILD source {token!r}"
                )
        if self.source_id == "collector-library-books":
            if parsed.scheme not in ("file", ""):
                raise CollectorConfigError(
                    f"collector-library-books only permits local operator files, got URL {url!r}"
                )
            return
        if not any(url.startswith(base) for base in self.base_urls):
            raise CollectorConfigError(
                f"source {self.source_id} target URL {url!r} is outside allowed base_urls {self.base_urls}"
            )
        if self.source_id == "collector-tw-instagram-popular":
            path = parsed.path or ""
            allowed_prefixes = tuple(
                self.raw_config.get("allowed_path_prefixes") or ("/explore/tags/", "/popular/")
            )
            forbidden_prefixes = tuple(
                self.raw_config.get("forbidden_path_prefixes")
                or ("/explore/search/", "/accounts/login", "/p/", "/reel/")
            )
            if any(path.startswith(fp) for fp in forbidden_prefixes) or not any(
                path.startswith(ap) for ap in allowed_prefixes
            ):
                raise CollectorConfigError(
                    f"Instagram source only permits logged-out popular tag pages {allowed_prefixes}, got {url!r}"
                )


@dataclass(frozen=True)
class CollectorConfig:
    schema_version: str
    collector_version: str
    purpose: str
    contact_user_agent: str
    browser_user_agent_mode: str
    raw_retention_days: int
    auto_pause_consecutive_blocks: int
    disabled_by_build_sources: tuple[str, ...]
    dropped_sources: tuple[str, ...]
    sources: Mapping[str, SourceFetchPolicy]

    def get_source(self, source_id: str) -> SourceFetchPolicy:
        if source_id in DROPPED_SOURCE_IDS or source_id in self.dropped_sources:
            raise CollectorConfigError(
                f"source {source_id!r} was dropped per DEV_PLAN §2 (uses TPEx OpenAPI directly)"
            )
        if source_id not in self.sources:
            raise CollectorConfigError(f"unknown collector source_id: {source_id!r}")
        return self.sources[source_id]


def _check_forbidden_settings(mapping: Mapping[str, Any], *, context: str) -> None:
    for flag in FORBIDDEN_GLOBAL_BOOL_FLAGS:
        if bool(mapping.get(flag, False)):
            raise CollectorConfigError(
                f"start-up refused: forbidden setting {flag}=true in {context} "
                "(stealth fetching, proxy rotation, CAPTCHA solving, and logged-in sessions are prohibited)"
            )
    for key in FORBIDDEN_NONEMPTY_KEYS:
        val = mapping.get(key)
        if val not in (None, False, "", [], (), {}):
            raise CollectorConfigError(
                f"start-up refused: forbidden non-empty setting {key} in {context} "
                "(proxies, CAPTCHA solvers, and session cookies/credentials are prohibited)"
            )
    fetcher = str(mapping.get("fetcher") or "").strip().lower()
    if fetcher in {"stealth", "stealthy", "camoufox", "undetected", "proxy"}:
        raise CollectorConfigError(
            f"start-up refused: forbidden fetcher={fetcher!r} in {context}"
        )


def _check_environment(env: Mapping[str, str] | None = None) -> None:
    environ = os.environ if env is None else env
    for var in FORBIDDEN_ENV_VARS:
        value = str(environ.get(var) or "").strip()
        if value and value.lower() not in {"0", "false", "no", "off"}:
            raise CollectorConfigError(
                f"start-up refused: forbidden environment variable {var} is set"
            )


def validate_collector_config(
    raw: Mapping[str, Any],
    *,
    env: Mapping[str, str] | None = None,
) -> CollectorConfig:
    """Validate collector configuration at start-up; fail closed on any policy violation."""

    if not isinstance(raw, Mapping):
        raise CollectorConfigError("collector configuration must be a JSON object")

    _check_environment(env)
    _check_forbidden_settings(raw, context="global config")

    schema_version = str(raw.get("schema_version") or "").strip()
    if schema_version not in ALLOWED_SCHEMA_VERSIONS:
        raise CollectorConfigError(f"unsupported collector config schema_version: {schema_version!r}")

    collector_version = str(raw.get("collector_version") or "").strip()
    if not collector_version:
        raise CollectorConfigError("collector_version is required")

    purpose = str(raw.get("purpose") or "").strip()
    if purpose != "aggregate market attention and sentiment research":
        raise CollectorConfigError(
            "purpose must equal 'aggregate market attention and sentiment research' per SD §6.4"
        )

    browser_ua_mode = str(raw.get("browser_user_agent_mode") or "").strip()
    if browser_ua_mode != "stock_chromium":
        raise CollectorConfigError(
            f"browser_user_agent_mode must be 'stock_chromium' (no fingerprint spoofing), got {browser_ua_mode!r}"
        )

    contact_ua = str(raw.get("contact_user_agent") or "").strip()
    if not contact_ua or "PantheonCollector" not in contact_ua:
        raise CollectorConfigError("contact_user_agent must identify PantheonCollector with contact address")

    raw_retention_days = int(raw.get("raw_retention_days") or 0)
    if raw_retention_days <= 0 or raw_retention_days > 90:
        raise CollectorConfigError("raw_retention_days must be between 1 and 90 per SD §6.4/§6.6")

    auto_pause_blocks = int(raw.get("auto_pause_consecutive_blocks") or 0)
    if auto_pause_blocks != 2:
        raise CollectorConfigError("auto_pause_consecutive_blocks must be 2 per SD §6.3")

    raw_sources = raw.get("sources")
    if not isinstance(raw_sources, Mapping) or not raw_sources:
        raise CollectorConfigError("sources mapping is required and cannot be empty")

    parsed_sources: dict[str, SourceFetchPolicy] = {}
    for key, entry in raw_sources.items():
        if not isinstance(entry, Mapping):
            raise CollectorConfigError(f"source entry {key!r} must be an object")
        source_id = str(entry.get("source_id") or key).strip()
        if source_id != key:
            raise CollectorConfigError(f"source key {key!r} does not match source_id {source_id!r}")
        if source_id in DROPPED_SOURCE_IDS:
            raise CollectorConfigError(
                f"source {source_id!r} is dropped per DEV_PLAN §2 and must not be configured as an active source"
            )
        if any(tok in source_id.lower() for tok in DISABLED_BY_BUILD_TOKENS):
            raise CollectorConfigError(
                f"source {source_id!r} is DISABLED_BY_BUILD per 2026-09-02 licence decision"
            )

        _check_forbidden_settings(entry, context=f"source {source_id}")

        if entry.get("logged_in") is not False:
            raise CollectorConfigError(f"source {source_id} must explicitly set logged_in=false")

        fetcher = str(entry.get("fetcher") or "").strip()
        if fetcher not in ALLOWED_FETCHERS:
            raise CollectorConfigError(
                f"source {source_id} fetcher must be one of {sorted(ALLOWED_FETCHERS)}, got {fetcher!r}"
            )

        respect_robots = entry.get("respect_robots")
        if not isinstance(respect_robots, bool):
            raise CollectorConfigError(f"source {source_id} respect_robots must be a boolean")

        robots_decision = str(entry.get("robots_decision") or "").strip()
        if robots_decision not in ALLOWED_ROBOTS_DECISIONS:
            raise CollectorConfigError(
                f"source {source_id} robots_decision must be one of {sorted(ALLOWED_ROBOTS_DECISIONS)}"
            )
        if not respect_robots:
            if (
                source_id not in OPERATOR_ROBOTS_EXEMPT_SOURCES
                or robots_decision != "exempted_by_operator_2026-11-10"
            ):
                raise CollectorConfigError(
                    f"source {source_id} cannot disable respect_robots without operator exemption"
                )

        base_urls_raw = entry.get("base_urls")
        if not isinstance(base_urls_raw, list) or not base_urls_raw:
            raise CollectorConfigError(f"source {source_id} must declare a non-empty base_urls list")
        base_urls = tuple(str(u).strip() for u in base_urls_raw if str(u).strip())
        for u in base_urls:
            for tok in DISABLED_BY_BUILD_TOKENS:
                if tok in u.lower():
                    raise CollectorConfigError(
                        f"source {source_id} base_url {u!r} references DISABLED_BY_BUILD source {tok!r}"
                    )

        if source_id == "collector-tw-instagram-popular":
            if not entry.get("popular_tags_only", False):
                raise CollectorConfigError(
                    "collector-tw-instagram-popular must set popular_tags_only=true"
                )
            for u in base_urls:
                parsed = urlparse(u)
                if not (
                    parsed.path.startswith("/explore/tags/") or parsed.path.startswith("/popular/")
                ):
                    raise CollectorConfigError(
                        f"collector-tw-instagram-popular base_url {u!r} must be a popular/tag path only"
                    )

        terms_ref = str(entry.get("terms_ref") or "").strip()
        if not terms_ref:
            raise CollectorConfigError(f"source {source_id} terms_ref is required")

        attribution_required = bool(entry.get("attribution_required", False))
        attribution = str(entry.get("attribution") or "").strip()
        if attribution_required and not attribution:
            raise CollectorConfigError(
                f"source {source_id} requires non-empty attribution text when attribution_required=true"
            )

        max_requests = int(entry.get("max_requests_per_run") or 0)
        if max_requests <= 0:
            raise CollectorConfigError(f"source {source_id} max_requests_per_run must be > 0")

        min_interval = float(entry.get("min_interval_seconds", -1.0))
        if min_interval < 0.0:
            raise CollectorConfigError(f"source {source_id} min_interval_seconds must be >= 0")
        if source_id == "collector-paper-arxiv" and min_interval < 3.0:
            raise CollectorConfigError("collector-paper-arxiv min_interval_seconds must be >= 3.0 per arXiv ToU")

        cadence = str(entry.get("cadence") or "").strip()
        cadence_seconds = int(entry.get("cadence_seconds") or 86400)
        if not cadence or cadence_seconds <= 0:
            raise CollectorConfigError(f"source {source_id} must specify valid cadence and cadence_seconds")

        enabled = entry.get("enabled")
        if not isinstance(enabled, bool):
            raise CollectorConfigError(f"source {source_id} enabled must be a boolean")

        parsed_sources[source_id] = SourceFetchPolicy(
            source_id=source_id,
            section_ref=str(entry.get("section_ref") or ""),
            description=str(entry.get("description") or ""),
            base_urls=base_urls,
            cadence=cadence,
            cadence_seconds=cadence_seconds,
            max_requests_per_run=max_requests,
            min_interval_seconds=min_interval,
            fetcher=fetcher,
            respect_robots=respect_robots,
            robots_decision=robots_decision,
            terms_ref=terms_ref,
            attribution_required=attribution_required,
            attribution=attribution,
            logged_in=False,
            enabled=enabled,
            raw_config=dict(entry),
        )

    return CollectorConfig(
        schema_version=schema_version,
        collector_version=collector_version,
        purpose=purpose,
        contact_user_agent=contact_ua,
        browser_user_agent_mode=browser_ua_mode,
        raw_retention_days=raw_retention_days,
        auto_pause_consecutive_blocks=auto_pause_blocks,
        disabled_by_build_sources=tuple(str(x) for x in (raw.get("disabled_by_build_sources") or ())),
        dropped_sources=tuple(str(x) for x in (raw.get("dropped_sources") or ())),
        sources=parsed_sources,
    )


def load_collector_config(
    path: Path | str = DEFAULT_POLICY_PATH,
    *,
    env: Mapping[str, str] | None = None,
) -> CollectorConfig:
    """Load and validate fetch-policy.json from disk."""

    policy_path = Path(path)
    raw = json.loads(policy_path.read_text(encoding="utf-8"))
    return validate_collector_config(raw, env=env)
