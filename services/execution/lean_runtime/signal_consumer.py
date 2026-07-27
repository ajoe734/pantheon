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
import math
import pathlib
import threading
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable

from .executor import execute, ExecutionError, _signal_context_metadata, _signal_market_price
from .pending_signal_store import ExecutionFence
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


class _ExecutionClaimHeartbeat:
    """Renew one Redis claim while its execution side effect is in progress."""

    def __init__(
        self,
        *,
        renew: Callable[[dict], bool],
        signal: dict,
        interval_seconds: float,
    ) -> None:
        self._renew = renew
        self._signal = signal
        self._interval = interval_seconds
        self._stop = threading.Event()
        self._lease_lost = threading.Event()
        self._loss_reason: str | None = None
        self._thread = threading.Thread(
            target=self._run,
            name=f"signal-claim-heartbeat-{signal.get('signal_id', 'unknown')}",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=max(self._interval * 2.0, 1.0))
        if self._thread.is_alive():
            log.error(
                "[%s] Claim heartbeat did not stop within the join deadline",
                self._signal.get("signal_id", "<unknown>"),
            )

    @property
    def lease_lost(self) -> bool:
        return self._lease_lost.is_set()

    @property
    def loss_reason(self) -> str | None:
        return self._loss_reason

    def _publish_lease_loss(self, reason: str) -> None:
        self._loss_reason = reason
        self._lease_lost.set()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                renewed = bool(self._renew(self._signal))
            except Exception as exc:  # noqa: BLE001 - lease ambiguity fails closed on reclaim
                self._publish_lease_loss(
                    f"renew_exception:{type(exc).__name__}:{exc}"
                )
                log.error(
                    "[%s] Claim heartbeat failed during execution: %s",
                    self._signal.get("signal_id", "<unknown>"),
                    exc,
                )
                return
            if not renewed:
                self._publish_lease_loss("renew_returned_false")
                log.error(
                    "[%s] Claim heartbeat lost ownership during execution",
                    self._signal.get("signal_id", "<unknown>"),
                )
                return


