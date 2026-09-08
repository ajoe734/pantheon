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
from datetime import datetime, timezone
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
        *,
        max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
        max_future_skew_seconds: int = DEFAULT_MAX_FUTURE_SKEW_SECONDS,
    ) -> None:
        self.max_age_seconds = max_age_seconds
        self.max_future_skew_seconds = max_future_skew_seconds
        # raw receipts: receipt_id -> CanonicalLoopReceipt
        self._receipts: Dict[str, CanonicalLoopReceipt] = {}
        # projected observations: (release_id, correlation_id, loop_id) -> LoopObservation
        self._observations: Dict[Tuple[str, str, int], LoopObservation] = {}
        # receipt receipts-by-key index: (release_id, correlation_id, loop_id) -> list[receipt_id]
        self._receipts_by_key: Dict[Tuple[str, str, int], List[str]] = {}
        # Rollback toggle: when disabled, read projections return degraded / fallback.
        env_enable = os.environ.get("PANTHEON_ENABLE_RECEIPT_LOOP_TRUTH", "true").strip().lower()
        self._enabled = env_enable not in {"false", "0", "no", "off"}

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable_projection(self) -> None:
        self._enabled = True

    def disable_projection(self) -> None:
        """Rollback: Disable the new read projection while preserving source receipts."""
        self._enabled = False

    def ingest_receipt(self, receipt: CanonicalLoopReceipt) -> LoopObservation:
        """Ingest a single receipt incrementally and update projection."""
        key = (receipt.release_id, receipt.correlation_id, receipt.loop_id)

        # Idempotency check: receipt already ingested
        if receipt.receipt_id in self._receipts:
            return self._observations[key]

        # Store raw receipt
        self._receipts[receipt.receipt_id] = receipt
        self._receipts_by_key.setdefault(key, []).append(receipt.receipt_id)

        # Update projection
        obs = self._apply_receipt_to_observation(receipt)
        return obs

    def ingest_receipts(self, receipts: Sequence[CanonicalLoopReceipt]) -> List[LoopObservation]:
        results = []
        for r in receipts:
            results.append(self.ingest_receipt(r))
        return results

    def _apply_receipt_to_observation(self, receipt: CanonicalLoopReceipt) -> LoopObservation:
        key = (receipt.release_id, receipt.correlation_id, receipt.loop_id)
        existing = self._observations.get(key)

        if existing is None:
            loop_tuple = CANONICAL_TWELVE_LOOPS.get(receipt.loop_id)
            owner = receipt.owner or (loop_tuple[2] if loop_tuple else "unknown")
            obs = LoopObservation(
                release_id=receipt.release_id,
                correlation_id=receipt.correlation_id,
                loop_id=receipt.loop_id,
                owner=owner,
                provenance=receipt.provenance,
                observed_at=receipt.observed_at,
                receipt_ids=[receipt.receipt_id],
            )
            self._observations[key] = obs
        else:
            obs = existing
            if receipt.receipt_id not in obs.receipt_ids:
                obs.receipt_ids.append(receipt.receipt_id)

        # Backfill protection rule: "backfill never overwrites a newer live observation; backfill cannot replace newer live truth"
        if receipt.provenance == "backfill" and obs.provenance == "live":
            # If observation already has live terminal truth, backfill cannot overwrite terminal or next_consumer
            if receipt.receipt_type == "stimulus" and obs.stimulus_id is None:
                obs.stimulus_id = receipt.receipt_id
                obs.stimulus_observed_at = receipt.observed_at
            if obs.causation_id is None and receipt.causation_id:
                obs.causation_id = receipt.causation_id
            # Do NOT overwrite terminal_status, terminal_id, next_consumer, observed_at, or provenance
            self._recompute_observation_status(obs)
            return obs

        # If incoming receipt is live and existing was backfill: live promotes provenance
        if receipt.provenance == "live" and obs.provenance != "live":
            obs.provenance = "live"

        # Apply based on receipt type
        if receipt.receipt_type == "stimulus":
            # If no stimulus yet, or newer/preferred stimulus
            if obs.stimulus_id is None or receipt.provenance == "live":
                obs.stimulus_id = receipt.receipt_id
                obs.stimulus_observed_at = receipt.observed_at
        elif receipt.receipt_type == "terminal":
            # Terminal receipt updates terminal execution truth
            obs.terminal_id = receipt.receipt_id
            obs.terminal_status = receipt.status or "completed"
            obs.terminal_observed_at = receipt.observed_at
            if receipt.owner:
                obs.owner = receipt.owner
            if receipt.degradation_reason:
                obs.degradation_reason = receipt.degradation_reason
        elif receipt.receipt_type == "next_consumer":
            # Next consumer receipt confirms consumption downstream
            obs.next_consumer_receipt_id = receipt.receipt_id
            obs.next_consumer_observed_at = receipt.observed_at

        if receipt.causation_id and not obs.causation_id:
            obs.causation_id = receipt.causation_id

        # Update observed_at if this receipt is more recent
        if receipt.observed_at > obs.observed_at:
            obs.observed_at = receipt.observed_at

        self._recompute_observation_status(obs)
        return obs

    def _recompute_observation_status(self, obs: LoopObservation, now: Optional[datetime] = None) -> None:
        """Derive status, freshness_status, and degradation_reason from receipts."""
        curr_time = now or datetime.now(timezone.utc)

        # 1. Status determination
        # Terminal error / failure
        if obs.terminal_status in _TERMINAL_FAILURE_STATUSES:
            obs.status = "failed"
            obs.degradation_reason = obs.degradation_reason or f"loop execution failed with status {obs.terminal_status}"
        elif obs.terminal_status in _TERMINAL_SUCCESS_STATUSES:
            if obs.next_consumer_receipt_id is not None:
                # Terminal plus next-consumer receipt is required for completion
                obs.status = "complete"
                obs.degradation_reason = None
            else:
                # An absent next receipt means open, not complete
                obs.status = "open"
                obs.degradation_reason = "awaiting next-consumer receipt acknowledgement"
        elif obs.stimulus_id is not None:
            # Stimulus received, awaiting terminal
            obs.status = "open"
            obs.terminal_status = obs.terminal_status if obs.terminal_status != "unobserved" else "pending"
            obs.degradation_reason = "processing; awaiting terminal execution receipt"
        else:
            obs.status = "unobserved"
            obs.degradation_reason = "no canonical receipts observed"

        # 2. Freshness determination
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
                        obs.degradation_reason = f"{obs.degradation_reason}; {stale_msg}"
                    else:
                        obs.degradation_reason = stale_msg


    def rebuild(self) -> List[LoopObservation]:
        """Replay all source receipts to rebuild observations from scratch.

        SD §7.2: Rebuild output must equal incremental output.
        To guarantee order invariance (e.g. backfill vs live, stimulus vs terminal),
        receipts for each key are partitioned into live and backfill/replay,
        preserving causal order.
        """
        self._observations.clear()

        # Sort receipts deterministically: live after backfill so live truth dominates,
        # and ordered by observed_at
        provenance_rank = {"backfill": 0, "replay": 1, "live": 2}
        type_rank = {"stimulus": 0, "terminal": 1, "next_consumer": 2}

        sorted_receipts = sorted(
            self._receipts.values(),
            key=lambda r: (
                r.release_id,
                r.correlation_id,
                r.loop_id,
                provenance_rank.get(r.provenance, 0),
                type_rank.get(r.receipt_type, 0),
                r.observed_at,
                r.receipt_id,
            ),
        )

        for r in sorted_receipts:
            self._apply_receipt_to_observation(r)

        return list(self._observations.values())

    def get_observation(
        self,
        release_id: str,
        correlation_id: str,
        loop_id: int,
    ) -> Optional[LoopObservation]:
        if not self._enabled:
            return None
        return self._observations.get((release_id, correlation_id, loop_id))

    def list_observations(
        self,
        *,
        release_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        loop_id: Optional[int] = None,
    ) -> List[LoopObservation]:
        if not self._enabled:
            return []
        items = list(self._observations.values())
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
                    # Pick newest observation if correlation_id was omitted
                    existing = obs_by_loop.get(loop_id)
                    if existing is None or obs.observed_at > existing.observed_at:
                        obs_by_loop[loop_id] = obs

        for loop_id in range(1, 13):
            canonical_id, name, owner = CANONICAL_TWELVE_LOOPS[loop_id]
            obs = obs_by_loop.get(loop_id)
            if obs is not None:
                row = obs.to_dict()
                rows.append(row)
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
