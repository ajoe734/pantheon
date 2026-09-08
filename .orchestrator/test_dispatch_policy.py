from __future__ import annotations

import pytest
import sys
from copy import deepcopy
from datetime import datetime, timezone
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dispatch_policy
from rewrite import integration_receipt
from dispatch_policy import (
    ALLOWLISTED_EXECUTION_RESOURCES,
    DEFAULT_ACTIVE_WORKER_STATUSES,
    REASON_OWNED_FINALIZE,
    REASON_OWNED_IN_PROGRESS,
    REASON_OWNED_READY,
    REASON_REVIEW_READY,
    dispatch_reason_priority,
    is_execution_dispatch_reason,
    is_operator_exact_head_acceptance,
    normalize_execution_resources,
    normalized_status_set,
    ready_dispatch_settings,
    task_execution_resources,
)


def operator_accepted_task(**overrides):
    head_sha = "b" * 40
    task = {
        "id": "ABC-001",
        "status": "review_approved",
        "review_binding": {
            "pr": 100,
            "head_sha": head_sha,
            "head_branch": "task/ABC-001",
            "base": "dev",
        },
        "operator_acceptance": {
            "pr": 100,
            "head_sha": head_sha,
            "head_branch": "task/ABC-001",
            "base": "dev",
            "decision": "operator-accept",
            "actor": "Human/Ops",
            "mode": "operator_exact_head",
            "operator_acceptance_proof_ref": (
                "refs/tags/pantheon-review/operator-accept/" + head_sha
            ),
        },
    }
    task.update(overrides)
    return task


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        (REASON_REVIEW_READY, 0),
        (REASON_OWNED_FINALIZE, 1),
        (REASON_OWNED_IN_PROGRESS, 2),
        (REASON_OWNED_READY, 3),
        ("legacy_noncanonical_dispatch", None),
        (None, None),
    ],
)
def test_dispatch_reason_priority_cases(reason: str | None, expected: int | None) -> None:
    assert dispatch_reason_priority(reason) == expected


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        (REASON_REVIEW_READY, True),
        (REASON_OWNED_FINALIZE, True),
        (REASON_OWNED_IN_PROGRESS, True),
        (REASON_OWNED_READY, True),
        ("legacy_noncanonical_dispatch", False),
        (None, False),
    ],
)
def test_is_execution_dispatch_reason_cases(reason: str | None, expected: bool) -> None:
    assert is_execution_dispatch_reason(reason) is expected


def test_operator_exact_head_acceptance_is_a_non_worker_lane() -> None:
    assert is_operator_exact_head_acceptance(operator_accepted_task()) is True


@pytest.mark.parametrize(
    "task",
    [
        operator_accepted_task(status="in_progress"),
        operator_accepted_task(operator_acceptance={}),
        operator_accepted_task(
            operator_acceptance={
                **operator_accepted_task()["operator_acceptance"],
                "actor": "Codex",
            }
        ),
        operator_accepted_task(
            operator_acceptance={
                **operator_accepted_task()["operator_acceptance"],
                "head_sha": "c" * 40,
            }
        ),
    ],
)
def test_malformed_operator_acceptance_does_not_suppress_dispatch(task) -> None:
    assert is_operator_exact_head_acceptance(task) is False


def test_ready_dispatch_settings_current_defaults() -> None:
    settings = ready_dispatch_settings({})
    assert settings["enabled"] is True
    assert settings["review_statuses"] == ["review"]
    assert settings["finalize_statuses"] == ["review_approved"]
    assert settings["owned_statuses"] == ["in_progress", "todo"]
    assert settings["dependency_done_statuses"] == ["done"]
    assert settings["worker_terminal_statuses"] == ["review", "done", "review_approved"]
    assert settings["active_worker_statuses"] == DEFAULT_ACTIVE_WORKER_STATUSES
    assert settings["max_dispatches_per_tick"] == 4
    assert settings["max_concurrent_per_account"] == {}
    assert settings["execution_resource_limits"] == {"pantheon-dev": 1}
    for retired in (
        "disabled_agents",
        "max_tasks_per_agent",
        "max_tasks_per_agent_by_agent",
        "max_concurrent_per_quota_group",
        "priority_preemption_grace_seconds",
    ):
        assert retired not in settings


