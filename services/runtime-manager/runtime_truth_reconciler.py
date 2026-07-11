"""Fail-closed reconciliation for runtime binding and telemetry identity truth.

The reconciler does not delete source rows and does not write another service's
store.  It produces bounded repair patches for fields on which authoritative
sources agree, or an explicit quarantine disposition when they do not.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


IDENTITY_FIELDS = (
    "persona_id",
    "deployment_plan_id",
    "artifact_id",
    "strategy_id",
    "broker_id",
    "capital_scope_kind",
    "capital_scope_id",
)


def _clean(value: Any) -> str | None:
    value = str(value or "").strip()
    return value or None


def _issue_codes(row: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    binding = row.get("binding") if isinstance(row.get("binding"), Mapping) else {}
    telemetry = row.get("telemetry") if isinstance(row.get("telemetry"), Mapping) else {}
    if not _clean(binding.get("persona_id")):
        issues.append("missing_persona_binding")
    if not _clean(binding.get("broker_id")):
        issues.append("missing_broker_identity")
    if not (_clean(binding.get("capital_scope_kind")) and _clean(binding.get("capital_scope_id"))):
        issues.append("missing_capital_scope")
    if not telemetry:
        issues.append("missing_telemetry")
    elif telemetry.get("stale") is True:
        issues.append("stale_telemetry")
    elif _clean(telemetry.get("runtime_id")) != _clean(row.get("runtime_id")):
        issues.append("telemetry_runtime_mismatch")
    return sorted(issues)


def _agreed_value(row: Mapping[str, Any], field: str) -> tuple[str | None, bool, list[str]]:
    values: dict[str, str] = {}
    for source_name in ("runtime", "deployment_plan", "persona_capital_binding", "telemetry"):
        source = row.get(source_name)
        if isinstance(source, Mapping):
            value = _clean(source.get(field))
            if value:
                values[source_name] = value
    distinct = sorted(set(values.values()))
    return (distinct[0] if len(distinct) == 1 else None, len(distinct) > 1, [f"{k}:{v}" for k, v in sorted(values.items())])


def _stable_key(rows: Iterable[Mapping[str, Any]]) -> str:
    normalized = json.dumps(list(rows), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(normalized.encode()).hexdigest()


@dataclass(frozen=True)
class ReconciliationReport:
    run_id: str
    idempotency_key: str
    records: list[dict[str, Any]]
    replayed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "idempotency_key": self.idempotency_key,
            "replayed": self.replayed,
            "records": self.records,
            "summary": {
                "input_count": len(self.records),
                "repaired": sum(r["disposition"] == "repaired" for r in self.records),
                "quarantined": sum(r["disposition"] == "quarantined" for r in self.records),
                "unchanged": sum(r["disposition"] == "unchanged" for r in self.records),
                "unresolved": sum(bool(r["after_issue_codes"]) for r in self.records),
            },
        }


class RuntimeTruthReconciler:
    def __init__(self, audit_path: str | Path) -> None:
        self.audit_path = Path(audit_path)

    def reconcile(self, rows: Iterable[Mapping[str, Any]], *, run_id: str | None = None) -> ReconciliationReport:
        source_rows = [json.loads(json.dumps(row, sort_keys=True, default=str)) for row in rows]
        key = _stable_key(source_rows)
        prior = self._find(key)
        if prior:
            return ReconciliationReport(prior["run_id"], key, prior["records"], replayed=True)

        records = [self._reconcile_row(row) for row in source_rows]
        run_id = run_id or f"runtime-truth-{key[:16]}"
        report = ReconciliationReport(run_id, key, records)
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {**report.to_dict(), "created_at": datetime.now(timezone.utc).isoformat()}
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
        return report

    def _find(self, key: str) -> dict[str, Any] | None:
        if not self.audit_path.exists():
            return None
        for line in self.audit_path.read_text(encoding="utf-8").splitlines():
            entry = json.loads(line)
            if entry.get("idempotency_key") == key:
                return entry
        return None

    def _reconcile_row(self, row: Mapping[str, Any]) -> dict[str, Any]:
        binding = dict(row.get("binding") or {})
        before = _issue_codes(row)
        patch: dict[str, str] = {}
        conflicts: dict[str, list[str]] = {}
        for field in IDENTITY_FIELDS:
            if _clean(binding.get(field)):
                continue
            value, conflict, evidence = _agreed_value(row, field)
            if conflict:
                conflicts[field] = evidence
            elif value:
                patch[field] = value

        projected = dict(row)
        projected["binding"] = {**binding, **patch}
        after = _issue_codes(projected)
        if conflicts:
            disposition = "quarantined"
            reason = "authoritative_identity_conflict"
        elif patch:
            disposition = "repaired"
            reason = "authoritative_sources_agree"
        elif before:
            disposition = "quarantined"
            reason = "insufficient_authoritative_evidence"
        else:
            disposition = "unchanged"
            reason = "source_truth_already_complete"
        return {
            "runtime_id": _clean(row.get("runtime_id")),
            "runtime_binding_id": _clean(binding.get("runtime_binding_id") or binding.get("id")),
            "before_issue_codes": before,
            "disposition": disposition,
            "reason": reason,
            "repair_patch": patch,
            "conflicts": conflicts,
            "after_issue_codes": after,
            "evidence_refs": list(row.get("evidence_refs") or []),
            "formal_attribution_allowed": not after and disposition != "quarantined",
        }
