"""The single authority for risk-increasing capital actions.

Pool activation, binding activation and rebalance apply all pass through
``CapitalGuard.authorize``: tenant check, kill switch / safe mode, risk_policy
limits, then an exact-action governance approval. Every unreadable input fails
closed. Risk-decreasing work never needs this guard.
"""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from services.governance.approval_authority import ApprovalInvalid, configured_approval_reader

try:
    from .risk_policy import RiskPolicy, RiskPolicyEvaluationContext, RiskPolicyEvaluator
except ImportError:
    from risk_policy import RiskPolicy, RiskPolicyEvaluationContext, RiskPolicyEvaluator  # type: ignore

_FACT_OF_LIMIT = {
    "allowed_stages": "stage", "max_single_name_weight": "target_weights", "gross_limit": "gross_exposure",
    "net_limit": "net_exposure", "max_leverage": "leverage", "turnover_limit": "turnover",
    "max_sector_exposure": "sector_exposures", "max_factor_exposure": "factor_exposures",
    "max_strategy_family_concentration": "strategy_family_concentration",
    "max_target_overlap": "target_overlap", "max_signal_correlation": "signal_correlation",
}
SAFE_MODE_OK = frozenset({"normal", "normal_restored"})
STAGE_DEPLOYMENT_SCOPE = {
    "paper": "paper", "paper_candidate": "paper", "paper_running": "paper",
    "canary": "canary", "canary_candidate": "canary", "canary_running": "canary",
    "live": "live", "live_candidate": "live", "live_running": "live",
}


def _val(obj: Any, key: str, default: Any = None) -> Any:
    return obj.get(key, default) if isinstance(obj, Mapping) else getattr(obj, key, default)


def line_deployment_scope(line: Any) -> Optional[str]:
    return STAGE_DEPLOYMENT_SCOPE.get(str(_val(line, "stage") or "").strip().lower())


def line_is_paper_scope(line: Any) -> bool:
    return line_deployment_scope(line) == "paper" and str(_val(line, "capital_scope") or "").strip().lower() == "paper_ledger"


is_paper_line = line_is_paper_scope


def line_increases_risk(line: Any, existing: Any = None) -> bool:
    return (float(_val(line, "target_weight", 0) or 0) > float(_val(line, "current_weight", 0) or 0)
            or bool(existing and line_is_paper_scope(existing) and not line_is_paper_scope(line)))


def project_contexts(*, allocations: Sequence[Any] = (), lines: Sequence[Any] = (), stage: Optional[str] = None) -> list[dict[str, Any]]:
    res = {str(_val(a, "allocation_id")): dict(a) if isinstance(a, Mapping) else a.__dict__.copy() for a in allocations if _val(a, "allocation_id")}
    for l in lines:
        aid = _val(l, "allocation_id")
        if aid:
            res[str(aid)] = {**res.get(str(aid), {}), **(dict(l) if isinstance(l, Mapping) else l.__dict__)}
    weights: dict[str, float] = {}
    for a in res.values():
        p = str(a.get("persona_id") or "")
        tw = a.get("target_weight")
        weights[p] = weights.get(p, 0.0) + float(tw if tw is not None else (a.get("current_weight") or 0))
    gross = sum(abs(w) for w in weights.values())
    facts = {"target_weights": weights, "gross_exposure": gross, "net_exposure": sum(weights.values()), "leverage": gross, "turnover": sum(abs(float(_val(l, "delta", 0) or 0)) for l in lines)}
    s = STAGE_DEPLOYMENT_SCOPE.get(str(stage).strip().lower(), stage) if stage else None
    stages = sorted({s} if s else {line_deployment_scope(a) for a in res.values()} - {None}, key=lambda x: str(x or ""))
    return [{"stage": st, **facts} for st in stages] or [facts]


def is_paper_operation(
    *, pool: Any, target_type: str, binding: Any = None, allocations: Sequence[Any] = (), proposal_lines: Sequence[Any] = (),
) -> bool:
    if (_val(pool, "metadata") or {}).get("execution_context") != "paper" or not all(is_paper_line(a) for a in allocations):
        return False
    if target_type == "capital_pool_activation":
        return True
    if target_type == "capital_binding_activation":
        return bool(binding and _val(binding, "role") == "paper_owner" and _val(binding, "allowed_deployment_scope") == "paper")
    return bool(target_type == "rebalance_apply" and proposal_lines and all(is_paper_line(l) for l in proposal_lines))


class CapitalGuardError(PermissionError):
    """A risk-increasing action was refused (maps to HTTP 403)."""