def test_ready_dispatch_settings_preserves_only_supplied_current_values() -> None:
    settings = ready_dispatch_settings(
        {
            "ready_dispatcher": {
                "review_statuses": ["needs_review"],
                "finalize_statuses": ["approved"],
                "owned_statuses": ["queued"],
                "max_dispatches_per_tick": 8,
                "max_concurrent_per_account": {"codex": 2},
            }
        }
    )
    assert settings["review_statuses"] == ["needs_review"]
    assert settings["finalize_statuses"] == ["approved"]
    assert settings["owned_statuses"] == ["queued"]
    assert settings["max_dispatches_per_tick"] == 8
    assert settings["max_concurrent_per_account"] == {"codex": 2}


def test_normalized_status_set_is_case_normalized() -> None:
    assert normalized_status_set(["Review", "DONE"], ["todo"]) == {"review", "done"}


def test_ready_dispatch_settings_execution_resource_limits() -> None:
    # Default is {'pantheon-dev': 1}
    assert ready_dispatch_settings({})["execution_resource_limits"] == {"pantheon-dev": 1}
    assert ready_dispatch_settings({"ready_dispatcher": {}})["execution_resource_limits"] == {"pantheon-dev": 1}
    assert ready_dispatch_settings({
        "ready_dispatcher": {"execution_resource_limits": {"pantheon-dev": 1}}
    })["execution_resource_limits"] == {"pantheon-dev": 1}

    # Rejection cases
    with pytest.raises(ValueError, match="boolean True is not allowed"):
        ready_dispatch_settings({"ready_dispatcher": {"execution_resource_limits": {"pantheon-dev": True}}})

    with pytest.raises(ValueError, match="expected int, got str"):
        ready_dispatch_settings({"ready_dispatcher": {"execution_resource_limits": {"pantheon-dev": "1"}}})

    with pytest.raises(ValueError, match="value must be 1, got 0"):
        ready_dispatch_settings({"ready_dispatcher": {"execution_resource_limits": {"pantheon-dev": 0}}})

    with pytest.raises(ValueError, match="value must be 1, got 2"):
        ready_dispatch_settings({"ready_dispatcher": {"execution_resource_limits": {"pantheon-dev": 2}}})

    with pytest.raises(ValueError, match="Unknown execution resource limit key"):
        ready_dispatch_settings({"ready_dispatcher": {"execution_resource_limits": {"custom-res": 1}}})


def test_normalize_execution_resources_valid_and_normalization() -> None:
    assert ALLOWLISTED_EXECUTION_RESOURCES == frozenset({"pantheon-dev"})
    assert normalize_execution_resources([]) == []
    assert normalize_execution_resources(["pantheon-dev"]) == ["pantheon-dev"]
    assert normalize_execution_resources(["  PANTHEON-DEV  "]) == ["pantheon-dev"]


def test_normalize_execution_resources_rejections() -> None:
    # Explicit null
    with pytest.raises(ValueError, match="must be a list, got null"):
        normalize_execution_resources(None)

    # Non-list
    with pytest.raises(ValueError, match="must be a list"):
        normalize_execution_resources("pantheon-dev")
    with pytest.raises(ValueError, match="must be a list"):
        normalize_execution_resources(123)
    with pytest.raises(ValueError, match="must be a list"):
        normalize_execution_resources({"pantheon-dev": 1})

    # Non-string element
    with pytest.raises(ValueError, match="elements must be strings"):
        normalize_execution_resources([123])
    with pytest.raises(ValueError, match="elements must be strings"):
        normalize_execution_resources([None])

    # Empty / whitespace string element
    with pytest.raises(ValueError, match="cannot be empty"):
        normalize_execution_resources([""])
    with pytest.raises(ValueError, match="cannot be empty"):
        normalize_execution_resources(["   "])

    # Unallowlisted resource
    with pytest.raises(ValueError, match="unallowlisted resource"):
        normalize_execution_resources(["unknown-res"])
    with pytest.raises(ValueError, match="allowlisted execution resources"):
        normalize_execution_resources(["vm-staging"])

    # Duplicate resource
    with pytest.raises(ValueError, match="duplicate resource"):
        normalize_execution_resources(["pantheon-dev", "pantheon-dev"])
    with pytest.raises(ValueError, match="duplicate resource"):
        normalize_execution_resources(["pantheon-dev", "  PANTHEON-DEV "])


