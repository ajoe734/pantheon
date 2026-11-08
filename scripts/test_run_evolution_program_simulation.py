from __future__ import annotations

import hashlib
from pathlib import Path
import pytest

from scripts.run_evolution_program_simulation import (
    REPO_ROOT,
    SHARED_POLICY_PATH,
    SimulationConfig,
    SimulationRefusalError,
    run_simulation,
    validate_simulation_config,
)


@pytest.mark.parametrize(
    ("kwargs", "expected_code"),
    [
        ({"allow_broker_orders": True}, "broker_capital_refused"),
        ({"allow_capital_writes": True}, "broker_capital_refused"),
        ({"allow_shared_policy_mutation": True}, "shared_policy_mutation_refused"),
        ({"tenant_id": "default"}, "non_synthetic_tenant_refused"),
        ({"tenant_id": "tenant-dev"}, "non_synthetic_tenant_refused"),
        ({"tenant_id": "production"}, "non_synthetic_tenant_refused"),
        ({"tenant_id": "regular-tenant-20261107"}, "non_synthetic_tenant_refused"),
        ({"data_scope": "natural"}, "non_synthetic_data_scope_refused"),
        ({"data_scope": "production"}, "non_synthetic_data_scope_refused"),
        ({"deployment_stage": "live"}, "non_paper_stage_refused"),
        ({"deployment_stage": "canary"}, "non_paper_stage_refused"),
        ({"execution_mode": "live"}, "non_paper_mode_refused"),
        (
            {"target_url": "https://pantheon-dev-bff.example.com"},
            "non_local_target_refused",
        ),
        ({"target_url": "http://10.128.0.5:8088"}, "non_local_target_refused"),
        (
            {"work_dir": Path("/tmp/pantheon/evolution")},
            "shared_state_path_refused",
        ),
        (
            {"work_dir": REPO_ROOT / "services" / "evolution"},
            "shared_state_path_refused",
        ),
    ],
)
def test_simulation_refusal_guards_fail_closed(
    kwargs: dict, expected_code: str
) -> None:
    config = SimulationConfig(**kwargs)
    with pytest.raises(SimulationRefusalError, match=expected_code):
        validate_simulation_config(config)
    with pytest.raises(SimulationRefusalError, match=expected_code):
        run_simulation(config)


