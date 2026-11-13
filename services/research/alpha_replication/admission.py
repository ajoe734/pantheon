"""Tenant-scoped ReplicationAdmission store.

Provides durable, reviewed admission for StrategySpecs to enter the Alpha Replication loop.
Seed files or unreviewed discovery cannot directly trigger replication; only explicit,
reviewed ReplicationAdmission records are admitted into the queue.
"""

from __future__ import annotations

import fcntl
import json
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


def _utc_now(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _require_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _admission_key(entry: Mapping[str, Any]) -> tuple[str, str]:
    return (
        str(entry.get("tenant_id") or ""),
        str(entry.get("strategy_spec_id") or entry.get("registry_id") or ""),
    )


class ReplicationAdmissionStore:
    """Process-safe durable store for reviewed ReplicationAdmission records."""

    def __init__(self, data_dir: str | Path) -> None:
        self._data_dir = Path(data_dir)
        self._path = self._data_dir / "replication_admissions.jsonl"
        self._legacy_json_path = self._data_dir / "admissions.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_path = self._path.with_suffix(".lock")
        self._lock = threading.Lock()

    @contextmanager
    def _lock_context(self):
        with self._lock:
            with self._lock_path.open("w", encoding="utf-8") as lock_handle:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    def create_admission(
        self,
        payload: Mapping[str, Any],
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Record an explicit, reviewed ReplicationAdmission.

        Duplicate admissions for the same (tenant_id, strategy_spec_id) return the existing entry
        if binding fields match, or raise ValueError if immutable review parameters conflict.
        """
        tenant_id = _require_text(payload.get("tenant_id"), "tenant_id")
        strategy_spec_id = _require_text(
            payload.get("strategy_spec_id") or payload.get("registry_id"),
            "strategy_spec_id",
        )
        strategy_id = _require_text(payload.get("strategy_id"), "strategy_id")
        spec_version = _require_text(
            payload.get("spec_version") or payload.get("strategy_spec_version") or payload.get("version"),
            "spec_version",
        )
        checksum = _require_text(payload.get("checksum"), "checksum")
        approval_decision_id = _require_text(
            payload.get("approval_decision_id"),
            "approval_decision_id",
        )
        approver = _require_text(payload.get("approver"), "approver")
        requested_by = _require_text(
            payload.get("requested_by") or payload.get("approver"),
            "requested_by",
        )
        mode = str(payload.get("mode") or "initial").strip().lower()
        if mode not in ("initial", "revalidation"):
            raise ValueError(f"Invalid admission mode: {mode!r}; must be 'initial' or 'revalidation'")

        review_ref = str(payload.get("review_ref") or f"review://{approval_decision_id}").strip()
        timestamp = _utc_now(now)

        with self._lock_context():
            entries = self._read()
            for entry in entries:
                if _admission_key(entry) == (tenant_id, strategy_spec_id):
                    # Check for binding conflicts
                    if (
                        entry.get("strategy_id") != strategy_id
                        or entry.get("spec_version") != spec_version
                        or entry.get("checksum") != checksum
                        or entry.get("approval_decision_id") != approval_decision_id
                    ):
                        raise ValueError(
                            f"ReplicationAdmission binding conflict for tenant_id={tenant_id!r}, "
                            f"strategy_spec_id={strategy_spec_id!r}"
                        )
                    return dict(entry)

            admission_id = str(payload.get("admission_id") or f"adm-{uuid.uuid4().hex[:12]}")
            new_entry: dict[str, Any] = {
                "admission_id": admission_id,
                "tenant_id": tenant_id,
                "strategy_spec_id": strategy_spec_id,
                "strategy_id": strategy_id,
                "spec_version": spec_version,
                "checksum": checksum,
                "approval_decision_id": approval_decision_id,
                "approver": approver,
                "approved_at": str(payload.get("approved_at") or timestamp),
                "requested_by": requested_by,
                "review_ref": review_ref,
                "mode": mode,
                "status": "admitted",
                "created_at": timestamp,
            }
            entries.append(new_entry)
            self._rewrite_durable(entries)
            return dict(new_entry)

    def get_admission(self, tenant_id: str, strategy_spec_id: str) -> dict[str, Any] | None:
        with self._lock_context():
            entries = self._read()
            target = (_require_text(tenant_id, "tenant_id"), _require_text(strategy_spec_id, "strategy_spec_id"))
            for entry in entries:
                if _admission_key(entry) == target:
                    return dict(entry)
            return None

    def list_admissions(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock_context():
            entries = self._read()
            if tenant_id is not None:
                entries = [e for e in entries if e.get("tenant_id") == tenant_id]
            return [dict(e) for e in entries]

    def _read(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        if self._path.exists():
            for line in self._path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                if isinstance(payload, dict):
                    entries.append(payload)
                    seen.add(_admission_key(payload))
        if self._legacy_json_path.exists():
            raw = self._legacy_json_path.read_text(encoding="utf-8").strip()
            if raw:
                parsed = json.loads(raw)
                items = (
                    list(parsed.values())
                    if isinstance(parsed, dict)
                    else (parsed if isinstance(parsed, list) else [])
                )
                for item in items:
                    if isinstance(item, dict):
                        key = _admission_key(item)
                        if key not in seen:
                            entries.append(item)
                            seen.add(key)
        return entries

    def _rewrite_durable(self, entries: list[dict[str, Any]]) -> None:
        content = "\n".join(
            json.dumps(entry, ensure_ascii=True, sort_keys=True) for entry in entries
        ) + "\n"
        temp_path = self._path.with_name(f".{self._path.name}.{os.getpid()}.tmp")
        try:
            with temp_path.open("w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self._path)
            directory_fd = os.open(self._path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except Exception as exc:
            raise RuntimeError(f"Failed to write admission entries: {exc}") from exc
        finally:
            if temp_path.exists():
                temp_path.unlink()


def evaluate_template_candidate(*, gate, artifact, dataset, demand, family_id, source_ref, timestamp):
    """Submit a compiled template to the existing §10 content-admission gate.

    This grants no StrategySpec review, queue approval, baseline or pool membership.
    The gate runs and records its own backtest of the exact compiled artifact.
    """
    from services.registry.strategy_artifact import strategy_artifact_checksum, validate_strategy_artifact
    from services.research.replication.gate_schema import ReplicationRequest

    validate_strategy_artifact(artifact)
    dataset = {**dataset, "strategy_id": artifact["strategy_id"]}
    request = ReplicationRequest(
        candidate_id=artifact["artifact_id"], source_task_id=demand["demand_id"],
        research_handoff={
            "source_metadata": {"api_endpoint": source_ref, "retrieved_at": timestamp,
                                "governance_context": "Persona-written research demand; paper research only"},
            "family_id": family_id, "strategy_artifact": artifact, "backtest_dataset": dataset,
            "normalized_findings": {
                "strategy_spec": {"strategy_id": artifact["strategy_id"]},
                "replication_notes": "Registered template compiled without changing its signal; tested after Taiwan costs.",
                "evaluation_hypotheses": "Positive deflated out-of-sample Sharpe with consistent walk-forward sign.",
            },
        },
        proposed_strategy_spec={
            "name": artifact["strategy_id"], "description": "Demand-driven registered alpha template",
            "signals": artifact["strategy_logic"], "parameters": artifact["parameters"],
            "strategy_artifact": artifact,
            "governance": {"policy_id": "template-demand-paper-research", "approval_required": True},
        },
        metadata={"tenant_id": demand["tenant_id"], "persona_id": demand["persona_id"],
                  "demand_ref": demand["demand_id"], "dataset_version_id": dataset["dataset_id"],
                  "strategy_artifact_checksum": strategy_artifact_checksum(artifact)},
        timestamp=timestamp,
    )
    return gate.evaluate_candidate(request)


__all__ = ["ReplicationAdmissionStore", "evaluate_template_candidate"]
