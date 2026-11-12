"""Checksum-bound portfolio logic carried by the existing execution artifact.

Members are embedded immutable snapshots, never resolved from a mutable pool.
Registry approval, DeploymentPlan and RuntimeBinding still authorize the envelope.
"""
from __future__ import annotations

import copy
from decimal import Decimal
import math
from typing import Any, Mapping

KIND = "persona_portfolio_v1"


def is_portfolio(artifact: Mapping[str, Any]) -> bool:
    logic = artifact.get("strategy_logic")
    return isinstance(logic, Mapping) and logic.get("kind") == KIND


def _symbol_key(symbol: str) -> str:
    from services.execution.shioaji_adapter import normalize_taiwan_symbol

    if symbol.rsplit(".", 1)[-1] in {"TW", "TWSE", "TWO", "TPEX"}:
        return str(normalize_taiwan_symbol(symbol))
    return symbol


def validate_portfolio_envelope(artifact: Mapping[str, Any]) -> None:
    from .strategy_artifact import (
        StrategyArtifactValidationError as Error,
        strategy_artifact_checksum,
        validate_strategy_artifact,
    )

    logic = artifact["strategy_logic"]
    members = logic["members"]
    params = artifact["parameters"]
    if artifact["mutation_surface"]["controls"]:
        raise Error("portfolio envelope is immutable; publish a new version to change it")
    if set(artifact["mutation_surface"]["immutable_parameters"]) != set(params):
        raise Error("all envelope parameters must be immutable")
    symbols = params.get("symbols")
    if not isinstance(symbols, list) or not symbols:
        raise Error("portfolio envelope requires symbols")
    if len({_symbol_key(s) for s in symbols}) != len(symbols):
        raise Error("portfolio envelope contains duplicate underlying aliases")
    ids = set()
    covered = set()
    gross = 0.0
    parents = set(artifact["lineage"].get("parent_registry_ids", []))
    for member in members:
        child = member["artifact"]
        if is_portfolio(child):
            raise Error("nested portfolio envelopes are unsupported")
        validate_strategy_artifact(child)
        if member["member_id"] in ids:
            raise Error("duplicate portfolio member_id")
        ids.add(member["member_id"])
        if (member["artifact_id"] != child["artifact_id"]
                or member["version"] != child["version"]
                or member["checksum"] != strategy_artifact_checksum(child)):
            raise Error("portfolio member identity or checksum mismatch")
        if child["artifact_id"] not in parents:
            raise Error("portfolio lineage must include every member artifact")
        child_params = child["parameters"]
        for field in ("bar_frequency", "data_source", "market"):
            if child_params.get(field) != params.get(field):
                raise Error(f"portfolio members must share {field}")
        if not isinstance(child_params.get("symbols"), list):
            raise Error("portfolio member requires a symbol list")
        child_symbols = {_symbol_key(s) for s in child_params["symbols"]}
        if not child_symbols or not child_symbols <= {_symbol_key(s) for s in symbols}:
            raise Error("member symbols must be inside the envelope universe")
        covered.update(child_symbols)
        weight = member["risk_weight"]
        if not math.isfinite(weight):
            raise Error("portfolio risk weights must be finite")
        if member["virtual_only"] and weight != 0:
            raise Error("virtual-only members must have zero execution weight")
        gross += weight
    limits = logic["risk_limits"]
    if gross > limits["gross_weight_limit"] + 1e-12:
        raise Error("portfolio gross risk weight exceeds envelope limit")
    if covered != {_symbol_key(s) for s in symbols}:
        raise Error("envelope symbols must be covered by pinned members")


def required_portfolio_history(artifact: Mapping[str, Any]) -> int:
    from .factor_rule import required_history_bars

    if is_portfolio(artifact):
        return max(required_portfolio_history(m["artifact"])
                   for m in artifact["strategy_logic"]["members"])
    logic, params = artifact["strategy_logic"], artifact["parameters"]
    if logic["kind"] == "close_to_close_momentum":
        return int(params[logic["lookback_parameter"]])
    return max(1, required_history_bars(logic, params))


