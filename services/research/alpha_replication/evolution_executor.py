"""Execution of approved ``mutate`` and ``revalidate`` evolution decisions (design §12.2).

Pure orchestration over injected ports so the research service can wire the real
Registry, governed dataset and §10 ReplicationGate while tests inject fakes:

* ``mutate``     – structural children of a ``factor_rule_v1`` parent, each judged
                   on the untouched out-of-sample window by the §10 gate; only
                   passing children are registered (candidate, lineage to parent).
* ``revalidate`` – the existing artifact is re-judged by the same gate on the
                   real backtest; nothing is registered.

Both end in an explicit terminal outcome so the run can be finished with a receipt.
"""

from __future__ import annotations

import copy
import os
from typing import Any, Callable, Mapping

from services.research.alpha_replication import evolution_search as search

MUTATE_OUTCOMES = ("candidate_created", "no_candidate")
REVALIDATE_OUTCOMES = ("revalidation_passed", "revalidation_failed")


def post_admission_dataset(dataset, joined_at, *, minimum_days=20):
    """Revalidation cannot reuse the admission sample, including its admission day."""
    from datetime import date
    joined = date.fromisoformat(joined_at[:10])
    if not dataset.get("source_dataset_refs"):
        raise ExecutorRefusal("evaluation_rejected", "post_admission_dataset_source_missing")
    records = [r for r in dataset.get("records", []) if date.fromisoformat(r["date"][:10]) > joined]
    if len({r["date"][:10] for r in records}) < minimum_days:
        raise ExecutorRefusal("evaluation_rejected", "insufficient_post_admission_trading_days")
    return {**dataset, "records": records}


def revalidate_member(*, artifact, dataset, member, baseline):
    """Fresh-data monitoring of an already admitted member on its gate return basis.

    This is not a new candidate admission: the original approved OOS proof stays
    mandatory. Requiring another two years inside a 20-day monitoring window
    would prevent periodic revalidation from ever evaluating its intended data.
    """
    from services.evolution.auto_consumer import DecayPolicy
    from services.execution.lean_runtime.member_virtual_book import gate_basis_metrics
    from services.registry.strategy_artifact import evaluate_strategy_view
    from services.registry.portfolio_envelope import required_portfolio_history
    from services.research.vectorbt.adapter.vectorbt_adapter import GovernedVectorbtInputAdapter, _bar_features_for_index
    from services.execution.shioaji_adapter import normalize_taiwan_symbol

    if baseline.get("status") != "approved":
        raise ExecutorRefusal("evaluation_rejected", "no_approved_member_baseline")
    fresh = post_admission_dataset(dataset, member["joined_at"])
    prepared = GovernedVectorbtInputAdapter().prepare(fresh)
    import math
    if prepared.data_frequency not in {"daily", "1d"} or any(
        not all(math.isfinite(value) and value > 0 for value in bar[:4])
        or not math.isfinite(bar[4]) or bar[4] < 0
        for bars in prepared.ohlcv_by_instrument.values() for bar in bars
    ):
        raise ExecutorRefusal("evaluation_rejected", "post_admission_observations_invalid")
    wanted = {normalize_taiwan_symbol(s) for s in artifact["parameters"]["symbols"]}
    instruments = [s for s in prepared.instruments if normalize_taiwan_symbol(s) in wanted]
    if {normalize_taiwan_symbol(s) for s in instruments} != wanted:
        raise ExecutorRefusal("evaluation_rejected", "post_admission_symbols_missing")
    calendars = [tuple(prepared.dates_by_instrument[s]) for s in instruments]
    if not calendars or len(calendars[0]) < 20:
        raise ExecutorRefusal("evaluation_rejected", "insufficient_post_admission_trading_days")
    if len(set(calendars)) != 1:
        raise ExecutorRefusal("evaluation_rejected", "post_admission_dates_unaligned")
    books = []
    warmup = required_portfolio_history(artifact)
    for symbol in instruments:
        prices = [r[3] for r in prepared.ohlcv_by_instrument[symbol]]
        observations = []
        for index, day in enumerate(prepared.dates_by_instrument[symbol]):
            if index + 1 < warmup:
                continue
            features, at = _bar_features_for_index(prepared, symbol, index)
            view = evaluate_strategy_view(artifact, prices[:index + 1], features=features, decision_time=at, symbol=symbol)
            # decided from this close, filled at the next session's open (shared convention)
            observations.append({"price": prices[index], "open": prepared.ohlcv_by_instrument[symbol][index][0],
                "direction": {"LONG": 1, "SHORT": -1, "FLAT": 0}[view["direction"]]})
        if len(observations) < 2:
            raise ExecutorRefusal("evaluation_rejected", "post_admission_warmup_incomplete")
        books.append({"symbol": symbol, "observations": observations})
    metric, missing = gate_basis_metrics(books, baseline["order_quantity"])
    if missing:
        raise ExecutorRefusal("evaluation_rejected", missing)
    if not all(math.isfinite(metric[key]) for key in ("sharpe_ratio", "max_drawdown")):
        raise ExecutorRefusal("evaluation_rejected", "post_admission_metrics_nonfinite")
    policy = DecayPolicy()
    passed = (metric["sharpe_ratio"] >= baseline["oos_sharpe"] * policy.min_sharpe_ratio_of_baseline
              and metric["max_drawdown"] <= baseline["oos_max_drawdown"] * policy.max_drawdown_multiple)
    return {"outcome": "revalidation_passed" if passed else "revalidation_failed",
            "reason": "post_admission_member_baseline_comparison", "verdict": {
                "passed": passed, "baseline_key": baseline["baseline_key"], "metrics": metric,
                "start_date": calendars[0][0], "end_date": calendars[0][-1],
                "source_dataset_refs": fresh["source_dataset_refs"]}}