def test_task_execution_resources_cases() -> None:
    # Omitted => []
    assert task_execution_resources(None) == []
    assert task_execution_resources({}) == []
    assert task_execution_resources({"id": "TASK-1"}) == []
    assert task_execution_resources({"id": "TASK-1", "execution_resources": []}) == []

    # Valid
    assert task_execution_resources({"id": "TASK-1", "execution_resources": ["pantheon-dev"]}) == ["pantheon-dev"]

    # Fails closed on explicit null / malformed / unallowlisted
    with pytest.raises(ValueError, match="must be a list, got null"):
        task_execution_resources({"id": "TASK-1", "execution_resources": None})
    with pytest.raises(ValueError, match="elements must be strings"):
        task_execution_resources({"id": "TASK-1", "execution_resources": [123]})
    with pytest.raises(ValueError, match="cannot be empty"):
        task_execution_resources({"id": "TASK-1", "execution_resources": [""]})
    with pytest.raises(ValueError, match="unallowlisted resource"):
        task_execution_resources({"id": "TASK-1", "execution_resources": ["bad"]})
    with pytest.raises(ValueError, match="duplicate resource"):
        task_execution_resources({"id": "TASK-1", "execution_resources": ["pantheon-dev", "pantheon-dev"]})


# DTG-CLEAN-M6 characterization tests for the candidate-evaluation/admission
# functions moved from supervisor.py -- not a re-test of
# .orchestrator/test_supervisor.py's extensive dispatch coverage (which
# already exercises this exact code through supervisor.py's re-export and
# continues to pass unchanged), but proof that this module is genuinely
# usable on its own: no circular import, the lazy supervisor handback
# resolves, and explain/live parity holds structurally (one function, both
# callers).


def test_module_imports_with_no_circular_dependency() -> None:
    # supervisor.py imports dispatch_policy at its own top level; importing
    # supervisor here (a second, independent path into the same dependency
    # graph) must not raise, proving the graph is a DAG (supervisor ->
    # dispatch_policy -> {common, rewrite.dispatch_admission,
    # rewrite.task_machine, task_archive}, with the reverse edge only ever
    # taken lazily, at call time, via _supervisor_module()).
    import supervisor  # noqa: F401


def test_lazy_supervisor_handback_resolves() -> None:
    import dispatch_policy

    supervisor = dispatch_policy._supervisor_module()
    for name in (
        "_admission_health_records",
        "parse_runtime_timestamp",
        "account_concurrency_limit",
        "agent_account_id",
        "build_dispatch_event",
        "delivery_lane_for_agent",
        "dependencies_satisfied",
        "dispatch_loop_agent_ids",
        "ready_dispatch_max_concurrent_workers",
        "review_decision_intent_replay_eligible",
        "runtime_delivery_health",
        "task_review_requeue_intent",
        "task_review_requeue_record",
    ):
        assert hasattr(supervisor, name), name


def test_explain_and_live_dispatch_share_one_candidate_function() -> None:
    """explain_dispatch_for_task and the live dispatch loop must evaluate
    every candidate through the exact same function object -- the
    architectural guarantee that makes 'explain' trustworthy."""

    import dispatch_policy
    import supervisor

    assert supervisor.evaluate_dispatch_candidate is dispatch_policy.evaluate_dispatch_candidate
    assert (
        supervisor.evaluate_task_delivery_admission
        is dispatch_policy.evaluate_task_delivery_admission
    )
    assert (
        supervisor.task_has_current_canonical_integration_receipt
        is dispatch_policy.task_has_current_canonical_integration_receipt
    )
    assert (
        supervisor.is_non_default_repository_finalization_pending
        is dispatch_policy.is_non_default_repository_finalization_pending
    )


def test_entry_points_are_exported() -> None:
    import dispatch_policy

    for name in (
        "build_delivery_admission_snapshot",
        "evaluate_task_delivery_admission",
        "dispatch_event_is_in_unchanged_cooldown",
        "task_review_requeue_is_materialized",
        "evaluate_dispatch_candidate",
        "task_has_current_canonical_integration_receipt",
        "is_non_default_repository_finalization_pending",
    ):
        assert callable(getattr(dispatch_policy, name)), name


def test_dispatch_event_cooldown_is_pure_and_time_bounded() -> None:
    from dispatch_policy import dispatch_event_is_in_unchanged_cooldown

    now = "2026-08-31T00:10:00Z"
    seen = {"evt-1": "2026-08-31T00:05:00Z"}
    assert dispatch_event_is_in_unchanged_cooldown(
        seen, "evt-1", cooldown_seconds=900, now=now
    )
    assert not dispatch_event_is_in_unchanged_cooldown(
        seen, "evt-1", cooldown_seconds=60, now=now
    )
    assert not dispatch_event_is_in_unchanged_cooldown(
        seen, "evt-missing", cooldown_seconds=900, now=now
    )
    assert not dispatch_event_is_in_unchanged_cooldown(
        seen, "evt-1", cooldown_seconds=0, now=now
    )


