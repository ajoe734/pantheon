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

from services.governance.approval_authority import (
    ApprovalInvalid,
    configured_approval_reader,
)

try:
    from .risk_policy import RiskPolicy, RiskPolicyEvaluationContext, RiskPolicyEvaluator
except ImportError:
    from risk_policy import RiskPolicy, RiskPolicyEvaluationContext, RiskPolicyEvaluator  # type: ignore

# RiskPolicy limit -> the fact it needs (the evaluator silently skips a missing fact).
_FACT_OF_LIMIT = {
    "allowed_stages": "stage", "max_single_name_weight": "target_weights", "gross_limit": "gross_exposure",
    "net_limit": "net_exposure", "max_leverage": "leverage", "turnover_limit": "turnover",
    "max_sector_exposure": "sector_exposures", "max_factor_exposure": "factor_exposures",
    "max_strategy_family_concentration": "strategy_family_concentration",
    "max_target_overlap": "target_overlap", "max_signal_correlation": "signal_correlation",
}
SAFE_MODE_OK = frozenset({"normal", "normal_restored"})
_STAGE_DEPLOYMENT_SCOPE = {
    "paper": "paper",
    "paper_candidate": "paper",
    "paper_running": "paper",
    "canary": "canary",
    "canary_candidate": "canary",
    "canary_running": "canary",
    "live": "live",
    "live_candidate": "live",
    "live_running": "live",
}


def is_paper_line(line: Any) -> bool:
    get = (lambda k: line.get(k)) if isinstance(line, dict) else (lambda k: getattr(line, k, None))
    stage = _STAGE_DEPLOYMENT_SCOPE.get(str(get("stage") or "").strip().lower())
    scope = str(get("capital_scope") or "").strip().lower()
    return stage == "paper" and scope == "paper_ledger"


def is_paper_operation(
    *,
    pool: Any,
    target_type: str,
    binding: Any = None,
    allocations: Sequence[Any] = (),
    proposal_lines: Sequence[Any] = (),
    contexts: Sequence[Mapping[str, Any]] = (),
) -> bool:
    if (getattr(pool, "metadata", None) or {}).get("execution_context") != "paper":
        return False
    if target_type == "capital_pool_activation":
        if allocations:
            return all(is_paper_line(a) for a in allocations)
        return all(
            _STAGE_DEPLOYMENT_SCOPE.get(str(c.get("stage") or "").strip().lower()) in (None, "paper")
            for c in contexts
            if c.get("stage") is not None
        )
    if target_type == "capital_binding_activation":
        get = (lambda k: binding.get(k)) if isinstance(binding, dict) else (lambda k: getattr(binding, k, None))
        return bool(
            binding
            and get("role") == "paper_owner"
            and get("allowed_deployment_scope") == "paper"
            and all(is_paper_line(a) for a in allocations)
        )
    if target_type == "rebalance_apply":
        return bool(
            proposal_lines
            and all(is_paper_line(l) for l in proposal_lines)
            and all(is_paper_line(a) for a in allocations)
        )
    return False


class CapitalGuardError(PermissionError):
    """A risk-increasing action was refused (maps to HTTP 403)."""


def read_safe_mode(pool_id: str) -> str:
    """Read the pool's safe-mode state from the runtime manager; raises if unreadable."""
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
    """Load ``<CAPITAL_RISK_POLICY_DIR>/<ref>.json``; raises if absent or unreadable."""
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
        contexts: Sequence[Mapping[str, Any]],
        binding: Any = None,
        allocations: Sequence[Any] = (),
        proposal_lines: Sequence[Any] = (),
    ) -> None:
        """Raise CapitalGuardError unless this exact risk increase is allowed now."""
        tenant = str(tenant_id or "").strip()
        if not tenant or _tenant_of(pool) != tenant:
            raise CapitalGuardError("Capital pool does not belong to the calling tenant")
        if is_paper_operation(
            pool=pool,
            target_type=target_type,
            binding=binding,
            allocations=allocations,
            proposal_lines=proposal_lines,
            contexts=contexts,
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

    def _require_risk_policy(self, pool: Any, target_type: str, target_id: str, contexts: Sequence[Mapping[str, Any]]) -> None:
        ref = str(pool.risk_policy_ref or "").strip()
        try:
            policy = self._policy_loader(ref)
            evaluator = RiskPolicyEvaluator()
            parsed = RiskPolicy.from_mapping(policy)
            for context in contexts:
                # A configured limit that the owner cannot observe is not a pass.
                # (zero-valued limits count as configured).
                for limit, fact in _FACT_OF_LIMIT.items():
                    if target_type == "capital_pool_activation" and limit == "allowed_stages" and "stage" not in context:
                        continue
                    configured = getattr(parsed, limit)
                    if configured is not None and configured != () and context.get(fact) in (None, ""):
                        raise CapitalGuardError(f"Risk policy limit {limit} cannot be evaluated: {fact} unavailable")
                evaluation = evaluator.evaluate(policy, RiskPolicyEvaluationContext.from_mapping({
                    "target_type": target_type, "target_id": target_id,
                    "capital_pool_id": pool.pool_id, "risk_policy_ref": ref, **context,
                }))
                if evaluation.rejected:
                    raise CapitalGuardError("Risk policy rejected: " + "; ".join(evaluation.blocking_reasons))
        except CapitalGuardError:
            raise
        except Exception as exc:
            raise CapitalGuardError(f"Risk policy unavailable: {exc}") from exc

    def _require_approval(self, decision_id: Optional[str], tenant: str, target_type: str, target_id: str, expected: Mapping[str, Any]) -> None:
        try:
            reader = self._approval_reader or configured_approval_reader("capital")
            reader.get(str(decision_id or "")).require_valid(
                expected={"tenant_id": tenant, "target_type": target_type, "target_id": target_id, **expected}
            )
        except ApprovalInvalid as exc:
            raise CapitalGuardError(f"Governance approval rejected: {exc}") from exc
        except Exception as exc:
            raise CapitalGuardError(f"Governance approval unavailable: {exc}") from exc
