"""Persona demand driven AlphaTemplate compilation and §10 admission.

Only registered time-series templates are considered. Market observations and
the mandate universe come from owners; this module never fills missing inputs.
Passing artifacts remain paper research candidates, not approved pool members.
"""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

from services.execution.shioaji_adapter import normalize_taiwan_symbol
from services.registry.alpha_template import alpha_template_checksum, validate_alpha_template
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
                  "data_source": str(dataset["dataset_id"]), "order_quantity": 10, "quantity_type": "SHARES"}
    identity = _digest([demand["tenant_id"], demand["demand_id"], source_ref, template,
                        symbols, dataset, parameters, signal, "positive_signal_long_v1"])[:24]
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


class TemplateDemandSearch:
    """One bounded search, using the same durable gate counters as replication."""

    def __init__(self, *, store, gate, context_loader, lock_path: Path,
                 family_budget=FAMILY_TRIAL_BUDGET, run_budget=RUN_BUDGET):
        if not 0 < family_budget <= FAMILY_TRIAL_BUDGET or not 0 < run_budget <= RUN_BUDGET:
            raise ValueError("invalid_template_search_budget")
        self.store, self.gate, self.context_loader = store, gate, context_loader
        self.lock_path = Path(lock_path)
        self.family_budget, self.run_budget = family_budget, run_budget

    def run(self, demand):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            # Refresh after acquiring the search lock: concurrent persona cycles
            # must see the prior receipt before they can run the same candidate.
            demand = next(r for r in self.store.list(demand["tenant_id"], demand["persona_id"])
                          if r["demand_id"] == demand["demand_id"])
            if demand["status"] != "open":
                return demand
            return self._run(demand)

    def _run(self, demand):
        from services.research.alpha_replication.admission import evaluate_template_candidate

        timestamp = self.store._now().isoformat()
        previous = ((demand.get("discovery") or {}).get("template_search") or {}).get("attempts") or {}
        record = {"run_at": timestamp, "attempts": {}, "excluded": [], "paper_only": True}
        tried = set(demand["tried_families"])
        try:
            context = self.context_loader(demand)
            dataset, universe = context["dataset"], _symbols(context["universe"])
            if not dataset.get("dataset_id") or not dataset.get("source_dataset_refs") or not dataset.get("records"):
                raise ValueError("governed_market_data_missing")
            if dataset.get("data_frequency") not in ("daily", "1d"):
                raise ValueError("template_search_requires_daily_bars")
            entries = context["templates"]
            for view in sorted(entries, key=lambda v: v["entry"]["registry_id"]):
                entry = view["entry"]
                if entry.get("artifact_type") != "alpha_template":
                    continue
                if entry.get("owner_tenant") not in (demand["tenant_id"], "__builtin__"):
                    record["excluded"].append({"registry_id": entry["registry_id"], "reason": "template_tenant_mismatch"})
                    continue
                template = validate_alpha_template(entry["metadata"]["alpha_template"])
                tid = template["template_id"]
                if alpha_template_checksum(template) != entry.get("checksum"):
                    record["excluded"].append({"template_id": tid, "reason": "template_checksum_mismatch"})
                    continue
                if template["scope"] != "time_series" or template["status"] in ("retired", "rejected"):
                    record["excluded"].append({"template_id": tid, "reason": "template_ineligible"})
                    continue
                family = f"template:{template['origin']['library']}:{template['family']}"
                if family in tried or self.gate.get_family_total_trial_count(family) >= self.family_budget:
                    tried.add(family)
                    record["excluded"].append({"template_id": tid, "family_id": family, "reason": "family_tried"})
                    continue
                if not inputs_available(template, universe, dataset):
                    record["excluded"].append({"template_id": tid, "reason": "template_inputs_missing"})
                    continue
                artifact = compile_template(template, universe=universe, demand=demand,
                                            source_ref="registry:" + entry["registry_id"], dataset=dataset)
                aid = artifact["artifact_id"]
                if aid in previous:
                    continue
                if len(record["attempts"]) >= self.run_budget:
                    record["reason"] = "run_budget_exhausted"
                    break
                before = self.gate.get_family_total_trial_count(family)
                response = evaluate_template_candidate(
                    gate=self.gate, artifact=artifact, dataset=dict(dataset), demand=demand,
                    family_id=family, source_ref="registry:" + entry["registry_id"], timestamp=timestamp)
                # Incomplete histories / invalid backtests return before the gate's
                # numeric trial check. They still consumed one compiled-template trial.
                if self.gate.get_family_total_trial_count(family) == before:
                    self.gate.record_family_trial(family)
                total = self.gate.get_family_total_trial_count(family)
                receipt = {"template_id": tid, "family_id": family, "family_trial_count": total,
                           "template_checksum": entry["checksum"],
                           "demand_ref": demand["demand_id"], "persona_id": demand["persona_id"],
                           "tenant_id": demand["tenant_id"], "paper_only": True,
                           "status": response.admission_status.value, "gate": response.to_dict()}
                if response.passed:
                    receipt.update(artifact=artifact, mechanism_status="mechanism_untested",
                                   mechanism_support=None)
                record["attempts"][aid] = receipt
                # Commit each terminal receipt before starting the next backtest.
                if total >= self.family_budget:
                    tried.add(family)
                self.store.record_template_search(demand["tenant_id"], demand["demand_id"], record,
                                                  tried_families=tried)
            record.setdefault("reason", "search_completed")
        except Exception as exc:
            record["reason"] = f"template_search_unavailable:{type(exc).__name__}:{str(exc)[:160]}"
        return self.store.record_template_search(demand["tenant_id"], demand["demand_id"], record,
                                                 tried_families=tried)