def test_task_review_requeue_is_materialized_fails_closed_on_no_record() -> None:
    # The full valid-schema "materialized" case is already covered
    # extensively by .orchestrator/test_supervisor.py through the
    # re-export; this proves the pure predicate fails closed on the
    # absence of a canonical record, matching task_review_requeue_record's
    # own fail-closed contract.
    from dispatch_policy import task_review_requeue_is_materialized

    assert not task_review_requeue_is_materialized(None)
    assert not task_review_requeue_is_materialized({})
    assert not task_review_requeue_is_materialized(
        {"review_requeue_intent": {"status": "pending"}}
    )


@pytest.mark.parametrize("authorization_state", ["pending_authorization", "revoked"])
@pytest.mark.parametrize("status,role", [("todo", "owner"), ("in_progress", "owner"), ("review", "reviewer"), ("review_approved", "owner")])
def test_wrapper_normalizes_only_auth_fence_for_canonical_purpose(authorization_state, status, role):
    import dispatch_policy
    import execution_authorization as ea
    from rewrite.dispatch_admission import AdmissionSnapshot, DeliveryEndpoint, DispatchLane, HealthRecord, HealthState
    from test_execution_authorization import ExecutionAuthorizationTestCase

    fixture = ExecutionAuthorizationTestCase()
    fixture.setUp()
    task = deepcopy(fixture._granted_task())
    task["execution_resources"] = ["pantheon-dev"]
    task["dev_bridge"]["task_spec"]["execution_resources"] = ["pantheon-dev"]
    fixture.policy = ea.derive_execution_policy(
        task_id=task["id"], work_class="security", repository="pantheon",
        resources=["pantheon-dev"], artifacts=task["artifacts"], task_spec=task["dev_bridge"]["task_spec"],
    )
    task["dev_bridge"]["task_spec_hash"] = fixture.policy["task_spec_hash"]
    task.update(status=status, waiting_for="Human/Ops")
    task["execution_authorization"] = ea.pending_authorization_hold(fixture.policy)
    task["execution_authorization"]["state"] = authorization_state
    original = deepcopy(task)
    lane = DispatchLane("test-lane", task[role], 1, (DeliveryEndpoint("endpoint", "provider", "account"),))
    snapshot = AdmissionSnapshot(
        now=datetime.now(timezone.utc),
        endpoint_health={"endpoint": HealthRecord(HealthState.HEALTHY)},
        account_health={"account": HealthRecord(HealthState.HEALTHY)},
        account_limits={"account": 1},
    )

    def evaluate():
        return dispatch_policy.evaluate_task_delivery_admission(
            {}, {}, task, task[role], {}, active_task_ids=set(), pending_task_ids=set(),
            agent_loads={}, active_account_loads={}, pending_account_loads={},
        )

    with (
        mock.patch.object(dispatch_policy, "delivery_lane_for_agent", return_value=lane),
        mock.patch.object(dispatch_policy, "build_delivery_admission_snapshot", return_value=snapshot),
        mock.patch.object(dispatch_policy, "dependencies_satisfied", return_value=True),
        mock.patch.object(dispatch_policy.rewrite_task_machine, "delivery_binding_is_current", return_value=True),
        mock.patch.object(dispatch_policy, "review_decision_intent_replay_eligible", return_value=False),
    ):
        decision = evaluate()
        if status in {"review", "review_approved"}:
            assert decision.eligible
        else:
            assert not decision.eligible
            assert decision.reason.value == "execution_authorization_required"
        assert task == original
        task["waiting_for"] = "Claude"
        assert evaluate().reason.value == "human_hold"
        task["waiting_for"] = "Human/Ops"
        task["execution_authorization"]["old_runtime_hold"] = False
        assert evaluate().reason.value == "human_hold"
        task["execution_authorization"]["old_runtime_hold"] = True
        task["review_decision_intent"] = {"nonce": "unresolved-independent-review-decision"}
        assert evaluate().reason.value == "human_hold"


