"""Single deterministic capital guard for every risk-increasing action."""
from __future__ import annotations

import json, math, os, urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from services.governance.approval_authority import ApprovalInvalid, configured_approval_reader
from services.capital.risk_policy import RiskPolicy, RiskPolicyEvaluationContext, RiskPolicyEvaluator

_FACT_OF_LIMIT = {
    "allowed_stages": "stage", "max_single_name_weight": "target_weights", "gross_limit": "gross_exposure",
    "net_limit": "net_exposure", "max_leverage": "leverage", "turnover_limit": "turnover",
    "max_sector_exposure": "sector_exposures", "max_factor_exposure": "factor_exposures",
    "max_strategy_family_concentration": "strategy_family_concentration",
    "max_target_overlap": "target_overlap", "max_signal_correlation": "signal_correlation",
    "max_canary_capital_scale_pct": "capital_scale_pct", "max_canary_gross_scale_pct": "gross_scale_pct",
    "allowed_asset_classes": "asset_classes", "forbidden_asset_classes": "asset_classes",
    "allowed_strategy_families": "strategy_family", "forbidden_strategy_families": "strategy_family",
    "liquidity_constraints": "liquidity", "drawdown_actions": "drawdown_pct",
}
_STRING_LIMITS = frozenset({"allowed_stages", "allowed_asset_classes", "forbidden_asset_classes", "allowed_strategy_families", "forbidden_strategy_families"})
_RAW_LIMIT_KEYS = ("gross_limit", "net_limit", "max_single_name_weight", "max_single_weight", "max_leverage", "turnover_limit", "max_target_overlap", "max_signal_correlation", "max_pairwise_correlation", "max_canary_capital_scale_pct", "max_canary_gross_scale_pct")
_RAW_LIST_KEYS = ("allowed_stages", "allowed_asset_classes", "forbidden_asset_classes", "allowed_strategy_families", "forbidden_strategy_families", "allowed_order_types", "allowed_time_in_force", "kill_switch_triggers")
_RAW_FLEX_MAP_KEYS = ("max_sector_exposure", "max_factor_exposure", "max_strategy_family_concentration")
_RAW_MAP_KEYS = ("drawdown_actions", "liquidity_constraints", "pause_rules", "liquidation_rules")
_SCOPE_RANK = {"paper": 0, "canary": 1, "live": 2}
SAFE_MODE_OK = frozenset({"normal", "normal_restored"})
STAGE_DEPLOYMENT_SCOPE = {f"{k}{s}": k for k in ("paper", "canary", "live") for s in ("", "_candidate", "_running")}


def _val(obj: Any, key: str, default: Any = None) -> Any: return obj.get(key, default) if isinstance(obj, Mapping) else getattr(obj, key, default)
def line_deployment_scope(line: Any) -> Optional[str]: return STAGE_DEPLOYMENT_SCOPE.get(str(_val(line, "stage") or "").strip().lower())
def line_is_paper_scope(line: Any) -> bool: return line_deployment_scope(line) == "paper" and str(_val(line, "capital_scope") or "").strip().lower() == "paper_ledger"
is_paper_line = line_is_paper_scope


def line_increases_risk(line: Any, existing: Any = None) -> bool:
    if float(_val(line, "target_weight", 0) or 0) > float(_val(line, "current_weight", 0) or 0): return True
    if not existing: return False
    if line_is_paper_scope(existing) and not line_is_paper_scope(line): return True
    ls, es = line_deployment_scope(line), line_deployment_scope(existing)
    return bool(ls and es and _SCOPE_RANK.get(ls, 0) > _SCOPE_RANK.get(es, 0))


def _is_finite_num(val: Any) -> bool:
    try: return not isinstance(val, bool) and math.isfinite(float(val))
    except (TypeError, ValueError): return False


def _finite_scale(v: Any, name: str) -> float:
    if _is_finite_num(v) and float(v) > 0: return float(v)
    raise CapitalGuardError(f"Invalid {name}: {v!r} must be a positive finite number")


def _validate_raw_policy(policy: Mapping[str, Any]) -> None:
    if not isinstance(policy, Mapping): raise CapitalGuardError(f"Malformed risk policy: {policy!r}")
    for k in _RAW_LIMIT_KEYS:
        if policy.get(k) is not None and not _is_finite_num(policy[k]): raise CapitalGuardError(f"Malformed risk policy limit {k}: {policy[k]!r}")
    for k in _RAW_LIST_KEYS:
        v = policy.get(k)
        if v is not None and (not isinstance(v, (list, tuple)) or isinstance(v, (str, bytes, bool)) or any(not isinstance(i, str) or isinstance(i, bool) for i in v)):
            raise CapitalGuardError(f"Malformed risk policy limit {k}: {v!r}")
    for k in _RAW_FLEX_MAP_KEYS:
        v = policy.get(k)
        if v is not None and (any(not _is_finite_num(i) for i in v.values()) if isinstance(v, Mapping) else not _is_finite_num(v)):
            raise CapitalGuardError(f"Malformed risk policy limit {k}: {v!r}")
    for k in _RAW_MAP_KEYS:
        v = policy.get(k)
        if v is not None and (not isinstance(v, Mapping) or any(not _is_finite_num(i) for i in v.values())):
            raise CapitalGuardError(f"Malformed risk policy limit {k}: {v!r}")


