"""Persona demand driven AlphaTemplate compilation and §10 admission.

Only registered time-series templates are considered. Market observations and
the market boundary come from the governed market dataset owner (never from a
persona symbol list); this module never fills missing inputs. Passing artifacts
remain paper research candidates, not approved pool members.

Re-evaluation rule (TEMPLATE-SEARCH-MARKET-BOUNDARY-20261125): the artifact id
digests the dataset snapshot, so it changes every trading day. A template is
evaluated at most once per demand regardless: an attempt already recorded for
its template_id is never repeated because a newer snapshot exists, so the
family trial count rises once per template, not once per template per day.
"""
from __future__ import annotations

import copy
from collections import Counter
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping

from services.execution.shioaji_adapter import normalize_taiwan_symbol
from services.registry.alpha_template import alpha_template_definition_checksum, validate_alpha_template
from services.registry.strategy_artifact import validate_strategy_artifact
from services.source_ingestion.hypothesis_compiler import attach_default_market_screen

FAMILY_TRIAL_BUDGET = 500
RUN_BUDGET = 50
# Hard guards on one template evaluation (TEMPLATE-SEARCH-MARKET-BOUNDARY-20261125):
# 1. Memory guard: peak RSS of evaluate_template_candidate is not driven by symbols x days alone. Measured in a
#    standalone Python 3.12 process on the dev VM over recorded TWSE series (KMID): the interpreter baseline before
#    evaluation is 118 MiB (123,731,968 B), 2x680 peaked at 364 MiB (381,681,664 B, +246 MiB) and 10x520 peaked at
#    316 MiB (+198 MiB). One evaluation therefore adds up to 246 MiB (257,949,696 B) over the process baseline.
#    Before evaluating, we read process RSS (/proc/self/statm) and the container memory limit (/sys/fs/cgroup/memory.max,
#    falling back to RESEARCH_TEMPLATE_SEARCH_MEMORY_LIMIT_BYTES or the 384 MiB compose limit of research-orchestrator-svc)
#    and refuse with market_evaluation_over_memory:<rss>+<increment>:<limit> when RSS + 246 MiB exceeds 90% of the limit
#    (or when RSS or the limit is unreadable).
# 2. Symbol-day guard (second bound): the registry replays the screen once per trading day over the whole market
#    (43.6 s for 2x520, 161.6 s for 5x520, 286.6 s for 10x520 at ~90 MiB). 10x520 = 5200 symbol-days remains the second
#    bound; a boundary above either guard is refused before compiling or spending a family trial, never truncated.
EVALUATION_MEMORY_INCREMENT_BYTES = 246 * 1024 * 1024
DEFAULT_MEMORY_LIMIT_BYTES = 384 * 1024 * 1024
MEMORY_LIMIT_ENV = "RESEARCH_TEMPLATE_SEARCH_MEMORY_LIMIT_BYTES"
CGROUP_MEMORY_MAX_PATH = Path("/sys/fs/cgroup/memory.max")
PROC_STATM_PATH = Path("/proc/self/statm")
MAX_EVALUATION_SYMBOL_DAYS = 5200
_UNSET = object()


def process_rss_bytes(statm_path: Path = PROC_STATM_PATH) -> int | None:
    """Resident set size of this process in bytes from /proc/self/statm, or None when unreadable."""
    try:
        rss_pages = int(Path(statm_path).read_text(encoding="utf-8").split()[1])
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        return rss_pages * page_size if rss_pages > 0 and page_size > 0 else None
    except (OSError, ValueError, IndexError):
        return None


