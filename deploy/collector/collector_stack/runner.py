"""Orchestration runner connecting config, auto-pause state, spiders/pollers, and bundle writer."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .bundle import BundleWriter, CollectedItem, WrittenBundle, _atomic_write_json, utc_iso
from .config import CollectorConfig, CollectorConfigError, SourceFetchPolicy
from .fetchers import FetchAccessBlockedError, FetchBackendUnavailableError, FetchTransport, LiveScraplingTransport
from .pollers import (
    ArxivPaperPoller,
    CbcMacroPoller,
    DbnomicsMacroPoller,
    DoclingLibraryPoller,
    GdeltRawFeedPoller,
    GeminiVideoNotesPoller,
    NdcIndicatorsPoller,
    OpenAlexPaperPoller,
    RssHubNewsPoller,
    RssHubPaperPoller,
    TwseOpenApiPoller,
    YouTubeDataApiPoller,
)
from .privacy import AuthorHasher
from .spiders import (
    CmoneyForumSpider,
    DgbasMacroSpider,
    InstagramPopularTagSpider,
    PttMirrorSpider,
    PttStockSpider,
    ThreadsWebSpider,
    TpexDailyPriceHistorySpider,
    TwseDailyPriceHistorySpider,
    TwseIndexBreadthSpider,
    TaifexVixSpider,
    TwseTaiexHistorySpider,
    TwseT86DailySpider,
    TW_EXCHANGE_HOLIDAYS,
)
from .state import CollectorStateStore, SourceRuntimeState


class SourceDisabledError(CollectorConfigError):
    """Raised when attempting to run a source that is disabled in fetch-policy.json."""


class SourcePausedError(RuntimeError):
    """Raised when attempting to run a source that was auto-paused after consecutive blocks."""

logger = logging.getLogger("collector_stack.runner")


@dataclass(frozen=True)
class RunOutcome:
    source_id: str
    run_id: str
    status: str
    bundle: WrittenBundle
    state: SourceRuntimeState
    skipped_count: int = 0


LISTING_SOURCE_ID = "collector-tw-security-listing"

LISTING_FILTERED_SOURCES = frozenset(
    {
        "collector-tw-twse-t86",
        "collector-tw-twse-price-history",
        "collector-tw-tpex-price-history",
        "collector-tw-trading-suspension",
    }
)


_LISTING_REL = Path("services") / "source_ingestion" / "security_listing.py"


def _security_listing_candidates(runner_file: Path) -> list[Path]:
    """Where the shared module lives: the image layout (runner at <app>/collector_stack/runner.py, module copied
    to <app>/services/source_ingestion/) first, then the repo checkout (runner at deploy/collector/collector_stack/).
    Never indexes past the filesystem root (a shallow path used to raise IndexError)."""
    parents = runner_file.parents
    return [parents[n] / _LISTING_REL for n in (1, 3) if n < len(parents)]


def load_security_listing_module() -> Any:
    """Load the ONE shared parser (services/source_ingestion/security_listing.py, stdlib only) by path, so the
    collector host and hosted run the same code. Override with PANTHEON_SECURITY_LISTING_MODULE."""
    import importlib
    import importlib.util

    if not os.environ.get("PANTHEON_SECURITY_LISTING_MODULE"):
        try:  # repo checkout / hosted image: the very same module object
            return importlib.import_module("services.source_ingestion.security_listing")
        except ImportError:
            pass
    override = os.environ.get("PANTHEON_SECURITY_LISTING_MODULE")
    candidates = [Path(override)] if override else _security_listing_candidates(Path(__file__).resolve())
    path = next((c for c in candidates if c.is_file()), None)
    if path is None:
        looked = ", ".join(str(c) for c in candidates)
        raise CollectorConfigError(f"security_listing module not found at {looked}")
    spec = importlib.util.spec_from_file_location("security_listing", path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    import sys

    sys.modules.setdefault("security_listing", mod)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


class CollectorRunner:
    """Executes one collector source run with policy enforcement, auto-pause, and bundle output."""

    def __init__(
        self,
        config: CollectorConfig,
        *,
        root_dir: Path | str,
        state_file: Path | str | None = None,
        library_dir: Path | str | None = None,
        hasher: AuthorHasher,
        transport: FetchTransport | None = None,
        listing_state_path: Path | str | None = None,
        warrants_mode: str | None = None,
    ) -> None:
        self.config = config
        # SOURCE-WARRANT-LISTING-EXPIRY: warrants are collected while listed (listed_only); "off" is the
        # rollback to the previous behaviour (no listing filter at all).
        self.warrants_mode = (warrants_mode or os.environ.get("COLLECTOR_WARRANTS_MODE") or "listed_only").strip()
        if self.warrants_mode not in {"listed_only", "off"}:
            raise CollectorConfigError(f"warrants mode must be listed_only or off, got {self.warrants_mode!r}")
        self.root_dir = Path(root_dir)
        self.library_dir = Path(library_dir) if library_dir else (self.root_dir / "library" / "books")
        self.state_store = CollectorStateStore(
            state_file or (self.root_dir / "state" / "collector-state.json"),
            auto_pause_threshold=config.auto_pause_consecutive_blocks,
        )
        self.bundle_writer = BundleWriter(
            self.root_dir, collector_version=config.collector_version
        )
        self.hasher = hasher
        self.transport = transport or LiveScraplingTransport()
        self.listing_state_path = Path(listing_state_path) if listing_state_path else (
            self.root_dir / "state" / "security-listing.json"
        )

    def listing_symbols(self, today: str) -> list[str] | None:
        """Allowed symbols for the listing filter, or None while the listing was never initialised on this
        host (rollout shadow: unfiltered, logged; the daily refresh initialises it). Once a listing exists it
        is fail closed: older than 3 trading days raises ListingUnavailable (``listing_unavailable``)."""
        sl = load_security_listing_module()
        state = sl.ListingState.load(self.listing_state_path)
        if state.last_success_date is None:
            logger.warning("security listing not initialised at %s: collecting unfiltered", self.listing_state_path)
            return None
        state.require_collectable(today)
        # listed now, plus every symbol ever listed as an ordinary share: a delisted share keeps its daily
        # history (survivorship); only expired warrants and codes in no listing are skipped.
        return sorted(state.listed_symbols() | set(state.ordinary_share_registry))

    def refresh_security_listing(self, fetch_page: Callable[[str], str], *, listing_date: str) -> dict[str, Any]:
        sl = load_security_listing_module()
        state = sl.ListingState.load(self.listing_state_path)
        result = sl.refresh_listing(state, fetch_page, listing_date=listing_date, state_path=self.listing_state_path)
        if result.get("ok"):
            result["published"] = self.publish_security_listing()
        return result

    def listing_fetch_policy(self) -> SourceFetchPolicy:
        """Policy for the two ISIN pages: same checks as any source (base_urls allow list, <= 1 connection,
        >= 5 s apart, collector contact User-Agent, no cookies). The URLs come from fetch-policy.json
        ``warrants.listing_sources`` through the module constants, so there is one list."""
        sl = load_security_listing_module()
        urls = (sl.TWSE_LISTED_URL, sl.TPEX_URL)
        return SourceFetchPolicy(
            source_id=LISTING_SOURCE_ID, section_ref="4.3",
            description="TWSE ISIN security listing pages (strMode=2 listed, strMode=4 OTC), once per trading day",
            base_urls=urls, cadence="daily", cadence_seconds=86400, max_requests_per_run=2,
            min_interval_seconds=5.0, fetcher="http", respect_robots=True, robots_decision="allowed",
            terms_ref="https://isin.twse.com.tw/", attribution_required=True,
            attribution="臺灣證券交易所國際證券辨識號碼公告 (ISIN)", logged_in=False, enabled=True, max_connections=1,
            raw_config={"source_id": LISTING_SOURCE_ID, "base_urls": list(urls)},
        )

    def refresh_listing_for_day(
        self, listing_date: str, *, sleep_fn: Callable[[float], None] | None = None, now: datetime | None = None
    ) -> dict[str, Any]:
        """CLI entry: refresh + publish the listing for one trading day, idempotent, outcome in status.json.

        ok=True/skipped covers: already refreshed and published for this day (no fetch), or not a trading day.
        Failures keep the previous allowlist (fail closed) and are recorded as status ``failed``."""
        from .fetchers import PolicyEnforcedFetcher
        from .spiders import TW_EXCHANGE_HOLIDAYS

        sl = load_security_listing_module()
        now_iso = utc_iso(now)
        day = datetime.strptime(listing_date, "%Y-%m-%d").date()
        run_id = f"listing-{listing_date}"

        def record(status: str, reason: str | None) -> None:
            self.bundle_writer.update_host_status(
                source_id=LISTING_SOURCE_ID, status=status, last_run_at=now_iso, last_run_id=run_id, reason=reason,
            )

        if day.weekday() >= 5 or listing_date in TW_EXCHANGE_HOLIDAYS:
            result: dict[str, Any] = {"ok": True, "skipped": "not_a_trading_day", "listing_date": listing_date}
            record("skipped", "not_a_trading_day")
            return result
        state = sl.ListingState.load(self.listing_state_path)
        target = self.root_dir / sl.BUNDLE_STATE_PATH
        if state.last_success_date and state.last_success_date > listing_date:
            result = {"ok": False, "reason": f"listing already at newer day {state.last_success_date}",
                      "listing_date": listing_date}
            record("failed", result["reason"])
            return result
        if state.last_success_date == listing_date:
            try:
                same = target.read_bytes() == self.listing_state_path.read_bytes()
            except OSError:
                same = False
            if same:
                result = {"ok": True, "skipped": "already_refreshed", "listing_date": listing_date}
                record("ok", "already_refreshed")
                return result
            result = self.publish_security_listing()
            result.update(listing_date=listing_date, republished=True)
            record("ok" if result.get("ok") else "failed", None if result.get("ok") else result.get("reason"))
            return result

        fetcher = PolicyEnforcedFetcher(
            self.listing_fetch_policy(), transport=self.transport,
            contact_user_agent=self.config.contact_user_agent, sleep_fn=sleep_fn,
        )

        def fetch_page(url: str) -> str:
            response = fetcher.fetch(url)
            if response.http_status != 200:
                raise RuntimeError(f"http {response.http_status} for {url}")
            return sl.decode_isin_bytes(response.body)

        result = self.refresh_security_listing(fetch_page, listing_date=listing_date)
        published = result.get("published") or {}
        ok = bool(result.get("ok")) and bool(published.get("ok"))
        result["ok"] = ok
        record("ok" if ok else "failed", None if ok else str(result.get("reason") or published.get("reason")))
        return result

    def publish_security_listing(self) -> dict[str, Any]:
        """Publish the dated listing state in the bundle tree (``bundles/security-listing.json``) so hosted reads it
        through the read only bundle server it already pulls from: one fetch (here), one dated listing, no exchange
        fetch from hosted. The bytes are the state file's bytes, replaced in one step; never more than the size cap."""
        sl = load_security_listing_module()
        try:
            data = self.listing_state_path.read_bytes()
        except OSError as exc:
            return {"ok": False, "reason": f"listing state not readable: {exc}"}
        if len(data) > sl.MAX_STATE_BYTES:
            return {"ok": False, "reason": f"listing state is {len(data)} bytes, over the {sl.MAX_STATE_BYTES} byte cap"}
        target = self.root_dir / sl.BUNDLE_STATE_PATH
        sl.write_state_bytes(target, data)
        return {"ok": True, "path": sl.BUNDLE_STATE_PATH, "bytes": len(data)}

    def build_collector(self, policy: SourceFetchPolicy) -> Any:
        sid = policy.source_id
        ua = self.config.contact_user_agent
        if sid == "collector-tw-threads":
            return ThreadsWebSpider(
                policy, transport=self.transport, hasher=self.hasher, contact_user_agent=ua
            )
        if sid == "collector-tw-instagram-popular":
            return InstagramPopularTagSpider(
                policy, transport=self.transport, hasher=self.hasher, contact_user_agent=ua
            )
        if sid == "collector-tw-cmoney-forum":
            return CmoneyForumSpider(
                policy, transport=self.transport, hasher=self.hasher, contact_user_agent=ua
            )
        if sid == "collector-tw-ptt-stock":
            return PttStockSpider(
                policy, transport=self.transport, hasher=self.hasher, contact_user_agent=ua
            )
        if sid == "collector-tw-ptt-mirrors":
            return PttMirrorSpider(
                policy, transport=self.transport, hasher=self.hasher, contact_user_agent=ua
            )
        if sid == "collector-tw-macro-dgbas":
            return DgbasMacroSpider(
                policy, transport=self.transport, contact_user_agent=ua
            )
        if sid == "collector-tw-macro-cbc":
            return CbcMacroPoller(
                policy, transport=self.transport, contact_user_agent=ua
            )
        if sid == "collector-tw-macro-ndc":
            return NdcIndicatorsPoller(
                policy, transport=self.transport, contact_user_agent=ua
            )
        if sid == "collector-tw-twse-openapi":
            return TwseOpenApiPoller(
                policy, transport=self.transport, contact_user_agent=ua
            )
        if sid == "collector-tw-twse-t86":
            return TwseT86DailySpider(
                policy, transport=self.transport, contact_user_agent=ua
            )
        if sid == "collector-tw-twse-price-history":
            return TwseDailyPriceHistorySpider(
                policy, transport=self.transport, contact_user_agent=ua
            )
        if sid == "collector-tw-tpex-price-history":
            return TpexDailyPriceHistorySpider(
                policy, transport=self.transport, contact_user_agent=ua
            )
        if sid in {TwseIndexBreadthSpider.source_id, TwseTaiexHistorySpider.source_id}:
            cls = TwseTaiexHistorySpider if sid == TwseTaiexHistorySpider.source_id else TwseIndexBreadthSpider
            return cls(policy, transport=self.transport, contact_user_agent=ua)
        if sid == TaifexVixSpider.source_id:
            return TaifexVixSpider(policy, transport=self.transport, contact_user_agent=ua,
                                   state_path=self.root_dir / "state" / "taifex-vix" / "ledger.json")
        if sid == "collector-gdelt":
            return GdeltRawFeedPoller(
                policy, transport=self.transport, contact_user_agent=ua,
                cache_dir=self.root_dir / "state" / "gdelt-feed"
            )
        if sid == "collector-macro-dbnomics":
            return DbnomicsMacroPoller(
                policy, transport=self.transport, contact_user_agent=ua
            )
        if sid == "collector-paper-arxiv":
            return ArxivPaperPoller(
                policy, transport=self.transport, contact_user_agent=ua
            )
        if sid == "collector-paper-openalex":
            return OpenAlexPaperPoller(
                policy, transport=self.transport, contact_user_agent=ua
            )
        if sid == "collector-paper-nber-rss":
            return RssHubPaperPoller(
                policy, transport=self.transport, contact_user_agent=ua
            )
        if sid == "collector-news-rss":
            return RssHubNewsPoller(
                policy, transport=self.transport, contact_user_agent=ua
            )
        if sid == "collector-youtube-finance":
            return YouTubeDataApiPoller(
                policy,
                transport=self.transport,
                hasher=self.hasher,
                state_store=self.state_store,
                contact_user_agent=ua,
            )
        if sid == "collector-youtube-gemini-notes":
            return GeminiVideoNotesPoller(
                policy,
                transport=self.transport,
                state_store=self.state_store,
                contact_user_agent=ua,
            )
        if sid == "collector-library-books":
            return DoclingLibraryPoller(policy, library_dir=self.library_dir)
        if sid == "collector-papers":
            return _CompositePaperCollector(
                config=self.config,
                transport=self.transport,
                contact_user_agent=ua,
            )
        raise CollectorConfigError(f"no spider or poller registered for source_id: {sid!r}")

    def run_source(
        self,
        source_id: str,
        *,
        run_id: str,
        started_at: datetime | str | None = None,
        finished_at: datetime | str | None = None,
        allow_disabled: bool = False,
        collect_kwargs: Mapping[str, Any] | None = None,
    ) -> RunOutcome:
        policy = self.config.get_source(source_id)
        if not policy.enabled and not allow_disabled:
            raise SourceDisabledError(
                f"source {source_id!r} is disabled by default in fetch-policy.json"
            )

        current_state = self.state_store.get_source_state(
            source_id, current_policy_hash=policy.policy_hash
        )
        if current_state.paused:
            raise SourcePausedError(
                f"source {source_id!r} is auto-paused ({current_state.paused_reason}); "
                "it resumes only when its fetch policy configuration changes"
            )

        started_iso = started_at if isinstance(started_at, str) else utc_iso(started_at)
        finished_iso = finished_at if isinstance(finished_at, str) else utc_iso(finished_at)

        collector = self.build_collector(policy)
        kwargs = dict(collect_kwargs or {})
        kwargs.setdefault("fetched_at", finished_iso)
        if source_id in LISTING_FILTERED_SOURCES and self.warrants_mode == "listed_only":
            # fail closed: raises ListingUnavailable (reason listing_unavailable) when the allowlist is
            # missing or older than 3 trading days; price collection stops, nothing is guessed.
            allowed = self.listing_symbols(finished_iso[:10])
            if allowed is not None:
                kwargs.setdefault("symbols_filter", allowed)

        try:
            items: Sequence[CollectedItem] = collector.collect(**kwargs)
        except FetchBackendUnavailableError as unavailable:
            logger.error("source %s failed: %s", source_id, unavailable)
            self.bundle_writer.write_bundle(
                policy=policy,
                run_id=run_id,
                items=(),
                status="partial",
                paused_reason=None,
                started_at=started_iso,
                finished_at=finished_iso,
                reason="fetch_failed",
            )
            self.bundle_writer.update_host_status(
                source_id=source_id,
                status="partial",
                last_run_at=finished_iso,
                last_run_id=run_id,
                paused_reason=None,
                cadence_seconds=policy.cadence_seconds,
                consecutive_blocks=current_state.consecutive_blocks,
                reason="fetch_failed",
            )
            raise
        except FetchAccessBlockedError as blocked:
            updated_state = self.state_store.record_run_outcome(
                source_id=source_id,
                run_id=run_id,
                finished_at=finished_iso,
                policy_hash=policy.policy_hash,
                block_reason=blocked.block_reason,
            )
            bundle_status = "paused" if updated_state.paused else "partial"
            paused_reason = updated_state.paused_reason if updated_state.paused else blocked.block_reason
            written = self.bundle_writer.write_bundle(
                policy=policy,
                run_id=run_id,
                items=(),
                status=bundle_status,
                paused_reason=paused_reason,
                started_at=started_iso,
                finished_at=finished_iso,
            )
            self.bundle_writer.update_host_status(
                source_id=source_id,
                status=bundle_status,
                last_run_at=finished_iso,
                last_run_id=run_id,
                paused_reason=paused_reason,
                cadence_seconds=policy.cadence_seconds,
                consecutive_blocks=updated_state.consecutive_blocks,
            )
            return RunOutcome(
                source_id=source_id,
                run_id=run_id,
                status=bundle_status,
                bundle=written,
                state=updated_state,
            )
        except FetchBackendUnavailableError:
            raise
        except Exception as parse_exc:  # noqa: BLE001 - a bad payload must still leave a manifest
            logger.error("source %s parse failed: %s", source_id, parse_exc)
            source_url = _first_request_url(collector) or (policy.base_urls[0] if policy.base_urls else None)
            written = self.bundle_writer.write_bundle(
                policy=policy, run_id=run_id, items=(), status="partial", paused_reason=None,
                started_at=started_iso, finished_at=finished_iso,
                reason=(f"parse_failed: {parse_exc}" if isinstance(collector, (TwseIndexBreadthSpider, TaifexVixSpider))
                        else "parse_failed"), source_url=source_url,
            )
            return RunOutcome(source_id=source_id, run_id=run_id, status="partial", bundle=written, state=current_state)

        skipped = _skipped_count(collector)
        route_errors = dict(getattr(collector, "route_errors", None) or {})
        all_routes_failed = bool(getattr(collector, "all_routes_failed", False))
        requests = _request_count(collector)
        reason: str | None = None
        if requests is not None and requests == 0:
            reason = "no_targets"  # nothing was requested, so nothing can be called complete
        elif all_routes_failed:
            reason = _all_routes_failed_reason(route_errors)
        elif requests is not None and not items:
            reason = _zero_item_reason(collector)
        bundle_status = "partial" if reason else "complete"
        if all_routes_failed:
            current_state.last_run_id = run_id
            current_state.last_run_at = finished_iso
            current_state.last_status = "partial"
            self.state_store.save_source_state(current_state)
            updated_state = current_state
        else:
            updated_state = self.state_store.record_run_outcome(
                source_id=source_id,
                run_id=run_id,
                finished_at=finished_iso,
                policy_hash=policy.policy_hash,
                block_reason=None,
            )
        written = self.bundle_writer.write_bundle(
            policy=policy,
            run_id=run_id,
            items=items,
            status=bundle_status,
            paused_reason=None,
            started_at=started_iso,
            finished_at=finished_iso,
            skipped_count=skipped,
            reason=reason,
        )
        if route_errors:
            manifest_with_errors = dict(written.manifest)
            manifest_with_errors["route_errors"] = dict(route_errors)
            _atomic_write_json(written.manifest_path, manifest_with_errors)
            written = WrittenBundle(
                source_id=written.source_id,
                date=written.date,
                run_id=written.run_id,
                bundle_dir=written.bundle_dir,
                manifest_path=written.manifest_path,
                items_path=written.items_path,
                manifest=manifest_with_errors,
            )
            for status_path in (self.root_dir / "status.json", self.bundle_writer.bundles_dir / "status.json"):
                if status_path.is_file():
                    status_payload = json.loads(status_path.read_text(encoding="utf-8"))
                    if isinstance(status_payload.get("sources"), dict) and source_id in status_payload["sources"]:
                        status_payload["sources"][source_id]["route_errors"] = dict(route_errors)
                        _atomic_write_json(status_path, status_payload)
        if isinstance(collector, GdeltRawFeedPoller):
            collector.acknowledge_bundle()
        if isinstance(collector, TaifexVixSpider) and bundle_status == "complete":
            collector.acknowledge_bundle()
        return RunOutcome(
            source_id=source_id,
            run_id=run_id,
            status=bundle_status,
            bundle=written,
            state=updated_state,
            skipped_count=skipped,
        )


