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

# Owner-side fact -> the RiskPolicy limit that needs it (the evaluator skips a missing fact).
_LIMIT_OF_FACT = {
    "stage": "allowed_stages", "target_weights": "max_single_name_weight", "gross_exposure": "gross_limit",
    "net_exposure": "net_limit", "leverage": "max_leverage", "turnover": "turnover_limit",
}
SAFE_MODE_OK = frozenset({"normal", "normal_restored"})


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
        required: Sequence[str] = (),
    ) -> None:
        """Raise CapitalGuardError unless this exact risk increase is allowed now."""
        tenant = str(tenant_id or "").strip()
        if not tenant or _tenant_of(pool) != tenant:
            raise CapitalGuardError("Capital pool does not belong to the calling tenant")
        self._require_safe_mode(pool.pool_id)
        self._require_risk_policy(pool, target_type, target_id, contexts, required)
        self._require_approval(decision_id, tenant, target_type, target_id, expected)

    def _require_safe_mode(self, pool_id: str) -> None:
        try:
            state = str(self._safe_mode_reader(pool_id)).strip().lower()
        except Exception as exc:
            raise CapitalGuardError(f"Kill switch / safe mode state unreadable: {exc}") from exc
        if state not in SAFE_MODE_OK:
            raise CapitalGuardError(f"Risk increase blocked while safe mode is {state!r}")

    def _require_risk_policy(self, pool: Any, target_type: str, target_id: str, contexts: Sequence[Mapping[str, Any]], required: Sequence[str]) -> None:
        ref = str(pool.risk_policy_ref or "").strip()
        try:
            policy = self._policy_loader(ref)
            evaluator = RiskPolicyEvaluator()
            parsed = RiskPolicy.from_mapping(policy)
            for context in contexts:
                # A configured limit that the owner cannot observe is not a pass.
                for fact in required:
                    if getattr(parsed, _LIMIT_OF_FACT[fact]) and context.get(fact) in (None, "", {}):
                        raise CapitalGuardError(f"Risk policy limit {_LIMIT_OF_FACT[fact]} cannot be evaluated: {fact} unavailable")
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