def test_task_has_current_canonical_integration_receipt_multirepo() -> None:
    config = {
        "coordination": {
            "repositories": {
                "execute-plans": {
                    "repo": "ajoe734/execute-plans",
                    "default_branch": "dev",
                }
            }
        }
    }
    head_sha = "598101a2b62395d4c39c19df619ebb4207ea8458"
    merge_sha = "8f8383b507b1fb631d44422031f01ebea5024d5e"
    task = {
        "id": "OPS-FE-REVIEW-PROOF-001",
        "status": "review_approved",
        "target_repo": "execute-plans",
        "generation": 1,
        "review_binding": {
            "pr": 747,
            "head_sha": head_sha,
            "head_branch": "task/OPS-FE-REVIEW-PROOF-001",
            "base": "dev",
        },
        "delivery_binding": {
            "kind": "pull_request",
            "pr": 747,
            "head_sha": head_sha,
            "head_branch": "task/OPS-FE-REVIEW-PROOF-001",
            "base": "dev",
        },
        "integration_receipt": {
            "version": 1,
            "result": "landed",
            "observation": "performed_merge",
            "task_generation": 1,
            "repository": "ajoe734/execute-plans",
            "target_branch": "dev",
            "pr": 747,
            "head_sha": head_sha,
            "merge_commit_sha": merge_sha,
            "observed_at": "2026-09-08T00:00:00Z",
            "source": "canonical_auto_integrator",
        },
    }

    assert dispatch_policy.task_has_current_canonical_integration_receipt(config, task) is True

    # Fail closed on missing or non-mapping
    assert dispatch_policy.task_has_current_canonical_integration_receipt(config, None) is False
    assert dispatch_policy.task_has_current_canonical_integration_receipt(config, {}) is False

    # Negative control: missing receipt
    t_no_receipt = deepcopy(task)
    del t_no_receipt["integration_receipt"]
    assert dispatch_policy.task_has_current_canonical_integration_receipt(config, t_no_receipt) is False

    # Negative control: malformed receipt version
    t_bad_ver = deepcopy(task)
    t_bad_ver["integration_receipt"]["version"] = 99
    assert dispatch_policy.task_has_current_canonical_integration_receipt(config, t_bad_ver) is False

    # Negative control: generation drift (receipt task_generation > current task generation)
    t_drift = deepcopy(task)
    t_drift["generation"] = 1
    t_drift["integration_receipt"]["task_generation"] = 2
    assert dispatch_policy.task_has_current_canonical_integration_receipt(config, t_drift) is False

    # Negative control: changed repository slug
    t_repo_drift = deepcopy(task)
    t_repo_drift["integration_receipt"]["repository"] = "ajoe734/pantheon"
    assert dispatch_policy.task_has_current_canonical_integration_receipt(config, t_repo_drift) is False

    # Negative control: changed target branch
    t_branch_drift = deepcopy(task)
    t_branch_drift["integration_receipt"]["target_branch"] = "main"
    assert dispatch_policy.task_has_current_canonical_integration_receipt(config, t_branch_drift) is False

    # Negative control: changed PR
    t_pr_drift = deepcopy(task)
    t_pr_drift["integration_receipt"]["pr"] = 999
    assert dispatch_policy.task_has_current_canonical_integration_receipt(config, t_pr_drift) is False

    # Negative control: changed head_sha
    t_sha_drift = deepcopy(task)
    t_sha_drift["integration_receipt"]["head_sha"] = "0" * 40
    assert dispatch_policy.task_has_current_canonical_integration_receipt(config, t_sha_drift) is False

    # Negative control: missing merge_commit_sha
    t_no_merge = deepcopy(task)
    t_no_merge["integration_receipt"]["merge_commit_sha"] = ""
    assert dispatch_policy.task_has_current_canonical_integration_receipt(config, t_no_merge) is False

    # Negative control: delivery binding mismatch
    t_mismatch = deepcopy(task)
    t_mismatch["delivery_binding"]["pr"] = 999
    assert dispatch_policy.task_has_current_canonical_integration_receipt(config, t_mismatch) is False

    # Negative control: unknown repository in registry
    t_unknown = deepcopy(task)
    t_unknown["target_repo"] = "nonexistent_repo"
    assert dispatch_policy.task_has_current_canonical_integration_receipt(config, t_unknown) is False

    # Negative control: conflicting repo artifacts
    t_conflict = deepcopy(task)
    t_conflict["artifacts"] = ["execute-plans/src/index.ts", "pantheon/api.py"]
    assert dispatch_policy.task_has_current_canonical_integration_receipt(config, t_conflict) is False