def _all_routes_failed_reason(route_errors: Mapping[str, str]) -> str:
    if route_errors and all(str(err).startswith("invalid_xml") for err in route_errors.values()):
        return "parse_failed"
    return "fetch_failed"


def _first_request_url(collector: Any) -> str | None:
    for fetcher in _fetchers(collector):
        for entry in getattr(fetcher, "request_log", ()):
            if entry.get("url"):
                return str(entry["url"])
    return None


def _fetchers(collector: Any) -> list[Any]:
    parts = (collector, *(getattr(collector, n, None) for n in ("arxiv", "openalex", "nber")))
    return [f for f in (getattr(p, "fetcher", None) for p in parts) if f is not None]


def _zero_item_reason(collector: Any) -> str:
    """Why a run that issued requests produced no items.

    fetch_failed: a request errored or returned a non-2xx status.
    no_data_for_date: only when the collector positively confirmed the source has no data (no_data_confirmed).
    parse_zero: every response was fetched OK but nothing could be parsed into items.
    """
    if isinstance(collector, GdeltRawFeedPoller):
        return collector.empty_reason
    for fetcher in _fetchers(collector):
        for entry in getattr(fetcher, "request_log", ()):
            status = entry.get("status")
            if status is None or not 200 <= int(status) < 300:
                return "fetch_failed"
    if getattr(collector, "no_data_confirmed", False):
        return "no_data_for_date"
    return "parse_zero"