def _is_obs_missing(limit: str, fact: str, val: Any, obs: Any) -> bool:
    if obs in (None, "", (), [], {}): return True
    if limit == "liquidity_constraints":
        return not isinstance(obs, Mapping) or any(not _is_finite_num(obs.get(o)) for k, o in (("min_avg_daily_volume", "avg_daily_volume"), ("max_order_pct_adv", "order_pct_adv")) if k in val) or any(not _is_finite_num(v) for v in obs.values())
    return False if limit in _STRING_LIMITS else any(not _is_finite_num(v) for v in (obs.values() if isinstance(obs, Mapping) else [obs]))


def project_contexts(*, allocations: Sequence[Any] = (), lines: Sequence[Any] = (), stage: Optional[str] = None) -> list[dict[str, Any]]:
    for l in lines:
        for k in ("capital_scale_pct", "gross_scale_pct"):
            if _val(l, k) is not None: _finite_scale(_val(l, k), k)
    res = {str(_val(a, "allocation_id")): dict(a) if isinstance(a, Mapping) else a.__dict__.copy() for a in allocations if _val(a, "allocation_id")}
    for l in lines:
        aid = _val(l, "allocation_id")
        if not aid: continue
        key, cur, tw = str(aid), res.get(str(aid)), _val(l, "target_weight")
        if cur:
            l_st, cur_st = _val(l, "stage"), cur.get("stage")
            if l_st and cur_st and (line_deployment_scope(l) != line_deployment_scope(cur) or str(l_st).strip().lower() != str(cur_st).strip().lower()):
                raise CapitalGuardError(f"Incompatible stage claim for allocation {key}: proposal stage {l_st!r} does not match persisted {cur_st!r}")
            target = cur
        else:
            target = res[key] = dict(l) if isinstance(l, Mapping) else l.__dict__.copy()
        if tw is not None: target["current_weight"] = target["target_weight"] = float(tw)
        if _val(l, "persona_id"): target["persona_id"] = _val(l, "persona_id")
        for k in ("asset_classes", "strategy_family", "liquidity", "drawdown_pct"):
            if _val(l, k) is not None: target[k] = _val(l, k)
    weights: dict[str, float] = {}
    for a in res.values():
        p, tw = str(a.get("persona_id") or ""), a.get("target_weight")
        weights[p] = weights.get(p, 0.0) + float(tw if tw is not None else (a.get("current_weight") or 0))
    gross = sum(abs(w) for w in weights.values())
    facts: dict[str, Any] = {"target_weights": weights, "gross_exposure": gross, "net_exposure": sum(weights.values()), "leverage": gross, "turnover": sum(abs(float(_val(l, "delta", 0) or 0)) for l in lines)}
    acs = sorted({ac for a in res.values() for ac in (_val(a, "asset_classes") if isinstance(_val(a, "asset_classes"), (list, tuple, set)) else ([_val(a, "asset_classes")] if _val(a, "asset_classes") else [])) if ac})
    if acs: facts["asset_classes"] = tuple(acs)
    fams = {str(_val(a, "strategy_family") or "").strip() for a in res.values()} - {""}
    if len(fams) == 1: facts["strategy_family"] = next(iter(fams))
    liq = next((_val(a, "liquidity") for a in res.values() if _val(a, "liquidity")), None)
    if liq: facts["liquidity"] = liq
    dd = next((_val(a, "drawdown_pct") for a in res.values() if _val(a, "drawdown_pct") is not None), None)
    if dd is not None: facts["drawdown_pct"] = dd
    s = STAGE_DEPLOYMENT_SCOPE.get(str(stage).strip().lower(), stage) if stage else None
    stages = sorted(({line_deployment_scope(a) for a in res.values()} - {None}) | ({s} if s else set()), key=lambda x: str(x or ""))
    canary_lines = [l for l in lines if (line_deployment_scope(l) or (line_deployment_scope(res.get(str(_val(l, "allocation_id")))) if _val(l, "allocation_id") else None) or s) == "canary"]
    contexts = [
        {"stage": st, **facts, **({k: _finite_scale(_val(l, k), k) for k in ("capital_scale_pct", "gross_scale_pct") if _val(l, k) is not None} if l else {})}
        for st in stages for l in (canary_lines if st == "canary" and canary_lines else [None])
    ]
    return contexts or [facts]


