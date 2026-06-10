"""
Signal Consumer
Polls SignalStore, validates each signal against schema.json, checks staleness,
groups FinRL rebalance batches, resolves conflicts, then calls executor.execute().

Integration point:
    In a LEAN QCAlgorithm, call SignalConsumer.drain() from OnData() or a
    scheduled event:

        from services.execution.lean_runtime.signal_consumer import SignalConsumer

        class MyAlgorithm(QCAlgorithm):
            def Initialize(self):
                self._consumer = SignalConsumer(
                    store_client=SignalStoreClient(...),
                    schema_path="services/research/schema.json",
                )
                self.Schedule.On(
                    self.DateRules.EveryDay(),
                    self.TimeRules.Every(TimeSpan.FromMinutes(1)),
                    self._consumer.drain,
                )

            def OnData(self, data):
                self._consumer.drain(algo=self)

Schema version contract:
    Major version mismatch → log error, skip (never crash runtime).
    Minor/patch mismatch   → log warning, proceed.
"""
from __future__ import annotations

import json
import logging
import pathlib
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from .executor import execute, ExecutionError
from .symbol_parser import SymbolParseError

try:
    import jsonschema
    _HAS_JSONSCHEMA = True
except ImportError:
    _HAS_JSONSCHEMA = False

log = logging.getLogger(__name__)

# How long to buffer an incomplete FinRL run_id batch before partial execution
_REBALANCE_TIMEOUT_BARS = 3
_SUPPORTED_SCHEMA_MAJOR = 1


