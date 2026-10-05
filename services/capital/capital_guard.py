"""Single deterministic capital guard for every risk-increasing action."""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from services.governance.approval_authority import ApprovalInvalid, configured_approval_reader
from services.capital.risk_policy import (
    RiskPolicy,
    RiskPolicyEvaluator,
    _optional_float,
)

_SCOPE_RANK = {"paper": 0, "canary": 1, "live": 2}
SAFE_MODE_OK = frozenset({"normal", "normal_restored"})
STAGE_DEPLOYMENT_SCOPE = {f"{k}{s}": k for k in ("paper", "canary", "live") for s in ("", "_candidate", "_running")}


class CapitalGuardError(PermissionError):
    pass


def _val(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def line_deployment_scope(line: Any) -> Optional[str]:
    stage = _val(line, "stage") or _val(line, "allowed_deployment_scope") or ""
    return STAGE_DEPLOYMENT_SCOPE.get(str(stage).strip().lower())


def line_is_paper_scope(line: Any) -> bool:
    capital_scope = str(_val(line, "capital_scope") or "").strip().lower()
    return line_deployment_scope(line) == "paper" and capital_scope == "paper_ledger"


def line_increases_risk(line: Any, existing: Any = None) -> bool:
    if float(_val(line, "target_weight", 0) or 0) > float(_val(line, "current_weight", 0) or 0):
        return True
    if not existing:
        return False
    if line_is_paper_scope(existing) and not line_is_paper_scope(line):
        return True
    new_scope = line_deployment_scope(line)
    old_scope = line_deployment_scope(existing)
    return bool(new_scope and old_scope and _SCOPE_RANK[new_scope] > _SCOPE_RANK[old_scope])


def _finite_scale(v: Any, name: str) -> float:
    val = _optional_float(v)
    if val is not None and val > 0:
        return val
    raise CapitalGuardError(f"Invalid {name}: {v!r} must be a positive finite number")


def _context(facts: Mapping[str, Any], stage: Optional[str]) -> dict[str, Any]:
    if stage:
        return {**facts, "stage": stage}
    return dict(facts)


def _allocation_record(alloc: Any) -> dict[str, Any]:
    record = {k: _val(alloc, k) for k in ("persona_id", "capital_sleeve_id", "binding_id", "stage", "target_weight")}
    record["allocation_id"] = str(_val(alloc, "allocation_id"))
    record["current_weight"] = _val(alloc, "current_weight", 0)
    return record


def _merge_line(records: dict[str, dict[str, Any]], line: Any) -> None:
    aid = str(_val(line, "allocation_id") or _val(line, "capital_sleeve_id") or _val(line, "persona_id") or id(line))
    record = records.get(aid)
    if record is None:
        record = records[aid] = {"allocation_id": aid, "stage": _val(line, "stage")}
    else:
        line_stage, held_stage = _val(line, "stage"), record.get("stage")
        if line_stage and held_stage and (
            line_deployment_scope(line) != line_deployment_scope(record)
            or str(line_stage).strip().lower() != str(held_stage).strip().lower()
        ):
            raise CapitalGuardError(
                f"Incompatible stage claim for allocation {aid}: proposal stage {line_stage!r} does not match persisted {held_stage!r}"
            )
    weight = _val(line, "target_weight")
    if weight is not None:
        record["current_weight"] = record["target_weight"] = float(weight)
    record.update({k: _val(line, k) for k in ("persona_id", "capital_sleeve_id", "binding_id") if _val(line, k)})


def _plan_facts(records: Mapping[str, Mapping[str, Any]], lines: Sequence[Any]) -> dict[str, Any]:
    weights: dict[str, float] = {}
    for rec in records.values():
        weight = rec.get("target_weight")
        if weight is None:
            weight = rec.get("current_weight") or 0
        persona = str(rec.get("persona_id") or "")
        weights[persona] = weights.get(persona, 0.0) + float(weight)
    gross = sum(abs(w) for w in weights.values())
    return {
        "target_weights": weights,
        "gross_exposure": gross,
        "net_exposure": sum(weights.values()),
        "leverage": gross,
        "turnover": sum(abs(float(_val(line, "delta", 0) or 0)) for line in lines),
    }


def project_contexts(
    *, allocations: Sequence[Any] = (), lines: Sequence[Any] = (), stage: Optional[str] = None,
    binding: Any = None, bindings: Sequence[Any] = (),
) -> list[dict[str, Any]]:
    for line in lines:
        for key in ("capital_scale_pct", "gross_scale_pct"):
            if _val(line, key) is not None:
                _finite_scale(_val(line, key), key)
    records = {str(_val(a, "allocation_id")): _allocation_record(a) for a in allocations if _val(a, "allocation_id")}
    for line in lines:
        _merge_line(records, line)
    facts = _plan_facts(records, lines)

    scope = STAGE_DEPLOYMENT_SCOPE.get(str(stage).strip().lower(), stage) if stage else None
    all_bindings = ([binding] if binding else []) + list(bindings)
    by_key = {_val(b, "binding_id"): b for b in all_bindings if _val(b, "binding_id")}
    by_key.update({
        (_val(b, "persona_id"), _val(b, "capital_sleeve_id")): b
        for b in all_bindings if _val(b, "persona_id") and _val(b, "capital_sleeve_id")
    })

    contexts = []
    for rec in records.values():
        held = by_key.get(_val(rec, "binding_id")) or by_key.get((_val(rec, "persona_id"), _val(rec, "capital_sleeve_id")))
        rec_scope = line_deployment_scope(rec) or (line_deployment_scope(held) if held else None)
        contexts.append(_context(facts, rec_scope or (None if binding else scope)))

    seen = {id(binding), _val(binding, "binding_id")} - {None} if binding else set()
    if binding:
        contexts.append(_context(facts, line_deployment_scope(binding) or scope))
    for b in bindings:
        bid = _val(b, "binding_id")
        if _val(b, "status") == "active" and id(b) not in seen and (not bid or bid not in seen):
            seen.update((id(b), bid))
            contexts.append(_context(facts, line_deployment_scope(b)))
    return contexts or [_context(facts, scope)]


def is_paper_operation(
    *, pool: Any, target_type: str, binding: Any = None, allocations: Sequence[Any] = (),
    proposal_lines: Sequence[Any] = (), bindings: Sequence[Any] = (),
) -> bool:
    if (_val(pool, "metadata") or {}).get("execution_context") != "paper":
        return False
    if not all(line_is_paper_scope(a) for a in allocations):
        return False
    if target_type == "capital_pool_activation":
        return not any(
            _val(b, "status") == "active" and line_deployment_scope(b) in ("live", "canary") for b in bindings
        )
    if target_type == "capital_binding_activation":
        return bool(
            binding and _val(binding, "role") == "paper_owner"
            and _val(binding, "allowed_deployment_scope") == "paper"
        )
    if target_type == "rebalance_apply":
        return bool(proposal_lines and all(line_is_paper_scope(line) for line in proposal_lines))
    return False


def read_safe_mode(pool_id: str) -> str:
    from services.runtime_auth import resolve_runtime_manager_auth
    base = os.getenv("PANTHEON_RUNTIME_MANAGER_URL", "").strip().rstrip("/")
    if not base:
        raise RuntimeError("PANTHEON_RUNTIME_MANAGER_URL is not configured")
    headers = {"Accept": "application/json", **resolve_runtime_manager_auth(token=None).headers()}
    req = urllib.request.Request(f"{base}/api/kill-switch/{pool_id}/safe-mode", headers=headers)
    with urllib.request.urlopen(req, timeout=5) as resp:
        return str(json.loads(resp.read().decode("utf-8"))["safe_mode_state"])


def load_risk_policy(ref: str) -> Mapping[str, Any]:
    root = os.getenv("CAPITAL_RISK_POLICY_DIR", "").strip() or str(Path(__file__).parent / "risk_policies")
    if not ref or "/" in ref or ref.startswith("."):
        raise RuntimeError("risk policy source is not configured for this reference")
    return json.loads((Path(root) / f"{ref}.json").read_text(encoding="utf-8"))


def _tenant_of(obj: Any) -> Optional[str]:
    # Only isolated legacy JSON entities lack the formal ownership attribute.
    return getattr(obj, "tenant_id", (getattr(obj, "metadata", None) or {}).get("tenant_id"))


class CapitalGuard:
    def __init__(
        self, *, approval_reader: Any = None, safe_mode_reader: Callable[[str], str] | None = None,
        policy_loader: Callable[[str], Mapping[str, Any]] | None = None,
    ) -> None:
        self._approval_reader = approval_reader
        self._safe_mode_reader = safe_mode_reader or read_safe_mode
        self._policy_loader = policy_loader or load_risk_policy

    def authorize(
        self, *, pool: Any, tenant_id: Optional[str], decision_id: Optional[str], target_type: str, target_id: str,
        expected: Mapping[str, Any], contexts: Sequence[Mapping[str, Any]] = (), binding: Any = None,
        allocations: Sequence[Any] = (), proposal_lines: Sequence[Any] = (), bindings: Sequence[Any] = (),
    ) -> None:
        tenant = str(tenant_id or "").strip()
        if not tenant or _tenant_of(pool) != tenant:
            raise CapitalGuardError("Capital pool does not belong to the calling tenant")
        if not contexts:
            contexts = project_contexts(
                allocations=allocations, lines=proposal_lines,
                stage=getattr(binding, "allowed_deployment_scope", None) if binding else None,
                binding=binding, bindings=bindings,
            )
        if is_paper_operation(
            pool=pool, target_type=target_type, binding=binding, allocations=allocations,
            proposal_lines=proposal_lines, bindings=bindings,
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
            policy = RiskPolicy.from_mapping(self._policy_loader(ref))
            for context in contexts:
                result = RiskPolicyEvaluator().evaluate(policy, {
                    **context, "target_type": target_type, "target_id": target_id,
                    "capital_pool_id": pool.pool_id, "risk_policy_ref": ref,
                })
                if result.rejected:
                    raise CapitalGuardError("Risk policy rejected: " + "; ".join(result.blocking_reasons))
        except CapitalGuardError:
            raise
        except Exception as exc:
            raise CapitalGuardError(f"Risk policy unavailable: {exc}") from exc

    def _require_approval(
        self, decision_id: Optional[str], tenant: str, target_type: str, target_id: str, expected: Mapping[str, Any],
    ) -> None:
        reader = self._approval_reader or configured_approval_reader("capital")
        binding = {"tenant_id": tenant, "target_type": target_type, "target_id": target_id, **expected}
        try:
            reader.get(str(decision_id or "")).require_valid(expected=binding)
        except ApprovalInvalid as exc:
            raise CapitalGuardError(f"Governance approval rejected: {exc}") from exc
        except Exception as exc:
            raise CapitalGuardError(f"Governance approval unavailable: {exc}") from exc
