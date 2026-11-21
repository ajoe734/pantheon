"""Unit tests for the first-class RiskPolicy evaluator contract."""
from __future__ import annotations

from services.capital.risk_policy import (
    RiskPolicy,
    RiskPolicyDecision,
    RiskPolicyEvaluationContext,
    RiskPolicyEvaluator,
    RiskPolicyTargetType,
)


def test_risk_policy_rejects_exposure_asset_and_liquidity_breaches() -> None:
    policy = RiskPolicy(
        risk_policy_id="risk-main",
        gross_limit=1.5,
        forbidden_asset_classes=("crypto",),
        liquidity_constraints={"min_avg_daily_volume": 1_000_000},
    )

    evaluation = RiskPolicyEvaluator().evaluate(
        policy,
        RiskPolicyEvaluationContext(
            target_type=RiskPolicyTargetType.ALLOCATION_PROPOSAL.value,
            target_id="proposal-001",
            capital_pool_id="pool-001",
            gross_exposure=1.8,
            asset_classes=("crypto",),
            liquidity={"avg_daily_volume": 250_000},
        ),
    )

    assert evaluation.decision == RiskPolicyDecision.REJECTED.value
    assert {check.code for check in evaluation.checks if check.status == "failed"} == {
        "forbidden_asset_class",
        "gross_exposure_limit_exceeded",
        "liquidity_min_adv_breach",
    }


def test_risk_evaluation_propagates_trade_envelope() -> None:
    from services.trade_journey.correlation_envelope import mint_trade_envelope
    incoming = mint_trade_envelope({"tenant_id": "tenant-1", "environment": "paper"}, producer="execution.signal-decision")
    evaluation = RiskPolicyEvaluator().evaluate(
        RiskPolicy(risk_policy_id="risk-main"),
        RiskPolicyEvaluationContext(target_type="order", target_id="order-1", capital_pool_id="pool-1", correlation_envelope=incoming),
    )
    outgoing = evaluation.to_dict()["correlation_envelope"]
    assert outgoing["journey_id"] == incoming["journey_id"]
    assert outgoing["causation_event_id"] == incoming["event_id"]
    assert outgoing["producer"] == "risk.evaluation"


def test_canary_scale_and_kill_switch_are_evaluable_limits() -> None:
    policy = RiskPolicy(
        risk_policy_id="risk-main",
        max_canary_capital_scale_pct=2.0,
        max_canary_gross_scale_pct=10.0,
        kill_switch_triggers=("operator_emergency_stop",),
    )

    evaluation = RiskPolicyEvaluator().evaluate(
        policy,
        {
            "target_type": "runtime_launch",
            "target_id": "rt-001",
            "capital_pool_id": "pool-001",
            "stage": "canary",
            "capital_scale_pct": 5.0,
            "gross_scale_pct": 25.0,
            "kill_switch_trigger": "operator_emergency_stop",
        },
    )

    assert evaluation.rejected is True
    assert [check.code for check in evaluation.checks if check.status == "failed"] == [
        "canary_capital_scale_pct_limit_exceeded",
        "canary_gross_scale_pct_limit_exceeded",
        "kill_switch_triggered",
    ]


def test_drawdown_warn_allows_with_conditions() -> None:
    policy = RiskPolicy(
        risk_policy_id="risk-main",
        drawdown_actions={"warn": 0.03, "risk_off": 0.08, "liquidate": 0.15},
    )

    evaluation = RiskPolicyEvaluator().evaluate(
        policy,
        {
            "target_type": "runtime_action",
            "target_id": "risk-scan-001",
            "capital_pool_id": "pool-001",
            "drawdown_pct": 0.05,
        },
    )

    assert evaluation.decision == RiskPolicyDecision.ALLOWED_WITH_CONDITIONS.value
    assert evaluation.warnings


def test_homogeneity_and_correlation_limits_are_hard_vetoes() -> None:
    policy = RiskPolicy(
        risk_policy_id="risk-main",
        max_strategy_family_concentration=0.65,
        max_target_overlap=0.8,
        max_signal_correlation=0.9,
    )

    evaluation = RiskPolicyEvaluator().evaluate(
        policy,
        RiskPolicyEvaluationContext(
            target_type=RiskPolicyTargetType.ALLOCATION_PROPOSAL.value,
            target_id="allocation-gate-review-001",
            capital_pool_id="pool-001",
            strategy_family_concentration={"mega_cap_momentum": 0.9},
            target_overlap=0.95,
            signal_correlation=0.97,
        ),
    )

    assert evaluation.rejected is True
    assert {check.code for check in evaluation.checks if check.status == "failed"} == {
        "strategy_family_concentration_limit_exceeded",
        "target_overlap_limit_exceeded",
        "signal_correlation_limit_exceeded",
    }