def test_is_non_default_repository_finalization_pending_cases() -> None:
    config = {
        "coordination": {
            "repositories": {
                "execute-plans": {
                    "repo": "ajoe734/execute-plans",
                    "default_branch": "dev",
                }
            }
        }
    }
    head_sha = "598101a2b62395d4c39c19df619ebb4207ea8458"
    merge_sha = "8f8383b507b1fb631d44422031f01ebea5024d5e"

    task_fe = {
        "id": "OPS-FE-REVIEW-PROOF-001",
        "status": "review_approved",
        "target_repo": "execute-plans",
        "generation": 1,
        "review_binding": {
            "pr": 747,
            "head_sha": head_sha,
            "head_branch": "task/OPS-FE-REVIEW-PROOF-001",
            "base": "dev",
        },
    }

    # Non-default repo, review_approved, no receipt -> pending (True)
    assert dispatch_policy.is_non_default_repository_finalization_pending(config, task_fe) is True

    # Non-default repo, with valid receipt -> reconciled, not pending (False)
    task_fe_reconciled = deepcopy(task_fe)
    task_fe_reconciled["integration_receipt"] = {
        "version": 1,
        "result": "landed",
        "observation": "performed_merge",
        "task_generation": 1,
        "repository": "ajoe734/execute-plans",
        "target_branch": "dev",
        "pr": 747,
        "head_sha": head_sha,
        "merge_commit_sha": merge_sha,
        "observed_at": "2026-09-08T00:00:00Z",
        "source": "canonical_auto_integrator",
    }
    assert dispatch_policy.is_non_default_repository_finalization_pending(config, task_fe_reconciled) is False

    # Normal unmerged Pantheon task (review_approved, no receipt) -> not suppressed (False)
    task_pantheon = {
        "id": "OPS-PAN-001",
        "status": "review_approved",
        "target_repo": "pantheon",
    }
    assert dispatch_policy.is_non_default_repository_finalization_pending(config, task_pantheon) is False

    # Non-review_approved task (e.g. in_progress) -> False
    task_in_progress = deepcopy(task_fe)
    task_in_progress["status"] = "in_progress"
    assert dispatch_policy.is_non_default_repository_finalization_pending(config, task_in_progress) is False

    # Unknown repository scope -> fails closed as pending (True)
    task_unknown = deepcopy(task_fe)
    task_unknown["target_repo"] = "nonexistent_repo"
    assert dispatch_policy.is_non_default_repository_finalization_pending(config, task_unknown) is True


