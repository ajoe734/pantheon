"""
services/lineage-read/strategy_lineage_tree.py

Strategy lineage tree backend read API (STRAT-V2-002).

Exposes get_tree(strategy_spec_id, depth) which returns the full downstream
lineage chain rooted at a StrategySpec:

  source_record -> strategy_spec -> experiment_runs -> candidate_artifacts
  -> deployment_plans -> runtime_bindings

Independent module. Does not import or modify the LIN-001 read-model.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

NODE_SOURCE_RECORD = "source_record"
NODE_STRATEGY_SPEC = "strategy_spec"
NODE_EXPERIMENT_RUN = "experiment_run"
NODE_CANDIDATE_ARTIFACT = "candidate_artifact"
NODE_DEPLOYMENT_PLAN = "deployment_plan"
NODE_RUNTIME_BINDING = "runtime_binding"

DEFAULT_MAX_DEPTH = 10


class StrategyLineageStore:
    """
    In-memory store for strategy lineage nodes, indexed by type and id.

    Designed for injection in tests and as a lightweight fixture-driven
    backend.  Thread-safety is not guaranteed; callers are responsible for
    synchronisation when writing concurrently.
    """

    def __init__(self) -> None:
        self._nodes: Dict[str, Dict[str, Dict[str, Any]]] = {}

    def add_node(self, node_type: str, node_id: str, data: Dict[str, Any]) -> None:
        if node_type not in self._nodes:
            self._nodes[node_type] = {}
        # Preserve the canonical node id selected by the caller/loader even
        # when a source record also carries a generic row-level "id".
        self._nodes[node_type][node_id] = {**data, "id": node_id}

    def get_node(self, node_type: str, node_id: str) -> Optional[Dict[str, Any]]:
        return self._nodes.get(node_type, {}).get(node_id)

    def find_by_field(
        self, node_type: str, field: str, value: Any
    ) -> List[Dict[str, Any]]:
        return [
            node
            for node in self._nodes.get(node_type, {}).values()
            if node.get(field) == value
        ]

    def load_corpus(self, corpus: Dict[str, Any]) -> None:
        """
        Bulk-load nodes from a corpus dict with a node_sets structure
        compatible with the LIN-001A benchmark format.
        """
        node_sets = corpus.get("node_sets", {})
        _ingest = [
            (node_sets.get("source_records", []), NODE_SOURCE_RECORD, "source_id"),
            (node_sets.get("strategy_specs", []), NODE_STRATEGY_SPEC, "strategy_id"),
            (node_sets.get("experiment_runs", []), NODE_EXPERIMENT_RUN, "run_id"),
            (node_sets.get("candidate_artifacts", []), NODE_CANDIDATE_ARTIFACT, "artifact_id"),
            (node_sets.get("deployment_plans", []), NODE_DEPLOYMENT_PLAN, "plan_id"),
            (node_sets.get("runtime_bindings", []), NODE_RUNTIME_BINDING, "binding_id"),
        ]
        for records, node_type, id_field in _ingest:
            for rec in records:
                node_id = rec.get(id_field) or rec.get("id", "")
                if node_id:
                    self.add_node(node_type, node_id, rec)


# Module-level default store; tests may replace or populate as needed.
_default_store: StrategyLineageStore = StrategyLineageStore()


# Ref fields that carry cross-node foreign keys; used to build lineage_refs.
_REF_FIELDS = frozenset(
    {
        "source_id",
        "source_record_id",
        "strategy_id",
        "run_id",
        "artifact_id",
        "plan_id",
        "binding_id",
        "approval_decision_id",
        "capital_pool_id",
        "persona_capital_binding_id",
        "runtime_id",
    }
)


def _format_node(node_type: str, node_data: Dict[str, Any]) -> Dict[str, Any]:
    node_id = node_data.get("id", "")
    created_at = node_data.get("created_at") or node_data.get("effective_at") or ""
    lineage_refs = {
        f: node_data[f]
        for f in _REF_FIELDS
        if f in node_data and node_data[f] is not None
    }
    return {
        "id": node_id,
        "artifact_type": node_type,
        "lineage_refs": lineage_refs,
        "created_at": created_at,
    }


def get_tree(
    strategy_spec_id: str,
    depth: int = DEFAULT_MAX_DEPTH,
    *,
    store: Optional[StrategyLineageStore] = None,
) -> Dict[str, Any]:
    """
    Return the full lineage tree rooted at a StrategySpec.

    Traverses the canonical chain:
        source_record -> strategy_spec
        -> experiment_runs -> candidate_artifacts
        -> deployment_plans -> runtime_bindings

    Parameters
    ----------
    strategy_spec_id:
        ID of the StrategySpec to root the tree at.
    depth:
        Maximum number of layers to traverse below strategy_spec.
        A depth of 0 returns only the strategy_spec node.
        A depth >= 5 returns the full chain.
        Prevents runaway queries on deeply nested or cyclical stores.
    store:
        Optional StrategyLineageStore override.  Uses the module-level
        default store when omitted.

    Returns
    -------
    dict
        On success:   {"status": 200, "tree": <nested node dict>}
        When unknown: {"status": 404, "error": "NOT_FOUND",
                       "strategy_spec_id": <id>}
        Never raises an exception.
    """
    s = store if store is not None else _default_store

    spec_data = s.get_node(NODE_STRATEGY_SPEC, strategy_spec_id)
    if spec_data is None:
        return {
            "status": 404,
            "error": "NOT_FOUND",
            "strategy_spec_id": strategy_spec_id,
        }

    tree = _format_node(NODE_STRATEGY_SPEC, spec_data)

    if depth <= 0:
        return {"status": 200, "tree": tree}

    # Layer -1 (upstream): source_record
    source_id = spec_data.get("source_id") or spec_data.get("source_record_id")
    if source_id:
        src_data = s.get_node(NODE_SOURCE_RECORD, source_id)
        if src_data is not None:
            tree["source_record"] = _format_node(NODE_SOURCE_RECORD, src_data)

    if depth <= 1:
        return {"status": 200, "tree": tree}

    # Layer 1: experiment_runs
    runs = s.find_by_field(NODE_EXPERIMENT_RUN, "strategy_id", strategy_spec_id)
    tree["experiment_runs"] = []
    for run_data in runs:
        run_node = _format_node(NODE_EXPERIMENT_RUN, run_data)
        run_id = run_data.get("id", "")

        if depth > 2:
            # Layer 2: candidate_artifacts
            artifacts = s.find_by_field(NODE_CANDIDATE_ARTIFACT, "run_id", run_id)
            run_node["candidate_artifacts"] = []
            for art_data in artifacts:
                art_node = _format_node(NODE_CANDIDATE_ARTIFACT, art_data)
                artifact_id = art_data.get("id", "")

                if depth > 3:
                    # Layer 3: deployment_plans
                    plans = s.find_by_field(NODE_DEPLOYMENT_PLAN, "artifact_id", artifact_id)
                    art_node["deployment_plans"] = []
                    for plan_data in plans:
                        plan_node = _format_node(NODE_DEPLOYMENT_PLAN, plan_data)
                        plan_id = plan_data.get("id", "")

                        if depth > 4:
                            # Layer 4: runtime_bindings
                            bindings = s.find_by_field(NODE_RUNTIME_BINDING, "plan_id", plan_id)
                            plan_node["runtime_bindings"] = [
                                _format_node(NODE_RUNTIME_BINDING, b) for b in bindings
                            ]

                        art_node["deployment_plans"].append(plan_node)

                run_node["candidate_artifacts"].append(art_node)

        tree["experiment_runs"].append(run_node)

    return {"status": 200, "tree": tree}