def test_runtime_action_future_order_gate_enforces_2x_gross_limit_rejecting_2_13_and_accepting_1_85() -> None:
    policy = RiskPolicy.from_mapping(
        {
            "risk_policy_id": "rp-pool-persona-paper-001",
            "version": "1.0",
            "status": "active",
            "allowed_stages": ["paper"],
            "gross_limit": 2.0,
            "net_limit": 2.0,
            "max_leverage": 2.0,
            "allowed_asset_classes": ["equity", "future"],
        }
    )
    assert policy.gross_limit == 2.0
    assert policy.max_leverage == 2.0
    assert policy.allowed_asset_classes == ("equity", "future")

    rejected_ctx = RiskPolicyEvaluationContext(
        target_type="runtime_action",
        target_id="dec-qff-2330-short",
        capital_pool_id="pool-persona-paper-001",
        stage="paper",
        gross_exposure=2.13,
        net_exposure=-2.13,
        leverage=2.13,
        asset_classes=["future"],
        decision_id="dec-qff-2330-short",
        instrument_id="ssf:QFF:202611",
    )
    assert rejected_ctx.asset_classes == ("future",)
    assert rejected_ctx.decision_id == "dec-qff-2330-short"
    assert rejected_ctx.instrument_id == "ssf:QFF:202611"

    rejected_eval = RiskPolicyEvaluator().evaluate(policy, rejected_ctx)
    assert rejected_eval.rejected is True
    assert rejected_eval.decision == RiskPolicyDecision.REJECTED.value
    assert {check.code for check in rejected_eval.checks if check.status == "failed"} == {
        "gross_exposure_limit_exceeded",
        "net_exposure_limit_exceeded",
        "leverage_limit_exceeded",
    }

    accepted_ctx = RiskPolicyEvaluationContext.from_mapping(
        {
            "target_type": "runtime_action",
            "decision_id": "dec-srf-0050-long",
            "instrument_id": "ssf:SRF:202611",
            "capital_pool_id": "pool-persona-paper-001",
            "stage": "paper",
            "gross_exposure": 1.85,
            "net_exposure": 1.85,
            "leverage": 1.85,
            "asset_classes": ["future"],
        }
    )
    assert accepted_ctx.target_id == "dec-srf-0050-long"
    assert accepted_ctx.asset_classes == ("future",)
    assert accepted_ctx.decision_id == "dec-srf-0050-long"
    assert accepted_ctx.instrument_id == "ssf:SRF:202611"

    accepted_eval = RiskPolicyEvaluator().evaluate(policy, accepted_ctx)
    assert accepted_eval.allowed is True
    assert accepted_eval.decision == RiskPolicyDecision.ALLOWED.value
    assert accepted_eval.blocking_reasons == ()