def container_memory_limit_bytes(
    cgroup_path: Path = CGROUP_MEMORY_MAX_PATH,
    configured_limit: Any = DEFAULT_MEMORY_LIMIT_BYTES,
) -> int | None:
    """Container memory limit in bytes from cgroup memory.max, falling back to configured_limit when unbounded/absent.

    Returns None (fail closed) if cgroup memory.max has unreadable/invalid content or if fallback is needed and the
    configured limit is missing or not a positive integer.
    """
    try:
        raw = Path(cgroup_path).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        raw = "max"
    except OSError:
        return None
    if raw != "max":
        try:
            limit = int(raw)
        except ValueError:
            return None
        return limit if limit > 0 else None
    if configured_limit is None or isinstance(configured_limit, bool):
        return None
    if isinstance(configured_limit, str):
        configured_limit = configured_limit.strip()
        if not configured_limit:
            return None
    try:
        limit = int(configured_limit)
    except (TypeError, ValueError):
        return None
    return limit if limit > 0 else None


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _symbols(universe):
    if not isinstance(universe, list) or not universe:
        raise ValueError("market_boundary_symbols_missing")
    return sorted({f"{code}.{'TWO' if venue in ('TPEX', 'OTC') else 'TW'}"
                   for code, venue in map(normalize_taiwan_symbol, universe)})


def market_from_dataset(dataset: Mapping) -> dict:
    """``candidate_pool_market.v1`` of the governed boundary, from the dataset's own stored bars only.

    The screen needs this payload. No survivorship attestation is made here: ``includes_delisted`` and
    ``includes_suspended`` stay false, so the registry marks the pool ``survivorship_limited`` and the gate
    refuses with that reason until the source owner's attestation exists (a correct refusal, never bypassed).
    A bar without a stored ``available_time`` is left out; nothing is invented or imputed.
    """
    symbols: dict[str, dict] = {}
    sessions: set[str] = set()
    for r in dataset.get("records") or []:
        close, available = r.get("close"), r.get("available_time")
        if not isinstance(available, str) or not isinstance(close, (int, float)) or isinstance(close, bool) \
                or not math.isfinite(close) or close <= 0:
            continue
        bar: dict[str, Any] = {"event_time": f"{r['date']}T13:30:00+08:00", "available_time": available, "close": close}
        for field in ("open", "high", "low", "volume", "traded_value"):
            v = r.get(field)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
                bar[field] = v
        if "volume" in bar:
            bar["suspended"] = bar["volume"] <= 0
        sessions.add(r["date"])
        symbols.setdefault(_symbols([r["instrument"]])[0], {"dataset": "tw_price_daily", "bars": []})["bars"].append(bar)
    if not symbols:
        raise ValueError("governed_market_data_missing")
    for payload in symbols.values():
        payload["bars"].sort(key=lambda b: b["event_time"])
    ordered = sorted(sessions)
    return {"schema_version": "candidate_pool_market.v1", "scope": "all_governed_tw",
            "source_refs": list(dataset["source_dataset_refs"]), "trading_sessions": ordered,
            "calendar_coverage_through": ordered[-1], "symbols": symbols,
            "includes_delisted": False, "includes_suspended": False}


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
    # The existing whole_governed_tw_boundary.v1 screen, from the code hypothesis_compiler uses for its
    # default (not a copy). Its constants join the declared controls; the template signal is untouched.
    logic = {"inputs": copy.deepcopy(template["inputs"])}
    screen_params: dict[str, Any] = {}
    attach_default_market_screen(logic, screen_params, controls)
    screen_params.pop("market")
    params.update(screen_params)
    parameters = {**params, "market": "TW", "symbols": symbols, "bar_frequency": "1d",
                  "data_source": str(dataset["dataset_id"]), "order_quantity": 10, "quantity_type": "SHARES"}
    identity = _digest([demand["tenant_id"], demand["demand_id"], source_ref, template,
                        symbols, dataset, parameters, signal, "positive_signal_long_v1", "whole_governed_tw_boundary.v1"])[:24]
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
            "kind": "factor_rule_v1", "inputs": logic["inputs"], "screen": logic["screen"], "signal": signal,
            "entry": {"long_when": {"op": "gt", "args": [{"ref": "signal"}, {"const_param": zero_key}]},
                      "short_when": None},
            "exit": {"max_holding_bars": None,
                     "flat_when": {"op": "not", "args": [{"op": "gt", "args": [{"ref": "signal"}, {"const_param": zero_key}]}]}},
            "strength": {"op": "clip01", "args": [{"op": "abs", "args": [{"ref": "signal"}]}]},
        },
        "parameters": parameters,
        "mutation_surface": {"controls": controls,
                             "immutable_parameters": [k for k in parameters if k not in params]},
        "lineage": {"source_strategy_spec_id": "template-demand-" + identity,
                    "source_run_ids": [demand["demand_id"]],
                    "source_dataset_refs": list(dataset["source_dataset_refs"])},
        "provenance_refs": [source_ref, "research-demand:" + demand["demand_id"]],
    }
    validate_strategy_artifact(artifact)
    return artifact


