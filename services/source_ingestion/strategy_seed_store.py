"""Persistent store for StrategySpecSeed objects.

JSONL dev implementation backed by JsonlRegistryStore. Production uses Postgres.
The store is the write boundary: only the materializer and seed review flows
write seeds. All other services read through the owner API.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
import hashlib
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from services.source_ingestion.registry.jsonl_store import JsonlRegistryStore
from services.source_ingestion.negative_memory import (
    is_blocking_negative_memory_match,
    negative_memory_record_from_seed,
)
from services.source_ingestion.strategy_seed_builder import (
    StrategySpecSeed,
    StrategySpecSeedStatus,
)

_FORBIDDEN_EXECUTION_HINTS = frozenset(
    [
        "broker",
        "live",
        "order_router",
        "execution",
        "runtime_direct",
        "lean_direct",
        "live_trading",
        "order_routing",
    ]
)


def _assert_no_direct_execution_route(seed: StrategySpecSeed) -> None:
    """Raise if seed metadata requests a direct execution route."""
    meta = seed.metadata
    if meta.get("execution_route") not in (None, "none", ""):
        route = meta["execution_route"]
        if str(route).lower() not in ("none", "research"):
            raise StrategySpecSeedStoreError(
                f"Seed metadata requests forbidden execution route: {route!r}"
            )
    backend = str(seed.backend_hint or "").lower()
    for forbidden in _FORBIDDEN_EXECUTION_HINTS:
        if forbidden in backend:
            raise StrategySpecSeedStoreError(
                f"Seed backend_hint contains forbidden execution keyword: {seed.backend_hint!r}"
            )
    lineage = seed.lineage
    if lineage.get("execution_route") not in (None, "none", ""):
        route = lineage["execution_route"]
        if str(route).lower() not in ("none",):
            raise StrategySpecSeedStoreError(
                f"Seed lineage requests forbidden execution route: {route!r}"
            )


def _assert_no_blocking_negative_memory(seed: StrategySpecSeed) -> None:
    if is_blocking_negative_memory_match(seed.negative_memory_match):
        match = dict(seed.negative_memory_match)
        memory_id = str(match.get("matched_memory_id") or "negative-memory-record")
        raise StrategySpecSeedStoreError(
            f"Seed has blocking negative_memory_match against {memory_id}: {match.get('reason')}"
        )


class StrategySpecSeedStoreError(ValueError):
    """Raised when a store invariant is violated."""


class StrategySpecSeedReviewError(StrategySpecSeedStoreError):
    """Raised when a seed review action cannot be applied."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SeedReviewDecisionAction(str, Enum):
    ACCEPT = "accept"
    REJECT = "reject"
    REQUEST_EVIDENCE = "request_evidence"
    CONVERT_TO_SPEC_SEED = "convert_to_spec_seed"
    ARCHIVE = "archive"
    MERGE = "merge"


@dataclass(frozen=True)
class SeedReviewDecision:
    """Audit record for a governed StrategySpecSeed review transition."""

    decision_id: str
    seed_id: str
    reviewer_id: str
    decision: str
    reason: str
    target_refs: Sequence[Mapping[str, Any]]
    created_at: str
    from_status: str
    to_status: str
    idempotency_key: str | None = None
    request_hash: str | None = None
    idempotent_replay: bool = False

    def to_dict(self) -> dict[str, Any]:
        record = {
            "decision_id": self.decision_id,
            "seed_id": self.seed_id,
            "reviewer_id": self.reviewer_id,
            "decision": self.decision,
            "reason": self.reason,
            "target_refs": [dict(item) for item in self.target_refs],
            "created_at": self.created_at,
            "from_status": self.from_status,
            "to_status": self.to_status,
        }
        if self.idempotency_key:
            record["idempotency_key"] = self.idempotency_key
        if self.request_hash:
            record["request_hash"] = self.request_hash
        return record

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
        *,
        idempotent_replay: bool = False,
    ) -> "SeedReviewDecision":
        return cls(
            decision_id=str(data.get("decision_id") or ""),
            seed_id=str(data.get("seed_id") or ""),
            reviewer_id=str(data.get("reviewer_id") or ""),
            decision=str(data.get("decision") or ""),
            reason=str(data.get("reason") or ""),
            target_refs=[
                dict(item)
                for item in data.get("target_refs") or []
                if isinstance(item, Mapping)
            ],
            created_at=str(data.get("created_at") or ""),
            from_status=str(data.get("from_status") or ""),
            to_status=str(data.get("to_status") or ""),
            idempotency_key=str(data.get("idempotency_key") or "").strip() or None,
            request_hash=str(data.get("request_hash") or "").strip() or None,
            idempotent_replay=idempotent_replay,
        )