def _request_count(collector: Any) -> int | None:
    """Requests issued by a collector (composites sum parts); None when it has no fetcher (local library)."""
    total: int | None = None
    for part in (collector, *(getattr(collector, n, None) for n in ("arxiv", "openalex", "nber"))):
        fetcher = getattr(part, "fetcher", None)
        if fetcher is not None and hasattr(fetcher, "request_count"):
            total = (total or 0) + int(fetcher.request_count)
    return total


def _skipped_count(collector: Any) -> int:
    """Items a parser skipped as malformed/incomplete (composite collectors sum their parts)."""
    total = int(getattr(collector, "skipped_count", 0) or 0)
    for part in ("arxiv", "openalex", "nber"):
        total += int(getattr(getattr(collector, part, None), "skipped_count", 0) or 0)
    return total


class _CompositePaperCollector:
    """Runs arXiv, OpenAlex, and NBER RSSHub pollers under the composite collector-papers source_id."""

    def __init__(
        self,
        *,
        config: CollectorConfig,
        transport: FetchTransport,
        contact_user_agent: str,
    ) -> None:
        self.arxiv = ArxivPaperPoller(
            config.get_source("collector-paper-arxiv"),
            transport=transport,
            contact_user_agent=contact_user_agent,
        )
        self.openalex = OpenAlexPaperPoller(
            config.get_source("collector-paper-openalex"),
            transport=transport,
            contact_user_agent=contact_user_agent,
        )
        self.nber = RssHubPaperPoller(
            config.get_source("collector-paper-nber-rss"),
            transport=transport,
            contact_user_agent=contact_user_agent,
        )

    def collect(self, *, fetched_at: datetime | str | None = None) -> list[CollectedItem]:
        results: list[CollectedItem] = []
        results.extend(self.arxiv.collect(fetched_at=fetched_at))
        results.extend(self.openalex.collect(fetched_at=fetched_at))
        results.extend(self.nber.collect(fetched_at=fetched_at))
        return results