def read_safe_mode(pool_id: str) -> str:
    from services.runtime_auth import resolve_runtime_manager_auth
    base = os.getenv("PANTHEON_RUNTIME_MANAGER_URL", "").strip().rstrip("/")
    if not base:
        raise RuntimeError("PANTHEON_RUNTIME_MANAGER_URL is not configured")
    request = urllib.request.Request(
        f"{base}/api/kill-switch/{pool_id}/safe-mode",
        headers={"Accept": "application/json", **resolve_runtime_manager_auth(token=None).headers()},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return str(json.loads(response.read().decode("utf-8"))["safe_mode_state"])


def load_risk_policy(ref: str) -> Mapping[str, Any]:
    root = os.getenv("CAPITAL_RISK_POLICY_DIR", "").strip() or str(Path(__file__).parent / "risk_policies")
    if not ref or "/" in ref or ref.startswith("."):
        raise RuntimeError("risk policy source is not configured for this reference")
    return json.loads((Path(root) / f"{ref}.json").read_text(encoding="utf-8"))


def _tenant_of(obj: Any) -> Optional[str]:
    return getattr(obj, "tenant_id", None) or (getattr(obj, "metadata", None) or {}).get("tenant_id")


class CapitalGuard:
    def __init__(
        self,
        *,
        approval_reader: Any = None,
        safe_mode_reader: Callable[[str], str] | None = None,
        policy_loader: Callable[[str], Mapping[str, Any]] | None = None,
    ) -> None:
        self._approval_reader = approval_reader
        self._safe_mode_reader = safe_mode_reader or read_safe_mode
        self._policy_loader = policy_loader or load_risk_policy

    def authorize(
        self,
        *,
        pool: Any,
        tenant_id: Optional[str],
        decision_id: Optional[str],
        target_type: str,
        target_id: str,
        expected: Mapping[str, Any],
        contexts: Sequence[Mapping[str, Any]] = (),
        binding: Any = None,
        allocations: Sequence[Any] = (),
        proposal_lines: Sequence[Any] = (),
    ) -> None:
        tenant = str(tenant_id or "").strip()
        if not tenant or _tenant_of(pool) != tenant:
            raise CapitalGuardError("Capital pool does not belong to the calling tenant")
        if not contexts:
            b_scope = getattr(binding, "allowed_deployment_scope", None) if binding else None
            contexts = project_contexts(allocations=allocations, lines=proposal_lines, stage=b_scope)
        if is_paper_operation(
            pool=pool, target_type=target_type, binding=binding, allocations=allocations, proposal_lines=proposal_lines,
        ):
            if str(getattr(pool, "risk_policy_ref", None) or "").strip():
                self._require_risk_policy(pool, target_type, target_id, contexts)
            return
        self._require_safe_mode(pool.pool_id)
        self._require_risk_policy(pool, target_type, target_id, contexts)
        self._require_approval(decision_id, tenant, target_type, target_id, expected)

    def _require_safe_mode(self, pool_id: str) -> None:
        try:
            state = str(self._safe_mode_reader(pool_id)).strip().lower()
        except Exception as exc:
            raise CapitalGuardError(f"Kill switch / safe mode state unreadable: {exc}") from exc
        if state not in SAFE_MODE_OK:
            raise CapitalGuardError(f"Risk increase blocked while safe mode is {state!r}")

    def _require_risk_policy(
        self, pool: Any, target_type: str, target_id: str, contexts: Sequence[Mapping[str, Any]],
    ) -> None:
        ref = str(getattr(pool, "risk_policy_ref", None) or "").strip()
        try:
            policy = self._policy_loader(ref)
            evaluator = RiskPolicyEvaluator()
            parsed = RiskPolicy.from_mapping(policy)
            for context in contexts:
                for limit, fact in _FACT_OF_LIMIT.items():
                    if target_type == "capital_pool_activation" and limit == "allowed_stages" and "stage" not in context:
                        continue
                    if getattr(parsed, limit, None) not in (None, ()) and context.get(fact) in (None, ""):
                        raise CapitalGuardError(f"Risk policy limit {limit} cannot be evaluated: {fact} unavailable")
                evaluation = evaluator.evaluate(policy, RiskPolicyEvaluationContext.from_mapping({
                    "target_type": target_type, "target_id": target_id, "capital_pool_id": pool.pool_id, "risk_policy_ref": ref, **context,
                }))
                if evaluation.rejected:
                    raise CapitalGuardError("Risk policy rejected: " + "; ".join(evaluation.blocking_reasons))
        except CapitalGuardError:
            raise
        except Exception as exc:
            raise CapitalGuardError(f"Risk policy unavailable: {exc}") from exc

    def _require_approval(
        self, decision_id: Optional[str], tenant: str, target_type: str, target_id: str, expected: Mapping[str, Any],
    ) -> None:
        try:
            reader = self._approval_reader or configured_approval_reader("capital")
            reader.get(str(decision_id or "")).require_valid(
                expected={"tenant_id": tenant, "target_type": target_type, "target_id": target_id, **expected}
            )
        except ApprovalInvalid as exc:
            raise CapitalGuardError(f"Governance approval rejected: {exc}") from exc
        except Exception as exc:
            raise CapitalGuardError(f"Governance approval unavailable: {exc}") from exc