def usable_symbols(template: Mapping, universe: list[str], dataset: Mapping) -> tuple[list[str], dict[str, str]]:
    """Judge a template by the fields present over the eligible boundary (``universe``).

    A boundary symbol is usable when it has rows and every input field is an observed finite number on
    every row; a symbol that lacks the data stays out with its reason (nothing is imputed, no OHLC/VWAP
    substitute). One incomplete symbol no longer rejects the template. Returns ``(usable, excluded)``;
    ``usable`` is empty when the template's dataset is not served by this authority.
    """
    wanted = _symbols(universe)
    if any(inp["dataset"] != "tw_price_volume" for inp in template["inputs"]):
        return [], {s: "template_dataset_not_served" for s in wanted}  # this bar authority serves price-volume only
    by_symbol: dict[str, list] = {s: [] for s in wanted}
    for row in dataset.get("records") or []:
        symbol = _symbols([row["instrument"]])[0]
        if symbol in by_symbol:
            by_symbol[symbol].append(row)
    usable: list[str] = []
    excluded: dict[str, str] = {}
    for symbol, rows in by_symbol.items():
        if not rows:
            excluded[symbol] = "no_bars"
            continue
        bad = next((inp["field"] for inp in template["inputs"]
                    if any(not isinstance(r.get(inp["field"]), (int, float))
                           or isinstance(r.get(inp["field"]), bool)
                           or not math.isfinite(r[inp["field"]]) for r in rows)), None)
        if bad is not None:
            excluded[symbol] = "field_incomplete:" + bad
        else:
            usable.append(symbol)
    return usable, excluded


def evaluation_cost(usable: list[str], dataset: Mapping) -> tuple[int, int]:
    """(symbols, trading days) of one evaluation over the usable symbols; nothing is sampled or trimmed."""
    wanted = set(usable)
    days = {r["date"] for r in dataset.get("records") or [] if _symbols([r["instrument"]])[0] in wanted}
    return len(wanted), len(days)


def inputs_available(template: Mapping, universe: list[str], dataset: Mapping) -> bool:
    return bool(usable_symbols(template, universe, dataset)[0])


