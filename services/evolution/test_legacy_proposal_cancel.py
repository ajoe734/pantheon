"""Unit tests for services/evolution/legacy_proposal_cancel.py.

Covers:
1. Default-off refusal unless PANTHEON_EVOLUTION_LEGACY_CANCEL_ENABLED=true.
2. Multi-tenant (pantheon-default + tenant-dev) plan + apply cancelling only
   pre-cutoff proposed rows while leaving post-cutoff proposed and pre-cutoff
   approved/executed/canceled rows untouched (outside_digest equal), including
   under EVOLUTION_AUTH_MODE=token + EVOLUTION_AUTH_ALLOWED_TENANTS=tenant-dev.
3. Idempotent re-run with the same selection_digest.
4. Digest mismatch refusal before mutating anything.
5. Live EvolutionDecisionStore reload visibility via _file_signature().
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Isolate evolution data dir before importing services.evolution.main
_TMP_DIR = tempfile.mkdtemp(prefix="evo_legacy_cancel_test_")
os.environ.setdefault("EVOLUTION_DATA_DIR", _TMP_DIR)
os.environ.setdefault("INCIDENT_DATA_DIR", _TMP_DIR)

_CP_GOV = Path(__file__).resolve().parent.parent / "control-plane" / "governance"
if str(_CP_GOV) not in sys.path:
    sys.path.insert(0, str(_CP_GOV))

from approval_decision import EvidenceRef, EvidenceRefType  # noqa: E402
from evolution_decision import (  # noqa: E402
    EvolutionDecision,
    EvolutionDecisionStore,
    ExecutionPlane,
    ExecutionResult,
    ExecutionStatus,
)
from services.evolution import legacy_proposal_cancel as lpc  # noqa: E402
from services.evolution import main as evo_main  # noqa: E402

PRE_CUTOFF_EARLY = "2026-10-05T10:15:00Z"
PRE_CUTOFF_EXACT = "2026-11-10T03:43:51.369000+00:00"
POST_CUTOFF = "2026-11-10T03:43:52.000000+00:00"


def _make_decision(
    decision_id: str,
    *,
    tenant_id: str,
    created_at: str,
    target_id: str | None = None,
    target_type: str = "candidate_artifact",
    action_type: str = "revalidate",
) -> EvolutionDecision:
    decision = EvolutionDecision.create_proposed(
        decision_id=decision_id,
        tenant_id=tenant_id,
        target_type=target_type,
        target_id=target_id or f"target-{decision_id}",
        target_version="1.0.0",
        action_type=action_type,
        rationale=f"Test decision {decision_id}",
        created_by_id="evolution-controller",
        created_by_role="evolution_controller",
        evidence_refs=[
            EvidenceRef(
                ref_type=EvidenceRefType.DRIFT_REPORT,
                ref_id=f"ev-{decision_id}",
            )
        ],
    )
    decision.created_at = created_at
    return decision


@pytest.fixture()
def isolated_store(tmp_path, monkeypatch):
    storage_path = tmp_path / "decisions.json"
    store = EvolutionDecisionStore(storage_path=str(storage_path), backend="json")
    monkeypatch.setattr(evo_main, "store", store)
    return store


def test_default_off_refusal(isolated_store, monkeypatch, capsys):
    monkeypatch.delenv(lpc.ENABLED_ENV, raising=False)
    isolated_store.put(
        _make_decision(
            "dec-default-off",
            tenant_id="pantheon-default",
            created_at=PRE_CUTOFF_EARLY,
        )
    )

    with pytest.raises(lpc.LegacyProposalCancelError, match="off by default"):
        lpc.build_plan(isolated_store)

    with pytest.raises(lpc.LegacyProposalCancelError, match="off by default"):
        lpc.apply_plan(isolated_store, expected_digest="sha256:" + "0" * 64)

    monkeypatch.setenv(lpc.ENABLED_ENV, "false")
    assert lpc.main(["plan"]) == 2
    err_plan = json.loads(capsys.readouterr().err)
    assert "off by default" in err_plan["error"]

    assert lpc.main(["apply", "--digest", "sha256:" + "0" * 64]) == 2
    err_apply = json.loads(capsys.readouterr().err)
    assert "off by default" in err_apply["error"]

    # Nothing was mutated
    stored = isolated_store.get("dec-default-off")
    assert stored is not None
    assert stored.decision_state == "proposed"
    assert stored.review_chain == []


def test_multi_tenant_plan_and_apply_preserves_outside_decisions(
    isolated_store, monkeypatch, capsys
):
    monkeypatch.setenv(lpc.ENABLED_ENV, "true")
    # Simulate Hosted Dev token auth where external HTTP rejects pantheon-default
    monkeypatch.setenv("EVOLUTION_AUTH_MODE", "token")
    monkeypatch.setenv("EVOLUTION_AUTH_TOKEN", "test-secret-token")
    monkeypatch.setenv("EVOLUTION_AUTH_ALLOWED_TENANTS", "tenant-dev")

    # 1. Pre-cutoff proposed decisions across pantheon-default and tenant-dev (SHOULD BE CANCELLED)
    d_def_1 = _make_decision(
        "dec-def-legacy-1",
        tenant_id="pantheon-default",
        created_at=PRE_CUTOFF_EARLY,
    )
    d_def_2 = _make_decision(
        "dec-def-legacy-boundary",
        tenant_id="pantheon-default",
        created_at=PRE_CUTOFF_EXACT,
    )
    d_dev_1 = _make_decision(
        "dec-dev-legacy-1",
        tenant_id="tenant-dev",
        created_at=PRE_CUTOFF_EARLY,
    )

    # 2. Pre-cutoff already canceled decision citing task_id (SELECTED IN PLAN, SKIPPED IN APPLY)
    d_dev_already = _make_decision(
        "dec-dev-already-task-canceled",
        tenant_id="tenant-dev",
        created_at=PRE_CUTOFF_EARLY,
    )
    d_dev_already.cancel(
        actor_role="operator",
        actor_id=f"operator-maintenance:{lpc.DEFAULT_TASK_ID}",
        note=f"Prior batch cancel per {lpc.DEFAULT_TASK_ID}",
        actor_tenant_id="tenant-dev",
    )

    # 3. Outside decisions: post-cutoff proposed, pre-cutoff approved/executed/unrelated-canceled
    d_post_dev = _make_decision(
        "dec-post-cutoff-dev",
        tenant_id="tenant-dev",
        created_at=POST_CUTOFF,
    )
    d_post_def = _make_decision(
        "dec-post-cutoff-def",
        tenant_id="pantheon-default",
        created_at=POST_CUTOFF,
    )

    d_pre_approved = _make_decision(
        "dec-pre-approved",
        tenant_id="tenant-dev",
        created_at=PRE_CUTOFF_EARLY,
    )
    d_pre_approved.mark_reviewed(
        actor_role="reviewer_on_duty",
        actor_id="rev-1",
        approval_decision_id="appr-1",
        actor_tenant_id="tenant-dev",
    )
    d_pre_approved.approve(
        actor_role="reviewer_on_duty",
        actor_id="rev-1",
        actor_tenant_id="tenant-dev",
    )

    d_pre_executed = _make_decision(
        "dec-pre-executed",
        tenant_id="pantheon-default",
        created_at=PRE_CUTOFF_EARLY,
    )
    d_pre_executed.mark_reviewed(
        actor_role="reviewer_on_duty",
        actor_id="rev-2",
        approval_decision_id="appr-2",
        actor_tenant_id="pantheon-default",
    )
    d_pre_executed.approve(
        actor_role="reviewer_on_duty",
        actor_id="rev-2",
        actor_tenant_id="pantheon-default",
    )
    d_pre_executed.execute(
        actor_role="evolution_controller",
        actor_id="ctrl-1",
        execution_result=ExecutionResult(
            status=ExecutionStatus.SUCCEEDED,
            plane=ExecutionPlane.RESEARCH,
            executed_at="2026-10-06T00:00:00Z",
            execution_ref_id="run-123",
            outcome_summary="completed",
        ),
        cooldown_started_at="2026-10-06T00:00:00Z",
        cooldown_ends_at="2026-10-07T00:00:00Z",
        observation_window_started_at="2026-10-06T00:00:00Z",
        observation_window_ends_at="2026-10-08T00:00:00Z",
        actor_tenant_id="pantheon-default",
    )

    d_pre_other_canceled = _make_decision(
        "dec-pre-unrelated-canceled",
        tenant_id="pantheon-default",
        created_at=PRE_CUTOFF_EARLY,
    )
    d_pre_other_canceled.cancel(
        actor_role="operator",
        actor_id="manual-op",
        note="Unrelated manual cancellation before task",
        actor_tenant_id="pantheon-default",
    )

    for item in (
        d_def_1,
        d_def_2,
        d_dev_1,
        d_dev_already,
        d_post_dev,
        d_post_def,
        d_pre_approved,
        d_pre_executed,
        d_pre_other_canceled,
    ):
        isolated_store.put(item)

    # Confirm external HTTP rejects pantheon-default under EVOLUTION_AUTH_ALLOWED_TENANTS=tenant-dev
    client = TestClient(evo_main.app)
    http_resp = client.post(
        "/api/evolution/proposals/dec-def-legacy-1/cancel",
        headers={
            "Authorization": "Bearer test-secret-token",
            "X-Tenant-Id": "pantheon-default",
        },
        json={
            "actor_role": "operator",
            "actor_id": "external-http",
            "note": "attempt via HTTP",
            "tenant_id": "pantheon-default",
        },
    )
    assert http_resp.status_code == 403
    assert isolated_store.get("dec-def-legacy-1").decision_state == "proposed"

    # Run CLI plan
    assert lpc.main(["plan"]) == 0
    plan_out = json.loads(capsys.readouterr().out)
    assert plan_out["scanned_count"] == 9
    assert plan_out["selected_count"] == 4
    assert plan_out["to_cancel_count"] == 3
    assert plan_out["already_canceled_count"] == 1
    assert plan_out["outside_count"] == 5
    assert plan_out["selected_ids"] == [
        "dec-def-legacy-1",
        "dec-def-legacy-boundary",
        "dec-dev-already-task-canceled",
        "dec-dev-legacy-1",
    ]
    assert plan_out["counts_by_tenant"] == {
        "pantheon-default": 2,
        "tenant-dev": 2,
    }
    assert plan_out["counts_by_state"] == {
        "canceled": 1,
        "proposed": 3,
    }
    assert plan_out["counts_by_tenant_and_state"] == {
        "pantheon-default": {"proposed": 2},
        "tenant-dev": {"canceled": 1, "proposed": 1},
    }
    assert plan_out["selection_digest"].startswith("sha256:")
    assert plan_out["outside_digest"].startswith("sha256:")
    for row in plan_out["decisions"]:
        assert set(row.keys()) == {
            "decision_id",
            "tenant_id",
            "target_type",
            "target_id",
            "action_type",
            "decision_state",
            "created_at",
        }

    # Spy on cancel_proposal to ensure apply routes every cancellation through it
    calls: list[tuple[str, str, str]] = []
    real_cancel = evo_main.cancel_proposal

    def tracked_cancel(decision_id, body):
        calls.append((decision_id, evo_main._current_tenant(), body.tenant_id))
        return real_cancel(decision_id, body)

    monkeypatch.setattr(evo_main, "cancel_proposal", tracked_cancel)

    # Run CLI apply with exact selection_digest
    digest = plan_out["selection_digest"]
    outside_digest_before = plan_out["outside_digest"]
    assert lpc.main(["apply", "--digest", digest]) == 0
    apply_out = json.loads(capsys.readouterr().out)

    assert apply_out["complete"] is True
    assert apply_out["canceled_count"] == 3
    assert apply_out["skipped_already_canceled_count"] == 1
    assert apply_out["remaining_legacy_proposed_count"] == 0
    assert apply_out["selection_digest"] == digest
    assert apply_out["outside_digest"] == outside_digest_before
    assert apply_out["outside_digest_unchanged"] is True
    assert len(apply_out["receipts"]) == 3

    # Verify cancel_proposal was called with _TENANT_CONTEXT bound to each decision's tenant
    assert calls == [
        ("dec-def-legacy-1", "pantheon-default", "pantheon-default"),
        ("dec-def-legacy-boundary", "pantheon-default", "pantheon-default"),
        ("dec-dev-legacy-1", "tenant-dev", "tenant-dev"),
    ]
    # And _TENANT_CONTEXT was reset after apply
    assert evo_main._current_tenant() == "pantheon-default"

    for receipt in apply_out["receipts"]:
        assert receipt["decision_state"] == "canceled"
        assert receipt["is_active"] is False
        last_step = receipt["review_chain"][-1]
        assert last_step["step_type"] == "canceled"
        assert last_step["actor_role"] == "operator"
        assert lpc.DEFAULT_TASK_ID in last_step["note"]

    # Verify outside decisions are completely untouched
    assert isolated_store.get("dec-post-cutoff-dev").decision_state == "proposed"
    assert isolated_store.get("dec-post-cutoff-def").decision_state == "proposed"
    assert isolated_store.get("dec-pre-approved").decision_state == "approved"
    assert isolated_store.get("dec-pre-executed").decision_state == "executed"
    unrelated = isolated_store.get("dec-pre-unrelated-canceled")
    assert unrelated.decision_state == "canceled"
    assert len(unrelated.review_chain) == 1
    assert unrelated.review_chain[0].actor_id == "manual-op"


def test_idempotent_rerun_with_same_digest(isolated_store, monkeypatch):
    monkeypatch.setenv(lpc.ENABLED_ENV, "true")
    isolated_store.put(
        _make_decision(
            "dec-idem-1",
            tenant_id="pantheon-default",
            created_at=PRE_CUTOFF_EARLY,
        )
    )
    isolated_store.put(
        _make_decision(
            "dec-idem-2",
            tenant_id="tenant-dev",
            created_at=PRE_CUTOFF_EARLY,
        )
    )
    isolated_store.put(
        _make_decision(
            "dec-idem-outside",
            tenant_id="tenant-dev",
            created_at=POST_CUTOFF,
        )
    )

    plan1 = lpc.build_plan(isolated_store)
    assert plan1.to_cancel_count == 2
    assert plan1.already_canceled_count == 0

    first_apply = lpc.apply_plan(
        isolated_store, expected_digest=plan1.selection_digest
    )
    assert first_apply["canceled_count"] == 2
    assert first_apply["skipped_already_canceled_count"] == 0
    assert first_apply["remaining_legacy_proposed_count"] == 0

    # Re-running plan keeps the exact same selection_digest and outside_digest
    plan2 = lpc.build_plan(isolated_store)
    assert plan2.selection_digest == plan1.selection_digest
    assert plan2.outside_digest == plan1.outside_digest
    assert plan2.to_cancel_count == 0
    assert plan2.already_canceled_count == 2

    # Re-running apply with the same digest is a clean no-op
    second_apply = lpc.apply_plan(
        isolated_store, expected_digest=plan1.selection_digest
    )
    assert second_apply["complete"] is True
    assert second_apply["canceled_count"] == 0
    assert second_apply["skipped_already_canceled_count"] == 2
    assert second_apply["remaining_legacy_proposed_count"] == 0
    assert second_apply["outside_digest"] == plan1.outside_digest
    assert second_apply["receipts"] == []


def test_digest_mismatch_fails_closed_before_mutation(
    isolated_store, monkeypatch, capsys
):
    monkeypatch.setenv(lpc.ENABLED_ENV, "true")
    isolated_store.put(
        _make_decision(
            "dec-mismatch-1",
            tenant_id="pantheon-default",
            created_at=PRE_CUTOFF_EARLY,
        )
    )

    wrong_digest = "sha256:" + "f" * 64
    with pytest.raises(lpc.LegacyProposalCancelError, match="selection digest mismatch"):
        lpc.apply_plan(isolated_store, expected_digest=wrong_digest)

    assert lpc.main(["apply", "--digest", wrong_digest]) == 2
    err = json.loads(capsys.readouterr().err)
    assert "selection digest mismatch" in err["error"]

    # Decision remains proposed and unmutated
    decision = isolated_store.get("dec-mismatch-1")
    assert decision is not None
    assert decision.decision_state == "proposed"
    assert decision.review_chain == []


def test_live_store_reload_visibility(tmp_path, monkeypatch):
    monkeypatch.setenv(lpc.ENABLED_ENV, "true")
    decisions_path = tmp_path / "decisions.json"

    # Separate EvolutionDecisionStore instance simulating the live running service
    live_service_store = EvolutionDecisionStore(
        storage_path=str(decisions_path), backend="json"
    )
    live_service_store.put(
        _make_decision(
            "dec-live-reload-default",
            tenant_id="pantheon-default",
            created_at=PRE_CUTOFF_EARLY,
        )
    )
    live_service_store.put(
        _make_decision(
            "dec-live-reload-post",
            tenant_id="tenant-dev",
            created_at=POST_CUTOFF,
        )
    )
    # Prime live_service_store's in-memory cache and _loaded_signature
    assert (
        live_service_store.get("dec-live-reload-default").decision_state == "proposed"
    )
    initial_signature = live_service_store._loaded_signature
    assert initial_signature is not None

    # Separate EvolutionDecisionStore instance used by the one-shot CLI process
    cli_store = EvolutionDecisionStore(
        storage_path=str(decisions_path), backend="json"
    )
    monkeypatch.setattr(evo_main, "store", cli_store)

    plan = lpc.build_plan(cli_store)
    result = lpc.apply_plan(cli_store, expected_digest=plan.selection_digest)
    assert result["canceled_count"] == 1
    assert result["remaining_legacy_proposed_count"] == 0

    # Without restarting or recreating live_service_store, reads automatically
    # detect the new _file_signature() and reload the cancelled state.
    reloaded = live_service_store.get("dec-live-reload-default")
    assert reloaded is not None
    assert reloaded.decision_state == "canceled"
    assert len(reloaded.review_chain) == 1
    assert lpc.DEFAULT_TASK_ID in (reloaded.review_chain[0].note or "")
    assert live_service_store._loaded_signature != initial_signature

    active_proposed = live_service_store.list_all(
        tenant_id=None, decision_states=["proposed"]
    )
    assert [d.decision_id for d in active_proposed] == ["dec-live-reload-post"]


def test_invalid_arguments_and_tenant_context_reset_on_error(
    isolated_store, monkeypatch
):
    monkeypatch.setenv(lpc.ENABLED_ENV, "true")

    with pytest.raises(lpc.LegacyProposalCancelError, match="valid ISO-8601"):
        lpc.build_plan(isolated_store, before="not-a-timestamp")

    with pytest.raises(lpc.LegacyProposalCancelError, match="task_id must not be empty"):
        lpc.build_plan(isolated_store, task_id="   ")

    isolated_store.put(
        _make_decision(
            "dec-err-reset",
            tenant_id="tenant-custom",
            created_at=PRE_CUTOFF_EARLY,
        )
    )
    plan = lpc.build_plan(isolated_store)

    def failing_cancel(decision_id, body):
        assert evo_main._current_tenant() == "tenant-custom"
        raise RuntimeError("simulated cancel error")

    monkeypatch.setattr(evo_main, "cancel_proposal", failing_cancel)
    with pytest.raises(RuntimeError, match="simulated cancel error"):
        lpc.apply_plan(isolated_store, expected_digest=plan.selection_digest)

    # _TENANT_CONTEXT was reset in finally despite the error
    assert evo_main._current_tenant() == "pantheon-default"

