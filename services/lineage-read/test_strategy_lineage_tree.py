"""
tests for services/lineage-read/strategy_lineage_tree.py (STRAT-V2-002)
"""
from __future__ import annotations

import pytest

from strategy_lineage_tree import (
    NODE_CANDIDATE_ARTIFACT,
    NODE_DEPLOYMENT_PLAN,
    NODE_EXPERIMENT_RUN,
    NODE_RUNTIME_BINDING,
    NODE_SOURCE_RECORD,
    NODE_STRATEGY_SPEC,
    StrategyLineageStore,
    get_tree,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _full_chain_store() -> StrategyLineageStore:
    """Build a store with one complete chain covering all 6 node types."""
    store = StrategyLineageStore()
    store.add_node(NODE_SOURCE_RECORD, "src-001", {
        "source_id": "src-001",
        "created_at": "2026-01-01T00:00:00Z",
    })
    store.add_node(NODE_STRATEGY_SPEC, "strat-001", {
        "strategy_id": "strat-001",
        "source_id": "src-001",
        "created_at": "2026-01-02T00:00:00Z",
    })
    store.add_node(NODE_EXPERIMENT_RUN, "run-001", {
        "run_id": "run-001",
        "strategy_id": "strat-001",
        "created_at": "2026-01-03T00:00:00Z",
    })
    store.add_node(NODE_CANDIDATE_ARTIFACT, "art-001", {
        "artifact_id": "art-001",
        "run_id": "run-001",
        "created_at": "2026-01-04T00:00:00Z",
    })
    store.add_node(NODE_DEPLOYMENT_PLAN, "plan-001", {
        "plan_id": "plan-001",
        "artifact_id": "art-001",
        "created_at": "2026-01-05T00:00:00Z",
    })
    store.add_node(NODE_RUNTIME_BINDING, "rb-001", {
        "binding_id": "rb-001",
        "plan_id": "plan-001",
        "created_at": "2026-01-06T00:00:00Z",
    })
    return store


# ---------------------------------------------------------------------------
# Full chain traversal
# ---------------------------------------------------------------------------


def test_get_tree_returns_all_six_node_types():
    store = _full_chain_store()
    result = get_tree("strat-001", depth=10, store=store)

    assert result["status"] == 200
    tree = result["tree"]

    # strategy_spec root
    assert tree["artifact_type"] == NODE_STRATEGY_SPEC
    assert tree["id"] == "strat-001"
    assert tree["created_at"] == "2026-01-02T00:00:00Z"

    # source_record (upstream)
    assert "source_record" in tree
    src = tree["source_record"]
    assert src["artifact_type"] == NODE_SOURCE_RECORD
    assert src["id"] == "src-001"

    # experiment_runs
    assert "experiment_runs" in tree
    assert len(tree["experiment_runs"]) == 1
    run = tree["experiment_runs"][0]
    assert run["artifact_type"] == NODE_EXPERIMENT_RUN
    assert run["id"] == "run-001"

    # candidate_artifacts
    assert "candidate_artifacts" in run
    assert len(run["candidate_artifacts"]) == 1
    art = run["candidate_artifacts"][0]
    assert art["artifact_type"] == NODE_CANDIDATE_ARTIFACT
    assert art["id"] == "art-001"

    # deployment_plans
    assert "deployment_plans" in art
    assert len(art["deployment_plans"]) == 1
    plan = art["deployment_plans"][0]
    assert plan["artifact_type"] == NODE_DEPLOYMENT_PLAN
    assert plan["id"] == "plan-001"

    # runtime_bindings
    assert "runtime_bindings" in plan
    assert len(plan["runtime_bindings"]) == 1
    rb = plan["runtime_bindings"][0]
    assert rb["artifact_type"] == NODE_RUNTIME_BINDING
    assert rb["id"] == "rb-001"


# ---------------------------------------------------------------------------
# 404-equivalent for unknown strategy_spec_id
# ---------------------------------------------------------------------------


def test_get_tree_unknown_spec_returns_404_dict_not_exception():
    store = StrategyLineageStore()  # empty store
    result = get_tree("does-not-exist", depth=10, store=store)

    assert result["status"] == 404
    assert result["error"] == "NOT_FOUND"
    assert result["strategy_spec_id"] == "does-not-exist"


# ---------------------------------------------------------------------------
# Depth limiting
# ---------------------------------------------------------------------------


def test_get_tree_depth_0_returns_only_strategy_spec():
    store = _full_chain_store()
    result = get_tree("strat-001", depth=0, store=store)

    assert result["status"] == 200
    tree = result["tree"]
    assert tree["artifact_type"] == NODE_STRATEGY_SPEC
    assert "source_record" not in tree
    assert "experiment_runs" not in tree


def test_get_tree_depth_1_includes_source_record_only():
    store = _full_chain_store()
    result = get_tree("strat-001", depth=1, store=store)

    assert result["status"] == 200
    tree = result["tree"]
    assert "source_record" in tree
    assert "experiment_runs" not in tree


def test_get_tree_depth_2_includes_experiment_runs_but_not_artifacts():
    store = _full_chain_store()
    result = get_tree("strat-001", depth=2, store=store)

    assert result["status"] == 200
    tree = result["tree"]
    assert "experiment_runs" in tree
    run = tree["experiment_runs"][0]
    assert "candidate_artifacts" not in run


def test_get_tree_depth_3_includes_artifacts_but_not_plans():
    store = _full_chain_store()
    result = get_tree("strat-001", depth=3, store=store)

    tree = result["tree"]
    run = tree["experiment_runs"][0]
    assert "candidate_artifacts" in run
    art = run["candidate_artifacts"][0]
    assert "deployment_plans" not in art


def test_get_tree_depth_4_includes_plans_but_not_bindings():
    store = _full_chain_store()
    result = get_tree("strat-001", depth=4, store=store)

    tree = result["tree"]
    plan = tree["experiment_runs"][0]["candidate_artifacts"][0]["deployment_plans"][0]
    assert "runtime_bindings" not in plan


# ---------------------------------------------------------------------------
# Node shape — each node has id, artifact_type, lineage_refs, created_at
# ---------------------------------------------------------------------------


def test_each_node_has_canonical_fields():
    store = _full_chain_store()
    result = get_tree("strat-001", depth=10, store=store)

    tree = result["tree"]
    nodes_to_check = [
        tree,
        tree["source_record"],
        tree["experiment_runs"][0],
        tree["experiment_runs"][0]["candidate_artifacts"][0],
        tree["experiment_runs"][0]["candidate_artifacts"][0]["deployment_plans"][0],
        tree["experiment_runs"][0]["candidate_artifacts"][0]["deployment_plans"][0]["runtime_bindings"][0],
    ]
    for node in nodes_to_check:
        assert "id" in node, f"missing id on {node}"
        assert "artifact_type" in node, f"missing artifact_type on {node}"
        assert "lineage_refs" in node, f"missing lineage_refs on {node}"
        assert "created_at" in node, f"missing created_at on {node}"
        assert isinstance(node["lineage_refs"], dict), f"lineage_refs not dict on {node}"


# ---------------------------------------------------------------------------
# lineage_refs contain cross-node foreign keys
# ---------------------------------------------------------------------------


def test_lineage_refs_populated_on_inner_nodes():
    store = _full_chain_store()
    result = get_tree("strat-001", depth=10, store=store)

    tree = result["tree"]
    # strategy_spec should have source_id in lineage_refs
    assert "source_id" in tree["lineage_refs"]
    assert tree["lineage_refs"]["source_id"] == "src-001"

    # experiment_run should have strategy_id
    run = tree["experiment_runs"][0]
    assert run["lineage_refs"].get("strategy_id") == "strat-001"

    # candidate_artifact should have run_id
    art = run["candidate_artifacts"][0]
    assert art["lineage_refs"].get("run_id") == "run-001"

    # deployment_plan should have artifact_id
    plan = art["deployment_plans"][0]
    assert plan["lineage_refs"].get("artifact_id") == "art-001"

    # runtime_binding should have plan_id
    rb = plan["runtime_bindings"][0]
    assert rb["lineage_refs"].get("plan_id") == "plan-001"


# ---------------------------------------------------------------------------
# Multiple experiment runs / artifacts / plans / bindings
# ---------------------------------------------------------------------------


def test_multiple_experiment_runs_all_included():
    store = StrategyLineageStore()
    store.add_node(NODE_STRATEGY_SPEC, "strat-multi", {
        "strategy_id": "strat-multi",
        "created_at": "2026-01-01T00:00:00Z",
    })
    for i in range(3):
        store.add_node(NODE_EXPERIMENT_RUN, f"run-{i}", {
            "run_id": f"run-{i}",
            "strategy_id": "strat-multi",
            "created_at": f"2026-01-0{i+2}T00:00:00Z",
        })

    result = get_tree("strat-multi", depth=2, store=store)

    assert result["status"] == 200
    assert len(result["tree"]["experiment_runs"]) == 3


# ---------------------------------------------------------------------------
# Spec with no source_record
# ---------------------------------------------------------------------------


def test_missing_source_record_does_not_raise():
    store = StrategyLineageStore()
    store.add_node(NODE_STRATEGY_SPEC, "strat-nosrc", {
        "strategy_id": "strat-nosrc",
        "created_at": "2026-01-01T00:00:00Z",
        # no source_id
    })

    result = get_tree("strat-nosrc", depth=1, store=store)

    assert result["status"] == 200
    assert "source_record" not in result["tree"]


# ---------------------------------------------------------------------------
# load_corpus helper
# ---------------------------------------------------------------------------


def test_load_corpus_populates_store():
    corpus = {
        "node_sets": {
            "source_records": [{"source_id": "src-c", "created_at": "2026-01-01T00:00:00Z"}],
            "strategy_specs": [{"strategy_id": "strat-c", "source_id": "src-c", "created_at": "2026-01-02T00:00:00Z"}],
            "experiment_runs": [{"run_id": "run-c", "strategy_id": "strat-c", "created_at": "2026-01-03T00:00:00Z"}],
            "candidate_artifacts": [{"artifact_id": "art-c", "run_id": "run-c", "created_at": "2026-01-04T00:00:00Z"}],
            "deployment_plans": [{"plan_id": "plan-c", "artifact_id": "art-c", "created_at": "2026-01-05T00:00:00Z"}],
            "runtime_bindings": [{"binding_id": "rb-c", "plan_id": "plan-c", "created_at": "2026-01-06T00:00:00Z"}],
        }
    }
    store = StrategyLineageStore()
    store.load_corpus(corpus)

    result = get_tree("strat-c", depth=10, store=store)

    assert result["status"] == 200
    tree = result["tree"]
    assert tree["id"] == "strat-c"
    assert tree["source_record"]["id"] == "src-c"
    assert tree["experiment_runs"][0]["id"] == "run-c"
    assert tree["experiment_runs"][0]["candidate_artifacts"][0]["id"] == "art-c"
    plan = tree["experiment_runs"][0]["candidate_artifacts"][0]["deployment_plans"][0]
    assert plan["id"] == "plan-c"
    assert plan["runtime_bindings"][0]["id"] == "rb-c"