def evaluate_portfolio_envelope(artifact, closes, *, features=None,
                                decision_time=None, symbol=None, holding_bars=None, current_direction=None):
    """Evaluate each member with its single-strategy interpreter, then net views.

    Legacy views without a strength represent a unit directional view. A full
    weight singleton retains its exact original view (including strength=None).
    Member state is evaluated independently from its own history; aggregate
    holdings are deliberately never supplied as member holdings.
    """
    from .strategy_artifact import (
        StrategyArtifactValidationError as Error,
        evaluate_strategy_view,
        validate_strategy_artifact,
    )
    from .factor_rule import assert_governed_feature_inputs, assert_governed_feature_observations

    validate_strategy_artifact(artifact)
    symbols = artifact["parameters"]["symbols"]
    if symbol is None:
        if len(symbols) != 1:
            raise Error("multi-underlying portfolio evaluation requires symbol")
        symbol = symbols[0]
    if _symbol_key(symbol) not in {_symbol_key(s) for s in symbols}:
        raise Error("portfolio symbol outside envelope universe")
    views = {}
    weighted = []
    for member in artifact["strategy_logic"]["members"]:
        child = member["artifact"]
        if _symbol_key(symbol) not in {_symbol_key(s) for s in child["parameters"]["symbols"]}:
            continue
        assert_governed_feature_inputs(child["strategy_logic"])
        assert_governed_feature_observations(child["strategy_logic"], features, decision_time)
        singleton = len(artifact["strategy_logic"]["members"]) == 1 and member["risk_weight"] == 1
        view = evaluate_strategy_view(
            child, closes, features=features, decision_time=decision_time,
            holding_bars=holding_bars if singleton else None,
            current_direction=current_direction if singleton else None,
        )
        views[member["member_id"]] = view
        if not member["virtual_only"]:
            strength = 1.0 if view["strength"] is None else view["strength"]
            sign = {"LONG": 1, "SHORT": -1, "FLAT": 0}[view["direction"]]
            weighted.append(Decimal(sign) * Decimal(str(strength)) * Decimal(str(member["risk_weight"])))
    limit = artifact["strategy_logic"]["risk_limits"]["max_net_strength"]
    net = float(max(-Decimal(str(limit)), min(Decimal(str(limit)), sum(weighted, Decimal(0)))))
    view = {"action": "BUY" if net > 0 else "SELL" if net < 0 else "HOLD",
            "direction": "LONG" if net > 0 else "SHORT" if net < 0 else "FLAT",
            "strength": abs(net), "momentum": net}
    members = artifact["strategy_logic"]["members"]
    if len(members) == 1 and members[0]["risk_weight"] == 1 and limit == 1:
        view = dict(views[members[0]["member_id"]])
    return {"view": view, "member_views": views}


def build_portfolio_envelope(*, artifact_id, strategy_id, version, members,
                             max_net_strength=1.0, gross_weight_limit=1.0,
                             pool_snapshot=None, parent_envelope_id=None):
    """Build a new immutable revision; registration starts at draft/candidate.

    ``pool_snapshot`` (pool version + digest) is pinned inside the checksummed
    logic and ``parent_envelope_id`` joins the lineage, so every revision names
    the pool version it came from and the envelope it replaces.
    """
    from .strategy_artifact import strategy_artifact_checksum, validate_strategy_artifact

    if not members:
        raise ValueError("portfolio requires members")
    template = copy.deepcopy(members[0]["artifact"])
    template.update(artifact_id=artifact_id, strategy_id=strategy_id, version=version)
    template.pop("binding_intent", None)
    pinned = []
    for member in members:
        child = copy.deepcopy(member["artifact"])
        pinned.append({"member_id": member["member_id"], "artifact_id": child["artifact_id"],
                       "version": child["version"], "checksum": strategy_artifact_checksum(child),
                       "risk_weight": member["risk_weight"],
                       "virtual_only": member.get("virtual_only", False), "artifact": child})
    symbols_by_underlying = {}
    for member in pinned:
        for symbol in member["artifact"]["parameters"]["symbols"]:
            symbols_by_underlying.setdefault(_symbol_key(symbol), symbol)
    template["parameters"]["symbols"] = sorted(symbols_by_underlying.values())
    template["strategy_logic"] = {"kind": KIND, "combination_rule": "clipped_weighted_sum_v1",
                                  "members": pinned, "risk_limits": {
                                      "max_net_strength": max_net_strength,
                                      "gross_weight_limit": gross_weight_limit}}
    parents = {m["artifact_id"] for m in pinned}
    if parent_envelope_id:
        parents.add(parent_envelope_id)
    if pool_snapshot is not None:
        # Scalar immutable parameters are inside the checksum and need no
        # schema extension (the strategy_logic schema forbids extra fields).
        template["parameters"]["pool_version"] = int(pool_snapshot["pool_version"])
        template["parameters"]["pool_digest"] = str(pool_snapshot["pool_digest"])
    template["lineage"] = {"parent_registry_ids": sorted(parents)}
    template["mutation_surface"] = {"controls": [], "immutable_parameters": sorted(template["parameters"])}
    validate_strategy_artifact(template)
    return template
