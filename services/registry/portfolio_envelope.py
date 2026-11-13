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


def is_screened(artifact):
    return "screen" in artifact.get("strategy_logic", {})


def singleton_passthrough(artifact):
    """Trials do not change the one execution member's legacy decision path."""
    members = artifact["strategy_logic"]["members"]
    active = [m for m in members if not m["virtual_only"] and m["risk_weight"] > 0]
    return (len(active) == 1 and active[0]["risk_weight"] == 1
            and not is_screened(active[0]["artifact"])
            and artifact["strategy_logic"]["risk_limits"]["max_net_strength"] == 1)


def uses_target_positions(artifact):
    return artifact.get("expression_policy", {}).get("kind") == "target_position_v1"


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
    if not members and (not params.get("pool_version") or not params.get("pool_digest")
                        or not artifact["lineage"].get("parent_registry_ids")):
        raise Error("empty portfolio requires pinned pool provenance and prior lineage")
    if artifact["mutation_surface"]["controls"]:
        raise Error("portfolio envelope is immutable; publish a new version to change it")
    if set(artifact["mutation_surface"]["immutable_parameters"]) != set(params):
        raise Error("all envelope parameters must be immutable")
    symbols = params.get("symbols")
    if not isinstance(symbols, list) or (members and not symbols and not any(is_screened(m["artifact"]) for m in members)):
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
        child_symbols = set() if is_screened(child) else {_symbol_key(s) for s in child_params["symbols"]}
        if (not child_symbols and not is_screened(child)) or not child_symbols <= {_symbol_key(s) for s in symbols}:
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
    if members and covered != {_symbol_key(s) for s in symbols}:
        raise Error("envelope symbols must be covered by pinned members")


def required_portfolio_history(artifact: Mapping[str, Any]) -> int:
    from .factor_rule import required_history_bars

    if is_portfolio(artifact):
        return max((required_portfolio_history(m["artifact"])
                    for m in artifact["strategy_logic"]["members"]), default=1)
    logic, params = artifact["strategy_logic"], artifact["parameters"]
    if logic["kind"] == "close_to_close_momentum":
        return int(params[logic["lookback_parameter"]])
    return max(1, required_history_bars(logic, params))


def evaluate_portfolio_envelope(artifact, closes, *, features=None,
                                decision_time=None, symbol=None, holding_bars=None, current_direction=None,
                                member_views=None):
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
    if member_views is None and _symbol_key(symbol) not in {_symbol_key(s) for s in symbols}:
        raise Error("portfolio symbol outside envelope universe")
    views = {}
    weighted = []
    for member in artifact["strategy_logic"]["members"]:
        child = member["artifact"]
        if member_views is None and is_screened(child) and member["risk_weight"] == 0:
            # Direct single-underlying interpreters need no zero-weight screen
            # to reproduce execution. The bar entry point evaluates its full
            # governed market and records virtual views separately.
            continue
        if member_views is not None:
            if member["member_id"] not in member_views:
                continue
            view = dict(member_views[member["member_id"]])
        elif _symbol_key(symbol) not in {_symbol_key(s) for s in child["parameters"]["symbols"]}:
            continue
        else:
            view = None
        if view is None:
            assert_governed_feature_inputs(child["strategy_logic"])
            assert_governed_feature_observations(child["strategy_logic"], features, decision_time)
            singleton = singleton_passthrough(artifact) and not member["virtual_only"]
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
    if singleton_passthrough(artifact):
        active = next(m for m in members if not m["virtual_only"] and m["risk_weight"] > 0)
        if active["member_id"] in views:
            view = dict(views[active["member_id"]])
    return {"view": view, "member_views": views}


