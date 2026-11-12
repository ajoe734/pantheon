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

PERIODS_PER_YEAR = 365  # vectorbt pf.sharpe_ratio() default year_freq=365 days with freq='1D'
# The gate scores share-sized (artifact order_quantity, init_cash) vectorbt
# portfolios per instrument, daily, sqrt(365) (vectorbt year_freq), Taiwan costs on every fill, and
# aggregates multi-symbol runs as the mean of per-instrument Sharpe.  A virtual
# book is comparable only when it reports exactly this basis; the exposure-fraction
# book does not, so comparison is explicitly ``incomparable_return_basis``.
RETURN_BASIS = "gate_share_sized_per_instrument_mean_sharpe_taiwan_fill_costs"
_DAILY_BARS = {"1d", "d", "daily", "1day"}


def baseline_key(tenant_id: str, persona_id: str, artifact_ref: str, checksum: str) -> str:
    return "/".join((tenant_id, persona_id, artifact_ref, checksum))


def _missing(key: str, reason: str, **refs: Any) -> dict[str, Any]:
    return {"status": "no_approved_baseline", "reason": reason, "baseline_key": key, **refs}


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _code(symbol: Any) -> str:
    return str(symbol).strip().upper().split(".")[0]


def _gate_proof_failure(entry: Mapping[str, Any], summary: Mapping[str, Any], run_id: str) -> str | None:
    """Reason the persisted proof is not an exact passed gate verdict, else ``None``."""
    if entry.get("replication_success") is None and not entry.get("replication_proof"):
        # Historical/unproven entry: the registry owner recorded no gate verdict.
        return "gate_verdict_not_recorded"
    if entry.get("replication_success") is not True:
        return "gate_not_passed"
    proof = entry.get("replication_proof")
    if not isinstance(proof, Mapping) or not proof:
        return "gate_proof_missing"
    status = getattr(proof.get("admission_status"), "value", proof.get("admission_status"))
    if str(status) != "admitted" or str(proof.get("backtest_run_id") or "") != run_id:
        return "gate_not_passed"
    proof_summary = proof.get("evaluation_summary")
    # The registry owner may add its own schema keys to the registered summary; every
    # key the gate produced must still be present and equal.
    if (not isinstance(proof_summary, Mapping) or not proof_summary
            or any(k not in summary or summary[k] != v for k, v in proof_summary.items())):
        return "gate_proof_differs_from_summary"
    verdicts = [r for r in proof.get("replication_results") or []
                if isinstance(r, Mapping) and r.get("criterion_id") == "real_backtest_validation"]
    if len(verdicts) != 1 or verdicts[0].get("passed") is not True:
        return "gate_not_passed"
    details = verdicts[0].get("details")
    details = details if isinstance(details, Mapping) else {}
    if (details.get("status") != "passed" or details.get("failed_checks")
            or str(details.get("backtest_run_id") or "") != run_id
            or not all(v is True for v in (details.get("checks") or {}).values())
            or not details.get("checks")):
        return "gate_not_passed"
    for field, detail_field in (("out_of_sample_sharpe_ratio", "out_of_sample_sharpe_ratio"),
                                ("max_drawdown", "out_of_sample_max_drawdown")):
        if summary.get(field) != details.get(detail_field):
            return "gate_proof_differs_from_summary"
    trips = summary.get("round_trips")
    if not _finite(trips) or trips <= 0:
        return "gate_round_trips_missing"
    return None


