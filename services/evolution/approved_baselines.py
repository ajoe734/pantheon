"""Pool-member approved baselines (SD_PERSONA_STRATEGY_POOL_AUTONOMY section 5.2).

A member baseline is the member's *own* approved registry entry plus its own
passed gate verdict.  Nothing here writes, caches or defaults a value: when proof
is missing, unapproved, or about another artifact/checksum/tenant/universe the
result is an explicit ``no_approved_baseline`` with a reason.  Comparison is
against the member's virtual book only, never the combined persona account.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

PERIODS_PER_YEAR = 252
RETURN_BASIS = "underlying_gross_virtual"
_VERDICT_KEY = "replication_gate_verdict"


def baseline_key(tenant_id: str, persona_id: str, artifact_ref: str, checksum: str) -> str:
    return "/".join((tenant_id, persona_id, artifact_ref, checksum))


def _missing(key: str, reason: str, **refs: Any) -> dict[str, Any]:
    return {"status": "no_approved_baseline", "reason": reason, "baseline_key": key, **refs}


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def resolve_member_baseline(
    *, tenant_id: str, persona_id: str, artifact_ref: str, checksum: str,
    entry: Mapping[str, Any] | None, universe: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Resolve one member's baseline from its own registry entry; never inherits a parent's."""
    key = baseline_key(tenant_id, persona_id, artifact_ref, checksum)
    if not isinstance(entry, Mapping) or not entry:
        return _missing(key, "registry_entry_missing")
    state = str(getattr(entry.get("artifact_state"), "value", entry.get("artifact_state")) or "")
    if str(entry.get("registry_id")) != artifact_ref:
        return _missing(key, "registry_entry_is_not_member_artifact")
    if state != "approved":
        return _missing(key, "artifact_not_approved")
    if str(entry.get("checksum") or "") != checksum:
        return _missing(key, "checksum_mismatch")
    if entry.get("owner_tenant") not in (tenant_id, None):
        return _missing(key, "wrong_tenant")
    metadata = entry.get("metadata") if isinstance(entry.get("metadata"), Mapping) else {}
    verdict = metadata.get(_VERDICT_KEY)
    if not isinstance(verdict, Mapping):
        return _missing(key, "gate_verdict_missing")
    if verdict.get("status") != "passed":
        return _missing(key, "gate_not_passed")
    if verdict.get("artifact_id") != artifact_ref or verdict.get("artifact_checksum") != checksum:
        return _missing(key, "gate_verdict_for_other_artifact")  # parent proof is not inherited
    if verdict.get("tenant_id") != tenant_id:
        return _missing(key, "wrong_tenant")
    run_id = str(verdict.get("backtest_run_id") or "").strip()
    if not run_id:
        return _missing(key, "backtest_run_missing")
    sharpe = verdict.get("out_of_sample_sharpe_ratio")
    drawdown = verdict.get("out_of_sample_max_drawdown")
    if not _finite(sharpe) or not _finite(drawdown):
        return _missing(key, "out_of_sample_metrics_missing")
    validated = verdict.get("validated_universe")
    if not isinstance(validated, list) or not validated:
        return _missing(key, "validated_universe_missing")
    if universe is not None and not {str(s) for s in universe} <= {str(s) for s in validated}:
        return _missing(key, "universe_not_validated")
    return {
        "status": "approved", "baseline_key": key, "tenant_id": tenant_id,
        "persona_id": persona_id, "artifact_ref": artifact_ref, "checksum": checksum,
        "oos_sharpe": float(sharpe), "oos_max_drawdown": abs(float(drawdown)),
        "backtest_run_id": run_id, "validated_universe": list(validated),
        "return_basis": verdict.get("return_basis"),
        "periods_per_year": verdict.get("periods_per_year"),
        "source_refs": {"registry_id": artifact_ref,
                        "approval_decision_id": entry.get("approval_decision_id"),
                        "backtest_run_id": run_id},
    }


def compare_member_book(baseline: Mapping[str, Any] | None, book: Mapping[str, Any]) -> dict[str, Any]:
    """Compare a member's own virtual-book metrics with its baseline; report only, no action."""
    if not isinstance(baseline, Mapping) or baseline.get("status") != "approved":
        reason = baseline.get("reason") if isinstance(baseline, Mapping) else "baseline_not_resolved"
        return {"status": "no_approved_baseline", "reason": reason or "baseline_not_resolved"}
    if book.get("basis") != baseline.get("return_basis") or \
            book.get("periods_per_year") != baseline.get("periods_per_year"):
        return {"status": "incomparable_return_basis",
                "reason": "gate and virtual book differ in return basis or annualisation"}
    sharpe, drawdown = book.get("sharpe_ratio"), book.get("max_drawdown")
    if not _finite(sharpe):
        return {"status": "member_metric_missing",
                "reason": book.get("sharpe_undefined_reason") or "sharpe_undefined"}
    if not _finite(drawdown):
        return {"status": "member_metric_missing", "reason": "drawdown_undefined"}
    return {"status": "compared", "baseline_key": baseline["baseline_key"],
            "sharpe_delta": sharpe - baseline["oos_sharpe"],
            "drawdown_delta": drawdown - baseline["oos_max_drawdown"],
            "below_baseline_sharpe": sharpe < baseline["oos_sharpe"],
            "exceeds_baseline_drawdown": drawdown > baseline["oos_max_drawdown"]}