def is_paper_operation(*, pool: Any, target_type: str, binding: Any = None, allocations: Sequence[Any] = (), proposal_lines: Sequence[Any] = ()) -> bool:
    if (_val(pool, "metadata") or {}).get("execution_context") != "paper" or not all(is_paper_line(a) for a in allocations): return False
    return target_type == "capital_pool_activation" or (
        target_type == "capital_binding_activation" and bool(binding and _val(binding, "role") == "paper_owner" and _val(binding, "allowed_deployment_scope") == "paper")
    ) or (target_type == "rebalance_apply" and bool(proposal_lines and all(is_paper_line(l) for l in proposal_lines)))


class CapitalGuardError(PermissionError): pass


def read_safe_mode(pool_id: str) -> str:
    from services.runtime_auth import resolve_runtime_manager_auth
    base = os.getenv("PANTHEON_RUNTIME_MANAGER_URL", "").strip().rstrip("/")
    if not base: raise RuntimeError("PANTHEON_RUNTIME_MANAGER_URL is not configured")
    req = urllib.request.Request(f"{base}/api/kill-switch/{pool_id}/safe-mode", headers={"Accept": "application/json", **resolve_runtime_manager_auth(token=None).headers()})
    with urllib.request.urlopen(req, timeout=5) as response:
        return str(json.loads(response.read().decode("utf-8"))["safe_mode_state"])


def load_risk_policy(ref: str) -> Mapping[str, Any]:
    root = os.getenv("CAPITAL_RISK_POLICY_DIR", "").strip() or str(Path(__file__).parent / "risk_policies")
    if not ref or "/" in ref or ref.startswith("."): raise RuntimeError("risk policy source is not configured for this reference")
    return json.loads((Path(root) / f"{ref}.json").read_text(encoding="utf-8"))


def _tenant_of(obj: Any) -> Optional[str]: return getattr(obj, "tenant_id", None) or (getattr(obj, "metadata", None) or {}).get("tenant_id")


class CapitalGuard:
    def __init__(self, *, approval_reader: Any = None, safe_mode_reader: Callable[[str], str] | None = None, policy_loader: Callable[[str], Mapping[str, Any]] | None = None) -> None:
        self._approval_reader, self._safe_mode_reader, self._policy_loader = approval_reader, safe_mode_reader or read_safe_mode, policy_loader or load_risk_policy

    def authorize(
        self, *, pool: Any, tenant_id: Optional[str], decision_id: Optional[str], target_type: str, target_id: str,
        expected: Mapping[str, Any], contexts: Sequence[Mapping[str, Any]] = (), binding: Any = None, allocations: Sequence[Any] = (), proposal_lines: Sequence[Any] = (),
    ) -> None:
        tenant = str(tenant_id or "").strip()
        if not tenant or _tenant_of(pool) != tenant: raise CapitalGuardError("Capital pool does not belong to the calling tenant")
        if not contexts:
            b_scope = getattr(binding, "allowed_deployment_scope", None) if binding else None
            contexts = project_contexts(allocations=allocations, lines=proposal_lines, stage=b_scope)
        if is_paper_operation(pool=pool, target_type=target_type, binding=binding, allocations=allocations, proposal_lines=proposal_lines):
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
        if state not in SAFE_MODE_OK: raise CapitalGuardError(f"Risk increase blocked while safe mode is {state!r}")

    def _require_risk_policy(self, pool: Any, target_type: str, target_id: str, contexts: Sequence[Mapping[str, Any]]) -> None:
        ref = str(getattr(pool, "risk_policy_ref", None) or "").strip()
        try:
            policy = self._policy_loader(ref)
            _validate_raw_policy(policy)
            parsed = RiskPolicy.from_mapping(policy)
            for context in contexts:
                for limit, fact in _FACT_OF_LIMIT.items():
                    if (limit in ("max_canary_capital_scale_pct", "max_canary_gross_scale_pct") and context.get("stage") != "canary") or (
                        target_type == "capital_pool_activation" and limit == "allowed_stages" and "stage" not in context
                    ):
                        continue
                    val = getattr(parsed, limit, None)
                    if val not in (None, (), {}):
                        obs = context.get(fact)
                        if _is_obs_missing(limit, fact, val, obs):
                            raise CapitalGuardError(f"Risk policy limit {limit} cannot be evaluated: {fact} unavailable")
                eval = RiskPolicyEvaluator().evaluate(policy, RiskPolicyEvaluationContext.from_mapping({
                    "target_type": target_type, "target_id": target_id, "capital_pool_id": pool.pool_id, "risk_policy_ref": ref, **context,
                }))
                if eval.rejected: raise CapitalGuardError("Risk policy rejected: " + "; ".join(eval.blocking_reasons))
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