def test_evaluate_task_delivery_admission_multirepo_gate() -> None:
    from rewrite.dispatch_admission import AdmissionSnapshot, DeliveryEndpoint, DispatchLane, HealthRecord, HealthState

    config = {
        "coordination": {
            "repositories": {
                "execute-plans": {
                    "repo": "ajoe734/execute-plans",
                    "default_branch": "dev",
                }
            }
        }
    }
    head_sha = "598101a2b62395d4c39c19df619ebb4207ea8458"
    merge_sha = "8f8383b507b1fb631d44422031f01ebea5024d5e"

    task_fe = {
        "id": "OPS-FE-REVIEW-PROOF-001",
        "status": "review_approved",
        "owner": "Codex",
        "target_repo": "execute-plans",
        "generation": 1,
        "review_binding": {
            "pr": 747,
            "head_sha": head_sha,
            "head_branch": "task/OPS-FE-REVIEW-PROOF-001",
            "base": "dev",
        },
        "delivery_binding": {
            "kind": "pull_request",
            "pr": 747,
            "head_sha": head_sha,
            "head_branch": "task/OPS-FE-REVIEW-PROOF-001",
            "base": "dev",
        },
    }

    lane = DispatchLane("test-lane", "Codex", 1, (DeliveryEndpoint("endpoint", "provider", "account"),))
    snapshot = AdmissionSnapshot(
        now=datetime.now(timezone.utc),
        endpoint_health={"endpoint": HealthRecord(HealthState.HEALTHY)},
        account_health={"account": HealthRecord(HealthState.HEALTHY)},
        account_limits={"account": 1},
    )

    with (
        mock.patch.object(dispatch_policy, "delivery_lane_for_agent", return_value=lane),
        mock.patch.object(dispatch_policy, "build_delivery_admission_snapshot", return_value=snapshot),
        mock.patch.object(dispatch_policy, "dependencies_satisfied", return_value=True),
        mock.patch.object(dispatch_policy.rewrite_task_machine, "delivery_binding_is_current", return_value=True),
        mock.patch.object(dispatch_policy, "review_decision_intent_replay_eligible", return_value=False),
    ):
        # 1. Unreceipted execute-plans task -> blocked
        dec_unreceipted = dispatch_policy.evaluate_task_delivery_admission(
            config, {}, task_fe, "Codex", {}, active_task_ids=set(), pending_task_ids=set(),
            agent_loads={}, active_account_loads={}, pending_account_loads={},
        )
        assert not dec_unreceipted.eligible
        assert dec_unreceipted.reason.value == "task_not_dispatchable"
        assert dec_unreceipted.task_reason.value == 1  # OWNED_FINALIZE

        # 2. Receipted execute-plans task -> admitted
        task_receipted = deepcopy(task_fe)
        task_receipted["integration_receipt"] = {
            "version": 1,
            "result": "landed",
            "observation": "performed_merge",
            "task_generation": 1,
            "repository": "ajoe734/execute-plans",
            "target_branch": "dev",
            "pr": 747,
            "head_sha": head_sha,
            "merge_commit_sha": merge_sha,
            "observed_at": "2026-09-08T00:00:00Z",
            "source": "canonical_auto_integrator",
        }
        dec_receipted = dispatch_policy.evaluate_task_delivery_admission(
            config, {}, task_receipted, "Codex", {}, active_task_ids=set(), pending_task_ids=set(),
            agent_loads={}, active_account_loads={}, pending_account_loads={},
        )
        assert dec_receipted.eligible
        assert dec_receipted.task_reason.value == 1

        # 3. Normal unmerged Pantheon task -> admitted
        task_pantheon = {
            "id": "OPS-PAN-001",
            "status": "review_approved",
            "owner": "Codex",
            "target_repo": "pantheon",
            "generation": 1,
            "review_binding": {
                "pr": 500,
                "head_sha": "a" * 40,
                "head_branch": "task/OPS-PAN-001",
                "base": "dev",
            },
        }
        dec_pantheon = dispatch_policy.evaluate_task_delivery_admission(
            config, {}, task_pantheon, "Codex", {}, active_task_ids=set(), pending_task_ids=set(),
            agent_loads={}, active_account_loads={}, pending_account_loads={},
        )
        assert dec_pantheon.eligible
        assert dec_pantheon.task_reason.value == 1

        # 4. evaluate_dispatch_candidate surfaces task_not_dispatchable
        cand_unreceipted = dispatch_policy.evaluate_dispatch_candidate(
            config, {}, {}, task_fe, "Codex", {},
            settings={}, active_task_ids=set(), pending_task_ids=set(),
            pending_event_keys=set(), agent_loads={}, active_account_loads={},
            pending_account_loads={}, seen_event_keys={}, checked_at="2026-09-08T00:00:00Z",
            cooldown_seconds=0,
        )
        assert not cand_unreceipted["eligible"]
        assert cand_unreceipted["first_blocking_gate"] == "task_not_dispatchable"

        # 5. Operator exact-head acceptance hold takes precedence over finalization
        task_op_accepted = operator_accepted_task(owner="Codex")
        dec_op_accepted = dispatch_policy.evaluate_task_delivery_admission(
            config, {}, task_op_accepted, "Codex", {}, active_task_ids=set(), pending_task_ids=set(),
            agent_loads={}, active_account_loads={}, pending_account_loads={},
        )
        assert not dec_op_accepted.eligible
        assert dec_op_accepted.reason.value == "human_hold"


@pytest.mark.parametrize(
    "repositories",
    [
        [],
        ["bad-entry"],
        {"execute_plans": 42},
        {"execute_plans": "bad"},
        {"execute_plans": None},
    ],
)
def test_malformed_registry_blocks_instead_of_crashing(repositories: Any) -> None:
    head_sha = "598101a2b62395d4c39c19df619ebb4207ea8458"
    task = {
        "id": "OPS-FE-REVIEW-PROOF-001",
        "status": "review_approved",
        "owner": "Codex",
        "target_repo": "execute-plans",
        "generation": 1,
        "review_binding": {
            "pr": 747,
            "head_sha": head_sha,
            "head_branch": "task/OPS-FE-REVIEW-PROOF-001",
            "base": "dev",
        },
        "delivery_binding": {
            "kind": "pull_request",
            "pr": 747,
            "head_sha": head_sha,
            "head_branch": "task/OPS-FE-REVIEW-PROOF-001",
            "base": "dev",
        },
    }
    config = {"coordination": {"repositories": repositories}}
    assert dispatch_policy.is_non_default_repository_finalization_pending(config, task) is True