class SignalConsumer:
    def __init__(
        self,
        store_client: Any,
        schema_path: str | pathlib.Path | None = None,
        rebalance_timeout_bars: int = _REBALANCE_TIMEOUT_BARS,
        binding_id: str | None = None,
        runtime_id: str | None = None,
        capital_pool_id: str | None = None,
    ) -> None:
        self._store = store_client
        self._schema = self._load_schema(schema_path)
        self._rebalance_timeout = rebalance_timeout_bars
        # When set, signals whose binding_id field doesn't match are discarded
        # as a defense-in-depth layer on top of queue-key isolation.
        self._binding_id: str | None = str(binding_id).strip() if binding_id else None
        # Runtime-level isolation: reject signals addressed to a different runtime.
        self._runtime_id: str | None = str(runtime_id).strip() if runtime_id else None
        # Capital-pool isolation: reject signals scoped to a different pool.
        self._capital_pool_id: str | None = str(capital_pool_id).strip() if capital_pool_id else None

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
        # Keep live rebalance claims from becoming visible to another worker.
        # A claim that already expired is removed from the local buffer and
        # left for durable reclaim instead of executing from a stale copy.
        self._renew_rebalance_claims()
        try:
            raw_signals: list[dict] = self._store.get_pending()
        except Exception as exc:
            log.error("SignalStore.get_pending() failed: %s", exc)
            return

        singles: list[dict] = []
        for raw in raw_signals:
            signal = self._validate(raw)
            if signal is None:
                # Validation failure: route raw payload to DLQ
                self._enqueue_dlq(raw if isinstance(raw, dict) else {"raw_payload": str(raw)}, "validation_failure")
                continue
            recorder = getattr(algo, "RecordSignalProcessed", None)
            if callable(recorder):
                recorder(signal)
            if self._is_duplicate(signal):
                self._record_filtered_signal_noop(
                    signal,
                    algo,
                    "duplicate_signal_id",
                    extra_metadata={
                        "duplicate_signal_id": signal["signal_id"],
                        "idempotent_replay": True,
                    },
                )
                self._ack_signal(signal)
                continue
            staleness_reason = self._staleness_reason(signal, algo)
            if staleness_reason:
                self._record_filtered_signal_noop(signal, algo, staleness_reason)
                self._ack_signal(signal)
                continue
            if self._is_wrong_binding(signal):
                self._record_filtered_signal_noop(
                    signal,
                    algo,
                    "binding_mismatch",
                    extra_metadata={
                        "expected_binding_id": self._binding_id,
                        "signal_binding_id": str(signal.get("binding_id") or "").strip(),
                    },
                    remember_processed=False,
                )
                if self._enqueue_dlq(signal, "binding_mismatch"):
                    self._remember_processed(signal["signal_id"])
                continue
            if self._is_wrong_runtime(signal):
                self._record_filtered_signal_noop(
                    signal,
                    algo,
                    "runtime_mismatch",
                    extra_metadata={
                        "expected_runtime_id": self._runtime_id,
                        "signal_runtime_id": str(signal.get("runtime_id") or "").strip(),
                    },
                    remember_processed=False,
                )
                if self._enqueue_dlq(signal, "runtime_mismatch"):
                    self._remember_processed(signal["signal_id"])
                continue
            if self._is_wrong_capital_pool(signal):
                signal_pool = str(
                    (signal.get("metadata") or {}).get("capital_pool_id") or ""
                ).strip()
                self._record_filtered_signal_noop(
                    signal,
                    algo,
                    "capital_pool_mismatch",
                    extra_metadata={
                        "expected_capital_pool_id": self._capital_pool_id,
                        "signal_capital_pool_id": signal_pool,
                    },
                    remember_processed=False,
                )
                if self._enqueue_dlq(signal, "capital_pool_mismatch"):
                    self._remember_processed(signal["signal_id"])
                continue
            if signal.get("run_id"):
                self._buffer_rebalance(signal)
            else:
                singles.append(signal)

        # Execute individual signals (conflict-resolved per symbol)
        resolved = self._resolve_conflicts(singles, algo=algo)
        for signal in resolved:
            self._execute_one(signal, algo)

        # Tick rebalance buffer; execute complete or timed-out batches
        self._tick_rebalance_buffer(algo)

    def _ack_signal(self, signal: dict) -> None:
        if hasattr(self._store, "ack"):
            try:
                self._store.ack(signal)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate(self, raw: dict) -> dict | None:
        if not isinstance(raw, dict):
            log.error("Signal payload must be an object — discarding")
            return None

        signal_id = raw.get("signal_id", "<unknown>")

        # Python accepts NaN/Infinity by default even though they are not JSON
        # numbers. Reject them, including inside metadata, before any runtime
        # recorder or journey event can observe this signal.
        try:
            json.dumps(raw, allow_nan=False)
        except (TypeError, ValueError) as exc:
            log.error("[%s] Signal is not strict JSON: %s — discarding", signal_id, exc)
            return None

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

        if not _is_finite_number(raw["quantity"], minimum=0.0):
            log.error("[%s] quantity must be a finite non-negative number — discarding", signal_id)
            return None

        limit_price = raw.get("limit_price")
        if limit_price is not None and not _is_finite_number(
            limit_price,
            minimum=0.0,
            exclusive=True,
        ):
            log.error("[%s] limit_price must be a finite positive number — discarding", signal_id)
            return None

        metadata = raw.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            log.error("[%s] metadata must be an object — discarding", signal_id)
            return None
        confidence_score = (metadata or {}).get("confidence_score")
        if confidence_score is not None and not _is_finite_number(
            confidence_score,
            minimum=0.0,
            maximum=1.0,
        ):
            log.error("[%s] confidence_score must be finite and within [0, 1] — discarding", signal_id)
            return None

        return raw

    def _is_duplicate(self, signal: dict) -> bool:
        sid = signal["signal_id"]
        if sid in self._processed_signal_ids:
            log.warning("[%s] Duplicate signal_id — discarding (idempotent)", sid)
            return True
        # Persistent idempotency: a signal processed before a worker restart is
        # detected via store.is_processed() and skipped gracefully.
        is_p = getattr(self._store, "is_processed", None)
        if callable(is_p) and is_p(sid) is True:
            log.warning("[%s] Duplicate signal_id (persistent) — discarding (idempotent)", sid)
            self._processed_signal_ids.add(sid)
            return True
        return False

    def _remember_processed(self, signal_id: str) -> None:
        sid = str(signal_id)
        self._processed_signal_ids.add(sid)
        mark = getattr(self._store, "mark_processed", None)
        if callable(mark):
            mark(sid)

    def _is_stale(self, signal: dict, algo: Any | None = None) -> bool:
        return self._staleness_reason(signal, algo) is not None

    def _staleness_reason(self, signal: dict, algo: Any | None = None) -> str | None:
        raw_ts = signal.get("timestamp")
        if not raw_ts:
            return None
        signal_dt = _parse_dt(str(raw_ts))
        if signal_dt is None:
            return None
        current_dt = getattr(algo, "Time", None)
        if not isinstance(current_dt, datetime):
            current_dt = datetime.now(timezone.utc)
        if current_dt.tzinfo is None and signal_dt.tzinfo is not None:
            signal_dt = signal_dt.replace(tzinfo=None)
        elif current_dt.tzinfo is not None and signal_dt.tzinfo is None:
            signal_dt = signal_dt.replace(tzinfo=timezone.utc)
        diff_seconds = (current_dt - signal_dt).total_seconds()
        if diff_seconds > 86400:
            log.warning(
                "[%s] Signal is stale (age=%.0fs > 86400s) — discarding",
                signal.get("signal_id", "<unknown>"), diff_seconds,
            )
            return "stale_signal"
        if diff_seconds < -3600:
            log.warning(
                "[%s] Signal timestamp >1h in future — discarding",
                signal.get("signal_id", "<unknown>"),
            )
            return "future_signal_anomaly"
        return None

    def _record_filtered_signal_noop(
        self,
        signal: dict,
        algo: Any | None,
        noop_reason: str,
        extra_metadata: dict[str, Any] | None = None,
        remember_processed: bool = True,
    ) -> None:
        sid = signal["signal_id"]
        if algo is None:
            if remember_processed:
                self._remember_processed(sid)
            return
        recorder = getattr(algo, "RecordSignalNoop", None)
        if not callable(recorder):
            if remember_processed:
                self._remember_processed(sid)
            return
        metadata = _signal_context_metadata(signal)
        metadata["filter_reason"] = noop_reason
        if extra_metadata:
            metadata.update(
                {key: value for key, value in extra_metadata.items() if value not in (None, "")}
            )
        price = _signal_market_price(signal)
        kwargs = {
            "signal_id": sid,
            "noop_reason": noop_reason,
            "requested_quantity": float(signal.get("quantity") or 0.0),
            "computed_quantity": 0.0,
            "quantity_type": signal.get("quantity_type", "UNKNOWN"),
            "order_type": signal.get("order_type", "MARKET"),
            "broker_submission_status": "not_submitted_signal_filtered",
            "submitted_to_broker": False,
            "metadata": metadata,
        }
        if price is not None:
            kwargs["price"] = price
        recorder(signal["symbol"], **kwargs)
        if remember_processed:
            self._remember_processed(sid)

    def _is_wrong_binding(self, signal: dict) -> bool:
        """Fail closed in governed paper mode when binding_id is missing or mismatched."""
        if not self._binding_id:
            return False
        signal_binding = str(signal.get("binding_id") or "").strip()
        if not signal_binding:
            log.warning(
                "[%s] Fail closed: signal has missing/empty binding_id — discarding",
                signal.get("signal_id", "<unknown>"),
            )
            return True
        if signal_binding == self._binding_id:
            return False
        log.warning(
            "[%s] Binding mismatch: expected %s, got %s — discarding",
            signal.get("signal_id", "<unknown>"), self._binding_id, signal_binding,
        )
        return True

    def _is_wrong_runtime(self, signal: dict) -> bool:
        """Fail closed in governed paper mode when runtime_id is missing or mismatched."""
        if not self._runtime_id:
            return False
        signal_runtime = str(signal.get("runtime_id") or "").strip()
        if not signal_runtime:
            log.warning(
                "[%s] Fail closed: signal has missing/empty runtime_id — discarding",
                signal.get("signal_id", "<unknown>"),
            )
            return True
        if signal_runtime == self._runtime_id:
            return False
        log.warning(
            "[%s] Runtime mismatch: expected %s, got %s — discarding",
            signal.get("signal_id", "<unknown>"), self._runtime_id, signal_runtime,
        )
        return True

    def _is_wrong_capital_pool(self, signal: dict) -> bool:
        """Fail closed in governed paper mode when capital_pool_id is missing or mismatched."""
        if not self._capital_pool_id:
            return False
        signal_pool = str(
            (signal.get("metadata") or {}).get("capital_pool_id") or ""
        ).strip()
        if not signal_pool:
            log.warning(
                "[%s] Fail closed: signal has missing/empty capital_pool_id — discarding",
                signal.get("signal_id", "<unknown>"),
            )
            return True
        if signal_pool == self._capital_pool_id:
            return False
        log.warning(
            "[%s] Capital pool mismatch: expected %s, got %s — discarding",
            signal.get("signal_id", "<unknown>"), self._capital_pool_id, signal_pool,
        )
        return True

    def _enqueue_dlq(self, signal: dict, reason: str) -> bool:
        """Send a rejected or failed signal to the store's DLQ."""
        enqueue = getattr(self._store, "enqueue_dlq", None)
        if not callable(enqueue):
            return False
        try:
            if isinstance(signal, dict):
                dlq_payload = {**signal, "_dlq_reason": reason}
            else:
                dlq_payload = {"raw_payload": str(signal), "_dlq_reason": reason}
            enqueue(dlq_payload)
            return True
        except Exception as exc:  # noqa: BLE001 - DLQ write must never break the signal path
            log.warning("[%s] DLQ enqueue failed (%s): %s",
                        signal.get("signal_id", "<unknown>") if isinstance(signal, dict) else "<unknown>", reason, exc)
            return False

    # ------------------------------------------------------------------
    # Conflict resolution (same symbol, different signals)
    # ------------------------------------------------------------------

    def _resolve_conflicts(self, signals: list[dict], algo: Any | None = None) -> list[dict]:
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
                    self._record_conflict_loser_noop(existing, winner=sig, algo=algo)
                    by_symbol[sym] = sig
                else:
                    log.info(
                        "[%s] Conflict on %s: retained newer/higher-confidence signal [%s]",
                        sig["signal_id"], sym, existing["signal_id"],
                    )
                    self._record_conflict_loser_noop(sig, winner=existing, algo=algo)
        return list(by_symbol.values())

    def _record_conflict_loser_noop(self, loser: dict, *, winner: dict, algo: Any | None) -> None:
        self._record_filtered_signal_noop(
            loser,
            algo,
            "signal_conflict_loser",
            extra_metadata={
                "conflict_winner_signal_id": winner.get("signal_id"),
                "conflict_loser_signal_id": loser.get("signal_id"),
                "conflict_symbol": loser.get("symbol"),
                "conflict_resolution_rule": "last_write_wins_timestamp_then_confidence",
            },
        )
        self._ack_signal(loser)

    # ------------------------------------------------------------------
    # FinRL rebalance batching
    # ------------------------------------------------------------------

    def _buffer_rebalance(self, signal: dict) -> None:
        run_id = signal["run_id"]
        self._rebalance_buffer[run_id]["signals"].append(signal)

    def _renew_rebalance_claims(self) -> None:
        renew = getattr(self._store, "renew_claim", None)
        if not callable(renew):
            return
        for run_id, batch in list(self._rebalance_buffer.items()):
            retained = [
                signal
                for signal in batch["signals"]
                if self._renew_execution_claim(signal)
            ]
            if retained:
                batch["signals"] = retained
            else:
                del self._rebalance_buffer[run_id]

    def _renew_execution_claim(self, signal: dict) -> bool:
        renew = getattr(self._store, "renew_claim", None)
        if not callable(renew):
            return True
        try:
            renewed = bool(renew(signal))
        except Exception as exc:  # noqa: BLE001 - ambiguous ownership fails closed
            log.error(
                "[%s] Claim renewal failed; refusing execution: %s",
                signal.get("signal_id", "<unknown>"),
                exc,
            )
            return False
        if not renewed:
            log.warning(
                "[%s] Claim expired or was reclaimed; refusing stale execution",
                signal.get("signal_id", "<unknown>"),
            )
        return renewed

    def _start_execution_claim_heartbeat(
        self,
        signal: dict,
    ) -> _ExecutionClaimHeartbeat | None:
        """Start continuous renewal when the store publishes a safe interval."""
        renew = getattr(self._store, "renew_claim", None)
        interval_provider = getattr(
            self._store,
            "claim_renewal_interval_seconds",
            None,
        )
        if not callable(renew) or not callable(interval_provider):
            return None
        try:
            interval = float(interval_provider())
        except (TypeError, ValueError, OverflowError) as exc:
            log.error(
                "[%s] Invalid claim heartbeat interval; refusing execution: %s",
                signal.get("signal_id", "<unknown>"),
                exc,
            )
            return None
        if not math.isfinite(interval) or interval <= 0:
            log.error(
                "[%s] Invalid claim heartbeat interval %.6f; refusing execution",
                signal.get("signal_id", "<unknown>"),
                interval,
            )
            return None
        heartbeat = _ExecutionClaimHeartbeat(
            renew=renew,
            signal=signal,
            interval_seconds=interval,
        )
        heartbeat.start()
        return heartbeat

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
                for sig in self._resolve_conflicts(batch["signals"], algo=algo):
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
            for sig in self._resolve_conflicts(batch["signals"], algo=algo):
                self._execute_one(sig, algo)

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def _execute_one(self, signal: dict, algo: Any | None) -> None:
        # Fence the actual side-effect boundary, not just the earlier claim
        # loop.  Buffered signals may outlive their original visibility lease.
        if not self._renew_execution_claim(signal):
            return
        if self._is_duplicate(signal):
            self._record_filtered_signal_noop(
                signal,
                algo,
                "duplicate_signal_id",
                extra_metadata={
                    "duplicate_signal_id": signal["signal_id"],
                    "idempotent_replay": True,
                    "execution_boundary_recheck": True,
                },
            )
            self._ack_signal(signal)
            return
        if algo is None:
            log.warning(
                "[%s] No algo instance — dry-run only", signal["signal_id"]
            )
            self._remember_processed(signal["signal_id"])
            self._ack_signal(signal)
            return
        execution_fence: ExecutionFence | None = None
        begin_execution = getattr(self._store, "begin_execution", None)
        if callable(begin_execution):
            try:
                candidate = begin_execution(signal)
            except Exception as exc:  # noqa: BLE001 - ambiguous authority fails closed
                log.error(
                    "[%s] Unable to reserve execution authority; refusing side effect: %s",
                    signal.get("signal_id", "<unknown>"),
                    exc,
                )
                return
            if isinstance(candidate, ExecutionFence):
                execution_fence = candidate
        if execution_fence is not None:
            if execution_fence.status == "lost_claim":
                log.warning(
                    "[%s] Claim expired before execution reservation; refusing side effect",
                    signal.get("signal_id", "<unknown>"),
                )
                return
            if execution_fence.status == "in_progress":
                log.error(
                    "[%s] Execution already started under another claim; "
                    "routing reclaimed copy to durable DLQ",
                    signal.get("signal_id", "<unknown>"),
                )
                self._record_filtered_signal_noop(
                    signal,
                    algo,
                    "execution_in_progress_elsewhere",
                    extra_metadata={
                        "execution_fence_status": execution_fence.status,
                    },
                    remember_processed=False,
                )
                self._enqueue_dlq(signal, "execution_in_progress_elsewhere")
                return
            if execution_fence.status == "completed":
                self._record_filtered_signal_noop(
                    signal,
                    algo,
                    "duplicate_signal_id",
                    extra_metadata={
                        "duplicate_signal_id": signal["signal_id"],
                        "idempotent_replay": True,
                        "execution_fence_status": execution_fence.status,
                    },
                )
                self._ack_signal(signal)
                return
            if (
                execution_fence.status != "acquired"
                or not execution_fence.token
            ):
                log.error(
                    "[%s] Invalid execution-fence result %r; refusing side effect",
                    signal.get("signal_id", "<unknown>"),
                    execution_fence,
                )
                return
        heartbeat = self._start_execution_claim_heartbeat(signal)
        execution_succeeded = False
        try:
            execute(signal, algo)
            execution_succeeded = True
        except (ExecutionError, SymbolParseError) as exc:
            log.error("[%s] Execution failed: %s", signal["signal_id"], exc)
            self._record_execution_error_noop(signal, algo, exc)
            if self._enqueue_dlq(signal, f"execution_error: {exc}"):
                self._remember_processed(signal["signal_id"])
        except Exception as exc:
            log.exception("Unexpected execution error for signal %s: %s",
                          signal.get("signal_id"), exc)
            self._enqueue_dlq(signal, f"unexpected_error: {exc}")
        finally:
            if heartbeat is not None:
                heartbeat.stop()
        if not execution_succeeded:
            return

        lease_loss_reason = (
            heartbeat.loss_reason
            if heartbeat is not None and heartbeat.lease_lost
            else None
        )
        if execution_fence is not None:
            complete_execution = getattr(self._store, "complete_execution", None)
            try:
                committed = callable(complete_execution) and bool(
                    complete_execution(
                        signal["signal_id"],
                        execution_fence.token,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - commit ambiguity stays recoverable
                log.error(
                    "[%s] Execution fence commit failed; retaining durable recovery: %s",
                    signal.get("signal_id", "<unknown>"),
                    exc,
                )
                self._enqueue_dlq(signal, f"execution_fence_commit_error: {exc}")
                return
            if not committed:
                log.error(
                    "[%s] Execution fence ownership changed before commit; "
                    "retaining durable recovery",
                    signal.get("signal_id", "<unknown>"),
                )
                self._enqueue_dlq(signal, "execution_fence_commit_refused")
                return
            self._processed_signal_ids.add(str(signal["signal_id"]))
            if lease_loss_reason:
                log.error(
                    "[%s] Queue claim lease was lost during execution (%s); "
                    "the durable execution token committed the single side effect",
                    signal.get("signal_id", "<unknown>"),
                    lease_loss_reason,
                )
            self._ack_signal(signal)
            return

        if lease_loss_reason:
            log.error(
                "[%s] Queue claim lease was lost during unfenced execution (%s); "
                "refusing processed/ack commit",
                signal.get("signal_id", "<unknown>"),
                lease_loss_reason,
            )
            self._enqueue_dlq(
                signal,
                f"unfenced_execution_claim_lost: {lease_loss_reason}",
            )
            return
        self._remember_processed(signal["signal_id"])
        self._ack_signal(signal)

    def _record_execution_error_noop(self, signal: dict, algo: Any | None, exc: Exception) -> None:
        reason = _execution_error_reason(exc)
        self._record_filtered_signal_noop(
            signal,
            algo,
            reason,
            extra_metadata={
                "execution_error_type": type(exc).__name__,
                "execution_error_message": str(exc),
                "execution_error_stage": "execute_signal",
                "execution_error_symbol": signal.get("symbol"),
                "signal_action": signal.get("action"),
                "signal_direction": signal.get("direction"),
            },
            remember_processed=False,
        )

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


def _is_finite_number(
    value: Any,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    exclusive: bool = False,
) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    number = float(value)
    if not math.isfinite(number):
        return False
    if minimum is not None:
        if exclusive and number <= minimum:
            return False
        if not exclusive and number < minimum:
            return False
    return maximum is None or number <= maximum


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


def _is_symbol_parse_error(exc: Exception) -> bool:
    if isinstance(exc, SymbolParseError):
        return True
    return isinstance(getattr(exc, "__cause__", None), SymbolParseError)


def _execution_error_reason(exc: Exception) -> str:
    if _is_symbol_parse_error(exc):
        return "symbol_parse_error"
    if "Unhandled action/direction combination" in str(exc):
        return "unsupported_action_direction"
    if "limit_price is required" in str(exc):
        return "limit_price_required"
    if "PERCENT_PORTFOLIO quantity_type is not supported" in str(exc):
        return "limit_percent_portfolio_unsupported"
    return "execution_error"