def test_stdlib_risk_policy_loader_and_bound_pool_resolver(tmp_path, monkeypatch) -> None:
    import json
    import pytest
    from services.capital.risk_policy import (
        RiskPolicyLoadError,
        load_risk_policy,
        normalize_risk_policy,
        read_capital_pool_record,
        resolve_bound_pool_risk_policy,
    )

    policy_dir = tmp_path / "policies"
    policy_dir.mkdir()
    (policy_dir / "rp-paper-futures.json").write_text(
        json.dumps(
            {
                "risk_policy_id": "rp-paper-futures",
                "version": "1.0",
                "status": "active",
                "allowed_stages": ["paper"],
                "allowed_asset_classes": ["equity", "future"],
                "exposure_limits": {
                    "gross_limit": 2.0,
                    "net_limit": 2.0,
                    "max_leverage": 2.0,
                    "max_single_name_weight": 0.5,
                },
            }
        ),
        encoding="utf-8",
    )
    pool_store = tmp_path / "capital_pools.json"
    pool_store.write_text(
        json.dumps(
            {
                "pools": [
                    {
                        "pool_id": "pool-paper-01",
                        "risk_policy_ref": "rp-paper-futures",
                        "status": "active",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CAPITAL_RISK_POLICY_DIR", str(policy_dir))
    monkeypatch.setenv("PANTHEON_CAPITAL_POOL_STORE_PATH", str(pool_store))

    raw = load_risk_policy("rp-paper-futures")
    assert raw["gross_limit"] == 2.0
    assert raw["max_single_name_weight"] == 0.5

    pool_rec = read_capital_pool_record("pool-paper-01")
    assert pool_rec is not None
    assert pool_rec["risk_policy_ref"] == "rp-paper-futures"

    resolved = resolve_bound_pool_risk_policy(capital_pool_id="pool-paper-01")
    assert resolved is not None
    assert resolved.risk_policy_id == "rp-paper-futures"
    assert resolved.gross_limit == 2.0
    assert resolved.max_single_name_weight == 0.5

    norm = normalize_risk_policy({"exposure_limits": {"gross_limit": 1.5}}, default_id="rp-default")
    assert norm.risk_policy_id == "rp-default"
    assert norm.gross_limit == 1.5

    with pytest.raises(RiskPolicyLoadError):
        load_risk_policy("missing-policy-ref")


def test_bound_pool_resolver_rejects_whole_pool_fallback_and_empty_inline_policy() -> None:
    import pytest
    from services.capital.risk_policy import (
        RiskPolicyError,
        resolve_bound_pool_risk_policy,
        resolve_pool_risk_policy_binding,
    )

    pool_without_policy = {
        "pool_id": "pool-no-policy",
        "tenant_id": "tenant-dev",
        "status": "active",
        "currency": "TWD",
        "budget": 100000.0,
        "single_runtime_enforced": True,
        "metadata": {"persona_id": "persona-1"},
    }
    assert (
        resolve_bound_pool_risk_policy(
            capital_pool_id="pool-no-policy",
            capital_pool_reader=lambda _: pool_without_policy,
        )
        is None
    )
    missing_res = resolve_pool_risk_policy_binding(
        capital_pool_id="pool-no-policy",
        pool=pool_without_policy,
        expected_tenant_id="tenant-dev",
    )
    assert missing_res.resolved is False
    assert missing_res.status == "missing"
    assert missing_res.reason_code == "risk_policy_missing"
    assert missing_res.allow_new_or_increased_risk is False
    assert missing_res.allow_risk_reducing_actions is True

    pool_with_empty_inline = {
        **pool_without_policy,
        "metadata": {"persona_id": "persona-1", "risk_policy": {}},
    }
    with pytest.raises(RiskPolicyError, match="risk_policy_invalid"):
        resolve_bound_pool_risk_policy(
            capital_pool_id="pool-no-policy",
            capital_pool_reader=lambda _: pool_with_empty_inline,
        )
    invalid_res = resolve_pool_risk_policy_binding(
        capital_pool_id="pool-no-policy",
        pool=pool_with_empty_inline,
        expected_tenant_id="tenant-dev",
    )
    assert invalid_res.resolved is False
    assert invalid_res.status == "invalid"
    assert invalid_res.reason_code == "risk_policy_invalid"
    assert invalid_res.allow_new_or_increased_risk is False
    assert invalid_res.allow_risk_reducing_actions is True


def test_resolve_pool_risk_policy_binding_covers_missing_invalid_stale_wrong_tenant_valid_explicit_and_owner_decision() -> None:
    import importlib.util
    from pathlib import Path
    import pytest
    from services.capital.risk_policy import (
        RiskPolicyError,
        resolve_bound_pool_risk_policy,
        resolve_pool_risk_policy_binding,
        risk_policy_digest,
    )

    gov_path = Path(__file__).resolve().parents[1] / "control-plane" / "governance" / "risk_policy.py"
    spec = importlib.util.spec_from_file_location("governance_risk_policy", gov_path)
    assert spec is not None and spec.loader is not None
    gov_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gov_mod)
    assert gov_mod.resolve_pool_risk_policy_binding is resolve_pool_risk_policy_binding
    assert gov_mod.risk_policy_digest is risk_policy_digest

    valid_inline_policy = {
        "risk_policy_id": "paper-inline-policy:persona-alpha",
        "version": "1.0",
        "status": "active",
        "allowed_stages": ["paper"],
        "gross_limit": 2.0,
        "net_limit": 2.0,
        "max_leverage": 2.0,
        "allowed_asset_classes": ["equity", "future"],
    }
    valid_pool = {
        "pool_id": "pool-valid",
        "tenant_id": "tenant-a",
        "status": "active",
        "budget": 100000.0,
        "currency": "TWD",
        "metadata": {"risk_policy": dict(valid_inline_policy)},
    }
    valid_res = resolve_pool_risk_policy_binding(
        capital_pool_id="pool-valid",
        pool=valid_pool,
        expected_tenant_id="tenant-a",
        expected_stage="paper",
    )
    assert valid_res.resolved is True
    assert valid_res.status == "valid"
    assert valid_res.profile_mapping_status == "explicit_policy"
    assert valid_res.risk_policy_id == "paper-inline-policy:persona-alpha"
    assert valid_res.risk_policy_version == "1.0"
    assert valid_res.risk_policy_digest == risk_policy_digest(valid_inline_policy)
    assert valid_res.effective_limits["gross_limit"] == 2.0
    assert valid_res.allow_new_or_increased_risk is True

    # Explicit policy contract preserved with custom max_single_name_weight and drawdown_actions
    explicit_policy = {
        "risk_policy_id": "rp-explicit-custom-v2",
        "version": "2.1",
        "status": "active",
        "tenant_id": "tenant-a",
        "allowed_stages": ["paper"],
        "gross_limit": 1.5,
        "net_limit": 1.0,
        "max_leverage": 1.5,
        "max_single_name_weight": 0.25,
        "allowed_asset_classes": ["equity"],
        "drawdown_actions": {"warn": 0.05, "risk_off": 0.1},
    }
    explicit_res = resolve_pool_risk_policy_binding(
        capital_pool_id="pool-explicit",
        pool={
            "pool_id": "pool-explicit",
            "tenant_id": "tenant-a",
            "risk_policy_ref": "rp-explicit-custom-v2",
            "metadata": {},
        },
        risk_policy_loader=lambda ref: dict(explicit_policy) if ref == "rp-explicit-custom-v2" else None,
        expected_tenant_id="tenant-a",
        expected_stage="paper",
        require_limits=("gross_limit", "max_single_name_weight"),
    )
    assert explicit_res.resolved is True
    assert explicit_res.status == "valid"
    assert explicit_res.profile_mapping_status == "explicit_policy"
    assert explicit_res.effective_limits["max_single_name_weight"] == 0.25
    assert explicit_res.effective_limits["drawdown_actions"] == {"warn": 0.05, "risk_off": 0.1}

    # Wrong-tenant policy rejected
    wrong_tenant_pool = {
        "pool_id": "pool-wrong-tenant",
        "tenant_id": "tenant-a",
        "metadata": {
            "risk_policy": {
                **valid_inline_policy,
                "risk_policy_id": "rp-tenant-b",
                "tenant_id": "tenant-b",
            }
        },
    }
    wrong_tenant_res = resolve_pool_risk_policy_binding(
        capital_pool_id="pool-wrong-tenant",
        pool=wrong_tenant_pool,
        expected_tenant_id="tenant-a",
    )
    assert wrong_tenant_res.resolved is False
    assert wrong_tenant_res.status == "wrong_tenant"
    assert wrong_tenant_res.reason_code == "risk_policy_wrong_tenant"
    assert wrong_tenant_res.allow_new_or_increased_risk is False
    assert wrong_tenant_res.allow_risk_reducing_actions is True
    with pytest.raises(RiskPolicyError, match="risk_policy_wrong_tenant"):
        resolve_bound_pool_risk_policy(
            capital_pool_id="pool-wrong-tenant",
            capital_pool_reader=lambda _: wrong_tenant_pool,
            expected_tenant_id="tenant-a",
        )

    # Stale policy rejected (expired, version mismatch, or authoritative ref version drift)
    stale_expired_pool = {
        "pool_id": "pool-stale",
        "tenant_id": "tenant-a",
        "metadata": {
            "risk_policy": {
                **valid_inline_policy,
                "expires_at": "2025-01-01T00:00:00Z",
            }
        },
    }
    stale_res = resolve_pool_risk_policy_binding(
        capital_pool_id="pool-stale",
        pool=stale_expired_pool,
        expected_tenant_id="tenant-a",
        as_of="2026-11-21T00:00:00Z",
    )
    assert stale_res.resolved is False
    assert stale_res.status == "stale"
    assert stale_res.reason_code == "risk_policy_stale"

    stale_version_res = resolve_pool_risk_policy_binding(
        capital_pool_id="pool-stale-ver",
        pool={
            "pool_id": "pool-stale-ver",
            "tenant_id": "tenant-a",
            "risk_policy_ref": "rp-versioned",
            "metadata": {"risk_policy": {**valid_inline_policy, "risk_policy_id": "rp-versioned", "version": "1.0"}},
        },
        risk_policy_loader=lambda _: {**valid_inline_policy, "risk_policy_id": "rp-versioned", "version": "2.0"},
        expected_tenant_id="tenant-a",
    )
    assert stale_version_res.resolved is False
    assert stale_version_res.status == "stale"
    assert stale_version_res.reason_code == "risk_policy_stale"

    # Invalid policy rejected (negative limit, wrong stage, missing required limit)
    neg_limit_res = resolve_pool_risk_policy_binding(
        capital_pool_id="pool-neg",
        pool={"pool_id": "pool-neg", "metadata": {"risk_policy": {**valid_inline_policy, "gross_limit": -1.0}}},
    )
    assert neg_limit_res.resolved is False
    assert neg_limit_res.status == "invalid"
    assert neg_limit_res.reason_code == "risk_policy_invalid_limit"

    live_stage_res = resolve_pool_risk_policy_binding(
        capital_pool_id="pool-live-stage",
        pool={"pool_id": "pool-live-stage", "metadata": {"risk_policy": {**valid_inline_policy, "allowed_stages": ["live"]}}},
        expected_stage="paper",
    )
    assert live_stage_res.resolved is False
    assert live_stage_res.status == "invalid"
    assert live_stage_res.reason_code == "risk_policy_stage_not_allowed"

    # Unapproved profile mapping surfaced to policy owner without inventing arbitrary numbers
    owner_res = resolve_pool_risk_policy_binding(
        capital_pool_id="pool-unmapped-profile",
        pool={"pool_id": "pool-unmapped-profile", "tenant_id": "tenant-a", "metadata": {}},
        expected_tenant_id="tenant-a",
        requested_profile="aggressive",
        require_approved_profile_mapping=True,
    )
    assert owner_res.resolved is False
    assert owner_res.status == "unresolved_profile_mapping"
    assert owner_res.reason_code == "risk_policy_profile_mapping_unapproved"
    assert owner_res.owner_decision_request is not None
    assert owner_res.owner_decision_request["policy_owner"] == "services/capital/risk_policy.py"
    assert owner_res.owner_decision_request["requested_profile"] == "aggressive"


def test_required_profile_mapping_fails_closed_on_inline_baseline_and_connects_owner_decision_readback() -> None:
    from services.capital.risk_policy import (
        RiskPolicyLoadError,
        resolve_pool_risk_policy_binding,
        risk_policy_digest,
    )

    baseline_policy = {
        "risk_policy_id": "rp-pool-persona-paper-06d4775a9b6a8f63b311",
        "version": "1.0",
        "status": "active",
        "allowed_stages": ["paper"],
        "allowed_asset_classes": ["equity", "future"],
        "gross_limit": 2.0,
        "net_limit": 2.0,
        "max_leverage": 2.0,
    }
    pool_record = {
        "pool_id": "pool-persona-paper-06d4775a9b6a8f63b311",
        "tenant_id": "tenant-dev",
        "approval_digest": "pool-activation-digest-001",
        "metadata": {
            "tenant_id": "tenant-dev",
            "risk_policy": dict(baseline_policy),
        },
    }

    # 1. Baseline inline policy fails closed on the normal path WITHOUT any opt-in flag
    unapproved_default_call = resolve_pool_risk_policy_binding(
        capital_pool_id="pool-persona-paper-06d4775a9b6a8f63b311",
        pool=pool_record,
        expected_tenant_id="tenant-dev",
        as_of="2026-11-21T00:00:00Z",
    )
    assert unapproved_default_call.resolved is False
    assert unapproved_default_call.status == "unresolved_profile_mapping"
    assert unapproved_default_call.reason_code == "risk_policy_profile_mapping_unapproved"
    assert unapproved_default_call.profile_mapping_status == "unresolved_owner_decision_required"
    assert unapproved_default_call.allow_new_or_increased_risk is False
    assert unapproved_default_call.allow_risk_reducing_actions is True

    unapproved = resolve_pool_risk_policy_binding(
        capital_pool_id="pool-persona-paper-06d4775a9b6a8f63b311",
        pool=pool_record,
        expected_tenant_id="tenant-dev",
        requested_profile="low",
        as_of="2026-11-21T00:00:00Z",
    )
    assert unapproved.resolved is False
    assert unapproved.status == "unresolved_profile_mapping"
    assert unapproved.reason_code == "risk_policy_profile_mapping_unapproved"
    assert unapproved.profile_mapping_status == "unresolved_owner_decision_required"
    assert unapproved.allow_new_or_increased_risk is False
    assert unapproved.allow_risk_reducing_actions is True
    assert unapproved.owner_decision_request is not None
    assert unapproved.owner_decision_request["requested_profile"] == "low"
    assert (
        unapproved.owner_decision_request["candidate_risk_policy_id"]
        == "rp-pool-persona-paper-06d4775a9b6a8f63b311"
    )

    # 2. Connecting the canonical tenant-scoped policy-owner ApprovalDecision via owner reader resolves the profile mapping
    from services.governance.test_approval_authority import approval_snapshot, SnapshotApprovalReader

    valid_decision = approval_snapshot(
        decision_id="apv-owner-low-001",
        tenant_id="tenant-dev",
        target_type="capital_pool_activation",
        target_id="pool-persona-paper-06d4775a9b6a8f63b311",
        target_version="pool-activation-digest-001",
        owner_user_id="proposer-1",
        risk_level="low",
        decided_at="2026-11-20T00:00:00Z",
        recorded_at="2026-11-20T00:00:00Z",
        expires_at="2026-12-20T00:00:00Z",
        metadata={
            "subject": {
                "pool_id": "pool-persona-paper-06d4775a9b6a8f63b311",
                "risk_direction": "increase",
                "requested_risk": "low",
                "risk_policy_id": "rp-pool-persona-paper-06d4775a9b6a8f63b311",
                "risk_policy_version": "1.0",
                "risk_policy_digest": risk_policy_digest(baseline_policy),
            },
            "approvals": [
                {"actor_id": "reviewer-1", "actor_role": "governance_reviewer"},
                {"actor_id": "reviewer-2", "actor_role": "governance_reviewer"},
            ],
        },
    )
    approved_res = resolve_pool_risk_policy_binding(
        capital_pool_id="pool-persona-paper-06d4775a9b6a8f63b311",
        pool=pool_record,
        expected_tenant_id="tenant-dev",
        requested_profile="low",
        approval_decision_id="apv-owner-low-001",
        approval_reader=SnapshotApprovalReader(valid_decision),
        as_of="2026-11-21T00:00:00Z",
    )
    assert approved_res.resolved is True
    assert approved_res.status == "valid"
    assert approved_res.profile_mapping_status == "approved_profile_mapping"
    assert approved_res.profile_mapping_approval_id == "apv-owner-low-001"
    assert approved_res.allow_new_or_increased_risk is True
    assert approved_res.owner_decision_request is None

    # 3. Attached metadata approval dictionaries are treated only as references and never accepted without canonical owner store readback
    empty_reader = type("EmptyReader", (), {"get": lambda self, dec_id: None})()
    for attached_payload in [
        {
            "decision_id": "not-in-owner-store",
            "tenant_id": "tenant-dev",
            "target_type": "capital_pool_activation",
            "target_id": "pool-persona-paper-06d4775a9b6a8f63b311",
            "decision_state": "decided",
            "decision": "approved",
            "authority_status": "authoritative",
            "actor_role": "unauthorized-role",
            "metadata": {"subject": dict(valid_decision["metadata"]["subject"])},
        },
        dict(valid_decision),
    ]:
        spoofed_pool = {
            **pool_record,
            "metadata": {
                **pool_record["metadata"],
                "profile_mapping_approval": attached_payload,
            },
        }
        spoofed_res = resolve_pool_risk_policy_binding(
            capital_pool_id="pool-persona-paper-06d4775a9b6a8f63b311",
            pool=spoofed_pool,
            expected_tenant_id="tenant-dev",
            requested_profile="low",
            approval_reader=empty_reader,
            as_of="2026-11-21T00:00:00Z",
        )
        assert spoofed_res.resolved is False
        assert spoofed_res.status == "unresolved_profile_mapping"
        assert spoofed_res.reason_code == "risk_policy_profile_mapping_unapproved"
        assert spoofed_res.allow_new_or_increased_risk is False

    # 4. Canonical owner reader records that fail ApprovalEvidence.require_valid or usage scope fail closed
    for bad_dec, extra_pool_meta in [
        ({**valid_decision, "tenant_id": "tenant-other"}, {}),
        ({**valid_decision, "expires_at": "2020-01-01T00:00:00Z"}, {}),
        ({**valid_decision, "expires_at": None}, {}),
        ({**valid_decision, "controller_record_ref": None}, {}),
        ({**valid_decision, "recorded_at": None}, {}),
        ({**valid_decision, "actor_role": "unauthorized-role"}, {}),
        ({**valid_decision, "revoked_at": "2026-11-20T12:00:00Z"}, {}),
        ({**valid_decision, "superseded_by": "apv-owner-low-002"}, {}),
        ({**valid_decision, "target_type": "registry_entry"}, {}),
        (
            {
                **valid_decision,
                "metadata": {
                    **valid_decision["metadata"],
                    "subject": {
                        **valid_decision["metadata"]["subject"],
                        "risk_policy_digest": "0" * 64,
                    },
                },
            },
            {},
        ),
        (
            {
                **valid_decision,
                "authorization_scope": {
                    "principal_subject": "pantheon-dev-paper-provisioner",
                    "allowed_environments": ["dev"],
                    "allowed_target_stages": ["paper"],
                    "allowed_execution_contexts": ["paper"],
                    "allowed_risk_levels": ["low"],
                    "max_capital_scale_pct": 0.0,
                    "forbid_live": True,
                    "forbid_real_capital": True,
                },
            },
            {"environment": "prod"},
        ),
    ]:
        bad_reader = type("BadReader", (), {"get": lambda self, dec_id, d=bad_dec: d if dec_id == "apv-owner-low-001" else None})()
        bad_res = resolve_pool_risk_policy_binding(
            capital_pool_id="pool-persona-paper-06d4775a9b6a8f63b311",
            pool={
                **pool_record,
                "metadata": {
                    **pool_record["metadata"],
                    **extra_pool_meta,
                },
            },
            expected_tenant_id="tenant-dev",
            requested_profile="low",
            approval_decision_id="apv-owner-low-001",
            approval_reader=bad_reader,
            as_of="2026-11-21T00:00:00Z",
        )
        assert bad_res.resolved is False
        assert bad_res.status == "unresolved_profile_mapping"
        assert bad_res.reason_code == "risk_policy_profile_mapping_unapproved"
        assert bad_res.allow_new_or_increased_risk is False

    # 4. Genuinely approved explicit owner contract via risk_policy_ref is preserved
    explicit_owner_policy = {
        "risk_policy_id": "rp-explicit-owner-v2",
        "version": "2.0",
        "status": "active",
        "allowed_stages": ["paper"],
        "gross_limit": 0.5,
        "net_limit": 0.5,
        "max_leverage": 1.0,
        "max_single_name_weight": 0.2,
        "metadata": {"tenant_id": "tenant-dev"},
    }
    explicit_res = resolve_pool_risk_policy_binding(
        capital_pool_id="pool-explicit-ref",
        pool={
            "pool_id": "pool-explicit-ref",
            "tenant_id": "tenant-dev",
            "risk_policy_ref": "rp-explicit-owner-v2",
            "metadata": {"tenant_id": "tenant-dev"},
        },
        risk_policy_loader=lambda ref: explicit_owner_policy if ref == "rp-explicit-owner-v2" else (_ for _ in ()).throw(RiskPolicyLoadError(ref)),
        expected_tenant_id="tenant-dev",
        requested_profile="low",
        require_approved_profile_mapping=True,
        as_of="2026-11-21T00:00:00Z",
    )
    assert explicit_res.resolved is True
    assert explicit_res.status == "valid"
    assert explicit_res.profile_mapping_status == "explicit_policy"
    assert explicit_res.effective_limits["gross_limit"] == 0.5


def test_risk_policy_digest_hashes_every_executable_dimension_and_provenance() -> None:
    from services.capital.risk_policy import risk_policy_digest

    base = {
        "risk_policy_id": "rp-digest-full",
        "version": "1.0",
        "status": "active",
        "allowed_stages": ["paper"],
        "gross_limit": 1.0,
        "net_limit": 1.0,
        "max_leverage": 1.0,
        "max_sector_exposure": {"tech": 0.1},
    }
    base_digest = risk_policy_digest(base)

    for mutated in [
        {**base, "max_sector_exposure": {"tech": 0.9}},
        {**base, "max_factor_exposure": {"momentum": 0.25}},
        {**base, "turnover_limit": 0.4},
        {**base, "allowed_strategy_families": ["momentum"]},
        {**base, "forbidden_strategy_families": ["martingale"]},
        {**base, "max_strategy_family_concentration": {"momentum": 0.5}},
        {**base, "max_target_overlap": 0.3},
        {**base, "max_signal_correlation": 0.6},
        {**base, "max_canary_capital_scale_pct": 10.0},
        {**base, "max_canary_gross_scale_pct": 15.0},
        {**base, "metadata": {"tenant_id": "tenant-a"}},
        {**base, "metadata": {"expires_at": "2027-01-01T00:00:00Z"}},
        {**base, "metadata": {"requested_profile": "low"}},
    ]:
        assert risk_policy_digest(mutated) != base_digest


def test_resolve_pool_risk_policy_binding_enforces_all_time_bounds_and_their_intersection() -> None:
    from services.capital.risk_policy import resolve_pool_risk_policy_binding

    base_explicit = {
        "risk_policy_id": "rp-window-intersection",
        "version": "1.0",
        "status": "active",
        "allowed_stages": ["paper"],
        "gross_limit": 1.0,
        "net_limit": 1.0,
        "max_leverage": 1.0,
    }

    # 1. Policy expires_at=2099-01-01 with effective_to=2020-01-01 must fail closed as stale
    dual_policy_upper = resolve_pool_risk_policy_binding(
        capital_pool_id="pool-dual-upper",
        pool={
            "pool_id": "pool-dual-upper",
            "tenant_id": "tenant-a",
            "metadata": {
                "risk_policy": {
                    **base_explicit,
                    "expires_at": "2099-01-01T00:00:00Z",
                    "effective_to": "2020-01-01T00:00:00Z",
                }
            },
        },
        expected_tenant_id="tenant-a",
        as_of="2026-11-21T00:00:00Z",
    )
    assert dual_policy_upper.resolved is False
    assert dual_policy_upper.status == "stale"
    assert dual_policy_upper.reason_code == "risk_policy_stale"
    assert dual_policy_upper.allow_new_or_increased_risk is False
    assert dual_policy_upper.allow_risk_reducing_actions is True

    # 2. Policy expires_at=2099-01-01 with pool risk_policy_expires_at=2020-01-01 must fail closed as stale
    policy_vs_pool_upper = resolve_pool_risk_policy_binding(
        capital_pool_id="pool-vs-policy-upper",
        pool={
            "pool_id": "pool-vs-policy-upper",
            "tenant_id": "tenant-a",
            "metadata": {
                "risk_policy_expires_at": "2020-01-01T00:00:00Z",
                "risk_policy": {
                    **base_explicit,
                    "expires_at": "2099-01-01T00:00:00Z",
                },
            },
        },
        expected_tenant_id="tenant-a",
        as_of="2026-11-21T00:00:00Z",
    )
    assert policy_vs_pool_upper.resolved is False
    assert policy_vs_pool_upper.status == "stale"
    assert policy_vs_pool_upper.reason_code == "risk_policy_stale"

    # 3. Policy effective_from=2020-01-01 with pool risk_policy_effective_from=2099-01-01 must fail closed as stale
    policy_vs_pool_lower = resolve_pool_risk_policy_binding(
        capital_pool_id="pool-vs-policy-lower",
        pool={
            "pool_id": "pool-vs-policy-lower",
            "tenant_id": "tenant-a",
            "metadata": {
                "risk_policy_effective_from": "2099-01-01T00:00:00Z",
                "risk_policy": {
                    **base_explicit,
                    "effective_from": "2020-01-01T00:00:00Z",
                },
            },
        },
        expected_tenant_id="tenant-a",
        as_of="2026-11-21T00:00:00Z",
    )
    assert policy_vs_pool_lower.resolved is False
    assert policy_vs_pool_lower.status == "stale"
    assert policy_vs_pool_lower.reason_code == "risk_policy_stale"

    # 4. Empty intersection (max(effective_from) >= min(expires_at/effective_to)) fails closed as invalid
    empty_intersection = resolve_pool_risk_policy_binding(
        capital_pool_id="pool-empty-intersection",
        pool={
            "pool_id": "pool-empty-intersection",
            "tenant_id": "tenant-a",
            "metadata": {
                "risk_policy_expires_at": "2026-05-01T00:00:00Z",
                "risk_policy": {
                    **base_explicit,
                    "effective_from": "2026-06-01T00:00:00Z",
                    "expires_at": "2099-01-01T00:00:00Z",
                },
            },
        },
        expected_tenant_id="tenant-a",
        as_of="2026-11-21T00:00:00Z",
    )
    assert empty_intersection.resolved is False
    assert empty_intersection.status == "invalid"
    assert empty_intersection.reason_code == "risk_policy_invalid"

    # 5. Malformed secondary time bound is not hidden by a valid primary time bound
    malformed_secondary = resolve_pool_risk_policy_binding(
        capital_pool_id="pool-malformed-secondary",
        pool={
            "pool_id": "pool-malformed-secondary",
            "tenant_id": "tenant-a",
            "metadata": {
                "risk_policy": {
                    **base_explicit,
                    "expires_at": "2099-01-01T00:00:00Z",
                    "effective_to": "not-an-iso-timestamp",
                }
            },
        },
        expected_tenant_id="tenant-a",
        as_of="2026-11-21T00:00:00Z",
    )
    assert malformed_secondary.resolved is False
    assert malformed_secondary.status == "invalid"
    assert malformed_secondary.reason_code == "risk_policy_invalid"





