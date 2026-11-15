"""Production market inputs for a multi-underlying persona pool envelope.

The Source owner exposes one read-only stored snapshot per symbol
(``/api/source-ingest/snapshots/latest``).  A pool envelope that nets several
underlyings needs one admitted snapshot per symbol *at one common bar*.  This
module obtains them with exactly one request per symbol, runs the shared
admission rule on each, and refuses the whole bar when any symbol fails or the
bars differ.  It never fabricates, carries forward or substitutes a bar: a
partial net view would size positions against a book the pool does not hold.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Mapping, Sequence

from services.execution.market_snapshot_admission import admit_market_snapshot

MAX_ENVELOPE_SYMBOLS = 50
REQUEST_TIMEOUT_SECONDS = 5


class PoolMarketInputsUnavailable(RuntimeError):
    """The pool cannot be evaluated this bar; ``code`` is the recorded reason."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def envelope_symbols(artifact: Mapping[str, Any]) -> list[str]:
    """Symbols a persona_portfolio_v1 envelope needs, in declared order."""
    parameters = artifact.get("parameters") if isinstance(artifact, Mapping) else None
    symbols = parameters.get("symbols") if isinstance(parameters, Mapping) else None
    if not isinstance(symbols, Sequence) or isinstance(symbols, (str, bytes)) or not symbols:
        raise PoolMarketInputsUnavailable("market_input_missing", "envelope declares no symbols")
    cleaned = [str(s).strip() for s in symbols]
    if any(not s for s in cleaned) or len(set(cleaned)) != len(cleaned):
        raise PoolMarketInputsUnavailable("market_input_invalid", "envelope symbols are blank or duplicated")
    if len(cleaned) > MAX_ENVELOPE_SYMBOLS:
        raise PoolMarketInputsUnavailable(
            "market_input_invalid", f"envelope has {len(cleaned)} symbols; bound is {MAX_ENVELOPE_SYMBOLS}"
        )
    return cleaned


def fetch_source_snapshot(source_ingest_url: str, symbol: str) -> Mapping[str, Any] | None:
    """One read of Source's stored snapshot; None only for Source's own 4xx 'no snapshot'."""
    url = (
        f"{source_ingest_url.rstrip('/')}/api/source-ingest/snapshots/latest"
        f"?symbol={urllib.parse.quote(symbol, safe='')}"
    )
    request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if 400 <= exc.code < 500:
            return None
        raise PoolMarketInputsUnavailable(
            "market_input_unavailable", f"Source stored snapshot is unavailable for {symbol}: HTTP {exc.code}"
        ) from exc
    except Exception as exc:  # noqa: BLE001 - transport failure is a recorded reason
        raise PoolMarketInputsUnavailable(
            "market_input_unavailable", f"Source stored snapshot is unavailable for {symbol}: {exc}"
        ) from exc
    return payload if isinstance(payload, Mapping) else None


def obtain_envelope_market_inputs(
    symbols: Sequence[str],
    *,
    source_ingest_url: str,
    max_age_seconds: int,
    minimum_closes: int,
    now_iso: str,
    binding_id: str | None = None,
    fetch: Callable[[str, str], Mapping[str, Any] | None] | None = None,
    admit: Callable[..., Any] = admit_market_snapshot,
) -> dict[str, dict[str, Any]]:
    """Return ``{symbol: admitted snapshot}`` at one common bar, or raise.

    Every symbol is read before any is judged so the recorded reason names the
    first failing symbol in declared order, deterministically.
    """
    if not source_ingest_url:
        raise PoolMarketInputsUnavailable(
            "market_input_unavailable", "PANTHEON_SOURCE_INGEST_URL is required for pool market inputs"
        )
    read = fetch or fetch_source_snapshot
    fetched = {symbol: read(source_ingest_url, symbol) for symbol in symbols}
    admitted: dict[str, dict[str, Any]] = {}
    for symbol in symbols:
        decision = admit(
            fetched[symbol],
            expected_symbol=symbol,
            max_age_seconds=max_age_seconds,
            minimum_closes=minimum_closes,
            now_iso=now_iso,
            binding_id=binding_id,
        )
        if not decision.admitted:
            raise PoolMarketInputsUnavailable(
                decision.reason_code or "market_input_invalid",
                f"pool symbol {symbol} failed admission: {decision.detail}",
            )
        admitted[symbol] = dict(fetched[symbol])
    bars = {symbol: snap.get("event_time") for symbol, snap in admitted.items()}
    if len(set(bars.values())) != 1:
        raise PoolMarketInputsUnavailable(
            "market_input_bar_mismatch",
            "pool symbols are not at one common bar: "
            + ", ".join(f"{s}={bars[s]}" for s in symbols),
        )
    return admitted