class ExecutorRefusal(Exception):
    """A stated, non-transport refusal: the run ends completed with this outcome."""

    def __init__(self, outcome: str, reason: str) -> None:
        super().__init__(f"{outcome}: {reason}")
        self.outcome = outcome
        self.reason = reason


class RequalifyDeferred(Exception):
    """Could not evaluate a legacy member (missing/short/synthetic dataset or owner read failure).

    Not a gate verdict failure: the member stays in the pool with ``operator_decision_required``
    and the run is retried on the next cadence.
    """

    def __init__(self, reason: str, detail: Mapping[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = dict(detail or {})


def requalify_legacy_member(
    *,
    artifact: Mapping[str, Any],
    dataset: Mapping[str, Any] | None,
    member: Mapping[str, Any],
    baseline: Mapping[str, Any],
    decision_id: str,
    tenant_id: str,
    gate: Any,
    entry: Mapping[str, Any] | None = None,
    spec: Mapping[str, Any] | None = None,
    existing_versions: Any = (),
    record_backtest: Callable[[dict[str, Any]], Any] | None = None,
    run_protocol: Callable[..., dict[str, Any]] | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Re-qualify a legacy close-fill member on governed 2-year PIT exchange data under next-open fills."""
    import hashlib
    from datetime import datetime, timezone
    from services.execution.lean_runtime.member_virtual_book import FILL_CONVENTION
    from services.registry.strategy_artifact import strategy_artifact_checksum, validate_strategy_artifact
    from services.research.rebaseline_next_open import is_synthetic_or_fixture_dataset
    from services.research.replication.gate_schema import ReplicationRequest
    from services.research.vectorbt.adapter.vectorbt_adapter import (
        INSUFFICIENT_HISTORY_STATUS,
        BacktestConfig,
        VectorbtWorkflowError,
        run_replication_backtest_protocol,
    )

    if baseline.get("status") == "approved" or baseline.get("reason") != "baseline_computed_under_decision_close_fill":
        raise ExecutorRefusal("evaluation_rejected", "member_baseline_is_not_legacy_close_fill")

    dataset_refusal = is_synthetic_or_fixture_dataset(dataset)
    if dataset_refusal is not None:
        raise RequalifyDeferred(
            dataset_refusal,
            detail={
                "dataset_id": (dataset or {}).get("dataset_id") if isinstance(dataset, Mapping) else None,
                "source_dataset_refs": list((dataset or {}).get("source_dataset_refs") or [])
                if isinstance(dataset, Mapping)
                else [],
            },
        )
    assert isinstance(dataset, Mapping)

    legacy_registry_id = str(member["artifact_ref"])
    new_version = search.allocate_child_version(artifact.get("version") or "1.0.0", existing_versions or ())
    digest = hashlib.sha256(
        f"{decision_id}:{legacy_registry_id}:{FILL_CONVENTION}:{new_version}".encode("utf-8")
    ).hexdigest()[:12]
    new_artifact_id = f"{legacy_registry_id}-requalify-{digest}"[:120]
    run_id = f"bt-{decision_id}-{new_artifact_id}"[:120]

    requalified_artifact = copy.deepcopy(dict(artifact))
    requalified_artifact["artifact_id"] = new_artifact_id
    requalified_artifact["version"] = new_version
    old_lineage = dict(requalified_artifact.get("lineage") or {})
    source_spec_id = str(
        old_lineage.get("source_strategy_spec_id")
        or (spec or {}).get("strategy_spec_id")
        or legacy_registry_id
    )
    prior_runs = [
        str(r)
        for r in (old_lineage.get("source_run_ids") or [])
        if str(r).strip() and str(r) not in {decision_id, run_id}
    ]
    requalified_artifact["lineage"] = {
        **old_lineage,
        "parent_registry_ids": [legacy_registry_id],
        "source_strategy_spec_id": source_spec_id,
        "source_run_ids": [decision_id, run_id, *prior_runs],
        "source_dataset_refs": list(dataset.get("source_dataset_refs") or []),
    }
    validate_strategy_artifact(requalified_artifact)
    checksum = strategy_artifact_checksum(requalified_artifact)

    protocol_fn = run_protocol or run_replication_backtest_protocol
    bound_dataset = {**dict(dataset), "strategy_id": str(requalified_artifact["strategy_id"])}
    prev_backend = os.environ.get("PANTHEON_VECTORBT_BACKEND")
    os.environ["PANTHEON_VECTORBT_BACKEND"] = "real"
    try:
        try:
            evaluated = protocol_fn(
                strategy_artifact=requalified_artifact,
                dataset=bound_dataset,
                run_id=run_id,
                config=BacktestConfig(
                    strategy_artifact=dict(requalified_artifact),
                    fill_convention=FILL_CONVENTION,
                ),
            )
        except (VectorbtWorkflowError, ValueError) as exc:
            raise RequalifyDeferred("backtest_dataset_invalid", detail={"error": str(exc)}) from None
    finally:
        if prev_backend is None:
            os.environ.pop("PANTHEON_VECTORBT_BACKEND", None)
        else:
            os.environ["PANTHEON_VECTORBT_BACKEND"] = prev_backend

    history_check = evaluated.get("history_check") if isinstance(evaluated.get("history_check"), Mapping) else {}
    if (
        evaluated.get("status") == INSUFFICIENT_HISTORY_STATUS
        or (history_check and not bool(history_check.get("sufficient", False)))
    ):
        raise RequalifyDeferred(
            "insufficient_point_in_time_history",
            detail={
                "history_check": dict(history_check),
                "dataset_id": evaluated.get("dataset_id"),
                "source_dataset_refs": list(evaluated.get("source_dataset_refs") or []),
            },
        )
    if evaluated.get("status") != "completed":
        raise RequalifyDeferred(
            "backtest_evaluation_unavailable",
            detail={"status": evaluated.get("status")},
        )

    if record_backtest is not None:
        record_backtest(evaluated)
    elif getattr(gate, "backtest_run_store", None) is not None:
        gate.backtest_run_store.record(dict(evaluated))

    ts = timestamp or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    raw_spec = dict(spec or {})
    non_schema = (
        "lifecycle_state",
        "backtest_evaluation",
        "backtest_dataset",
        "strategy_artifact",
        "skip_backtest",
        "history_trading_days",
        "hypothesis_card",
        "hypothesis_predictions",
        "mechanism_inputs",
    )
    evaluation_spec = {k: v for k, v in raw_spec.items() if k not in non_schema}
    evaluation_spec.setdefault("name", str(requalified_artifact.get("strategy_id") or new_artifact_id))
    evaluation_spec.setdefault("description", "Legacy pool member next-open re-qualification")
    evaluation_spec.setdefault("signals", requalified_artifact.get("strategy_logic") or ["momentum"])
    evaluation_spec.setdefault("parameters", dict(requalified_artifact.get("parameters") or {}))
    evaluation_spec.setdefault(
        "governance",
        {"approval_required": True, "policy_id": "oc-001-research-normalization"},
    )

    family_id = str(requalified_artifact.get("strategy_id") or new_artifact_id)
    request = ReplicationRequest(
        candidate_id=f"{decision_id}:{new_artifact_id}"[:120],
        source_task_id=decision_id,
        research_handoff={
            "source_metadata": {
                "api_endpoint": f"evolution://decisions/{decision_id}",
                "retrieved_at": ts,
                "governance_context": f"Approved EvolutionDecision {decision_id} legacy baseline re-qualification",
            },
            "normalized_findings": {
                "strategy_spec": {"strategy_id": family_id, "name": evaluation_spec["name"]},
                "replication_notes": "Legacy close-fill pool member re-qualified on governed 2-year PIT exchange data under next-open fills.",
                "evaluation_hypotheses": "H1: strategy achieves positive deflated out-of-sample Sharpe after statutory Taiwan costs under next-open fills.",
            },
            "family_id": family_id,
            "strategy_artifact": requalified_artifact,
            "backtest_dataset": bound_dataset,
            **{
                key: dict(raw_spec[key])
                for key in ("hypothesis_card", "mechanism_inputs")
                if isinstance(raw_spec.get(key), Mapping)
            },
            **(
                {"hypothesis_predictions": list(raw_spec["hypothesis_predictions"])}
                if isinstance(raw_spec.get("hypothesis_predictions"), list)
                else {}
            ),
        },
        proposed_strategy_spec=evaluation_spec,
        backtest_evaluation=evaluated,
        metadata={
            "tenant_id": tenant_id,
            "persona_id": member.get("persona_id"),
            "decision_id": decision_id,
            "dataset_version_id": evaluated.get("dataset_id"),
            "strategy_artifact_checksum": checksum,
        },
        timestamp=ts,
    )
    response = gate.evaluate_candidate(request)
    bt_result = next(
        (
            r
            for r in (getattr(response, "results", []) or [])
            if getattr(r, "criterion_id", None) == "real_backtest_validation"
        ),
        None,
    )
    details = getattr(bt_result, "details", None) or {}
    per_symbol = ((history_check.get("details") or {}).get("per_symbol") or {}) if isinstance(history_check, Mapping) else {}
    start_dates = [
        str(v["start_date"])
        for v in per_symbol.values()
        if isinstance(v, Mapping) and v.get("start_date")
    ]
    end_dates = [
        str(v["end_date"])
        for v in per_symbol.values()
        if isinstance(v, Mapping) and v.get("end_date")
    ]
    gate_run_id = str(getattr(response, "gate_run_id", "") or "")
    backtest_run_id = str(evaluated.get("backtest_run_id") or run_id)
    verdict = {
        "passed": bool(response.passed),
        "gate_run_id": gate_run_id,
        "backtest_run_id": backtest_run_id,
        "verdict_ref": f"gate:{gate_run_id}:{backtest_run_id}",
        "fill_convention": evaluated.get("fill_convention"),
        "legacy_registry_id": legacy_registry_id,
        "legacy_checksum": member.get("checksum"),
        "requalified_artifact_id": new_artifact_id if response.passed else None,
        "requalified_checksum": checksum if response.passed else None,
        "dataset_id": evaluated.get("dataset_id"),
        "source_dataset_refs": list(evaluated.get("source_dataset_refs") or []),
        "window": {
            "start_date": min(start_dates) if start_dates else None,
            "end_date": max(end_dates) if end_dates else None,
            "calendar_days": history_check.get("calendar_days"),
            "trading_days": history_check.get("trading_days"),
            "in_sample_ratio": (evaluated.get("protocol") or {}).get("in_sample_ratio"),
            "out_of_sample_ratio": (evaluated.get("protocol") or {}).get("out_of_sample_ratio"),
            "walk_forward_folds": (evaluated.get("protocol") or {}).get("walk_forward_folds"),
        },
        "metrics": {
            "in_sample_sharpe_ratio": details.get("in_sample_sharpe_ratio"),
            "out_of_sample_sharpe_ratio": details.get("out_of_sample_sharpe_ratio"),
            "out_of_sample_max_drawdown": details.get("out_of_sample_max_drawdown"),
            "out_of_sample_total_return": details.get("out_of_sample_total_return"),
            "out_of_sample_round_trips": details.get("out_of_sample_round_trips"),
            "deflated_sharpe_threshold": details.get("deflated_sharpe_threshold"),
            "buy_and_hold_sharpe_ratio": details.get("buy_and_hold_sharpe_ratio"),
            "tw_0050_sharpe_ratio": details.get("tw_0050_sharpe_ratio"),
            "walk_forward_fold_sharpes": list(details.get("walk_forward_fold_sharpes") or []),
        },
        "checks": dict(details.get("checks") or {}),
        "failed_checks": list(details.get("failed_checks") or []),
        "failed_criteria": [
            getattr(r, "criterion_id", None)
            for r in (getattr(response, "results", []) or [])
            if not getattr(r, "passed", True)
        ],
    }
    proof_dict = response.to_dict() if callable(getattr(response, "to_dict", None)) else {}
    return {
        "outcome": "revalidation_passed" if response.passed else "revalidation_failed",
        "reason": "replaced_by_requalified_entry" if response.passed else "requalification_failed_next_open_fill",
        "verdict": verdict,
        "artifact": requalified_artifact if response.passed else None,
        "requalified_artifact": requalified_artifact if response.passed else None,
        "gate_proof": proof_dict,
        "gate_response": proof_dict,
        "backtest_evaluation": evaluated,
    }


def compose_member_case_file(parent, *, params, tenant_id, decision_id, parent_registry_ref):
    """Read owners as the configured research service, never as a persona.

    The caller persists the result before checking availability. Credentials and
    roles come from deployment configuration, not decision/model input.
    """
    from services.knowledge.case_file import Caller, CaseFileError, Owners, Subject, compose_case_file

    member = params["pool_member"]
    symbols = (parent.get("parameters") or {}).get("symbols")
    if not isinstance(symbols, list) or not symbols or any(not isinstance(s, str) or not s.strip() for s in symbols):
        raise ExecutorRefusal("no_candidate", "case_file_underlyings_missing")
    subject = Subject(
        decision_type="evolution_proposal", persona_id=str(member.get("persona_id") or ""),
        tenant_id=tenant_id, strategy_family=str(parent.get("strategy_id") or ""),
        underlyings=tuple(symbols), member_artifact_refs=(parent_registry_ref,),
        instrument_class=str((parent.get("parameters") or {}).get("instrument_class") or ""),
        persona_capital_binding_id=str(member.get("persona_capital_binding_id") or ""),
    )
    caller = Caller(
        actor_id=os.getenv("RESEARCH_CASE_FILE_ACTOR_ID", ""),
        actor_roles=tuple(r.strip() for r in os.getenv("RESEARCH_CASE_FILE_ACTOR_ROLES", "").split(",") if r.strip()),
        session_id=decision_id,
    )
    try:
        return compose_case_file(subject, caller, Owners.from_env())
    except CaseFileError as exc:
        raise ExecutorRefusal("no_candidate", f"case_file_{exc.reason}") from None


def validate_case_file_availability(case_file):
    """An unavailable read cannot be declared an absence of applicable lessons."""
    for name in ("facts", "similar_history", "lessons", "evidence"):
        group = case_file["groups"][name]
        for missing in group["unavailable"]:
            # Both omissions are explicit optional-source boundaries of M3.
            if missing.get("expected") or (
                missing["owner"] == "telemetry"
                and missing["reason"] == "persona_capital_binding_id_not_supplied"
            ):
                continue
            raise ExecutorRefusal("no_candidate", f"case_file_unavailable:{name}:{missing['owner']}:{missing['reason']}")


def child_lesson_context(case_file, declaration):
    """A child proposal must explicitly cite this case file or explain no reuse.

    This context is a research record/registry metadata sidecar. It is never
    inserted into the executable strategy artifact or passed to execution.
    """
    validate_case_file_availability(case_file)
    if declaration is None:
        raise ExecutorRefusal("no_candidate", "addresses_lessons_missing")
    if not isinstance(declaration, Mapping):
        raise ExecutorRefusal("no_candidate", "addresses_lessons_invalid")
    ids = declaration.get("lesson_ids")
    if declaration.get("none_applicable") is True:
        if ids or not isinstance(declaration.get("reason"), str) or not declaration["reason"].strip():
            raise ExecutorRefusal("no_candidate", "addresses_lessons_none_applicable_requires_reason")
        normalized = {"none_applicable": True, "reason": declaration["reason"].strip()}
    else:
        if (not isinstance(ids, list) or not ids
                or any(not isinstance(i, str) or not i.strip() for i in ids)
                or len(ids) != len(set(ids))):
            raise ExecutorRefusal("no_candidate", "addresses_lessons_invalid")
        available = {
            item["item_id"] for group in ("lessons", "similar_history", "evidence")
            for item in case_file["groups"][group]["items"]
        }
        if not set(ids) <= available:
            raise ExecutorRefusal("no_candidate", "addresses_lessons_unknown_citation")
        normalized = {"lesson_ids": list(ids)}
    return {"case_file_id": case_file["case_file_id"], "case_file_digest": case_file["digest"],
            "addresses_lessons": normalized}


def build_gate_request_factory(
    *,
    spec: Mapping[str, Any],
    dataset: Mapping[str, Any],
    decision_id: str,
    tenant_id: str,
    record_backtest: Callable[[dict[str, Any]], Any],
    run_protocol: Callable[..., dict[str, Any]] | None = None,
    family_id: str,
    timestamp: str,
) -> Callable[[Mapping[str, Any]], Any]:
    """``request_factory(artifact)`` -> ReplicationRequest bound to a real backtest run.

    The protocol (70/30 IS/OOS, walk-forward, costs, benchmarks) is actually run
    on the governed dataset for exactly this artifact; the evaluation is recorded
    in the authoritative run store the gate checks it against.
    """
    from services.registry.strategy_artifact import strategy_artifact_checksum
    from services.research.replication.gate_schema import ReplicationRequest

    if run_protocol is None:
        from services.research.vectorbt.adapter.vectorbt_adapter import run_replication_backtest_protocol as run_protocol

    non_schema = ("lifecycle_state", "backtest_evaluation", "backtest_dataset", "strategy_artifact",
                  "skip_backtest", "history_trading_days", "hypothesis_card",
                  "hypothesis_predictions", "mechanism_inputs")
    evaluation_spec = {k: v for k, v in dict(spec).items() if k not in non_schema}

    def factory(artifact: Mapping[str, Any]) -> Any:
        artifact = dict(artifact)
        checksum = strategy_artifact_checksum(artifact)
        evaluated = run_protocol(
            strategy_artifact=artifact, dataset=dict(dataset),
            run_id=f"bt-{decision_id}-{artifact['artifact_id']}"[:120],
        )
        record_backtest(evaluated)
        return ReplicationRequest(
            candidate_id=f"{decision_id}:{artifact['artifact_id']}"[:120],
            source_task_id=decision_id,
            research_handoff={
                "source_metadata": {
                    "api_endpoint": f"evolution://decisions/{decision_id}",
                    "retrieved_at": timestamp,
                    "governance_context": f"Approved EvolutionDecision {decision_id}",
                },
                "normalized_findings": {
                    "strategy_spec": {"strategy_id": artifact.get("strategy_id")},
                    "replication_notes": "Evolution child judged on the untouched out-of-sample window.",
                    "evaluation_hypotheses": "H1: child achieves positive deflated out-of-sample Sharpe after costs.",
                },
                "family_id": family_id,
                "strategy_artifact": artifact,
                "backtest_dataset": dict(dataset),
                **{key: dict(spec[key]) for key in ("hypothesis_card", "mechanism_inputs")
                   if isinstance(spec.get(key), Mapping)},
                **({"hypothesis_predictions": list(spec["hypothesis_predictions"])}
                   if isinstance(spec.get("hypothesis_predictions"), list) else {}),
            },
            proposed_strategy_spec=evaluation_spec,
            backtest_evaluation=evaluated,
            metadata={
                "tenant_id": tenant_id,
                "decision_id": decision_id,
                "dataset_version_id": evaluated.get("dataset_id"),
                "strategy_artifact_checksum": checksum,
            },
            timestamp=timestamp,
        )

    return factory


def execute_mutate(
    parent: Mapping[str, Any],
    *,
    decision_id: str,
    family_id: str,
    gate: Any,
    request_factory: Callable[[Mapping[str, Any]], Any],
    register_child: Callable[[Mapping[str, Any], Mapping[str, Any]], Any],
    children: int = 1,
    family_versions: Callable[[], Any] | None = None,
    parent_registry_ref: str | None = None,
    case_file: Mapping[str, Any] | None = None,
    declare_lessons: Callable[[Mapping[str, Any], Mapping[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    logic = parent.get("strategy_logic")
    if not isinstance(logic, Mapping) or logic.get("kind") != "factor_rule_v1":
        raise ExecutorRefusal("no_candidate", "mutate_requires_factor_rule_v1")
    contexts = {}

    def guarded_request(child):
        if case_file is not None:
            declaration = declare_lessons(copy.deepcopy(child), copy.deepcopy(case_file)) if declare_lessons else None
            contexts[child["artifact_id"]] = child_lesson_context(case_file, declaration)
        return request_factory(child)

    def guarded_register(child, verdict):
        return register_child(child, {**verdict, **contexts.get(child["artifact_id"], {})})

    result = search.run_mutation_decision(
        parent, decision_id=decision_id, gate=gate, request_factory=guarded_request,
        family_id=family_id, children=children, register_child=guarded_register,
        family_versions=family_versions, parent_registry_ref=parent_registry_ref,
    )
    for child in result["children"]:
        if child.get("built"):
            child.update(contexts.get(child["verdict"]["child_artifact_id"], {}))
    built = [c for c in result["children"] if c.get("built")]
    registered = [c["verdict"]["child_artifact_id"] for c in built if c.get("registered")]
    if registered:
        outcome, reason = "candidate_created", "child_passed_out_of_sample_gate"
    elif not built:
        outcome, reason = "no_candidate", "no_child_could_be_built"
    else:
        outcome, reason = "no_candidate", "children_failed_out_of_sample_gate"
    return {"outcome": outcome, "reason": reason, "registered_children": registered, "detail": result}


def execute_revalidate(
    artifact: Mapping[str, Any],
    *,
    family_id: str,
    gate: Any,
    request_factory: Callable[[Mapping[str, Any]], Any],
) -> dict[str, Any]:
    request = request_factory(artifact)
    evaluation = getattr(request, "backtest_evaluation", None)
    if isinstance(evaluation, Mapping) and evaluation.get("status") != "completed":
        raise ExecutorRefusal("evaluation_rejected", "post_admission_evaluation_unavailable")
    verdict = search.judge_child_out_of_sample(artifact, gate=gate, request_factory=lambda _: request, family_id=family_id)
    passed = bool(verdict["passed"])
    return {
        "outcome": "revalidation_passed" if passed else "revalidation_failed",
        "reason": "out_of_sample_gate_passed" if passed else "out_of_sample_gate_failed",
        "verdict": verdict,
    }