def resolve_member_baseline(
    *, tenant_id: str, persona_id: str, artifact_ref: str, checksum: str,
    entry: Mapping[str, Any] | None, universe: Sequence[str] | None = None,
    run_record: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve one member's baseline from its own immutable approved registry entry.

    The proof is what the existing gate/promotion path persists: the entry's
    ``evaluation_summary`` (``create_promotion_request``), its ``lineage`` run
    ids, and the embedded strategy artifact whose checksum is the entry
    checksum.  A parent's or sibling's entry is never consulted.

    A summary Sharpe above a threshold is not a gate verdict.  Approval needs the
    persisted passed gate proof (``replication_success``, admitted
    ``replication_proof`` whose ``real_backtest_validation`` passed with no failed
    checks and matches the summary) *and* the authoritative backtest run record
    (``BacktestRunStore``) binding that run id to this member's checksum.  The
    entry itself carries no checksum for the run, so without ``run_record`` the
    result is ``backtest_run_binding_unavailable`` rather than an approval.
    """
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
    artifact = metadata.get("strategy_artifact")
    if not isinstance(artifact, Mapping):
        return _missing(key, "strategy_artifact_missing")
    from services.registry.strategy_artifact import strategy_artifact_checksum

    try:
        embedded_checksum = strategy_artifact_checksum(dict(artifact))
    except Exception:  # noqa: BLE001 - an uncomputable checksum is not proof
        embedded_checksum = None
    if embedded_checksum != checksum:
        return _missing(key, "artifact_checksum_mismatch")
    summary = entry.get("evaluation_summary")
    summary = summary if isinstance(summary, Mapping) else {}
    run_id = str(summary.get("backtest_run_id") or "").strip()
    if not run_id:
        return _missing(key, "backtest_run_missing")
    lineage = entry.get("lineage") if isinstance(entry.get("lineage"), Mapping) else {}
    if run_id not in (lineage.get("source_run_ids") or []):
        return _missing(key, "backtest_run_not_in_lineage")
    sharpe = summary.get("out_of_sample_sharpe_ratio")
    drawdown = summary.get("max_drawdown")
    if not _finite(sharpe) or not _finite(drawdown):
        return _missing(key, "out_of_sample_metrics_missing")
    failed = _gate_proof_failure(entry, summary, run_id)
    if failed:
        return _missing(key, failed)
    if not isinstance(run_record, Mapping) or str(run_record.get("backtest_run_id") or "") != run_id:
        return _missing(key, "backtest_run_binding_unavailable")
    if run_record.get("status") != "completed":
        return _missing(key, "backtest_run_not_completed")
    if str(run_record.get("artifact_checksum") or "") != checksum:
        return _missing(key, "backtest_run_bound_to_other_artifact")
    parameters = artifact.get("parameters") if isinstance(artifact.get("parameters"), Mapping) else {}
    validated = [str(s) for s in parameters.get("symbols") or []]
    if not validated:
        return _missing(key, "validated_universe_missing")
    if str(parameters.get("bar_frequency") or "").lower() not in _DAILY_BARS:
        return _missing(key, "gate_return_basis_unknown")
    quantity = parameters.get("order_quantity")
    if not _finite(quantity) or quantity <= 0:
        return _missing(key, "order_quantity_missing")
    if universe is not None and {_code(s) for s in universe} != {_code(s) for s in validated}:
        return _missing(key, "universe_not_validated")
    return {
        "status": "approved", "baseline_key": key, "tenant_id": tenant_id,
        "persona_id": persona_id, "artifact_ref": artifact_ref, "checksum": checksum,
        "oos_sharpe": float(sharpe), "oos_max_drawdown": abs(float(drawdown)),
        "backtest_run_id": run_id, "validated_universe": validated,
        "return_basis": RETURN_BASIS, "order_quantity": quantity, "periods_per_year": PERIODS_PER_YEAR,
        "source_refs": {"registry_id": artifact_ref,
                        "approval_decision_id": entry.get("approval_decision_id"),
                        "backtest_run_id": run_id},
    }


def http_registry_entry_reader(registry_id: str, tenant_id: str) -> Mapping[str, Any]:
    """Registry owner read (same owner endpoint/principal as the pool store); ``{}`` if absent."""
    import json
    import os
    from urllib.error import HTTPError
    from urllib.parse import quote
    from urllib.request import Request, urlopen

    from services.service_token_file import configured_service_token

    base = (os.getenv("PERSONA_REGISTRY_BASE_URL") or "").rstrip("/")
    token = configured_service_token("PERSONA_REGISTRY_SERVICE_TOKEN")
    if not base or not token:
        raise RuntimeError("registry owner URL and read principal are required")
    request = Request(
        base + "/api/registry/entries/" + quote(registry_id, safe=""),
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                 "X-Tenant-Id": tenant_id}, method="GET")
    try:
        with urlopen(request, timeout=float(os.getenv("PERSONA_REGISTRY_TIMEOUT_SECONDS", "5"))) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 404:
            return {}
        raise
    entry = payload.get("entry") if isinstance(payload, Mapping) else None
    if not isinstance(entry, Mapping) or entry.get("registry_id") != registry_id:
        raise RuntimeError("registry returned a malformed or mismatched entry")
    return entry


def backtest_run_record_reader(run_id: str) -> Mapping[str, Any] | None:
    """Read-only view of the research owner's ``BacktestRunStore`` file.

    Uses the owner's existing ``ALPHA_REPLICATION_DATA_DIR``; with it unset there is
    no authoritative store and the result is ``None`` (explicit missing proof).
    """
    import json
    import os
    from pathlib import Path

    directory = (os.getenv("ALPHA_REPLICATION_DATA_DIR") or "").strip()
    if not directory or not run_id:
        return None
    path = Path(directory) / "backtest_runs.json"
    if not path.is_file():
        return None
    runs = json.loads(path.read_text(encoding="utf-8"))
    record = runs.get(run_id) if isinstance(runs, dict) else None
    return dict(record) if isinstance(record, dict) else None


def compare_member_book(baseline: Mapping[str, Any] | None, book: Mapping[str, Any]) -> dict[str, Any]:
    """Compare a member's own virtual-book metrics with its baseline; report only, no action."""
    if not isinstance(baseline, Mapping) or baseline.get("status") != "approved":
        reason = baseline.get("reason") if isinstance(baseline, Mapping) else "baseline_not_resolved"
        return {"status": "no_approved_baseline", "reason": reason or "baseline_not_resolved"}
    # Use the member metric block that is on the gate's return basis.
    book_in = book
    candidates = [b for b in (book, book.get("net_of_costs"), book.get("gate_basis")) if isinstance(b, Mapping)]
    book = next((b for b in candidates if b.get("basis") == baseline.get("return_basis")), None)
    if book is None and isinstance(book_in.get("gate_basis"), Mapping) \
            and book_in["gate_basis"].get("unavailable_reason"):
        return {"status": "member_metric_missing", "reason": book_in["gate_basis"]["unavailable_reason"]}
    if book is None or book.get("periods_per_year") != baseline.get("periods_per_year"):
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
