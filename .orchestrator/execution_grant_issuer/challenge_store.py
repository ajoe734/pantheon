"""Thread-safe transactional challenge store for execution grant issuance.

OPS-EXECUTION-MFA-ISSUER-001.
Maintains ephemeral challenges bound to operator UID, task ID, generation,
policy snapshot, and expiry. Enforces atomic single-use consumption.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

from .models import Challenge, ChallengeError


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise ChallengeError(f"Invalid JSON in policy snapshot: {exc}") from exc


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ChallengeStore:
    """In-memory thread-safe challenge store with atomic single-use consume."""

    def __init__(self, default_ttl_seconds: int = 180) -> None:
        self.default_ttl_seconds = default_ttl_seconds
        self._challenges: dict[str, Challenge] = {}
        self._lock = threading.Lock()

    def create_challenge(
        self,
        *,
        actor_uid: str,
        actor_email: str,
        task_id: str,
        generation: int,
        policy_snapshot: dict[str, Any],
        policy_digest: str,
        environment: str,
        resources: list[str],
        ttl_seconds: int | None = None,
        now: datetime | None = None,
    ) -> Challenge:
        """Create and record a new task-bound challenge."""
        if not actor_uid or not actor_uid.strip():
            raise ChallengeError("actor_uid is required")
        if not task_id or not task_id.strip():
            raise ChallengeError("task_id is required")
        if not isinstance(generation, int) or generation < 0:
            raise ChallengeError("generation must be a non-negative integer")
        if not isinstance(policy_snapshot, dict) or not policy_snapshot:
            raise ChallengeError("policy_snapshot must be a non-empty dictionary")
        if not policy_digest or not policy_digest.strip():
            raise ChallengeError("policy_digest is required")

        current_time = now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)
        effective_ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl_seconds
        if effective_ttl <= 0 or effective_ttl > 600:
            raise ChallengeError("Challenge TTL must be between 1 and 600 seconds")

        expires_at = current_time + timedelta(seconds=effective_ttl)
        challenge_id = secrets.token_hex(24)

        raw_digest_input = (
            f"{actor_uid.strip()}:{task_id.strip()}:{generation}:{policy_digest.strip()}:{challenge_id}"
        ).encode("utf-8")
        challenge_digest = _sha256_hex(raw_digest_input)

        challenge = Challenge(
            challenge_id=challenge_id,
            actor_uid=actor_uid.strip(),
            actor_email=actor_email.strip(),
            task_id=task_id.strip(),
            generation=generation,
            policy_snapshot=deepcopy(policy_snapshot),
            policy_digest=policy_digest.strip(),
            environment=environment.strip(),
            resources=sorted({str(r).strip() for r in resources if str(r).strip()}),
            created_at=current_time,
            expires_at=expires_at,
            consumed=False,
            consumed_at=None,
            challenge_digest=challenge_digest,
        )

        with self._lock:
            self._challenges[challenge_id] = challenge

        return challenge

    def consume_challenge(
        self,
        *,
        challenge_id: str,
        actor_uid: str,
        task_id: str,
        generation: int,
        policy_snapshot: dict[str, Any],
        now: datetime | None = None,
    ) -> Challenge:
        """Atomically validate and consume a challenge, enforcing single-use."""
        if not challenge_id or not challenge_id.strip():
            raise ChallengeError("challenge_id is required")

        current_time = now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)
        cid = challenge_id.strip()

        with self._lock:
            challenge = self._challenges.get(cid)
            if challenge is None:
                raise ChallengeError("Challenge not found", status_code=404)

            if challenge.consumed:
                raise ChallengeError("Challenge was already consumed (replay detected)", status_code=409)

            if current_time >= challenge.expires_at:
                raise ChallengeError("Challenge has expired", status_code=410)

            # Atomically mark as consumed immediately before running remaining binding validations
            challenge.consumed = True
            challenge.consumed_at = current_time

            # Validate actor binding
            if challenge.actor_uid != actor_uid.strip():
                raise ChallengeError("Challenge actor mismatch: challenge was not issued to this actor", status_code=403)

            # Validate task ID binding
            if challenge.task_id != task_id.strip():
                raise ChallengeError(
                    f"Challenge task mismatch: challenged for {challenge.task_id!r}, requested for {task_id!r}",
                    status_code=400,
                )

            # Validate generation binding
            if challenge.generation != generation:
                raise ChallengeError(
                    f"Challenge generation mismatch: challenged for {challenge.generation}, requested for {generation}",
                    status_code=400,
                )

            # Validate policy snapshot byte-for-byte canonical match (prevents client policy substitutions)
            expected_canonical = _canonical_json(challenge.policy_snapshot)
            actual_canonical = _canonical_json(policy_snapshot)
            if expected_canonical != actual_canonical:
                raise ChallengeError(
                    "Client-supplied policy snapshot does not match the challenged canonical policy",
                    status_code=400,
                )

            return challenge

    def get_challenge(self, challenge_id: str) -> Challenge | None:
        """Read challenge state without consuming (for inspection)."""
        with self._lock:
            challenge = self._challenges.get(challenge_id.strip())
            return deepcopy(challenge) if challenge else None

    def prune_expired(self, max_retention_seconds: int = 3600, *, now: datetime | None = None) -> int:
        """Prune old expired or consumed challenges."""
        current_time = now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)
        cutoff = current_time - timedelta(seconds=max_retention_seconds)
        pruned = 0
        with self._lock:
            to_delete = [
                cid
                for cid, c in self._challenges.items()
                if c.expires_at < cutoff or (c.consumed and c.consumed_at and c.consumed_at < cutoff)
            ]
            for cid in to_delete:
                del self._challenges[cid]
                pruned += 1
        return pruned
