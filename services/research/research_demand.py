"""Persona-written research demands (research_demand.v1), kept by the research owner.

The persona's daily pool cycle writes a demand; this owner stores, de-duplicates,
serves, satisfies and expires it. Nothing here chooses candidates or creates
capital bindings. A demand is satisfied only by a member that joined the same
persona's pool after the demand was created, and expires 30 days after creation.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "research_demand.v1.schema.json"
DEMAND_TTL = timedelta(days=30)


class DemandError(ValueError):
    pass


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(SCHEMA_PATH.read_text()))


def validate_demand(record: Mapping[str, Any]) -> None:
    errors = sorted(e.message for e in _validator().iter_errors(record))
    if errors:
        raise DemandError("; ".join(errors))


class ResearchDemandStore:
    """Tenant-partitioned JSON document store with a cross-process lock."""

    def __init__(self, path: Path | str, *, now=None) -> None:
        self.path = Path(path)
        self._now = now or (lambda: datetime.now(timezone.utc))

    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_suffix(self.path.suffix + ".lock").open("a")
        fcntl.flock(lock, fcntl.LOCK_EX)
        return lock

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists() or not self.path.read_text().strip():
            return []
        return json.loads(self.path.read_text())

    def _save(self, rows: list[dict[str, Any]]) -> None:
        descriptor, temporary = tempfile.mkstemp(dir=self.path.parent)
        with os.fdopen(descriptor, "w") as handle:
            json.dump(rows, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)

    def _expire(self, rows: list[dict[str, Any]]) -> bool:
        changed = False
        now = self._now()
        for row in rows:
            if row["status"] == "open" and _parse(row["expires_at"]) <= now:
                row["status"] = "expired"
                changed = True
        return changed

    def open(self, tenant_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        """Create a demand unless this persona already has an open one for the same short measure."""
        now = self._now()
        persona_id, measure = str(body.get("persona_id") or ""), str(body.get("short_measure") or "")
        digest = hashlib.sha256(json.dumps([tenant_id, persona_id, measure, _iso(now)]).encode()).hexdigest()[:20]
        record = {
            "schema_version": "research_demand.v1", "demand_id": "rd-" + digest,
            "tenant_id": tenant_id, "persona_id": persona_id,
            "mandate": body.get("mandate"), "missing_datasets": list(body.get("missing_datasets") or []),
            "tried_families": sorted(set(body.get("tried_families") or [])),
            "short_measure": measure, "pool_version": body.get("pool_version"),
            "evidence_ref": body.get("evidence_ref"), "status": "open",
            "created_at": _iso(now), "expires_at": _iso(now + DEMAND_TTL),
            "satisfied_at": None, "satisfied_by": None,
        }
        validate_demand(record)
        lock = self._locked()
        try:
            rows = self._load()
            changed = self._expire(rows)
            for row in rows:
                if (row["tenant_id"], row["persona_id"], row["short_measure"], row["status"]) == \
                        (tenant_id, persona_id, measure, "open"):
                    if changed:
                        self._save(rows)
                    return {**row, "created": False}
            rows.append(record)
            self._save(rows)
            return {**record, "created": True}
        finally:
            lock.close()

    def list(self, tenant_id: str, persona_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        lock = self._locked()
        try:
            rows = self._load()
            if self._expire(rows):
                self._save(rows)
        finally:
            lock.close()
        return [r for r in rows if r["tenant_id"] == tenant_id
                and (persona_id is None or r["persona_id"] == persona_id)
                and (status is None or r["status"] == status)]

    def reconcile(self, tenant_id: str, persona_id: str, members: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Satisfy open demands from members that joined after the demand; expire old ones."""
        lock = self._locked()
        try:
            rows = self._load()
            changed = self._expire(rows)
            for row in rows:
                if (row["tenant_id"], row["persona_id"], row["status"]) != (tenant_id, persona_id, "open"):
                    continue
                joined = [m for m in members if m.get("joined_at") and _parse(m["joined_at"]) > _parse(row["created_at"])]
                if joined:
                    first = min(joined, key=lambda m: _parse(m["joined_at"]))
                    row.update(status="satisfied", satisfied_at=_iso(self._now()), satisfied_by=first["artifact_ref"])
                    changed = True
            if changed:
                self._save(rows)
            return [r for r in rows if r["tenant_id"] == tenant_id and r["persona_id"] == persona_id]
        finally:
            lock.close()

    def record_discovery(self, tenant_id: str, demand_id: str, record: Mapping[str, Any]) -> dict[str, Any]:
        """Record one discovery run on an open demand.

        ``record`` carries ``run_at``, ``matched``/``excluded`` counts, an optional ``reason`` and
        ``actions`` (receipts). An action already recorded for the same ``(match_id, action)`` is
        kept as first recorded, so a later run never repeats or overwrites an executed receipt.
        """
        lock = self._locked()
        try:
            rows = self._load()
            changed = self._expire(rows)
            for row in rows:
                if row["tenant_id"] != tenant_id or row["demand_id"] != demand_id:
                    continue
                if row["status"] != "open":
                    if changed:
                        self._save(rows)
                    raise DemandError(f"demand {demand_id} is {row['status']}, not open")
                known = dict(row.get("discovery") or {})
                actions = list(known.get("actions") or [])
                index = {(a.get("match_id"), a.get("action")): i for i, a in enumerate(actions)}
                run_at = record.get("run_at")
                for action in record.get("actions") or []:
                    key = (action.get("match_id"), action.get("action"))
                    attempt = {"run_at": run_at, "status": action.get("status"), "reason": action.get("reason")}
                    if key not in index:
                        index[key] = len(actions)
                        actions.append({**action, "attempts": [attempt]})
                        continue
                    kept = actions[index[key]]
                    history = list(kept.get("attempts") or [{"run_at": None, "status": kept.get("status"), "reason": kept.get("reason")}])
                    if kept.get("status") != "failed":
                        continue  # an executed receipt is authoritative: never repeated, never overwritten
                    # a failed attempt stays in the history; a later outcome (success or failure) becomes current
                    actions[index[key]] = {**action, "attempts": history + [attempt]}
                row["discovery"] = {
                    **known,
                    "actions": actions,
                    "last_run": {k: v for k, v in record.items() if k != "actions"},
                    "run_count": int(known.get("run_count") or 0) + 1,
                }
                validate_demand(row)
                self._save(rows)
                return dict(row)
            if changed:
                self._save(rows)
            raise DemandError(f"demand {demand_id} not found")
        finally:
            lock.close()

    def record_template_search(self, tenant_id: str, demand_id: str, record: Mapping[str, Any],
                               *, tried_families=()) -> dict[str, Any]:
        """Merge template receipts independently of external-text discovery actions."""
        lock = self._locked()
        try:
            rows = self._load()
            self._expire(rows)
            for row in rows:
                if (row["tenant_id"], row["demand_id"]) != (tenant_id, demand_id):
                    continue
                if row["status"] != "open":
                    self._save(rows)
                    raise DemandError(f"demand {demand_id} is {row['status']}, not open")
                discovery = dict(row.get("discovery") or {})
                previous = discovery.get("template_search") or {}
                attempts = dict(previous.get("attempts") or {})
                attempts.update(record.get("attempts") or {})
                discovery["template_search"] = {**record, "attempts": attempts}
                row["discovery"] = discovery
                row["tried_families"] = sorted(set(row["tried_families"]) | set(tried_families))
                validate_demand(row)
                self._save(rows)
                return dict(row)
            raise DemandError(f"demand {demand_id} not found")
        finally:
            lock.close()