def build_portfolio_envelope(*, artifact_id, strategy_id, version, members,
                             max_net_strength=1.0, gross_weight_limit=1.0,
                             pool_snapshot=None, parent_envelope_id=None, empty_template=None):
    """Build a new immutable revision; registration starts at draft/candidate.

    ``pool_snapshot`` (pool version + digest) is pinned inside the checksummed
    logic and ``parent_envelope_id`` joins the lineage, so every revision names
    the pool version it came from and the envelope it replaces.
    """
    from .strategy_artifact import strategy_artifact_checksum, validate_strategy_artifact

    if not members and (not empty_template or not pool_snapshot or not parent_envelope_id):
        raise ValueError("empty portfolio requires a prior envelope and pinned pool snapshot")
    execution = [m for m in members if not m.get("virtual_only", False) and m["risk_weight"] > 0]
    passthrough = (len(execution) == 1 and execution[0]["risk_weight"] == 1
                   and not is_screened(execution[0]["artifact"]) and max_net_strength == 1)
    source = execution[0]["artifact"] if passthrough else members[0]["artifact"] if members else empty_template
    template = copy.deepcopy(source)
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
        for symbol in ([] if is_screened(member["artifact"]) else member["artifact"]["parameters"]["symbols"]):
            symbols_by_underlying.setdefault(_symbol_key(symbol), symbol)
    if members:
        template["parameters"]["symbols"] = sorted(symbols_by_underlying.values())
    template["strategy_logic"] = {"kind": KIND, "combination_rule": "clipped_weighted_sum_v1",
                                  "members": pinned, "risk_limits": {
                                      "max_net_strength": max_net_strength,
                                      "gross_weight_limit": gross_weight_limit}}
    if not singleton_passthrough(template):
        from services.execution.lean_runtime.expression_policy import DEFAULT_PARAMETERS
        template["expression_policy"] = {
            "version": "1.0", "kind": "target_position_v1",
            "parameters": {**DEFAULT_PARAMETERS,
                           **template.get("expression_policy", {}).get("parameters", {}),
                           "close_before_reverse": True, "rebalance_band_fraction": 0.2},
        }
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


def _target_view(view):
    """An exit order's side is not its post-decision position direction."""
    direction = view.get("target_direction", view["direction"])
    if view.get("exit_reason"):
        direction = "FLAT"
    return {**view, "direction": direction,
            "action": {"LONG": "BUY", "SHORT": "SELL", "FLAT": "HOLD"}[direction],
            "strength": 0.0 if direction == "FLAT" else view["strength"]}


def screened_member_history(artifact, market, decision_time):
    """Rebuild only this member's virtual holdings from governed dated history.

    No aggregate position or mutable process cache enters a screen's exit rules.
    The final bar uses exactly the standalone candidate snapshot evaluator.
    """
    from .candidate_pool import _market, evaluate_screened_views
    from .factor_rule import _parse_timestamp_utc

    _, symbols = _market(market)
    cutoff = _parse_timestamp_utc(decision_time, is_decision_time=True)
    days = sorted({str(row["event_time"])[:10] for payload in symbols.values()
                   for row in payload["bars"] if str(row["event_time"])[:10] <= cutoff.date().isoformat()})
    final_day = cutoff.date().isoformat()
    days = sorted(set(days) | {final_day})
    positions = {}
    views = {}
    for day in days:
        for position in positions.values():
            if position.get("current_direction") in {"LONG", "SHORT"}:
                position["holding_bars"] += 1
        raw = evaluate_screened_views(
            artifact, market, decision_time if day == final_day else day, positions=positions)
        for symbol, view in raw.items():
            prior = positions.get(symbol, {})
            target = _target_view(view)
            if view["action"] == "HOLD" and target["direction"] != "FLAT":
                target["strength"] = prior.get("strength", 0.0)
            views[symbol] = target
            positions[symbol] = {
                "current_direction": target["direction"],
                "holding_bars": view.get("holding_bars", 0),
                "strength": target["strength"],
            }
    return views


