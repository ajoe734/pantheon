import json

from runtime_truth_reconciler import RuntimeTruthReconciler, build_reconciliation_rows


def _complete(runtime_id="runtime-1"):
    identity = {
        "persona_id": "persona-1", "deployment_plan_id": "plan-1",
        "artifact_id": "artifact-1", "strategy_id": "strategy-1",
        "broker_id": "paper-broker", "capital_scope_kind": "paper_ledger",
        "capital_scope_id": "ledger-1",
    }
    return {
        "runtime_id": runtime_id,
        "binding": {"id": "binding-1", **identity},
        "runtime": {"runtime_id": runtime_id, **identity},
        "deployment_plan": identity,
        "persona_capital_binding": identity,
        "telemetry": {"runtime_id": runtime_id, **identity},
        "evidence_refs": ["runtime-manager:binding-1"],
    }


def test_normal_row_is_unchanged_and_formal(tmp_path):
    record = RuntimeTruthReconciler(tmp_path / "audit.jsonl").reconcile([_complete()]).records[0]
    assert record["disposition"] == "unchanged"
    assert record["formal_attribution_allowed"] is True


def test_missing_binding_is_repaired_only_when_authorities_agree(tmp_path):
    row = _complete()
    del row["binding"]["persona_id"]
    record = RuntimeTruthReconciler(tmp_path / "audit.jsonl").reconcile([row]).records[0]
    assert record["disposition"] == "repair_proposed"
    assert record["repair_patch"]["persona_id"] == "persona-1"
    assert record["after_issue_codes"] == []
    assert record["formal_attribution_allowed"] is False


def test_conflict_is_quarantined_and_remains_visible(tmp_path):
    row = _complete()
    del row["binding"]["capital_scope_id"]
    row["runtime"]["capital_scope_id"] = "ledger-a"
    row["deployment_plan"]["capital_scope_id"] = "ledger-b"
    record = RuntimeTruthReconciler(tmp_path / "audit.jsonl").reconcile([row]).records[0]
    assert record["disposition"] == "quarantined"
    assert "capital_scope_id" in record["conflicts"]
    assert record["formal_attribution_allowed"] is False


def test_missing_and_stale_telemetry_are_quarantined(tmp_path):
    missing = _complete("runtime-missing")
    missing["telemetry"] = {}
    stale = _complete("runtime-stale")
    stale["telemetry"]["stale"] = True
    report = RuntimeTruthReconciler(tmp_path / "audit.jsonl").reconcile([missing, stale])
    assert [r["disposition"] for r in report.records] == ["quarantined", "quarantined"]
    assert report.to_dict()["summary"]["input_count"] == 2
    assert all(not r["formal_attribution_allowed"] for r in report.records)


def test_replay_is_idempotent_and_writes_one_audit_entry(tmp_path):
    audit = tmp_path / "audit.jsonl"
    reconciler = RuntimeTruthReconciler(audit)
    first = reconciler.reconcile([_complete()], run_id="hosted-before-after-1")
    second = reconciler.reconcile([_complete()], run_id="ignored")
    assert second.replayed is True
    assert second.run_id == first.run_id
    assert len(audit.read_text().splitlines()) == 1
    assert json.loads(audit.read_text())["summary"]["input_count"] == 1


def test_hosted_source_join_keeps_unresolved_runtime_visible(tmp_path):
    snapshot = {
        "runtime_bindings": [
            {"binding_id": "rb-good", "runtime_id": "rt-good", "plan_id": "plan-1", "persona_capital_binding_id": "pcb-1", "artifact_id": "art-1", "strategy_id": "strat-1", "broker_id": "paper-broker", "deployment_mode": "paper", "capital_pool_id": "pool-1"},
            {"binding_id": "rb-gap", "runtime_id": "rt-gap", "plan_id": "plan-missing"},
        ],
        "deployment_plans": [{"plan_id": "plan-1", "persona_id": "persona-1", "artifact_id": "art-1", "strategy_id": "strat-1", "broker_id": "paper-broker", "target_stage": "paper", "capital_pool_id": "pool-1"}],
        "persona_capital_bindings": [{"binding_id": "pcb-1", "persona_id": "persona-1", "strategy_id": "strat-1", "capital_pool_id": "pool-1"}],
        "capital_pools": [{"pool_id": "pool-1"}],
        "telemetry_summaries": [{"runtime_id": "rt-good", "plan_id": "plan-1", "persona_id": "persona-1", "artifact_id": "art-1", "strategy_id": "strat-1", "broker_id": "paper-broker", "deployment_stage": "paper", "capital_pool_id": "pool-1"}],
    }
    rows = build_reconciliation_rows(snapshot)
    assert [row["runtime_id"] for row in rows] == ["rt-good", "rt-gap"]
    report = RuntimeTruthReconciler(tmp_path / "audit.jsonl").reconcile(rows)
    gap = report.records[1]
    assert gap["disposition"] == "quarantined"
    assert "missing_persona_binding" in gap["after_issue_codes"]
    assert gap["evidence_refs"][-1] == "telemetry_runtime:rt-gap"


def test_hosted_source_join_marks_stale_telemetry_fail_closed(tmp_path):
    row = _complete("rt-stale")
    snapshot = {
        "runtime_bindings": [{"binding_id": "rb-1", "runtime_id": "rt-stale", **row["runtime"]}],
        "deployment_plans": [{"plan_id": "plan-1", **row["deployment_plan"]}],
        "persona_capital_bindings": [{"binding_id": "pcb-1", **row["persona_capital_binding"]}],
        "capital_pools": [{"pool_id": "ledger-1"}],
        "telemetry_summaries": [{**row["telemetry"], "telemetry_stale": True}],
    }
    record = RuntimeTruthReconciler(tmp_path / "audit.jsonl").reconcile(build_reconciliation_rows(snapshot)).records[0]
    assert "stale_telemetry" in record["after_issue_codes"]
    assert record["formal_attribution_allowed"] is False