_TERMINAL_REVIEW_STATUSES = frozenset(
    {
        StrategySpecSeedStatus.REJECTED.value,
        StrategySpecSeedStatus.ARCHIVED_AS_INSIGHT.value,
        StrategySpecSeedStatus.MERGED.value,
    }
)

_REVIEW_TRANSITIONS: dict[str, dict[SeedReviewDecisionAction, str]] = {
    StrategySpecSeedStatus.DRAFT.value: {
        SeedReviewDecisionAction.ACCEPT: StrategySpecSeedStatus.ACCEPTED.value,
        SeedReviewDecisionAction.REJECT: StrategySpecSeedStatus.REJECTED.value,
        SeedReviewDecisionAction.REQUEST_EVIDENCE: StrategySpecSeedStatus.NEEDS_MORE_EVIDENCE.value,
        SeedReviewDecisionAction.ARCHIVE: StrategySpecSeedStatus.ARCHIVED_AS_INSIGHT.value,
        SeedReviewDecisionAction.MERGE: StrategySpecSeedStatus.MERGED.value,
    },
    StrategySpecSeedStatus.NEEDS_MORE_EVIDENCE.value: {
        SeedReviewDecisionAction.ACCEPT: StrategySpecSeedStatus.ACCEPTED.value,
        SeedReviewDecisionAction.REJECT: StrategySpecSeedStatus.REJECTED.value,
        SeedReviewDecisionAction.REQUEST_EVIDENCE: StrategySpecSeedStatus.NEEDS_MORE_EVIDENCE.value,
        SeedReviewDecisionAction.ARCHIVE: StrategySpecSeedStatus.ARCHIVED_AS_INSIGHT.value,
        SeedReviewDecisionAction.MERGE: StrategySpecSeedStatus.MERGED.value,
    },
    StrategySpecSeedStatus.ACCEPTED.value: {
        SeedReviewDecisionAction.CONVERT_TO_SPEC_SEED: StrategySpecSeedStatus.PROMOTED_TO_STRATEGY_SPEC.value,
        SeedReviewDecisionAction.REJECT: StrategySpecSeedStatus.REJECTED.value,
        SeedReviewDecisionAction.REQUEST_EVIDENCE: StrategySpecSeedStatus.NEEDS_MORE_EVIDENCE.value,
        SeedReviewDecisionAction.ARCHIVE: StrategySpecSeedStatus.ARCHIVED_AS_INSIGHT.value,
        SeedReviewDecisionAction.MERGE: StrategySpecSeedStatus.MERGED.value,
    },
}


