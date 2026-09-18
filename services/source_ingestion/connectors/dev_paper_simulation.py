"""Dev-only synthetic market-data connector for the paper baseline (DEV-PAPER-MARKET-INPUT-STALENESS-001).

This connector exists to close a structural gap in the dev deployment: the
paper baseline's US persona (market="US", symbol SPY) has no code-owned,
durable, freshness-safe market-data connector, so it depended on a one-off
operator-registered `static_records` connector whose ``event_time`` values
were frozen absolute timestamps baked into config at registration time
(see docs/deployment/evidence/S5-LOOPS-001/source-readbacks/connector-post.json).
Frozen timestamps age out of `evaluate_taiwan_market_freshness`'s flat
``age_seconds > max_age_seconds`` admission rule
(services/execution/market_snapshot_admission.py) and can never become fresh
again, which is what pauses the RuntimeBinding with
``market_input_stale`` and ultimately times out persona provisioning.

Every record this adapter emits computes ``event_time`` / ``available_time``
relative to "now" at call time (the most recently completed UTC daily close
before now), so a fresh ingest run always produces a fresh snapshot. It is
explicit, honest simulation, never real market data: every emitted row and
the connector itself carry ``is_real: false`` and ``provenance: "simulation"``,
mirroring the conventions of the frozen fixture it replaces.

This connector must never be selected outside of ``PANTHEON_ENV=dev`` — see
``is_dev_environment()`` below and its callers in
``services/source_ingestion/persona_source_reconciler.py`` and
``services/control-plane/bff/personas/service.py``.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence

from .base import (
    AuthPolicy,
    AuthType,
    ConnectorMode,
    LicensePolicy,
    RateLimitPolicy,
    SourceConnector,
    SourceConnectorProvider,
    SourceMetadata,
    SourceRecord,
)

DEV_PAPER_SIMULATION_CONNECTOR_ID = "dev-paper-us-equity-simulation"
DEV_PAPER_SIMULATION_PROVIDER = "Explicit controlled simulation"
DEV_PAPER_SIMULATION_LICENSE_SCOPE = "internal"
DEV_PAPER_SIMULATION_SCHEMA_HASH = "dev_paper_simulation_us_price_daily.v1"
DEFAULT_DEV_PAPER_SIMULATION_SYMBOLS: tuple[str, ...] = ("SPY",)

# Deterministic-but-varying baseline close price per symbol so repeated runs
# produce plausible, slowly drifting values instead of a constant.
_BASE_CLOSE_BY_SYMBOL: Mapping[str, float] = {
    "SPY": 520.0,
    "QQQ": 460.0,
}
_DEFAULT_BASE_CLOSE = 100.0


def is_dev_environment(env: Mapping[str, str] | None = None) -> bool:
    """Return True only when PANTHEON_ENV is explicitly 'dev'.

    Mirrors the gate used by scripts/bootstrap_dev_paper_baseline.py's
    assert_dev_paper_boundary(): this synthetic connector must never be
    selectable in staging/prod.
    """

    source = env if env is not None else os.environ
    return str(source.get("PANTHEON_ENV", "") or "").strip().lower() == "dev"


def _most_recent_completed_daily_close(now: datetime) -> datetime:
    """Return the most recently completed daily-close timestamp before ``now``.

    Uses a fixed UTC-day close marker (00:00:00Z) strictly before ``now`` so
    that two calls separated by real wall-clock time always produce
    different, monotonically increasing event_time values as "now" advances,
    while a single ingest run stays internally consistent.
    """

    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    close_today = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)
    if now <= close_today:
        close_today -= timedelta(days=1)
    return close_today


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stable_row_hash(payload: Mapping[str, Any]) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()[:16]


def _synthetic_close(symbol: str, event_time: datetime) -> float:
    base = _BASE_CLOSE_BY_SYMBOL.get(symbol.upper(), _DEFAULT_BASE_CLOSE)
    # Small deterministic drift keyed off the day, so consecutive daily
    # closes are distinguishable without needing external state.
    day_ordinal = event_time.toordinal()
    drift = ((day_ordinal * 37) % 200) / 100.0 - 1.0
    return round(base + drift, 6)


@dataclass(frozen=True)
class DevPaperUsEquitySimulationAdapter(SourceConnectorProvider):
    """Explicit controlled simulation adapter for the dev paper baseline's US symbols.

    Dev-only: callers must check ``is_dev_environment()`` before registering
    or selecting this connector. This adapter never performs network I/O; it
    is a lean, self-contained record generator, deliberately kept much
    smaller than the real Taiwan official-market adapter.
    """

    connector_id: str = DEV_PAPER_SIMULATION_CONNECTOR_ID
    symbols: Sequence[str] = field(default_factory=lambda: DEFAULT_DEV_PAPER_SIMULATION_SYMBOLS)
    source_metadata: SourceMetadata | Mapping[str, Any] | None = None
    connector_metadata: Mapping[str, Any] = field(default_factory=dict)
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(timezone.utc))

    def connector(self) -> SourceConnector:
        return SourceConnector(
            connector_id=self.connector_id,
            source_type="market",
            provider=DEV_PAPER_SIMULATION_PROVIDER,
            license_scope=DEV_PAPER_SIMULATION_LICENSE_SCOPE,
            auth_type=AuthType.NONE,
            supported_modes=(ConnectorMode.BATCH,),
            auth_policy=AuthPolicy(auth_type=AuthType.NONE),
            license_policy=LicensePolicy(
                license_scope=DEV_PAPER_SIMULATION_LICENSE_SCOPE,
                allowed_use=("research",),
                attribution_required=False,
                redistribution_allowed=False,
            ),
            rate_limit_policy=RateLimitPolicy(
                policy_ref="source-ingest://policy/dev-paper-simulation-local",
            ),
            source_metadata=self.source_metadata
            or SourceMetadata(
                display_name="Dev paper baseline synthetic US equity feed",
                owner="pantheon-source-ingest",
                tags=("dev_only", "simulation", "paper_only", "us_equity"),
            ),
            metadata={
                "is_real": False,
                "provenance": "simulation",
                "dev_only": True,
                "market": "US",
                "symbols": list(self.symbols),
                "normalized_datasets": ["us_price_daily"],
                "schema_hash": DEV_PAPER_SIMULATION_SCHEMA_HASH,
                **dict(self.connector_metadata),
            },
        )

    def fetch_config(self) -> Mapping[str, Any]:
        return {
            "mode": "provider_owned_adapter",
            "adapter": "DevPaperUsEquitySimulationAdapter.records_from_now",
            "adapter_config": {"symbols": list(self.symbols)},
            "request": {"symbols": list(self.symbols)},
            "next_watermark": None,
            "dataset": "us_price_daily",
        }

    def records_from_now(
        self,
        *,
        symbols: Sequence[str] | None = None,
        trace_id: str = "",
    ) -> tuple[SourceRecord, ...]:
        """Emit one fresh, honestly-labeled synthetic record per symbol.

        ``event_time``/``available_time`` are always computed relative to
        ``self.clock()`` at call time, never frozen into config, so a fresh
        ingest run always yields a fresh, admissible snapshot.
        """

        now = self.clock()
        event_time = _most_recent_completed_daily_close(now)
        event_time_iso = _iso(event_time)
        observed_at_iso = _iso(now)
        records: list[SourceRecord] = []
        for raw_symbol in symbols or self.symbols:
            symbol = str(raw_symbol).strip().upper()
            if not symbol:
                continue
            close = _synthetic_close(symbol, event_time)
            normalized_row = {
                "symbol": symbol,
                "close": close,
                "event_time": event_time_iso,
                "is_real": False,
                "provenance": "simulation",
            }
            row_hash = _stable_row_hash({"symbol": symbol, "event_time": event_time_iso, "close": close})
            records.append(
                SourceRecord(
                    source_id=f"dev-paper-simulation:{symbol}:{event_time_iso}:{row_hash}",
                    connector_id=self.connector_id,
                    source_type="market",
                    title=f"SIMULATION dev-paper-baseline {symbol} {event_time_iso}",
                    content_ref=f"simulation://dev-paper-us-equity/{symbol}/{event_time_iso}",
                    metadata={
                        "is_real": False,
                        "provenance": "simulation",
                        "license_scope": DEV_PAPER_SIMULATION_LICENSE_SCOPE,
                        "access_scope": ["research"],
                        "event_time": event_time_iso,
                        "available_time": observed_at_iso,
                        "observed_at": observed_at_iso,
                        "normalized_row": normalized_row,
                        "dev_only": True,
                    },
                    trace_id=trace_id,
                )
            )
        return tuple(records)
