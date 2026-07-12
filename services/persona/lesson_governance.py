"""Memory and evaluation governance for Trade Lesson Candidates.

Implements the proposed/pending/endorsed/merged/quarantined/expired lifecycle,
evaluation gates (sample size, cross-regime), and fail-closed promotion checks
before merging into Persona Memory.
"""

from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import jsonschema

from services.memory.persona_memory_store import (
    PersonaMemoryEntry,
    PersonaMemoryStore,
    PersonaMemoryType,
    PersonaRelevanceScope,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_lesson_candidate_json(data: Dict[str, Any]) -> List[str]:
    """Validate raw dict against the canonical TradeLessonCandidate JSON schema."""
    schema_path = Path(__file__).parent / "trade_lesson_candidate.schema.json"
    if not schema_path.exists():
        return [f"Schema file not found: {schema_path}"]

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft7Validator(schema)
    errors = []
    for err in sorted(validator.iter_errors(data), key=lambda e: list(e.path)):
        errors.append(f"{list(err.path)}: {err.message}")
    return errors


def is_sensitive_change(candidate: Dict[str, Any]) -> bool:
    """Check if the lesson candidate proposes changes to policy, risk, capital, or live behaviors."""
    scope = str(candidate.get("scope", "")).lower()
    change = str(candidate.get("proposed_change", "")).lower()
    sensitive_keywords = {"policy", "risk", "capital", "live", "allocation", "leverage", "limit"}
    if scope in sensitive_keywords:
        return True
    return any(keyword in change for keyword in sensitive_keywords)


def check_evaluation_gates(candidate: Dict[str, Any], episodes: List[Dict[str, Any]]) -> List[str]:
    """Enforce sample size, cross-regime validation, and sensitive scope deployment gates.

    Returns a list of validation errors; empty list means all gates passed.
    """
    errors = []
    scope = str(candidate.get("scope", "")).lower()

    # 1. Sample size gate (Pattern lessons require at least 3 supporting episodes)
    if scope in {"strategy", "regime", "pattern"}:
        min_sample = 3
        actual_sample = len(candidate.get("trade_episode_ids", []))
        if actual_sample < min_sample:
            errors.append(f"Pattern lesson requires at least {min_sample} supporting episodes, got {actual_sample}")

    # 2. Cross regime gate (Pattern lessons require at least 2 distinct market regimes)
    if scope in {"strategy", "regime", "pattern"}:
        regimes = set()
        for ep in episodes:
            regime = ep.get("regime") or ep.get("market_regime") or ep.get("metadata", {}).get("regime")
            if regime:
                regimes.add(str(regime).strip().lower())
        if len(regimes) < 2:
            errors.append("Pattern lesson requires supporting episodes from at least 2 distinct market regimes")

    # 3. Sensitive scope gate: policy/risk/capital/live
    if is_sensitive_change(candidate):
        receipt = candidate.get("receipt")
        if not receipt:
            errors.append("Sensitive changes (policy/risk/capital/live) require an endorsement receipt with approval and deployment plan references.")
        else:
            reason = receipt.get("reason", "")
            # Verify that the reason explicitly references approval decision and deployment plan
            has_approval = "approval" in reason.lower() or "app-" in reason.lower() or "dec-" in reason.lower()
            has_deployment = "deployment" in reason.lower() or "plan-" in reason.lower()
            audit_id = receipt.get("audit_receipt_id")
            if not audit_id:
                errors.append("Sensitive changes require a valid audit_receipt_id in the receipt.")
            if not (has_approval and has_deployment):
                errors.append("Sensitive changes require both approval decision and deployment plan references in the receipt reason.")

    # 4. Versioned lineage check
    reflection_version = candidate.get("reflection_version")
    if not reflection_version:
        errors.append("Missing reflection_version in candidate.")
    else:
        import re
        if not re.match(r"^v[0-9]+(\.[0-9]+)*$", str(reflection_version)):
            errors.append(f"Invalid reflection_version format: {reflection_version}")

    # 5. Environment promotion check
    target_env = candidate.get("target_env", "paper")
    if target_env in {"canary", "live"}:
        receipt = candidate.get("receipt")
        if not receipt:
            errors.append(f"Promotion to {target_env} requires an endorsement receipt.")
        else:
            audit_id = receipt.get("audit_receipt_id")
            if not audit_id:
                errors.append(f"Promotion to {target_env} requires a valid audit_receipt_id in the receipt.")

    return errors


class TradeLessonCandidateError(ValueError):
    """Raised when trade lesson candidate validation or store operations fail."""


class TradeLessonCandidateStore:
    """Thread-safe trade lesson candidate store with optional JSON persistence."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._lock = threading.Lock()
        self._candidates: Dict[str, Dict[str, Any]] = {}
        self._path = path
        if path and path.exists():
            self._load(path)

    def create(self, candidate_data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and insert a new trade lesson candidate."""
        candidate = deepcopy(candidate_data)
        # Ensure default fields
        if "review_state" not in candidate:
            candidate["review_state"] = "proposed"
        if "created_at" not in candidate:
            candidate["created_at"] = utc_now()
        if "updated_at" not in candidate:
            candidate["updated_at"] = candidate["created_at"]
        if "target_env" not in candidate:
            candidate["target_env"] = "paper"

        # Enforce fail-closed creation restriction: candidate can only be created with target_env="paper" and promotion_stage="proposed"
        target_env = candidate["target_env"]
        if target_env != "paper":
            raise TradeLessonCandidateError(
                f"Cannot create candidate with target_env '{target_env}'. New candidates must start with target_env='paper'."
            )

        if "promotion_stage" not in candidate:
            candidate["promotion_stage"] = "proposed"
        elif candidate["promotion_stage"] != "proposed":
            raise TradeLessonCandidateError(
                f"Cannot create candidate with promotion_stage '{candidate['promotion_stage']}'. New candidates must start with promotion_stage='proposed'."
            )

        json_errors = validate_lesson_candidate_json(candidate)
        if json_errors:
            raise TradeLessonCandidateError(f"Schema validation failed: {json_errors}")

        with self._lock:
            lc_id = candidate["lesson_candidate_id"]
            if lc_id in self._candidates:
                raise TradeLessonCandidateError(f"Candidate already exists: {lc_id}")
            self._candidates[lc_id] = candidate
            self._save()
            return deepcopy(candidate)

    def get(self, lesson_candidate_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a candidate by ID."""
        with self._lock:
            candidate = self._candidates.get(lesson_candidate_id)
            return deepcopy(candidate) if candidate else None

    def update(self, candidate_data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and update an existing candidate."""
        candidate = deepcopy(candidate_data)
        candidate["updated_at"] = utc_now()

        env_to_stage = {
            "paper": "proposed",
            "canary": "canary_approved",
            "live": "live_approved"
        }
        target_env = candidate.get("target_env", "paper")
        if target_env not in env_to_stage:
            raise TradeLessonCandidateError(f"Invalid target environment: {target_env}")
            
        expected_stage = env_to_stage[target_env]
        if "promotion_stage" not in candidate:
            candidate["promotion_stage"] = expected_stage
        elif candidate["promotion_stage"] != expected_stage:
            raise TradeLessonCandidateError(
                f"promotion_stage '{candidate['promotion_stage']}' does not match target_env '{target_env}'."
            )

        json_errors = validate_lesson_candidate_json(candidate)
        if json_errors:
            raise TradeLessonCandidateError(f"Schema validation failed: {json_errors}")

        with self._lock:
            lc_id = candidate["lesson_candidate_id"]
            if lc_id not in self._candidates:
                raise TradeLessonCandidateError(f"Candidate not found: {lc_id}")
            self._candidates[lc_id] = candidate
            self._save()
            return deepcopy(candidate)

    def list(
        self,
        *,
        persona_id: Optional[str] = None,
        review_state: Optional[str] = None,
        scope: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List candidates filtered by attributes."""
        with self._lock:
            candidates = list(self._candidates.values())

        if persona_id:
            candidates = [c for c in candidates if c.get("persona_id") == persona_id]
        if review_state:
            candidates = [c for c in candidates if c.get("review_state") == review_state]
        if scope:
            candidates = [c for c in candidates if c.get("scope") == scope]

        return sorted(candidates, key=lambda c: c.get("created_at", ""), reverse=True)

    def _save(self) -> None:
        if not self._path:
            return
        self._path.write_text(json.dumps(list(self._candidates.values()), indent=2), encoding="utf-8")

    def _load(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return
        data = json.loads(text)
        if not isinstance(data, list):
            raise TradeLessonCandidateError(f"Expected JSON array in {path}")
        for record in data:
            json_errors = validate_lesson_candidate_json(record)
            if json_errors:
                lc_id = record.get("lesson_candidate_id", "<unknown>")
                raise TradeLessonCandidateError(
                    f"Schema validation failed for persisted candidate {lc_id}: {json_errors}"
                )
            self._candidates[record["lesson_candidate_id"]] = record


class LessonGovernanceService:
    """Governance service orchestrating the lesson candidate review lifecycle and evaluation gates."""

    def __init__(self, store: TradeLessonCandidateStore) -> None:
        self.store = store

    def submit_review(self, lesson_candidate_id: str) -> Dict[str, Any]:
        """Transition candidate state from proposed -> pending_review."""
        candidate = self.store.get(lesson_candidate_id)
        if not candidate:
            raise TradeLessonCandidateError(f"Candidate not found: {lesson_candidate_id}")

        current_state = candidate.get("review_state")
        if current_state != "proposed":
            raise TradeLessonCandidateError(
                f"Cannot submit review for candidate in state '{current_state}'. Must be 'proposed'."
            )

        candidate["review_state"] = "pending_review"
        return self.store.update(candidate)

    def decide(
        self,
        lesson_candidate_id: str,
        *,
        action: str,
        operator_id: str,
        reason: str,
        audit_receipt_id: str,
        episodes: List[Dict[str, Any]] = None,
        target_env: Optional[str] = None,
        promotion_stage: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Transition candidate state to endorsed, rejected, quarantined, or expired.

        For endorsement, evaluation gates are evaluated and must pass.
        """
        if action not in {"endorse", "reject", "quarantine", "expire"}:
            raise TradeLessonCandidateError(f"Invalid decision action: {action}")

        candidate = self.store.get(lesson_candidate_id)
        if not candidate:
            raise TradeLessonCandidateError(f"Candidate not found: {lesson_candidate_id}")

        current_state = candidate.get("review_state")
        if current_state not in {"proposed", "pending_review", "endorsed"}:
            raise TradeLessonCandidateError(
                f"Cannot make decision for candidate in state '{current_state}'. Must be 'proposed', 'pending_review', or 'endorsed'."
            )

        if current_state == "endorsed":
            if action != "endorse":
                raise TradeLessonCandidateError(
                    f"Cannot perform action '{action}' on an already endorsed candidate."
                )
            if not target_env:
                raise TradeLessonCandidateError(
                    "Target environment must be specified when updating endorsement."
                )

        # Build receipt
        receipt = {
            "operator_id": operator_id,
            "decided_at": utc_now(),
            "action": action,
            "reason": reason,
            "audit_receipt_id": audit_receipt_id,
        }
        candidate["receipt"] = receipt

        if action == "endorse":
            old_env = candidate.get("target_env", "paper")
            old_stage = candidate.get("promotion_stage", "proposed")
            
            new_env = target_env or old_env
            
            # Map promotion stages server-side
            env_to_stage = {
                "paper": "proposed",
                "canary": "canary_approved",
                "live": "live_approved"
            }
            if new_env not in env_to_stage:
                raise TradeLessonCandidateError(f"Invalid target environment: {new_env}")
                
            new_stage = env_to_stage[new_env]
            
            # If caller explicitly provided a stage, it must match the server-side derived stage
            if promotion_stage and promotion_stage != new_stage:
                raise TradeLessonCandidateError(
                    f"Invalid promotion_stage '{promotion_stage}' for target_env '{new_env}'. "
                    f"Must be '{new_stage}'."
                )

            # Enforce sequential transition matrix:
            # - To promote to live, current state must be canary & canary_approved
            # - To promote to canary, current state must be paper & proposed
            if new_env == "live" and old_env != "live":
                if old_env != "canary" or old_stage != "canary_approved":
                    raise TradeLessonCandidateError(
                        f"Promotion to live is blocked: candidate must transition from canary (canary_approved), "
                        f"but current state is {old_env} ({old_stage})."
                    )
            elif new_env == "canary" and old_env != "canary":
                if old_env != "paper" or old_stage != "proposed":
                    raise TradeLessonCandidateError(
                        f"Promotion to canary is blocked: candidate must transition from paper (proposed), "
                        f"but current state is {old_env} ({old_stage})."
                    )

            candidate["target_env"] = new_env
            candidate["promotion_stage"] = new_stage

            # Check gates before allowing endorsement
            gate_errors = check_evaluation_gates(candidate, episodes or [])
            if gate_errors:
                # Revert on gate failure
                candidate["target_env"] = old_env
                candidate["promotion_stage"] = old_stage
                raise TradeLessonCandidateError(
                    f"Endorsement blocked by evaluation gates: {gate_errors}"
                )
            candidate["review_state"] = "endorsed"
        elif action == "reject":
            candidate["review_state"] = "rejected"
        elif action == "quarantine":
            candidate["review_state"] = "quarantined"
        elif action == "expire":
            candidate["review_state"] = "expired"

        return self.store.update(candidate)

    def merge_to_memory(self, lesson_candidate_id: str, memory_store: PersonaMemoryStore) -> Dict[str, Any]:
        """Merge an endorsed candidate into the persona memory store.

        Moves state to 'merged'.
        """
        candidate = self.store.get(lesson_candidate_id)
        if not candidate:
            raise TradeLessonCandidateError(f"Candidate not found: {lesson_candidate_id}")

        current_state = candidate.get("review_state")
        if current_state != "endorsed":
            raise TradeLessonCandidateError(
                f"Cannot merge candidate in state '{current_state}'. Must be 'endorsed' first."
            )

        # Create persona memory entry
        memory_id = f"pmem-lesson-{candidate['lesson_candidate_id']}"
        entry = PersonaMemoryEntry(
            memory_id=memory_id,
            persona_id=candidate["persona_id"],
            memory_type=PersonaMemoryType.STRATEGY_LESSON.value,
            content={
                "summary": f"Scope: {candidate['scope']}. Proposed Change: {candidate['proposed_change']}",
                "structured_payload": {
                    "lesson_candidate_id": candidate["lesson_candidate_id"],
                    "reflection_id": candidate["reflection_id"],
                    "reflection_version": candidate["reflection_version"],
                    "trade_episode_ids": candidate["trade_episode_ids"],
                    "scope": candidate["scope"],
                    "proposed_change": candidate["proposed_change"],
                    "confidence": candidate["confidence"],
                    "receipt": candidate.get("receipt"),
                    "target_env": candidate.get("target_env"),
                    "promotion_stage": candidate.get("promotion_stage"),
                },
                "tags": ["strategy_lesson", candidate["scope"], candidate["persona_id"]],
            },
            source_event_type="operator_feedback",
            source_event_id=candidate["reflection_id"],
            written_at=utc_now(),
            write_authority="persona-memory-svc",
            relevance_scope=PersonaRelevanceScope.PERSONA_AND_COMMITTEE.value,
        )

        # Store in memory store
        memory_store.create(entry)

        # Update candidate state to merged
        candidate["review_state"] = "merged"
        return self.store.update(candidate)
