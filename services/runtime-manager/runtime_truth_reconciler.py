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


def _record_id(record: Mapping[str, Any], *fields: str) -> str | None:
    for field in fields:
        value = _clean(record.get(field))
        if value:
            return value
    return None


def _identity_view(source: Mapping[str, Any]) -> dict[str, Any]:
    """Translate canonical service field names into reconciliation identity."""
    return {
        "persona_id": source.get("persona_id"),
        "deployment_plan_id": source.get("deployment_plan_id") or source.get("plan_id"),
        "artifact_id": source.get("artifact_id"),
        "strategy_id": source.get("strategy_id") or source.get("strategy_ref"),
        "broker_id": source.get("broker_id") or source.get("broker") or source.get("broker_ref"),
        "capital_scope_kind": (
            source.get("capital_scope_kind")
            or source.get("deployment_stage")
            or source.get("deployment_mode")
            or source.get("target_stage")
        ),
        "capital_scope_id": (
            source.get("capital_scope_id")
            or source.get("capital_pool_id")
            or source.get("pool_id")
            or source.get("target_pool_id")
        ),
    }


def build_reconciliation_rows(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Join authoritative hosted source captures without suppressing runtimes.

    ``runtime_bindings`` is deliberately the driving table. Missing plan,
    persona-binding, pool, or telemetry records therefore remain represented
    and are quarantined by the reconciler rather than disappearing from counts.
    """
    runtimes = list(snapshot.get("runtime_bindings") or [])
    plans = list(snapshot.get("deployment_plans") or [])
    persona_bindings = list(snapshot.get("persona_capital_bindings") or snapshot.get("bindings") or [])
    pools = list(snapshot.get("capital_pools") or [])
    telemetry = list(snapshot.get("telemetry_summaries") or [])
    plans_by_id = {_record_id(x, "plan_id", "id"): x for x in plans if _record_id(x, "plan_id", "id")}
    bindings_by_id = {
        _record_id(x, "persona_capital_binding_id", "binding_id", "id"): x
        for x in persona_bindings
        if _record_id(x, "persona_capital_binding_id", "binding_id", "id")
    }
    pools_by_id = {_record_id(x, "capital_pool_id", "pool_id", "id"): x for x in pools if _record_id(x, "capital_pool_id", "pool_id", "id")}
    telemetry_by_runtime = {_record_id(x, "runtime_id", "id"): x for x in telemetry if _record_id(x, "runtime_id", "id")}

    rows: list[dict[str, Any]] = []
    for runtime in runtimes:
        runtime_id = _record_id(runtime, "runtime_id", "id", "binding_id")
        plan_id = _record_id(runtime, "deployment_plan_id", "plan_id")
        plan = plans_by_id.get(plan_id, {})
        binding_id = _record_id(runtime, "persona_capital_binding_id")
        if not binding_id:
            binding_ids = plan.get("binding_ids") if isinstance(plan.get("binding_ids"), list) else []
            binding_id = _clean(binding_ids[0]) if binding_ids else None
        persona_binding = bindings_by_id.get(binding_id, {})
        pool_id = (
            _record_id(runtime, "capital_scope_id", "capital_pool_id", "pool_id")
            or _record_id(plan, "capital_scope_id", "capital_pool_id", "target_pool_id", "pool_id")
            or _record_id(persona_binding, "capital_scope_id", "capital_pool_id", "pool_id")
        )
        pool = pools_by_id.get(pool_id, {})
        telemetry_row = telemetry_by_runtime.get(runtime_id, {})
        binding = {
            **_identity_view(runtime),
            "runtime_binding_id": _record_id(runtime, "runtime_binding_id", "binding_id", "id"),
        }
        rows.append({
            "runtime_id": runtime_id,
            "binding": binding,
            "runtime": _identity_view(runtime),
            "deployment_plan": _identity_view(plan),
            "persona_capital_binding": _identity_view(persona_binding),
            "capital_pool": _identity_view(pool),
            "telemetry": {"runtime_id": telemetry_row.get("runtime_id"), **_identity_view(telemetry_row), **({"stale": True} if telemetry_row.get("stale") is True or telemetry_row.get("telemetry_stale") is True else {})} if telemetry_row else {},
            "evidence_refs": [
                f"runtime_binding:{_record_id(runtime, 'runtime_binding_id', 'binding_id', 'id') or 'missing'}",
                f"deployment_plan:{plan_id or 'missing'}",
                f"persona_capital_binding:{binding_id or 'missing'}",
                f"capital_pool:{pool_id or 'missing'}",
                f"telemetry_runtime:{runtime_id or 'missing'}",
            ],
        })
    return rows


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
