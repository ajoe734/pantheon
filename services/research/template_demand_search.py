"""Persona demand driven AlphaTemplate compilation and §10 admission.

Only registered time-series templates are considered. Market observations and
the mandate universe come from owners; this module never fills missing inputs.
Passing artifacts remain paper research candidates, not approved pool members.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

from services.execution.shioaji_adapter import normalize_taiwan_symbol
from services.registry.alpha_template import validate_alpha_template
from services.registry.strategy_artifact import validate_strategy_artifact

FAMILY_TRIAL_BUDGET = 500
RUN_BUDGET = 50


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _symbols(universe):
    if not isinstance(universe, list) or not universe:
        raise ValueError("mandate_universe_missing")
    return sorted({f"{code}.{'TWO' if venue in ('TPEX', 'OTC') else 'TW'}"
                   for code, venue in map(normalize_taiwan_symbol, universe)})


def compile_template(template: Mapping, *, universe: list[str], demand: Mapping,
                     source_ref: str, dataset: Mapping) -> dict:
    """Lift literal constants into declared controls; preserve the template signal.

    A fixed positive-signal long / non-positive flat rule supplies the trading
    boundary. No parameter selection observes the out-of-sample window.
    """
    template = validate_alpha_template(template)
    if template["scope"] != "time_series":
        raise ValueError("cross_sectional_template")
    symbols = _symbols(universe)
    params = copy.deepcopy(template["parameters"])
    zero_key = "demand_zero"
    while zero_key in params:
        zero_key += "_"
    params[zero_key] = 0.0

    def lift(node):
        if "const" in node:
            key = f"literal_{len(params)}"
            while key in params:
                key += "_"
            params[key] = node["const"]
            return {"const_param": key}
        return {k: [lift(a) for a in v] if k == "args" else v for k, v in node.items()}

    signal = lift(template["expression"])
    controls = [{"parameter_key": k, "value_type": "integer" if type(v) is int else "number",
                 "current_value": v, "allowed_range": {"min": v, "max": v}, "step": 1}
                for k, v in params.items()]
    parameters = {**params, "market": "TW", "symbols": symbols, "bar_frequency": "1d",
                  "data_source": str(dataset["dataset_id"]), "order_quantity": 1000, "quantity_type": "SHARES"}
    identity = _digest([demand["tenant_id"], demand["demand_id"], source_ref, template,
                        symbols, dataset])[:24]
    artifact = {
        "artifact_schema_version": "1.0", "artifact_id": "template-demand-" + identity,
        "strategy_id": "template-" + _digest([demand["tenant_id"], template["template_id"]])[:20],
        "version": "1.0.0",
        "algorithm_ref": {
            "engine": "lean", "repository": "ajoe734/pantheon",
            "commit": "1cfaf933fbcc119605a455ebea483d54d2521ebb",
            "path": "services/registry/strategy_artifact.py",
            "entrypoint": "services.registry.strategy_artifact:evaluate_strategy_action",
            "logic_interpreter": "services.registry.strategy_artifact:evaluate_strategy_action",
            "signal_interface": "services.execution.lean_runtime.paper_signal_producer:Strategy",
            "signal_schema_version": "1.0",
        },
        "strategy_logic": {
            "kind": "factor_rule_v1", "inputs": copy.deepcopy(template["inputs"]), "signal": signal,
            "entry": {"long_when": {"op": "gt", "args": [{"ref": "signal"}, {"const_param": zero_key}]},
                      "short_when": None},
            "exit": {"max_holding_bars": None,
                     "flat_when": {"op": "not", "args": [{"op": "gt", "args": [{"ref": "signal"}, {"const_param": zero_key}]}]}},
            "strength": {"op": "clip01", "args": [{"op": "abs", "args": [{"ref": "signal"}]}]},
        },
        "parameters": parameters,
        "mutation_surface": {"controls": controls,
                             "immutable_parameters": [k for k in parameters if k not in params]},
        "lineage": {"source_run_ids": [demand["demand_id"]],
                    "source_dataset_refs": list(dataset["source_dataset_refs"])},
        "provenance_refs": [source_ref, "research-demand:" + demand["demand_id"]],
    }
    validate_strategy_artifact(artifact)
    return artifact


def inputs_available(template: Mapping, universe: list[str], dataset: Mapping) -> bool:
    """Require observed finite fields for every mandate symbol, without OHLC/VWAP imputation."""
    wanted = set(_symbols(universe))
    by_symbol = {s: [] for s in wanted}
    for row in dataset.get("records") or []:
        symbol = _symbols([row["instrument"]])[0]
        if symbol in by_symbol:
            by_symbol[symbol].append(row)
    for rows in by_symbol.values():
        if not rows:
            return False
        for inp in template["inputs"]:
            if inp["dataset"] != "tw_price_volume":
                return False  # this governed bar authority supplies only price-volume observations
            if any(not isinstance(r.get(inp["field"]), (int, float))
                   or isinstance(r.get(inp["field"]), bool)
                   or not math.isfinite(r[inp["field"]]) for r in rows):
                return False
    return True
