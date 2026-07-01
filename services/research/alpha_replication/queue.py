"""Alpha replication queue.

Accepts reviewed (lifecycle_state=approved) StrategySpec payloads and holds
them until the revalidation worker processes them. Duplicate enqueues for the
same (strategy_id, spec_version) are silently ignored (idempotent).

Production adapters remain fail-closed. This queue only holds pending work;
it does not dispatch to live systems and has no deployment authority.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REVIEWABLE_STATES = frozenset({"approved", "review"})


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _require_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


class AlphaReplicationQueue:
    """Thread-safe, file-backed queue for approved StrategySpec entries."""

    def __init__(self, data_dir: str | Path) -> None:
        self._path = Path(data_dir) / "alpha_replication_queue.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def enqueue(
        self, spec_payload: dict[str, Any], *, enqueued_by: str = "system"
    ) -> dict[str, Any] | None:
        """Enqueue a StrategySpec for alpha replication.

        Returns the new queue entry, or None when the spec is already present
        (idempotent).  Raises ValueError when lifecycle_state is not approved
        or review.
        """
        strategy_id = _require_text(spec_payload.get("strategy_id"), "strategy_id")
        lifecycle_state = str(spec_payload.get("lifecycle_state") or "").lower()
        if lifecycle_state not in REVIEWABLE_STATES:
            raise ValueError(
                f"Only approved StrategySpec entries enter the replication queue; "
                f"got lifecycle_state={lifecycle_state!r} for strategy_id={strategy_id!r}"
            )
        spec_version = _require_text(spec_payload.get("spec_version"), "spec_version")

        with self._lock:
            entries = self._read()
            key = (strategy_id, spec_version)
            for entry in entries:
                if (entry["strategy_id"], entry["spec_version"]) == key:
                    return None  # already enqueued — idempotent
            timestamp = _utc_now()
            new_entry: dict[str, Any] = {
                "strategy_id": strategy_id,
                "spec_version": spec_version,
                "lifecycle_state": lifecycle_state,
                "enqueued_at": timestamp,
                "enqueued_by": enqueued_by,
                "status": "pending",
                "last_revalidation_at": None,
                "last_revalidation_status": None,
                "experiment_run_ids": [],
                "revalidation_count": 0,
            }
            self._append(new_entry)
            return new_entry

    def list_pending(self) -> list[dict[str, Any]]:
        """Return all entries with status='pending'."""
        with self._lock:
            return [e for e in self._read() if e.get("status") == "pending"]

    def list_all(self) -> list[dict[str, Any]]:
        """Return all entries regardless of status."""
        with self._lock:
            return list(self._read())

    def mark_revalidated(
        self,
        strategy_id: str,
        spec_version: str,
        *,
        run_id: str,
        status: str,
    ) -> bool:
        """Record a completed revalidation attempt. Returns True when found."""
        timestamp = _utc_now()
        with self._lock:
            entries = self._read()
            updated = False
            new_entries = []
            for entry in entries:
                if (
                    entry["strategy_id"] == strategy_id
                    and entry["spec_version"] == spec_version
                ):
                    entry = dict(entry)
                    entry["last_revalidation_at"] = timestamp
                    entry["last_revalidation_status"] = status
                    entry["revalidation_count"] = (
                        int(entry.get("revalidation_count") or 0) + 1
                    )
                    run_ids: list[str] = list(entry.get("experiment_run_ids") or [])
                    if run_id and run_id not in run_ids:
                        run_ids.append(run_id)
                    entry["experiment_run_ids"] = run_ids
                    updated = True
                new_entries.append(entry)
            if updated:
                self._rewrite(new_entries)
            return updated

    def get_metrics(self) -> dict[str, Any]:
        """Return observable health metrics for this queue."""
        with self._lock:
            entries = self._read()
        pending = sum(1 for e in entries if e.get("status") == "pending")
        revalidated = sum(
            1 for e in entries if (e.get("revalidation_count") or 0) > 0
        )
        failed = sum(
            1
            for e in entries
            if e.get("last_revalidation_status") == "failed"
        )
        return {
            "total": len(entries),
            "pending": pending,
            "revalidated": revalidated,
            "last_revalidation_failed": failed,
        }

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _read(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        entries: list[dict[str, Any]] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                entries.append(payload)
        return entries

    def _append(self, entry: dict[str, Any]) -> None:
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=True, sort_keys=True))
            fh.write("\n")

    def _rewrite(self, entries: list[dict[str, Any]]) -> None:
        lines = [
            json.dumps(e, ensure_ascii=True, sort_keys=True) for e in entries
        ]
        self._path.write_text("\n".join(lines) + "\n", encoding="utf-8")


__all__ = ["AlphaReplicationQueue", "REVIEWABLE_STATES"]
