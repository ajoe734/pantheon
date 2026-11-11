"""Orchestration runner connecting config, auto-pause state, spiders/pollers, and bundle writer."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .bundle import BundleWriter, CollectedItem, WrittenBundle, utc_iso
from .config import CollectorConfig, CollectorConfigError, SourceFetchPolicy
from .fetchers import FetchAccessBlockedError, FetchBackendUnavailableError, FetchTransport, LiveScraplingTransport
from .pollers import (
    ArxivPaperPoller,
    CbcMacroPoller,
    DbnomicsMacroPoller,
    DoclingLibraryPoller,
    GdeltDocPoller,
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
    TwseT86DailySpider,
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
    ) -> None:
        self.config = config
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
        if sid == "collector-gdelt":
            return GdeltDocPoller(
                policy, transport=self.transport, contact_user_agent=ua
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
                reason="parse_failed", source_url=source_url,
            )
            return RunOutcome(source_id=source_id, run_id=run_id, status="partial", bundle=written, state=current_state)

        skipped = _skipped_count(collector)
        updated_state = self.state_store.record_run_outcome(
            source_id=source_id,
            run_id=run_id,
            finished_at=finished_iso,
            policy_hash=policy.policy_hash,
            block_reason=None,
        )
        requests = _request_count(collector)
        reason: str | None = None
        if requests is not None and requests == 0:
            reason = "no_targets"  # nothing was requested, so nothing can be called complete
        elif requests is not None and not items:
            reason = _zero_item_reason(collector)
        bundle_status = "partial" if reason else "complete"
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
        return RunOutcome(
            source_id=source_id,
            run_id=run_id,
            status=bundle_status,
            bundle=written,
            state=updated_state,
            skipped_count=skipped,
        )


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