@pytest.mark.parametrize(
    "repo_override",
    [
        {"default_branch": None},
        {"default_branch": ""},
        {"default_branch": 123},
        {"repo": None},
        {"repo": ""},
        {"repo": 456},
    ],
)
def test_misconfigured_repo_attributes_cannot_reconcile(repo_override: dict[str, Any]) -> None:
    head_sha = "598101a2b62395d4c39c19df619ebb4207ea8458"
    merge_sha = "fda58bb05052c90e0e18310666ad174b5ab3ff51"
    task = {
        "id": "OPS-FE-REVIEW-PROOF-001",
        "status": "review_approved",
        "owner": "Codex",
        "target_repo": "execute-plans",
        "generation": 1,
        "review_binding": {
            "pr": 747,
            "head_sha": head_sha,
            "head_branch": "task/OPS-FE-REVIEW-PROOF-001",
            "base": "dev",
        },
        "delivery_binding": {
            "kind": "pull_request",
            "pr": 747,
            "head_sha": head_sha,
            "head_branch": "task/OPS-FE-REVIEW-PROOF-001",
            "base": "dev",
        },
        "integration_receipt": {
            "version": 1,
            "result": "landed",
            "observation": "reconciled_existing_merge",
            "task_generation": 1,
            "repository": "ajoe734/execute-plans",
            "target_branch": "dev",
            "pr": 747,
            "head_sha": head_sha,
            "merge_commit_sha": merge_sha,
            "observed_at": "2026-09-08T05:00:00Z",
            "source": "canonical_auto_integrator",
        },
    }
    config = {"coordination": {"repositories": {"execute_plans": repo_override}}}
    assert not dispatch_policy.task_has_current_canonical_integration_receipt(config, task)
    assert dispatch_policy.is_non_default_repository_finalization_pending(config, task) is True


def test_consumer_and_dispatch_agree_on_same_canonical_receipt() -> None:
    head_sha = "598101a2b62395d4c39c19df619ebb4207ea8458"
    merge_sha = "fda58bb05052c90e0e18310666ad174b5ab3ff51"
    task = {
        "id": "OPS-FE-REVIEW-PROOF-001",
        "status": "review_approved",
        "owner": "Codex",
        "target_repo": "execute-plans",
        "generation": 1,
        "review_binding": {
            "pr": 747,
            "head_sha": head_sha,
            "head_branch": "task/OPS-FE-REVIEW-PROOF-001",
            "base": "dev",
        },
        "delivery_binding": {
            "kind": "pull_request",
            "pr": 747,
            "head_sha": head_sha,
            "head_branch": "task/OPS-FE-REVIEW-PROOF-001",
            "base": "dev",
        },
        "integration_receipt": {
            "version": 1,
            "result": "landed",
            "observation": "reconciled_existing_merge",
            "task_generation": 1,
            "repository": "ajoe734/execute-plans",
            "target_branch": "dev",
            "pr": 747,
            "head_sha": head_sha,
            "merge_commit_sha": merge_sha,
            "observed_at": "2026-09-08T05:00:00Z",
            "source": "canonical_auto_integrator",
        },
    }

    # With default / empty config
    assert (
        dispatch_policy.task_has_current_canonical_integration_receipt({}, task)
        == integration_receipt.integration_receipt_consumes_candidate(task)
        is True
    )

    # With explicit coordination config
    config = {
        "coordination": {
            "repositories": {
                "execute_plans": {
                    "repo": "ajoe734/execute-plans",
                    "default_branch": "dev",
                }
            }
        }
    }
    assert (
        dispatch_policy.task_has_current_canonical_integration_receipt(config, task)
        == integration_receipt.integration_receipt_consumes_candidate(task, config=config)
        is True
    )

    # Negative control: when receipt is absent
    t_no_receipt = deepcopy(task)
    del t_no_receipt["integration_receipt"]
    assert (
        dispatch_policy.task_has_current_canonical_integration_receipt(config, t_no_receipt)
        == integration_receipt.integration_receipt_consumes_candidate(t_no_receipt, config=config)
        is False
    )