def evaluate_portfolio_bar(artifact, snapshots, *, market=None, held_symbols=()):
    """Evaluate all member views before allocation; daily screen picks stay unpinned."""
    from .candidate_pool import canonical_symbol, _market
    from .factor_rule import (evaluate_factor_rule_view, resolve_point_in_time_inputs,
                              evaluate_factor_expression, _eval_node_series)
    from .strategy_artifact import evaluate_strategy_view, validate_strategy_artifact

    validate_strategy_artifact(artifact)
    keyed = {canonical_symbol(s): dict(row) for s, row in snapshots.items()}
    if len(keyed) != len(snapshots) or not keyed:
        raise ValueError("portfolio market inputs missing or duplicate aliases")
    stamps = {row.get("event_time") for row in keyed.values()}
    if len(stamps) != 1 or not next(iter(stamps)):
        raise ValueError("portfolio requires one common decision bar")
    at = next(iter(stamps))
    evaluated = {}
    required = {canonical_symbol(s) for s in artifact["parameters"]["symbols"]}
    required.update(canonical_symbol(s) for s in held_symbols)
    digests, pool_snapshots = {}, {}
    for member in artifact["strategy_logic"]["members"]:
        child, mid = member["artifact"], member["member_id"]
        if is_screened(child):
            _, market_symbols = _market(market)
            if not set(market_symbols) <= set(keyed):
                raise ValueError("screen market_inputs must cover the complete governed market")
            views = screened_member_history(child, market, at)
            snapshot = next(iter(views.values()))["candidate_pool_snapshot"]
            digests[mid], pool_snapshots[mid] = snapshot["digest"], snapshot
            if snapshot.get("data_status") == "needs_data":
                raise ValueError("member_candidate_pool_needs_data:" + mid)
            required.update(snapshot["members"])
            required.update(s for s, v in views.items()
                            if v["direction"] != "FLAT" or v.get("exit_reason"))
            evaluated[mid] = views
        else:
            views = {}
            for symbol in child["parameters"]["symbols"]:
                symbol = canonical_symbol(symbol)
                if symbol not in keyed:
                    raise ValueError("portfolio snapshot missing for " + symbol)
                row = keyed[symbol]
                if singleton_passthrough(artifact) and not member["virtual_only"] and member["risk_weight"] == 1:
                    # A screened virtual trial must not change the active
                    # singleton's legacy view/exit interpretation.
                    view = evaluate_strategy_view(child, row["closes"],
                        features=row.get("features"), decision_time=at, symbol=symbol,
                        current_direction=row.get("current_direction"),
                        holding_bars=row.get("holding_bars"))
                elif child["strategy_logic"]["kind"] == "factor_rule_v1":
                    view = evaluate_factor_rule_view(
                        child, row["closes"], features=row.get("features"), decision_time=at,
                        include_position_state=True)
                    # The interpreter reconstructs this member's own position
                    # state across its history, never the persona's holdings.
                    if view["action"] == "HOLD" and view.get("target_direction") in {"LONG", "SHORT"}:
                        # HOLD means retain this member's state, not flatten it.
                        # Read its declared strength expression at the same PIT cutoff.
                        logic, params = child["strategy_logic"], child["parameters"]
                        env = resolve_point_in_time_inputs(logic, row["closes"],
                            features=row.get("features"), decision_time=at,
                            bar_frequency=params.get("bar_frequency"))
                        signal, _ = _eval_node_series(logic["signal"], env, params)
                        strength = evaluate_factor_expression(logic["strength"],
                            {**env, "signal": signal}, params)
                        view["strength"] = max(0.0, min(1.0, strength))
                    view = _target_view(view)
                else:
                    view = evaluate_strategy_view(child, row["closes"],
                        features=row.get("features"), decision_time=at, symbol=symbol)
                views[symbol] = view
            evaluated[mid] = views
    if not required <= set(keyed):
        raise ValueError("portfolio held/picked snapshot missing: " + ",".join(sorted(required - set(keyed))))
    results = {}
    for symbol in sorted(required):
        row = keyed[symbol]
        if not row.get("source_ref") or not row.get("closes"):
            raise ValueError("portfolio market input missing mark/source: " + symbol)
        views = {mid: values[symbol] for mid, values in evaluated.items() if symbol in values}
        result = evaluate_portfolio_envelope(artifact, row["closes"], symbol=symbol,
            decision_time=at, features=row.get("features"), member_views=views)
        results[symbol] = {**result, "member_candidate_pool_digests": digests,
                           "member_candidate_pool_snapshots": pool_snapshots}
    return results