class TemplateDemandSearch:
    """One bounded search, using the same durable gate counters as replication."""

    def __init__(self, *, store, gate, context_loader, lock_path: Path,
                 registrar=None, family_budget=FAMILY_TRIAL_BUDGET, run_budget=RUN_BUDGET,
                 max_symbol_days=MAX_EVALUATION_SYMBOL_DAYS,
                 memory_limit_bytes: Any = _UNSET,
                 memory_increment_bytes: int = EVALUATION_MEMORY_INCREMENT_BYTES,
                 cgroup_memory_max_path: Path = CGROUP_MEMORY_MAX_PATH,
                 statm_path: Path = PROC_STATM_PATH):
        if not 0 < family_budget <= FAMILY_TRIAL_BUDGET or not 0 < run_budget <= RUN_BUDGET:
            raise ValueError("invalid_template_search_budget")
        self.store, self.gate, self.context_loader = store, gate, context_loader
        self.registrar = registrar
        self.lock_path = Path(lock_path)
        self.family_budget, self.run_budget = family_budget, run_budget
        if not 0 < max_symbol_days <= MAX_EVALUATION_SYMBOL_DAYS:
            raise ValueError("invalid_template_search_evaluation_limit")
        self.max_symbol_days = max_symbol_days
        if not isinstance(memory_increment_bytes, int) or isinstance(memory_increment_bytes, bool) \
                or memory_increment_bytes < EVALUATION_MEMORY_INCREMENT_BYTES:
            raise ValueError("invalid_template_search_memory_increment")
        self.memory_increment_bytes = memory_increment_bytes
        if memory_limit_bytes is _UNSET:
            env_limit = os.getenv(MEMORY_LIMIT_ENV)
            self.memory_limit_bytes = env_limit if env_limit is not None else DEFAULT_MEMORY_LIMIT_BYTES
        else:
            self.memory_limit_bytes = memory_limit_bytes
        self.cgroup_memory_max_path = Path(cgroup_memory_max_path)
        self.statm_path = Path(statm_path)

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
        from services.research.candidate_registration import TemplateProofCapturingGate

        timestamp = self.store._now().isoformat()
        previous = ((demand.get("discovery") or {}).get("template_search") or {}).get("attempts") or {}
        record = {"run_at": timestamp, "attempts": {}, "templates_attempted": 0,
                  "excluded": [], "excluded_by_reason": {}, "paper_only": True}
        tried = set(demand["tried_families"])
        capturing_gate = TemplateProofCapturingGate(self.gate)
        new_attempts = 0

        def persist():
            # Demand receipts are cumulative; this count belongs only to this run.
            record["excluded_by_reason"] = dict(sorted(Counter(
                item["reason"] for item in record["excluded"]
            ).items()))
            return self.store.record_template_search(
                demand["tenant_id"], demand["demand_id"], record, tried_families=tried)

        try:
            context = self.context_loader(demand)
            dataset, universe = context["dataset"], _symbols(context["universe"])
            if not dataset.get("dataset_id") or not dataset.get("source_dataset_refs") or not dataset.get("records"):
                raise ValueError("governed_market_data_missing")
            if dataset.get("data_frequency") not in ("daily", "1d"):
                raise ValueError("template_search_requires_daily_bars")
            # A market the dataset owner supplies with its own attestation is used as supplied; otherwise the
            # unattested market of the same bars is built (survivorship_limited at the gate, never bypassed).
            gate_dataset = {**dataset, "candidate_pool_market": dataset.get("candidate_pool_market")
                            or market_from_dataset(dataset)}
            entries = context["templates"]
            if self.registrar is not None:
                for prev_aid, prev_receipt in previous.items():
                    if (isinstance(prev_receipt, Mapping)
                            and prev_receipt.get("status") == "admitted"
                            and (prev_receipt.get("registration") or {}).get("status") != "approved"
                            and isinstance(prev_receipt.get("artifact"), Mapping)):
                        tid = prev_receipt.get("template_id")
                        tpl_entry = next(
                            (v["entry"] for v in entries
                             if isinstance(v.get("entry"), Mapping)
                             and ((v["entry"].get("metadata") or {}).get("alpha_template") or {}).get("template_id") == tid),
                            None)
                        tpl = (validate_alpha_template(tpl_entry["metadata"]["alpha_template"])
                               if tpl_entry is not None
                               else {"template_id": tid, "inputs": prev_receipt["artifact"]["strategy_logic"]["inputs"]})
                        updated = copy.deepcopy(dict(prev_receipt))
                        reg = self.registrar.register_passed_attempt(
                            demand=demand, template=tpl, artifact=updated["artifact"], receipt=updated)
                        updated.update(registration=reg, registry_id=reg["registry_id"],
                                       baseline_ref=reg["approval_decision_id"])
                        record["attempts"][prev_aid] = updated
                        persist()
            for view in sorted(entries, key=lambda v: v["entry"]["registry_id"]):
                entry = view["entry"]
                if entry.get("artifact_type") != "alpha_template":
                    continue
                if entry.get("owner_tenant") not in (demand["tenant_id"], "__builtin__"):
                    record["excluded"].append({"registry_id": entry["registry_id"], "reason": "template_tenant_mismatch"})
                    continue
                template = validate_alpha_template(entry["metadata"]["alpha_template"])
                tid = template["template_id"]
                if alpha_template_definition_checksum(template) != entry.get("checksum"):
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
                if any(isinstance(r, Mapping) and r.get("template_id") == tid for r in previous.values()):
                    continue  # evaluated once per demand: a newer daily snapshot never re-spends a family trial
                usable, dropped = usable_symbols(template, universe, dataset)
                if not usable:
                    record["excluded"].append({"template_id": tid, "reason": "template_inputs_missing"})
                    continue
                if new_attempts >= self.run_budget:
                    record["reason"] = "run_budget_exhausted"
                    break
                rss = process_rss_bytes(self.statm_path)
                mem_limit = container_memory_limit_bytes(self.cgroup_memory_max_path, self.memory_limit_bytes)
                if rss is None or mem_limit is None or (rss + self.memory_increment_bytes) * 10 > mem_limit * 9:
                    # Fail closed before compiling or spending a trial when RSS + increment exceeds 90% of the limit
                    # or when either RSS or the memory limit cannot be read.
                    rss_label = "unreadable" if rss is None else str(rss)
                    limit_label = "unreadable" if mem_limit is None else str(mem_limit)
                    record["reason"] = (
                        f"market_evaluation_over_memory:{rss_label}+{self.memory_increment_bytes}:{limit_label}"
                    )
                    break
                n_symbols, n_days = evaluation_cost(usable, dataset)
                if n_symbols * n_days > self.max_symbol_days:
                    # Second bound: fail closed before compiling or spending a trial; the boundary is never truncated.
                    record["reason"] = f"market_evaluation_over_limit:{n_symbols}x{n_days}:{self.max_symbol_days}"
                    break
                artifact = compile_template(template, universe=usable, demand=demand,
                                            source_ref="registry:" + entry["registry_id"], dataset=dataset)
                aid = artifact["artifact_id"]
                if aid in previous:
                    continue
                new_attempts += 1
                before = self.gate.get_family_total_trial_count(family)
                response = evaluate_template_candidate(
                    gate=capturing_gate, artifact=artifact, dataset=dict(gate_dataset), demand=demand,
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
                           "dataset_id": dataset["dataset_id"], "boundary_symbols": len(usable),
                           "symbols_without_inputs": len(dropped),
                           "status": response.admission_status.value, "gate": response.to_dict()}
                if response.passed:
                    receipt.update(artifact=artifact, mechanism_status="mechanism_untested",
                                   mechanism_support=None)
                record["attempts"][aid] = receipt
                record["templates_attempted"] += 1
                # Commit each terminal receipt before starting registration or the next backtest.
                if total >= self.family_budget:
                    tried.add(family)
                persist()
                if response.passed and self.registrar is not None:
                    reg = self.registrar.register_passed_attempt(
                        demand=demand, template=template, artifact=artifact, receipt=receipt,
                        backtest_evaluation=capturing_gate.last_evaluation)
                    receipt.update(registration=reg, registry_id=reg["registry_id"],
                                   baseline_ref=reg["approval_decision_id"])
                    persist()
            integrity_excluded = sum(item["reason"] in (
                "template_checksum_mismatch", "template_tenant_mismatch"
            ) for item in record["excluded"])
            if not new_attempts and integrity_excluded:
                record["reason"] = (
                    "template_search_unavailable:template_integrity_mismatch:"
                    + str(integrity_excluded)
                )
            record.setdefault("reason", "search_completed")
        except Exception as exc:
            record["reason"] = f"template_search_unavailable:{type(exc).__name__}:{str(exc)[:160]}"
        return persist()
