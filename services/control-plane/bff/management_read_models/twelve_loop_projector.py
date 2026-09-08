"""Durable receipt-derived twelve-loop truth projector.

Normative implementation under task LOOP-TRUTH-001 (pkt-pantheon-structural-closure-functional-v2-20260903,
SD §7.2, SA ADR-05):
  - Persist a projection keyed by (release_id, correlation_id, loop_id).
  - Consumes existing canonical owner receipts: stimulus, terminal, next_consumer.
  - Terminal plus next-consumer receipt is required for completion (absent next receipt = open).
  - Incremental update equals rebuild output.
  - Backfill cannot replace newer live truth.
  - Static registry supplies labels and order only; registry maturity never sets runtime completion.
  - Mandatory deletion: static or incident-derived success substitution excised.
  - Rollback: disable the new read projection while preserving source receipts.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
import os
from typing import Any, Dict, Iterable, List, Literal, Mapping, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

# Canonical 12 loops definition: (loop_id, canonical_id, name, owner)
CANONICAL_TWELVE_LOOPS: Dict[int, Tuple[str, str, str]] = {
    1: ("source_ingestion", "Source Ingestion", "source-ingest connector and schedule stores"),
    2: ("strategy_distillation", "Strategy Distillation", "distillation connector/worker"),
    3: ("alpha_replication", "Alpha Replication", "alpha-replication-controller"),
    4: ("persona_teaching", "Persona Teaching", "training session / teaching store"),
    5: ("agora_interaction_evidence", "Agora / Human Trader Interaction Evidence", "agora store / research"),
    6: ("human_imitation_shadow_evaluation", "Human Imitation / Shadow Evaluation", "policy learning / imitation"),
    7: ("consultation", "Consultation", "openclaw / consultation provider"),
    8: ("promotion_deployment", "Promotion / Deployment", "deployment planner / orchestrator"),
    9: ("capital_pool_execution", "Capital Pool Execution", "capital pool / order execution"),
    10: ("telemetry_reconciliation", "Telemetry / Reconciliation", "telemetry reconciler / audit"),
    11: ("evolution", "Evolution", "evolution engine / candidate store"),
    12: ("bff_health_monitoring", "BFF Health Monitoring", "bff health monitor / downstream probes"),
}

LOOP_ID_TO_INT: Dict[str, int] = {
    canonical_id: num for num, (canonical_id, _, _) in CANONICAL_TWELVE_LOOPS.items()
}
LOOP_INT_TO_ID: Dict[int, str] = {
    num: canonical_id for num, (canonical_id, _, _) in CANONICAL_TWELVE_LOOPS.items()
}

_TERMINAL_SUCCESS_STATUSES = {"completed", "success", "ok", "passed", "healthy"}
_TERMINAL_FAILURE_STATUSES = {"failed", "error", "rejected", "degraded", "aborted"}
_CONSUMER_SUCCESS_STATUSES = {"completed", "success", "ok", "passed", "healthy", "acknowledged", "accepted", "admitted", "consumed", "valid", ""}
_CONSUMER_FAILURE_STATUSES = {"failed", "error", "rejected", "aborted", "degraded", "declined", "invalid"}

DEFAULT_MAX_AGE_SECONDS = 900
DEFAULT_MAX_FUTURE_SKEW_SECONDS = 60



def parse_timestamp(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def format_timestamp(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat()


def resolve_loop_id_int(value: Any) -> int:
    if isinstance(value, int) and 1 <= value <= 12:
        return value
    val_str = str(value or "").strip().lower()
    if val_str.isdigit():
        num = int(val_str)
        if 1 <= num <= 12:
            return num
    if val_str in LOOP_ID_TO_INT:
        return LOOP_ID_TO_INT[val_str]
    raise ValueError(f"Invalid loop identifier: {value!r}. Must be 1..12 or one of {list(LOOP_ID_TO_INT.keys())}")


@dataclass(frozen=True)
class CanonicalLoopReceipt:
    receipt_id: str
    receipt_type: Literal["stimulus", "terminal", "next_consumer"]
    loop_id: int
    correlation_id: str
    release_id: str
    owner: str
    provenance: Literal["live", "replay", "backfill"]
    status: str = ""
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    degradation_reason: Optional[str] = None
    causation_id: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CanonicalLoopReceipt:
        loop_id = resolve_loop_id_int(data.get("loop_id"))
        receipt_type = str(data.get("receipt_type") or "").strip().lower()
        if receipt_type not in {"stimulus", "terminal", "next_consumer"}:
            raise ValueError(f"Invalid receipt_type: {receipt_type!r}")
        provenance = str(data.get("provenance") or "live").strip().lower()
        if provenance not in {"live", "replay", "backfill"}:
            raise ValueError(f"Invalid provenance: {provenance!r}")
        raw_ts = data.get("observed_at") or data.get("timestamp") or data.get("created_at")
        observed_at = parse_timestamp(raw_ts) or datetime.now(timezone.utc)
        owner = str(data.get("owner") or "").strip() or CANONICAL_TWELVE_LOOPS[loop_id][2]

        return cls(
            receipt_id=str(data["receipt_id"]).strip(),
            receipt_type=receipt_type,
            loop_id=loop_id,
            correlation_id=str(data["correlation_id"]).strip(),
            release_id=str(data["release_id"]).strip(),
            owner=owner,
            provenance=provenance,
            status=str(data.get("status") or "").strip().lower(),
            observed_at=observed_at,
            degradation_reason=data.get("degradation_reason"),
            causation_id=data.get("causation_id"),
            payload=dict(data.get("payload") or {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "receipt_type": self.receipt_type,
            "loop_id": self.loop_id,
            "canonical_id": LOOP_INT_TO_ID.get(self.loop_id),
            "correlation_id": self.correlation_id,
            "release_id": self.release_id,
            "owner": self.owner,
            "provenance": self.provenance,
            "status": self.status,
            "observed_at": format_timestamp(self.observed_at),
            "degradation_reason": self.degradation_reason,
            "causation_id": self.causation_id,
            "payload": self.payload,
        }


@dataclass
class LoopObservation:
    release_id: str
    correlation_id: str
    loop_id: int
    owner: str
    stimulus_id: Optional[str] = None
    stimulus_observed_at: Optional[datetime] = None
    terminal_id: Optional[str] = None
    terminal_status: str = "unobserved"
    terminal_observed_at: Optional[datetime] = None
    next_consumer_receipt_id: Optional[str] = None
    next_consumer_observed_at: Optional[datetime] = None
    status: Literal["open", "complete", "failed", "degraded", "unobserved"] = "unobserved"
    freshness_status: Literal["fresh", "stale", "unavailable"] = "unavailable"
    provenance: Literal["live", "replay", "backfill"] = "live"
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    degradation_reason: Optional[str] = None
    causation_id: Optional[str] = None
    receipt_ids: List[str] = field(default_factory=list)

    @property
    def loop_name(self) -> str:
        return CANONICAL_TWELVE_LOOPS.get(self.loop_id, ("", "", ""))[1]

    @property
    def canonical_id(self) -> str:
        return CANONICAL_TWELVE_LOOPS.get(self.loop_id, ("", "", ""))[0]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "release_id": self.release_id,
            "correlation_id": self.correlation_id,
            "loop_id": self.loop_id,
            "canonical_id": self.canonical_id,
            "loop_name": self.loop_name,
            "owner": self.owner,
            "stimulus_id": self.stimulus_id,
            "stimulus_observed_at": format_timestamp(self.stimulus_observed_at),
            "terminal_id": self.terminal_id,
            "terminal_status": self.terminal_status,
            "terminal_observed_at": format_timestamp(self.terminal_observed_at),
            "next_consumer_receipt_id": self.next_consumer_receipt_id,
            "next_consumer_observed_at": format_timestamp(self.next_consumer_observed_at),
            "status": self.status,
            "freshness_status": self.freshness_status,
            "provenance": self.provenance,
            "observed_at": format_timestamp(self.observed_at),
            "degradation_reason": self.degradation_reason,
            "causation_id": self.causation_id,
            "receipt_ids": list(self.receipt_ids),
        }


class TwelveLoopTruthProjector:
    """Read-only and incremental projector over canonical owner receipts.

    Enforces all invariants required by SD §7.2:
      1. Terminal plus next-consumer receipt is required for completion.
      2. An absent next receipt means open, not complete.
      3. Backfill never overwrites a newer live observation.
      4. Incremental output equals rebuild output.
      5. Static registry data supplies label/order only; registry maturity
         never sets runtime completion.
      6. All writes are idempotent by receipt identity.
    """

    def __init__(
        self,
        store: Optional[Any] = None,
        *,
        max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
        max_future_skew_seconds: int = DEFAULT_MAX_FUTURE_SKEW_SECONDS,
        auto_load: bool = True,
    ) -> None:
        self.store = store
        self.max_age_seconds = max_age_seconds
        self.max_future_skew_seconds = max_future_skew_seconds
        # raw receipts: receipt_id -> CanonicalLoopReceipt
        self._receipts: Dict[str, CanonicalLoopReceipt] = {}
        # projected observations: (release_id, correlation_id, loop_id) -> LoopObservation
        self._observations: Dict[Tuple[str, str, int], LoopObservation] = {}
        # receipt receipts-by-key index: (release_id, correlation_id, loop_id) -> dict[receipt_id, CanonicalLoopReceipt]
        self._receipts_by_key: Dict[Tuple[str, str, int], Dict[str, CanonicalLoopReceipt]] = {}
        # Rollback toggle: when disabled, read projections return degraded / fallback.
        env_enable = os.environ.get("PANTHEON_ENABLE_RECEIPT_LOOP_TRUTH", "true").strip().lower()
        self._enabled = env_enable not in {"false", "0", "no", "off"}
        if self.store is not None and auto_load:
            self.load_from_store()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable_projection(self) -> None:
        self._enabled = True

    def disable_projection(self) -> None:
        """Rollback: Disable the new read projection while preserving source receipts."""
        self._enabled = False

    def load_from_store(self) -> None:
        """Load stored receipts and rebuild in-memory projection."""
        if self.store is None:
            return
        try:
            stored_receipts = self.store.list_receipts()
            for r in stored_receipts:
                self._receipts[r.receipt_id] = r
                key = (r.release_id, r.correlation_id, r.loop_id)
                self._receipts_by_key.setdefault(key, {})[r.receipt_id] = r
            self.rebuild()
        except Exception as exc:
            logger.warning("Failed to load receipts from store: %s", exc)

    def ingest_receipt(self, receipt: CanonicalLoopReceipt) -> LoopObservation:
        """Ingest a single receipt incrementally and update projection."""
        key = (receipt.release_id, receipt.correlation_id, receipt.loop_id)

        # Idempotency check: receipt already ingested
        if receipt.receipt_id in self._receipts:
            obs = self._observations.get(key)
            if obs is not None:
                self._recompute_freshness(obs, now=datetime.now(timezone.utc))
                return obs

        # Store raw receipt
        self._receipts[receipt.receipt_id] = receipt
        self._receipts_by_key.setdefault(key, {})[receipt.receipt_id] = receipt

        if self.store is not None:
            try:
                self.store.record_receipt(receipt)
            except Exception as exc:
                logger.warning("Failed to record receipt to store: %s", exc)

        # Update projection using deterministic reduction
        obs = self._reduce_key(key, self._receipts_by_key[key])
        self._observations[key] = obs

        if self.store is not None:
            try:
                self.store.upsert_observation(obs)
            except Exception as exc:
                logger.warning("Failed to upsert observation to store: %s", exc)

        return obs

    def ingest_receipts(self, receipts: Sequence[CanonicalLoopReceipt]) -> List[LoopObservation]:
        results = []
        for r in receipts:
            results.append(self.ingest_receipt(r))
        return results

    def _reduce_key(
        self,
        key: Tuple[str, str, int],
        receipts_dict: Dict[str, CanonicalLoopReceipt],
        now: Optional[datetime] = None,
    ) -> LoopObservation:
        """Deterministic reduction over all receipts for a single key."""
        release_id, correlation_id, loop_id = key
        curr_time = now or datetime.now(timezone.utc)
        receipts = list(receipts_dict.values())

        if not receipts:
            canonical_id, name, default_owner = CANONICAL_TWELVE_LOOPS.get(loop_id, ("", "", "unknown"))
            return LoopObservation(
                release_id=release_id,
                correlation_id=correlation_id,
                loop_id=loop_id,
                owner=default_owner,
                status="unobserved",
                freshness_status="unavailable",
                provenance="live",
                observed_at=curr_time,
                degradation_reason="no canonical receipts observed",
            )

        provenance_rank = {"backfill": 0, "replay": 1, "live": 2}
        overall_provenance_val = max(provenance_rank.get(r.provenance, 0) for r in receipts)
        overall_provenance: Literal["live", "replay", "backfill"] = {
            0: "backfill",
            1: "replay",
            2: "live",
        }[overall_provenance_val]

        stimulus_receipts = [r for r in receipts if r.receipt_type == "stimulus"]
        terminal_receipts = [r for r in receipts if r.receipt_type == "terminal"]
        next_receipts = [r for r in receipts if r.receipt_type == "next_consumer"]

        # Select stimulus:
        # Prefer higher provenance, then latest observed_at, then receipt_id
        chosen_stimulus: Optional[CanonicalLoopReceipt] = None
        if stimulus_receipts:
            chosen_stimulus = max(
                stimulus_receipts,
                key=lambda r: (provenance_rank.get(r.provenance, 0), r.observed_at, r.receipt_id),
            )

        # Select terminal:
        # 1. Backfill cannot replace newer live truth. If stimulus is live, terminal must not be backfill.
        # 2. Terminal must be causally and temporally compatible with stimulus.
        chosen_terminal: Optional[CanonicalLoopReceipt] = None
        valid_terminals = terminal_receipts
        if chosen_stimulus is not None:
            min_prov = provenance_rank.get(chosen_stimulus.provenance, 0)
            valid_terminals = [
                r for r in valid_terminals
                if provenance_rank.get(r.provenance, 0) >= min_prov
                and r.observed_at >= (chosen_stimulus.observed_at - timedelta(seconds=self.max_future_skew_seconds))
            ]
        elif overall_provenance == "live":
            valid_terminals = [
                r for r in valid_terminals
                if r.provenance != "backfill"
            ]

        if valid_terminals:
            chosen_terminal = max(
                valid_terminals,
                key=lambda r: (provenance_rank.get(r.provenance, 0), r.observed_at, r.receipt_id),
            )

        # Select next_consumer:
        chosen_next: Optional[CanonicalLoopReceipt] = None
        valid_nexts = next_receipts
        if chosen_stimulus is not None:
            min_prov = provenance_rank.get(chosen_stimulus.provenance, 0)
            valid_nexts = [
                r for r in valid_nexts
                if provenance_rank.get(r.provenance, 0) >= min_prov
                and r.observed_at >= (chosen_stimulus.observed_at - timedelta(seconds=self.max_future_skew_seconds))
            ]
        elif overall_provenance == "live":
            valid_nexts = [
                r for r in valid_nexts
                if r.provenance != "backfill"
            ]

        if valid_nexts:
            chosen_next = max(
                valid_nexts,
                key=lambda r: (provenance_rank.get(r.provenance, 0), r.observed_at, r.receipt_id),
            )

        # Determine owner
        loop_tuple = CANONICAL_TWELVE_LOOPS.get(loop_id)
        default_owner = loop_tuple[2] if loop_tuple else "unknown"
        owner = (
            (chosen_terminal.owner if chosen_terminal and chosen_terminal.owner else None)
            or (chosen_stimulus.owner if chosen_stimulus and chosen_stimulus.owner else None)
            or default_owner
        )

        # Determine causation_id
        causation_id = (
            (chosen_terminal.causation_id if chosen_terminal else None)
            or (chosen_stimulus.causation_id if chosen_stimulus else None)
            or (chosen_next.causation_id if chosen_next else None)
        )

        # Max observed_at
        all_observed = [r.observed_at for r in receipts]
        max_observed_at = max(all_observed) if all_observed else curr_time

        obs = LoopObservation(
            release_id=release_id,
            correlation_id=correlation_id,
            loop_id=loop_id,
            owner=owner,
            stimulus_id=chosen_stimulus.receipt_id if chosen_stimulus else None,
            stimulus_observed_at=chosen_stimulus.observed_at if chosen_stimulus else None,
            terminal_id=chosen_terminal.receipt_id if chosen_terminal else None,
            terminal_status=chosen_terminal.status if chosen_terminal else ("pending" if chosen_stimulus else "unobserved"),
            terminal_observed_at=chosen_terminal.observed_at if chosen_terminal else None,
            next_consumer_receipt_id=chosen_next.receipt_id if chosen_next else None,
            next_consumer_observed_at=chosen_next.observed_at if chosen_next else None,
            status="unobserved",
            freshness_status="unavailable",
            provenance=overall_provenance,
            observed_at=max_observed_at,
            causation_id=causation_id,
            receipt_ids=sorted(receipts_dict.keys()),
        )

        # Status determination:
        if chosen_terminal is not None:
            t_status = (chosen_terminal.status or "").lower()
            if t_status in _TERMINAL_FAILURE_STATUSES:
                obs.status = "failed"
                obs.degradation_reason = (
                    chosen_terminal.degradation_reason
                    or f"loop execution failed with status {chosen_terminal.status}"
                )
            elif t_status in _TERMINAL_SUCCESS_STATUSES:
                if chosen_next is not None:
                    next_status = (chosen_next.status or "").lower()
                    if next_status in _CONSUMER_FAILURE_STATUSES:
                        obs.status = "failed"
                        obs.degradation_reason = (
                            chosen_next.degradation_reason
                            or f"next-consumer receipt {chosen_next.receipt_id} rejected with status {chosen_next.status}"
                        )
                    else:
                        obs.status = "complete"
                        obs.degradation_reason = None
                else:
                    obs.status = "open"
                    obs.degradation_reason = "awaiting next-consumer receipt acknowledgement"
            else:
                obs.status = "open"
                obs.degradation_reason = f"terminal execution in status {chosen_terminal.status}"
        elif chosen_stimulus is not None:
            obs.status = "open"
            obs.degradation_reason = "processing; awaiting terminal execution receipt"
        else:
            obs.status = "open" if chosen_next else "unobserved"
            obs.degradation_reason = (
                "orphan next-consumer receipt; awaiting stimulus and terminal execution receipts"
                if chosen_next else "no canonical receipts observed"
            )

        self._recompute_freshness(obs, now=curr_time)
        return obs

    def _recompute_freshness(self, obs: LoopObservation, now: Optional[datetime] = None) -> None:
        """Recompute dynamic freshness status and degradation reason for read operations."""
        curr_time = now or datetime.now(timezone.utc)
        if obs.status == "unobserved":
            obs.freshness_status = "unavailable"
        else:
            age = (curr_time - obs.observed_at).total_seconds()
            if -self.max_future_skew_seconds <= age <= self.max_age_seconds:
                obs.freshness_status = "fresh"
            else:
                obs.freshness_status = "stale"
                stale_msg = f"observation age ({int(age)}s) exceeds freshness window ({self.max_age_seconds}s)"
                if obs.status != "failed":
                    if obs.degradation_reason:
                        if stale_msg not in obs.degradation_reason:
                            obs.degradation_reason = f"{obs.degradation_reason}; {stale_msg}"
                    else:
                        obs.degradation_reason = stale_msg

    def _recompute_observation_status(self, obs: LoopObservation, now: Optional[datetime] = None) -> None:
        """Compatibility alias for _recompute_freshness."""
        self._recompute_freshness(obs, now=now)

    def rebuild(self) -> List[LoopObservation]:
        """Replay all source receipts to rebuild observations from scratch.

        SD §7.2: Rebuild output must equal incremental output.
        Uses the exact same deterministic reduction _reduce_key as incremental ingestion.
        """
        now = datetime.now(timezone.utc)
        self._observations.clear()
        for key, receipts_dict in self._receipts_by_key.items():
            obs = self._reduce_key(key, receipts_dict, now=now)
            self._observations[key] = obs
            if self.store is not None:
                try:
                    self.store.upsert_observation(obs)
                except Exception as exc:
                    logger.warning("Failed to upsert observation to store during rebuild: %s", exc)
        return list(self._observations.values())

    def get_observation(
        self,
        release_id: str,
        correlation_id: str,
        loop_id: int,
    ) -> Optional[LoopObservation]:
        if not self._enabled:
            return None
        obs = self._observations.get((release_id, correlation_id, loop_id))
        if obs is not None:
            self._recompute_freshness(obs, now=datetime.now(timezone.utc))
        return obs

    def list_observations(
        self,
        *,
        release_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        loop_id: Optional[int] = None,
    ) -> List[LoopObservation]:
        if not self._enabled:
            return []
        now = datetime.now(timezone.utc)
        items = list(self._observations.values())
        for obs in items:
            self._recompute_freshness(obs, now=now)
        if release_id:
            items = [obs for obs in items if obs.release_id == release_id]
        if correlation_id:
            items = [obs for obs in items if obs.correlation_id == correlation_id]
        if loop_id is not None:
            items = [obs for obs in items if obs.loop_id == loop_id]
        return sorted(items, key=lambda obs: (obs.release_id, obs.correlation_id, obs.loop_id))

    def project_twelve_canonical_loops(
        self,
        release_id: str,
        correlation_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Project exactly the twelve canonical loop rows for release and correlation.

        SA ADR-05: emits twelve rows with loop ID and owner; stimulus, terminal,
        and next-consumer receipt identities; observed release/service identity;
        status, freshness and degradation reason; and correlation/causation continuity.
        """
        rows: List[Dict[str, Any]] = []
        now = datetime.now(timezone.utc)

        if not self._enabled:
            # Rollback: Return typed unavailable / degraded rows while preserving underlying receipts
            for loop_id in range(1, 13):
                canonical_id, name, owner = CANONICAL_TWELVE_LOOPS[loop_id]
                rows.append({
                    "loop_id": loop_id,
                    "canonical_id": canonical_id,
                    "loop_name": name,
                    "owner": owner,
                    "release_id": release_id,
                    "correlation_id": correlation_id,
                    "stimulus_id": None,
                    "terminal_id": None,
                    "terminal_status": "unavailable",
                    "next_consumer_receipt_id": None,
                    "status": "unobserved",
                    "freshness_status": "unavailable",
                    "provenance": "unavailable",
                    "observed_at": None,
                    "degradation_reason": "twelve-loop receipt projection is disabled (rollback mode)",
                    "causation_id": None,
                    "receipt_ids": [],
                })
            return rows

        # Find matching observations for this release_id (and correlation_id if given)
        obs_by_loop: Dict[int, LoopObservation] = {}
        for (rel, corr, loop_id), obs in self._observations.items():
            if rel == release_id:
                if correlation_id is None or corr == correlation_id:
                    self._recompute_freshness(obs, now=now)
                    existing = obs_by_loop.get(loop_id)
                    if existing is None or obs.observed_at > existing.observed_at:
                        obs_by_loop[loop_id] = obs

        for loop_id in range(1, 13):
            canonical_id, name, owner = CANONICAL_TWELVE_LOOPS[loop_id]
            obs = obs_by_loop.get(loop_id)
            if obs is not None:
                self._recompute_freshness(obs, now=now)
                rows.append(obs.to_dict())
            else:
                rows.append({
                    "loop_id": loop_id,
                    "canonical_id": canonical_id,
                    "loop_name": name,
                    "owner": owner,
                    "release_id": release_id,
                    "correlation_id": correlation_id,
                    "stimulus_id": None,
                    "terminal_id": None,
                    "terminal_status": "unobserved",
                    "next_consumer_receipt_id": None,
                    "status": "unobserved",
                    "freshness_status": "unavailable",
                    "provenance": "unavailable",
                    "observed_at": None,
                    "degradation_reason": "no runtime receipts observed for this release and correlation",
                    "causation_id": None,
                    "receipt_ids": [],
                })

        return rows