# --- Same-day close capture -------------------------------------------------------------------
# The OpenAPI daily feed only publishes a day's rows the next morning, so a timer fires `capture-close`
# every ten minutes from 14:40 to 17:00 Taipei. Each source is fetched at most once per trading day (a
# ledger marks completed dates); a failed or not-yet-published attempt is retried by the next tick until
# the window closes. One connection, one request per source, >= the policy ``min_interval_seconds``
# between the two sources.

CLOSE_CAPTURE_SOURCES = (
    "collector-tw-twse-price-history",
    "collector-tw-tpex-price-history",
)
TAIPEI = timezone(timedelta(hours=8))
WINDOW_START = dtime(14, 40)
WINDOW_END = dtime(17, 0)


@dataclass
class CloseCaptureResult:
    trade_date: str
    state: str  # outside_window | not_trading_day | attempted
    sources: dict[str, str] = field(default_factory=dict)

    @property
    def retry_needed(self) -> bool:
        return any(v not in {"captured", "already_captured", "disabled"} for v in self.sources.values())

    def to_dict(self) -> dict[str, Any]:
        return {"trade_date": self.trade_date, "state": self.state, "sources": dict(self.sources)}


def _listing_unavailable_types() -> tuple[type[BaseException], ...]:
    types: list[type[BaseException]] = []
    try:
        from services.source_ingestion.security_listing import ListingUnavailable

        types.append(ListingUnavailable)
    except Exception:  # noqa: BLE001
        pass
    try:
        cls = load_security_listing_module().ListingUnavailable
        if cls not in types:
            types.append(cls)
    except Exception:  # noqa: BLE001
        pass
    return tuple(types)