class SignalConsumer:
    def __init__(
        self,
        store_client: Any,
        schema_path: str | pathlib.Path | None = None,
        rebalance_timeout_bars: int = _REBALANCE_TIMEOUT_BARS,
        binding_id: str | None = None,
    ) -> None:
        self._store = store_client
        self._schema = self._load_schema(schema_path)
        self._rebalance_timeout = rebalance_timeout_bars
        # When set, signals whose binding_id field doesn't match are discarded
        # as a defense-in-depth layer on top of queue-key isolation.
        self._binding_id: str | None = str(binding_id).strip() if binding_id else None

        # run_id → {"signals": [...], "bars_waited": int}
        self._rebalance_buffer: dict[str, dict] = defaultdict(
            lambda: {"signals": [], "bars_waited": 0}
        )
        self._processed_signal_ids: set[str] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def drain(self, algo: Any | None = None) -> None:
        """
        Pull all pending signals from store, validate, and execute.
        Call from LEAN scheduled event or OnData().
        """
        try:
            raw_signals: list[dict] = self._store.get_pending()
        except Exception as exc:
            log.error("SignalStore.get_pending() failed: %s", exc)
            return

        singles: list[dict] = []
        for raw in raw_signals:
            signal = self._validate(raw)
            if signal is None:
                continue
            if self._is_duplicate(signal):
                continue
            if self._is_stale(signal, algo):
                continue
            if self._is_wrong_binding(signal):
                continue
            if signal.get("run_id"):
                self._buffer_rebalance(signal)
            else:
                singles.append(signal)

        # Execute individual signals (conflict-resolved per symbol)
        resolved = self._resolve_conflicts(singles)
        for signal in resolved:
            self._execute_one(signal, algo)

        # Tick rebalance buffer; execute complete or timed-out batches
        self._tick_rebalance_buffer(algo)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate(self, raw: dict) -> dict | None:
        signal_id = raw.get("signal_id", "<unknown>")

        # Schema version check (major version must match)
        version_str = str(raw.get("version", "0.0"))
        try:
            major = int(version_str.split(".")[0])
        except (ValueError, IndexError):
            log.error("[%s] Cannot parse version '%s' — discarding", signal_id, version_str)
            return None

        if major != _SUPPORTED_SCHEMA_MAJOR:
            log.error(
                "[%s] Unsupported schema major version %d (expected %d) — discarding",
                signal_id, major, _SUPPORTED_SCHEMA_MAJOR,
            )
            return None
        if version_str != f"{_SUPPORTED_SCHEMA_MAJOR}.0":
            log.warning("[%s] Minor version drift: %s", signal_id, version_str)

        # JSON Schema validation (if jsonschema available)
        if _HAS_JSONSCHEMA and self._schema:
            try:
                jsonschema.validate(raw, self._schema)
            except jsonschema.ValidationError as exc:
                log.error("[%s] Schema validation failed: %s — discarding", signal_id, exc.message)
                return None

        # Required fields (defensive fallback if jsonschema not installed)
        required = ("signal_id", "version", "strategy_id", "timestamp",
                    "symbol", "action", "direction", "quantity", "quantity_type")
        for field in required:
            if field not in raw:
                log.error("[%s] Missing required field '%s' — discarding", signal_id, field)
                return None

        return raw

    def _is_duplicate(self, signal: dict) -> bool:
        sid = signal["signal_id"]
        if sid in self._processed_signal_ids:
            log.warning("[%s] Duplicate signal_id — discarding (idempotent)", sid)
            return True
        return False

    def _is_stale(self, signal: dict, algo: Any | None = None) -> bool:
        """
        Staleness check. Uses algo.Time if available (real-time or backtest time),
        falling back to current UTC time.
        
        Note: algo.Time is naive and represents the exchange's local time.
        For accurate staleness checks, we compare against signal's timestamp
        which should also be in a consistent timezone (typically UTC per schema).
        To avoid mixing aware and naive datetimes, we normalize both to naive
        datetimes for comparison.
        """
        sid = signal["signal_id"]
        
        # Determine "now" based on algo context
        if algo and hasattr(algo, "Time"):
            now = algo.Time
        else:
            now = datetime.now(timezone.utc)

        ts = _parse_dt(signal["timestamp"])
        if not ts:
            return False
            
        # Normalize both to naive datetimes for comparison (strip timezone info)
        # This handles the case where algo.Time is naive but represents exchange time
        if ts.tzinfo is not None:
            ts = ts.replace(tzinfo=None)
        if now.tzinfo is not None:
            now = now.replace(tzinfo=None)

        diff_seconds = (now - ts).total_seconds()
        
        # Discard signals >24h old
        if diff_seconds > 86400:
            log.warning("[%s] Signal timestamp >24h old (now=%s, ts=%s) — discarding as stale", 
                        sid, now.isoformat(), ts.isoformat())
            return True
            
        # Reject signals >1h in future (anomaly check for clock drift)
        if diff_seconds < -3600:
             log.warning("[%s] Signal timestamp >1h in future (now=%s, ts=%s) — discarding as anomalous", 
                         sid, now.isoformat(), ts.isoformat())
             return True

        return False

    def _is_wrong_binding(self, signal: dict) -> bool:
        """Defense-in-depth: discard signals routed to a different binding.

        Only active when this consumer was constructed with a *binding_id*.
        Signals that carry no ``binding_id`` field pass through regardless —
        they predate the routing field and must not be silently dropped.
        """
        if not self._binding_id:
            return False
        signal_binding = str(signal.get("binding_id") or "").strip()
        if not signal_binding:
            return False
        if signal_binding == self._binding_id:
            return False
        log.warning(
            "[%s] Binding mismatch: expected %s, got %s — discarding",
            signal.get("signal_id", "<unknown>"), self._binding_id, signal_binding,
        )
        return True

    # ------------------------------------------------------------------
    # Conflict resolution (same symbol, different signals)
    # ------------------------------------------------------------------

    def _resolve_conflicts(self, signals: list[dict]) -> list[dict]:
        """
        Last-write-wins by timestamp.  Tie-break by confidence_score (higher wins).
        Returns one signal per symbol.
        """
        by_symbol: dict[str, dict] = {}
        for sig in signals:
            sym = sig["symbol"]
            if sym not in by_symbol:
                by_symbol[sym] = sig
            else:
                existing = by_symbol[sym]
                if _signal_wins(sig, existing):
                    log.info(
                        "[%s] Conflict on %s: replaced by newer/higher-confidence signal [%s]",
                        existing["signal_id"], sym, sig["signal_id"],
                    )
                    by_symbol[sym] = sig
        return list(by_symbol.values())

    # ------------------------------------------------------------------
    # FinRL rebalance batching
    # ------------------------------------------------------------------

    def _buffer_rebalance(self, signal: dict) -> None:
        run_id = signal["run_id"]
        self._rebalance_buffer[run_id]["signals"].append(signal)

    def _tick_rebalance_buffer(self, algo: Any | None) -> None:
        completed: list[str] = []
        for run_id, batch in self._rebalance_buffer.items():
            batch["bars_waited"] += 1
            if batch["bars_waited"] >= self._rebalance_timeout:
                n = len(batch["signals"])
                log.warning(
                    "run_id %s: rebalance timeout after %d bars with %d signal(s) — "
                    "executing partial batch",
                    run_id, self._rebalance_timeout, n,
                )
                for sig in self._resolve_conflicts(batch["signals"]):
                    self._execute_one(sig, algo)
                completed.append(run_id)

        for run_id in completed:
            del self._rebalance_buffer[run_id]

    def flush_rebalance(self, run_id: str, algo: Any | None) -> None:
        """
        Called when SignalStore confirms all signals for a run_id are delivered.
        Executes the batch immediately without waiting for timeout.
        """
        batch = self._rebalance_buffer.pop(run_id, None)
        if batch:
            for sig in self._resolve_conflicts(batch["signals"]):
                self._execute_one(sig, algo)

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def _execute_one(self, signal: dict, algo: Any | None) -> None:
        if algo is None:
            log.warning(
                "[%s] No algo instance — dry-run only", signal["signal_id"]
            )
            self._processed_signal_ids.add(signal["signal_id"])
            return
        try:
            execute(signal, algo)
            # Mark as processed only after successful execution
            self._processed_signal_ids.add(signal["signal_id"])
        except (ExecutionError, SymbolParseError) as exc:
            log.error("[%s] Execution failed: %s", signal["signal_id"], exc)
        except Exception as exc:
            log.exception("Unexpected execution error for signal %s: %s",
                          signal.get("signal_id"), exc)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _load_schema(path: str | pathlib.Path | None) -> dict | None:
        if path is None:
            default = pathlib.Path(__file__).parents[3] / "services/research/schema.json"
            path = default if default.exists() else None
        if path is None:
            log.warning("schema.json not found — structural validation disabled")
            return None
        try:
            return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("Could not load schema.json: %s — validation disabled", exc)
            return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_dt(ts_str: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except Exception:
        return None


def _signal_wins(candidate: dict, incumbent: dict) -> bool:
    """True if candidate should replace incumbent (newer or higher confidence on tie)."""
    c_ts = _parse_dt(candidate["timestamp"])
    i_ts = _parse_dt(incumbent["timestamp"])
    if c_ts and i_ts:
        if c_ts > i_ts:
            return True
        if c_ts == i_ts:
            c_conf = (candidate.get("metadata") or {}).get("confidence_score", 0)
            i_conf = (incumbent.get("metadata") or {}).get("confidence_score", 0)
            return c_conf > i_conf
    return False
