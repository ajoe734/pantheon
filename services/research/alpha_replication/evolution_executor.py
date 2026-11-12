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


class ExecutorRefusal(Exception):
    """A stated, non-transport refusal: the run ends completed with this outcome."""

    def __init__(self, outcome: str, reason: str) -> None:
        super().__init__(f"{outcome}: {reason}")
        self.outcome = outcome
        self.reason = reason


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
                  "skip_backtest", "history_trading_days")
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
) -> dict[str, Any]:
    logic = parent.get("strategy_logic")
    if not isinstance(logic, Mapping) or logic.get("kind") != "factor_rule_v1":
        raise ExecutorRefusal("no_candidate", "mutate_requires_factor_rule_v1")
    result = search.run_mutation_decision(
        parent, decision_id=decision_id, gate=gate, request_factory=request_factory,
        family_id=family_id, children=children, register_child=register_child,
        family_versions=family_versions,
    )
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
    verdict = search.judge_child_out_of_sample(artifact, gate=gate, request_factory=request_factory, family_id=family_id)
    passed = bool(verdict["passed"])
    return {
        "outcome": "revalidation_passed" if passed else "revalidation_failed",
        "reason": "out_of_sample_gate_passed" if passed else "out_of_sample_gate_failed",
        "verdict": verdict,
    }