def _ledger_path(root: Path, source_id: str) -> Path:
    return root / "state" / "same-day-close" / f"{source_id}.json"


def _load(path: Path) -> dict[str, Any]:
    return dict(json.loads(path.read_text(encoding="utf-8")).get("completed", {})) if path.is_file() else {}


def _mark(path: Path, trade_date: str, run_id: str) -> None:
    done = _load(path)
    done[trade_date] = {"run_id": run_id}
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"completed": done}, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


def capture_same_day_close(
    runner: CollectorRunner,
    *,
    now: datetime,
    sources: Iterable[str] = CLOSE_CAPTURE_SOURCES,
    holidays: Iterable[str] = TW_EXCHANGE_HOLIDAYS,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> CloseCaptureResult:
    local = now.astimezone(TAIPEI)
    trade_date = local.date().isoformat()
    if local.weekday() >= 5 or trade_date in set(holidays):
        return CloseCaptureResult(trade_date, "not_trading_day")
    if not WINDOW_START <= local.time().replace(tzinfo=None) <= WINDOW_END:
        return CloseCaptureResult(trade_date, "outside_window")

    result = CloseCaptureResult(trade_date, "attempted")
    requested = False
    listing_exc_types = _listing_unavailable_types()
    for source_id in sources:
        ledger = _ledger_path(runner.root_dir, source_id)
        if trade_date in _load(ledger):
            result.sources[source_id] = "already_captured"
            continue
        run_id = f"close-{trade_date}-{local.strftime('%H%M')}"
        try:
            policy = runner.config.get_source(source_id)
            if policy.enabled:
                interval = float(policy.min_interval_seconds)
                if requested and interval > 0:
                    sleep_fn(interval)
                requested = True
            outcome = runner.run_source(
                source_id, run_id=run_id, started_at=now, finished_at=now,
                collect_kwargs={"dates": [trade_date]},
            )
        except SourceDisabledError:
            result.sources[source_id] = "disabled"
            continue
        except (
            SourcePausedError,
            FetchAccessBlockedError,
            FetchBackendUnavailableError,
            CollectorConfigError,
            *listing_exc_types,
        ) as exc:
            result.sources[source_id] = f"failed:{type(exc).__name__}"
            continue
        if outcome.status == "complete":
            _mark(ledger, trade_date, run_id)
            result.sources[source_id] = "captured"
        else:
            # Page not published yet / fetch failed: the next timer tick retries until 17:00.
            result.sources[source_id] = f"retry:{outcome.bundle.manifest.get('reason') or outcome.status}"
    return result