def test_run_evolution_program_simulation_candidate_created_end_to_end(
    tmp_path: Path,
) -> None:
    policy_digest_before = hashlib.sha256(SHARED_POLICY_PATH.read_bytes()).hexdigest()

    report = run_simulation(
        SimulationConfig(
            work_dir=tmp_path,
            target_url="http://127.0.0.1:0",
            retrain_variant="better",
        )
    )

    policy_digest_after = hashlib.sha256(SHARED_POLICY_PATH.read_bytes()).hexdigest()
    assert policy_digest_after == policy_digest_before

    assert report["task_id"] == "EVOLUTION-ISOLATED-STIMULUS-20261107"
    assert len(report["source_sha"]) == 40
    assert report["evidence_level"] == "local_integration_simulation"
    assert report["natural_market_qualification_proven"] is False
    assert report["synthetic_provenance"]["synthetic"] is True
    assert report["synthetic_provenance"]["data_scope"] == "synthetic"
    assert len(report["synthetic_provenance"]["mark_event_ids"]) == 3
    assert len(report["synthetic_provenance"]["drift_evaluation_ids"]) == 3

    policy_diff = report["policy_diff"]
    assert policy_diff["shared_policy_unchanged"] is True
    assert policy_diff["shared_policy_before"]["allow_test_scope"] is False
    assert policy_diff["shared_policy_after"]["allow_test_scope"] is False
    refusal = policy_diff["shared_policy_refusal_on_synthetic_target"]
    assert refusal["qualified"] is False
    assert refusal["first_failing_criterion"] == "real_target"
    assert refusal["real_target_criterion"]["result"] == "failed"
    assert "synthetic" in refusal["real_target_criterion"]["reason"]

    qual = report["qualification"]
    assert qual["evaluated"] == 1
    assert qual["qualified"] == 1
    assert qual["drafted"] == 1
    assert qual["action_type"] == "retrain"
    assert qual["metric_name"] == "rolling_drawdown_multiple"
    assert qual["all_six_criteria_passed"] is True
    criteria_by_name = {c["criterion"]: c for c in qual["criteria"]}
    assert set(criteria_by_name) == {
        "real_threshold_signal",
        "real_target",
        "persistent_breach",
        "fresh_evidence",
        "executable_action",
        "no_open_duplicate",
    }
    assert criteria_by_name["persistent_breach"]["observed"]["distinct_breach_days"] == 3
    assert len(criteria_by_name["persistent_breach"]["observed"]["mark_dates"]) == 3

    pre_stop = report["pre_approval_stop"]
    assert pre_stop["stopped_before_dispatch"] is True
    assert pre_stop["program_origin"] == "auto_qualified"
    assert pre_stop["program_status"] == "under_review"
    assert pre_stop["decision_state"] == "proposed"
    assert pre_stop["outbox_record_count"] == 0
    assert pre_stop["research_run_count"] == 0

    neg = report["negative_governance_proofs"]
    assert neg["missing_approval_refusal"]["http_status"] in (409, 422)
    assert neg["mismatched_binding_refusal"]["http_status"] in (409, 422)
    assert neg["already_decided_rejected_refusal"]["http_status"] in (409, 422)
    assert neg["pending_approval_refusal"]["http_status"] in (409, 422)
    assert neg["proposer_self_decide_refusal"]["http_status"] in (403, 409, 422)
    assert neg["work_started_during_invalid_or_pending_attempts"] is False

    gov = report["governance_approval"]
    assert gov["decision_state"] == "decided"
    assert gov["decision"] == "approved"
    assert gov["owner_user_id"] != gov["actor_id"]
    assert gov["audit_event_count"] >= 3
    assert gov["program_status_after_approval"] == "active"
    assert gov["decision_state_after_approval"] == "approved"
    assert gov["outbox_status_after_approval"] == "pending"

    res = report["research_execution"]
    assert res["task_status"] == "completed"
    assert res["run_status"] == "completed"
    assert res["executor"] == "services.research.main._execute_retrain"
    assert res["adapter"] == "training_session_evaluation"
    assert res["outcome"] == "candidate_created"
    assert res["candidate_artifact_id"] is not None
    assert res["candidate_artifact_version"] == "1.1.0"
    assert res["candidate_artifact_state"] == "candidate"
    assert res["runtime_binding_changed"] is False
    assert res["capital_records_created"] == 0
    assert res["broker_orders_created"] == 0
    assert res["session_id"] is not None
    assert res["baseline_job_id"] is not None
    assert res["candidate_job_id"] is not None
    assert res["decision_readback"]["decision_state"] == "executed"
    assert res["decision_readback"]["execution_result_status"] == "succeeded"
    assert res["decision_readback"]["execution_result_run_id"] == res["run_id"]
    assert res["outbox_readback"]["status"] == "published"
    assert res["program_readback"]["research_run_ids"] == [res["run_id"]]
    assert res["program_readback"]["runs_endpoint_total"] == 1

    idem = report["idempotency"]
    assert idem["repeat_daily_sweep_created_decisions"] == 0
    assert idem["total_decisions"] == 1
    assert idem["total_programs"] == 1
    assert idem["repeat_approve_program_http_status"] == 200
    assert idem["repeat_approve_program_same_revision"] is True
    assert idem["repeat_dispatch_poll_claimed"] == 0
    assert idem["total_research_tasks"] == 1
    assert idem["total_research_runs"] == 1

    cleanup = report["cleanup"]
    assert cleanup["cleaned"] is True
    assert cleanup["exists_after_cleanup"] is False


def test_run_evolution_program_simulation_no_candidate_variant(
    tmp_path: Path,
) -> None:
    report = run_simulation(
        SimulationConfig(
            work_dir=tmp_path,
            retrain_variant="worse",
        )
    )
    res = report["research_execution"]
    assert res["task_status"] == "completed"
    assert res["run_status"] == "completed"
    assert res["outcome"] == "no_candidate"
    assert res["candidate_artifact_id"] is None
    assert res["decision_readback"]["decision_state"] == "executed"
    assert res["outbox_readback"]["status"] == "published"
    assert report["cleanup"]["cleaned"] is True
