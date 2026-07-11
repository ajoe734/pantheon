import json

from runtime_truth_reconciler import RuntimeTruthReconciler


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
    assert record["disposition"] == "repaired"
    assert record["repair_patch"]["persona_id"] == "persona-1"
    assert record["after_issue_codes"] == []


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