class StrategySpecSeedStore:
    """JSONL-backed store for StrategySpecSeed persistence.

    One record per seed_id (upsert semantics).  Idempotency: the builder
    derives seed_id from a stable hash of evidence_bundle_id + source_ids +
    hypothesis, so re-materializing the same evidence produces the same key
    and upserts rather than duplicates.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        resolved = (
            Path(path)
            if path
            else Path(
                os.environ.get(
                    "STRATEGY_SEED_STORE_PATH",
                    "data/strategy_seed_store/seeds.jsonl",
                )
            )
        )
        self._store = JsonlRegistryStore(resolved, id_field="seed_id")

    @property
    def path(self) -> Path:
        return self._store.path

    def save(self, seed: StrategySpecSeed) -> None:
        """Persist seed (upsert).  Validates governance invariants before writing."""
        _assert_no_direct_execution_route(seed)
        _assert_no_blocking_negative_memory(seed)
        record = seed.to_dict()
        # Promote license_scope and allowed_use to top-level queryable fields.
        record.setdefault("license_scope", seed.metadata.get("source_license_scope", ""))
        record.setdefault("allowed_use", list(seed.metadata.get("access_scope") or []))
        self._store.upsert(record)

    def get(self, seed_id: str) -> StrategySpecSeed | None:
        record = self._store.read_by_id(seed_id)
        if record is None:
            return None
        return StrategySpecSeed.from_dict(record)

    def list_all(self) -> list[StrategySpecSeed]:
        return [StrategySpecSeed.from_dict(r) for r in self._store.read_all()]

    def list_by_status(self, status: str | StrategySpecSeedStatus) -> list[StrategySpecSeed]:
        target = status.value if isinstance(status, StrategySpecSeedStatus) else str(status)
        return [
            StrategySpecSeed.from_dict(r)
            for r in self._store.read_all()
            if r.get("status") == target
        ]

    def list_by_bundle(self, evidence_bundle_id: str) -> list[StrategySpecSeed]:
        return [
            StrategySpecSeed.from_dict(r)
            for r in self._store.read_all()
            if r.get("evidence_bundle_id") == evidence_bundle_id
        ]

    def list_negative_memory_records(self) -> list[dict[str, Any]]:
        """Return rejected/retired/failed seed records as matcher inputs."""
        records: list[dict[str, Any]] = []
        for raw in self._store.read_all():
            record = negative_memory_record_from_seed(raw)
            if record is not None:
                records.append(record)
        return records

    def record_review_decision(
        self,
        seed_id: str,
        *,
        decision: str | SeedReviewDecisionAction,
        reviewer_id: str,
        reason: str = "",
        target_refs: Sequence[Mapping[str, Any]] = (),
        created_at: datetime | str | None = None,
        idempotency_key: str | None = None,
        request_hash: str | None = None,
    ) -> tuple[StrategySpecSeed, SeedReviewDecision]:
        """Apply a governed review decision and append an audit record."""
        action = _normalize_review_decision(decision)
        if action == SeedReviewDecisionAction.MERGE:
            raise StrategySpecSeedReviewError(
                "invalid_review_action",
                "Use merge_seed for merge review decisions.",
            )
        seed = self._load_seed_for_review(seed_id)
        existing_decision = _find_review_decision_by_idempotency_key(seed, idempotency_key)
        if existing_decision is not None:
            _assert_review_idempotency_match(
                existing_decision,
                action=action,
                reviewer_id=reviewer_id,
                reason=reason,
                target_refs=target_refs,
                request_hash=request_hash,
            )
            return seed, replace(existing_decision, idempotent_replay=True)

        from_status = _status_value(seed.status)
        to_status = _review_transition(from_status, action)
        decision_record = _seed_review_decision(
            seed=seed,
            action=action,
            reviewer_id=reviewer_id,
            reason=reason,
            target_refs=target_refs,
            created_at=created_at,
            from_status=from_status,
            to_status=to_status,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        updated = self._save_review_transition(seed, to_status, decision_record)
        return updated, decision_record

    def merge_seed(
        self,
        seed_id: str,
        *,
        target_seed_id: str,
        reviewer_id: str,
        reason: str = "",
        created_at: datetime | str | None = None,
        idempotency_key: str | None = None,
        request_hash: str | None = None,
        target_refs: Sequence[Mapping[str, Any]] = (),
    ) -> tuple[StrategySpecSeed, SeedReviewDecision]:
        """Mark a seed as merged into another seed candidate and audit the action."""
        source = self._load_seed_for_review(seed_id)
        target = _require_text(target_seed_id, "target_seed_id")
        refs = list(target_refs) or [{"type": "strategy_spec_seed", "id": target}]
        existing_decision = _find_review_decision_by_idempotency_key(source, idempotency_key)
        if existing_decision is not None:
            _assert_review_idempotency_match(
                existing_decision,
                action=SeedReviewDecisionAction.MERGE,
                reviewer_id=reviewer_id,
                reason=reason,
                target_refs=refs,
                request_hash=request_hash,
            )
            return source, replace(existing_decision, idempotent_replay=True)

        if target == source.seed_id:
            raise StrategySpecSeedReviewError(
                "invalid_merge_target",
                "target_seed_id must be different from seed_id",
            )
        if self.get(target) is None:
            raise StrategySpecSeedReviewError(
                "merge_target_not_found",
                f"StrategySpecSeed merge target not found: {target}",
            )

        from_status = _status_value(source.status)
        to_status = _review_transition(from_status, SeedReviewDecisionAction.MERGE)
        decision_record = _seed_review_decision(
            seed=source,
            action=SeedReviewDecisionAction.MERGE,
            reviewer_id=reviewer_id,
            reason=reason,
            target_refs=refs,
            created_at=created_at,
            from_status=from_status,
            to_status=to_status,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        updated = self._save_review_transition(
            source,
            to_status,
            decision_record,
            lineage_updates={"merged_into_seed_id": target},
        )
        return updated, decision_record

    def record_replication_submission(
        self,
        seed_id: str,
        *,
        replication_ref: str,
        experiment_task_id: str,
        strategy_id: str,
        strategy_spec_version: str,
        submitted_by: str,
        submitted_at: str,
        idempotency_key: str,
        research_task_ref: str | None = None,
    ) -> StrategySpecSeed:
        """Attach a research replication submission ref to seed lineage."""
        seed = self.get(seed_id)
        if seed is None:
            raise StrategySpecSeedStoreError(f"StrategySpecSeed not found: {seed_id}")

        lineage = dict(seed.lineage)
        existing_ref = str(lineage.get("replication_ref") or "").strip()
        existing_task_id = str(lineage.get("experiment_task_id") or "").strip()
        if existing_ref and existing_task_id:
            return seed

        submission = {
            "replication_ref": _require_text(replication_ref, "replication_ref"),
            "experiment_task_id": _require_text(experiment_task_id, "experiment_task_id"),
            "strategy_id": _require_text(strategy_id, "strategy_id"),
            "strategy_spec_version": _require_text(strategy_spec_version, "strategy_spec_version"),
            "submitted_by": _require_text(submitted_by, "submitted_by"),
            "submitted_at": _require_text(submitted_at, "submitted_at"),
            "idempotency_key": _require_text(idempotency_key, "idempotency_key"),
            "registry_write_performed": False,
            "execution_route": "none",
        }
        if research_task_ref:
            submission["research_task_ref"] = str(research_task_ref)

        lineage.update(submission)
        lineage["registry_write_performed"] = False
        lineage["execution_route"] = "none"
        submissions = list(lineage.get("replication_submissions") or [])
        already_recorded = any(
            item.get("replication_ref") == submission["replication_ref"]
            for item in submissions
            if isinstance(item, dict)
        )
        if not already_recorded:
            submissions.append(dict(submission))
        lineage["replication_submissions"] = submissions

        payload = seed.to_dict()
        payload["lineage"] = lineage
        updated = StrategySpecSeed.from_dict(payload)
        self.save(updated)
        return updated

    def _load_seed_for_review(self, seed_id: str) -> StrategySpecSeed:
        normalized = _require_text(seed_id, "seed_id")
        seed = self.get(normalized)
        if seed is None:
            raise StrategySpecSeedReviewError(
                "seed_not_found",
                f"StrategySpecSeed not found: {normalized}",
            )
        return seed

    def _save_review_transition(
        self,
        seed: StrategySpecSeed,
        to_status: str,
        decision: SeedReviewDecision,
        *,
        lineage_updates: Mapping[str, Any] | None = None,
    ) -> StrategySpecSeed:
        payload = seed.to_dict()
        payload["status"] = to_status
        lineage = dict(payload.get("lineage") or {})
        decisions = [
            dict(item)
            for item in lineage.get("review_decisions") or []
            if isinstance(item, Mapping)
        ]
        decision_payload = decision.to_dict()
        decisions.append(decision_payload)
        lineage["review_decisions"] = decisions
        lineage["last_review_decision"] = decision_payload
        lineage["review_status"] = to_status
        lineage["review_state_machine"] = "strategy_seed_review_v1"
        lineage["registry_write_performed"] = False
        lineage["execution_route"] = "none"
        if to_status == StrategySpecSeedStatus.PROMOTED_TO_STRATEGY_SPEC.value:
            lineage["strategy_spec_conversion_eligible"] = True
            lineage["promotion_review_decision_id"] = decision.decision_id
        if lineage_updates:
            lineage.update(dict(lineage_updates))
        payload["lineage"] = lineage
        updated = StrategySpecSeed.from_dict(payload)
        self.save(updated)
        return updated

    def get_by_bundle_idempotent(
        self,
        evidence_bundle_id: str,
        source_ids: Sequence[str],
    ) -> StrategySpecSeed | None:
        """Return an existing seed for this bundle+sources if one exists.

        Used by the materializer to detect idempotent re-runs without
        re-building the full seed payload.
        """
        source_set = set(source_ids)
        for r in self._store.read_all():
            if r.get("evidence_bundle_id") != evidence_bundle_id:
                continue
            stored_sources = set(r.get("source_ids") or [])
            if stored_sources == source_set:
                return StrategySpecSeed.from_dict(r)
        return None

    def count(self) -> int:
        return len(self._store.read_all())


def _require_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise StrategySpecSeedStoreError(f"{field_name} is required")
    return text


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(value: datetime | str | None) -> str:
    if value is None:
        value = _utc_now()
    if isinstance(value, str):
        return value
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _status_value(status: StrategySpecSeedStatus | str) -> str:
    return status.value if isinstance(status, StrategySpecSeedStatus) else str(status)


def _normalize_review_decision(value: str | SeedReviewDecisionAction) -> SeedReviewDecisionAction:
    if isinstance(value, SeedReviewDecisionAction):
        return value
    normalized = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "request_more_evidence": SeedReviewDecisionAction.REQUEST_EVIDENCE.value,
        "needs_more_evidence": SeedReviewDecisionAction.REQUEST_EVIDENCE.value,
        "convert": SeedReviewDecisionAction.CONVERT_TO_SPEC_SEED.value,
        "convert_to_strategy_spec": SeedReviewDecisionAction.CONVERT_TO_SPEC_SEED.value,
        "convert_to_spec_seed": SeedReviewDecisionAction.CONVERT_TO_SPEC_SEED.value,
        "archived_as_insight": SeedReviewDecisionAction.ARCHIVE.value,
    }
    normalized = aliases.get(normalized, normalized)
    try:
        return SeedReviewDecisionAction(normalized)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in SeedReviewDecisionAction)
        raise StrategySpecSeedReviewError(
            "invalid_review_action",
            f"review decision must be one of: {allowed}",
        ) from exc


def _normalized_idempotency_key(value: str | None) -> str:
    return str(value or "").strip()


def _normalized_request_hash(value: str | None) -> str:
    return str(value or "").strip()


def _normalized_target_refs(refs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        dict(ref)
        for ref in refs
        if isinstance(ref, Mapping)
    ]


def _find_review_decision_by_idempotency_key(
    seed: StrategySpecSeed,
    idempotency_key: str | None,
) -> SeedReviewDecision | None:
    normalized_key = _normalized_idempotency_key(idempotency_key)
    if not normalized_key:
        return None
    lineage = dict(seed.lineage or {})
    for raw in lineage.get("review_decisions") or []:
        if not isinstance(raw, Mapping):
            continue
        if _normalized_idempotency_key(str(raw.get("idempotency_key") or "")) == normalized_key:
            return SeedReviewDecision.from_dict(raw, idempotent_replay=True)
    return None


def _assert_review_idempotency_match(
    existing: SeedReviewDecision,
    *,
    action: SeedReviewDecisionAction,
    reviewer_id: str,
    reason: str,
    target_refs: Sequence[Mapping[str, Any]],
    request_hash: str | None,
) -> None:
    existing_hash = _normalized_request_hash(existing.request_hash)
    incoming_hash = _normalized_request_hash(request_hash)
    if existing_hash and incoming_hash and existing_hash != incoming_hash:
        raise StrategySpecSeedReviewError(
            "idempotency_conflict",
            "Idempotency key was already used with a different review request.",
        )

    if existing_hash and not incoming_hash:
        return

    reviewer = _require_text(reviewer_id, "reviewer_id")
    if (
        existing.decision != action.value
        or existing.reviewer_id != reviewer
        or existing.reason != str(reason or "").strip()
        or _normalized_target_refs(existing.target_refs) != _normalized_target_refs(target_refs)
    ):
        raise StrategySpecSeedReviewError(
            "idempotency_conflict",
            "Idempotency key was already used with a different review request.",
        )


def _review_transition(from_status: str, action: SeedReviewDecisionAction) -> str:
    if from_status in _TERMINAL_REVIEW_STATUSES:
        raise StrategySpecSeedReviewError(
            "terminal_seed_status",
            f"StrategySpecSeed status {from_status!r} is terminal and cannot be reviewed further.",
        )
    allowed = _REVIEW_TRANSITIONS.get(from_status, {})
    if action not in allowed:
        allowed_actions = ", ".join(item.value for item in allowed) or "none"
        raise StrategySpecSeedReviewError(
            "invalid_status_transition",
            (
                f"Review decision {action.value!r} is not allowed from status "
                f"{from_status!r}; allowed actions: {allowed_actions}"
            ),
        )
    return allowed[action]


def _seed_review_decision(
    *,
    seed: StrategySpecSeed,
    action: SeedReviewDecisionAction,
    reviewer_id: str,
    reason: str,
    target_refs: Sequence[Mapping[str, Any]],
    created_at: datetime | str | None,
    from_status: str,
    to_status: str,
    idempotency_key: str | None,
    request_hash: str | None,
) -> SeedReviewDecision:
    timestamp = _iso(created_at)
    normalized_refs = [
        dict(ref)
        for ref in target_refs
        if isinstance(ref, Mapping)
    ]
    reviewer = _require_text(reviewer_id, "reviewer_id")
    decision_id = "seed-review-" + hashlib.sha1(
        "\n".join(
            [
                seed.seed_id,
                action.value,
                reviewer,
                timestamp,
                str(idempotency_key or ""),
            ]
        ).encode("utf-8")
    ).hexdigest()[:12]
    return SeedReviewDecision(
        decision_id=decision_id,
        seed_id=seed.seed_id,
        reviewer_id=reviewer,
        decision=action.value,
        reason=str(reason or "").strip(),
        target_refs=normalized_refs,
        created_at=timestamp,
        from_status=from_status,
        to_status=to_status,
        idempotency_key=str(idempotency_key or "").strip() or None,
        request_hash=str(request_hash or "").strip() or None,
    )
