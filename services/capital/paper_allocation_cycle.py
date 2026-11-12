"""Governed quarterly paper allocation cycle (owner side).

One cycle per tenant and quarter: admitted quarterly ranking snapshot -> the real allocation
policy arithmetic over every paper persona -> a paper rebalance proposal -> automatic apply inside
the paper ledger with an audit receipt and a rollback target.  The cycle can only create or apply
a proposal whose lines are all ``paper_ledger`` scope in a pool whose execution context is paper;
real capital (canary/live) proposals and their human approval path are never reached from here.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List

try:
    from . import allocation_lineage as lineage
    from .allocation_store import AllocationAuthorityNotFound
    from .models import ApplyRebalanceRequest, CreateRebalanceRequest, PaperAllocationCycleRequest
except ImportError:  # pragma: no cover - flat-module import path
    import allocation_lineage as lineage  # type: ignore
    from allocation_store import AllocationAuthorityNotFound  # type: ignore
    from models import ApplyRebalanceRequest, CreateRebalanceRequest, PaperAllocationCycleRequest  # type: ignore

CYCLE_KIND = "paper_quarterly_cycle"
QUARTERLY_INCREASE_CAP = 0.25


def cycle_rebalance_id(tenant: str, quarter: str, pool_id: str) -> str:
    digest = hashlib.sha256(f"{tenant}|{quarter}|{pool_id}".encode()).hexdigest()[:16]
    return f"paper-quarterly-{quarter.lower()}-{digest}"


def _held_by_persona(service: Any, pool_id: str, tenant: str) -> Dict[str, Dict[str, Any]]:
    held = service.allocation_store.list_allocations(capital_pool_id=pool_id, tenant_id=tenant)
    return {str(a.get("persona_id")): a for a in held if a.get("capital_scope") == "paper_ledger"}


def _cycle_result(service: Any, rebalance_id: str, tenant: str, *, replayed: bool) -> Dict[str, Any]:
    proposal = service.allocation_store.get_rebalance(rebalance_id, tenant_id=tenant)
    return {"cycle": CYCLE_KIND, "rebalance": proposal, "replayed": replayed,
            "applied": bool(proposal.get("applied")), "receipt": proposal.get("apply_receipt")}


def _apply(service: Any, rebalance_id: str, body: PaperAllocationCycleRequest) -> None:
    command_id = f"cmd-{rebalance_id}"
    service.apply_rebalance(rebalance_id, ApplyRebalanceRequest(
        actor_id=body.actor_id, actor_role=body.actor_role, idempotency_key=command_id,
        request_hash=hashlib.sha256(command_id.encode()).hexdigest(), command_id=command_id,
        approval_ref=f"paper-auto:{rebalance_id}",
    ))


def run_paper_allocation_cycle(service: Any, body: PaperAllocationCycleRequest, *, tenant: str, paper_environment: bool) -> Dict[str, Any]:
    service._authorize("Rebalance", "create", body.actor_role)
    if not paper_environment:
        raise PermissionError("Paper allocation cycle is only available in the paper environment")
    if not tenant:
        raise PermissionError("Paper allocation cycle requires a tenant")
    pool = service.get_pool(body.capital_pool_id)
    if (getattr(pool, "metadata", None) or {}).get("execution_context") != "paper":
        raise PermissionError("Paper allocation cycle requires a paper capital pool")
    if pool.status != "active":
        raise ValueError(f"CapitalPool {pool.pool_id!r} must be active for the paper allocation cycle")
    rebalance_id = cycle_rebalance_id(tenant, body.quarter, pool.pool_id)

    try:  # rerun / catch-up: the quarter already has its cycle; finish an unapplied one, never recompute
        existing = service.allocation_store.get_rebalance(rebalance_id, tenant_id=tenant)
    except AllocationAuthorityNotFound:
        existing = None
    if existing is not None:
        if not existing.get("applied") and existing.get("status") != "failed":
            _apply(service, rebalance_id, body)
        return _cycle_result(service, rebalance_id, tenant, replayed=True)

    snapshot = lineage._load_snapshot(body.ranking_snapshot_id)
    if snapshot.get("period") != body.quarter:
        raise lineage.AllocationLineageError("ranking snapshot period does not match the cycle quarter")
    held = _held_by_persona(service, pool.pool_id, tenant)
    rows: List[Dict[str, Any]] = []
    skipped: List[Dict[str, str]] = []
    for item in snapshot["items"]:
        persona_id = str(item.get("persona_id") or "")
        if str(item.get("stage") or "").strip().lower() != "paper_running":
            continue
        ledger = str(item.get("paper_ledger_id") or "").strip()
        bindings = [
            b for b in service.list_bindings(persona_id=persona_id, capital_pool_id=pool.pool_id)
            if b.role == "paper_owner" and not service._normalized_sleeve_id(b.capital_sleeve_id)
            and service._binding_matches_rebalance_line(
                b, capital_pool_id=pool.pool_id, persona_id=persona_id, capital_sleeve_id=None, required_scope="paper")
        ]
        if not ledger:
            skipped.append({"persona_id": persona_id, "reason": "paper_ledger_missing"})
        elif len(bindings) != 1:
            skipped.append({"persona_id": persona_id, "reason": "no_unique_paper_owner_binding"})
        else:
            health = body.pool_health.get(persona_id) or {}
            rows.append({
                "persona_id": persona_id, "ranking_snapshot_id": snapshot["ranking_snapshot_id"],
                "capital_scope": "paper_ledger", "paper_ledger_id": ledger, "capital_pool_id": pool.pool_id,
                "capital_sleeve_id": None, "binding_id": bindings[0].binding_id,
                "current_weight": float((held.get(persona_id) or {}).get("current_weight") or 0.0),
                **{k: health[k] for k in ("risk_budget_factor", "capacity_factor") if k in health},
                "pool_health_factors": health or {"status": "unavailable", "reason": "pool_health_not_supplied"},
            })
    if not rows:
        raise lineage.AllocationLineageError("no eligible paper persona in the ranking snapshot; cycle not created")
    evaluation = lineage.evaluate_allocation(
        {"ranking_snapshot_id": snapshot["ranking_snapshot_id"], "rows": rows}, paper=True, quarterly=True)
    lines = evaluation["lines"]
    if any(line.get("capital_scope") != "paper_ledger" for line in lines):
        raise PermissionError("paper cycle cannot carry a non-paper line")
    budget = getattr(pool, "budget", None)
    request_hash = hashlib.sha256(rebalance_id.encode()).hexdigest()
    created = service.create_rebalance(CreateRebalanceRequest(
        actor_id=body.actor_id, actor_role=body.actor_role, idempotency_key=rebalance_id, request_hash=request_hash,
        rebalance_id=rebalance_id, capital_pool_id=pool.pool_id,
        ranking_snapshot_id=evaluation["ranking_snapshot_id"],
        allocation_evaluation_id=evaluation["allocation_evaluation_id"],
        allocation_policy_version=evaluation["allocation_policy_version"],
        reason=f"governed paper quarterly allocation {body.quarter}", lines=lines,
        simulation={"kind": CYCLE_KIND, "quarter": body.quarter, "paper_total": budget,
                    "paper_total_basis": "capital_pool.budget" if budget is not None else "unknown",
                    "target_weight_sum": round(sum(l["target_weight"] for l in lines), 8),
                    "line_capital": ({l["persona_id"]: round(l["target_weight"] * budget, 8) for l in lines}
                                     if budget is not None else None),
                    "skipped": skipped},
        constraints={"max_total_weight": 1.0, "quarterly_increase_cap": QUARTERLY_INCREASE_CAP,
                     "tier_caps": {"s": 0.25, "a": 0.15, "b": 0.08, "watch": 0.0},
                     "capital_scope": "paper_ledger", "human_approval": "not_required_in_paper"},
        rollback_target={"kind": "previous_paper_allocation_weights",
                         "allocations": [{"allocation_id": f"{pool.pool_id}|persona:{l['persona_id']}",
                                          "persona_id": l["persona_id"], "weight": l["current_weight"],
                                          "allocation_version": (held.get(l["persona_id"]) or {}).get("allocation_version", 0)}
                                         for l in lines]},
        audit_refs=[f"paper-quarterly-cycle:{body.quarter}:{evaluation['allocation_evaluation_id']}"],
    ))
    _apply(service, created["rebalance_id"], body)
    return _cycle_result(service, rebalance_id, tenant, replayed=False)
