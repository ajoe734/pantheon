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


def test_runtime_entry_points_enforce_profile_mapping_on_existing_pools_and_admit_approved_mapping(
    monkeypatch,
) -> None:
    import pytest
    from services.capital.risk_policy import (
        RiskPolicyError,
        capital_pool_approval_digest,
        resolve_bound_pool_risk_policy,
        resolve_pool_risk_policy_binding,
        risk_policy_digest,
    )
    from services.execution.lean_runtime.expression_inputs import (
        ExpressionInputsProvider,
        ExpressionInputsUnavailable,
    )
    from services.execution.lean_runtime.paper_runtime import PaperExecutionAlgorithm
    from services.governance import approval_authority
    from services.governance.test_approval_authority import (
        SnapshotApprovalReader,
        approval_snapshot,
    )

    pool_id = "pool-persona-paper-06d4775a9b6a8f63b311"
    baseline_v1 = {
        "risk_policy_id": f"rp-{pool_id}",
        "version": "1.0",
        "status": "active",
        "allowed_stages": ["paper"],
        "allowed_asset_classes": ["equity", "future"],
        "gross_limit": 2.0,
        "net_limit": 2.0,
        "max_leverage": 2.0,
    }
    # Existing active pool with rp-pool-persona-paper-* inline v1 and NO mapping approval or persisted new flags
    unapproved_existing_pool = {
        "pool_id": pool_id,
        "tenant_id": "tenant-dev",
        "owner_id": "tenant-dev",
        "owner_type": "org",
        "status": "active",
        "currency": "TWD",
        "budget": 100000.0,
        "single_runtime_enforced": True,
        "metadata": {
            "internal": True,
            "execution_context": "paper",
            "tenant_id": "tenant-dev",
            "risk_policy": dict(baseline_v1),
        },
    }
    unapproved_existing_pool["approval_digest"] = capital_pool_approval_digest(unapproved_existing_pool)
    runtime_binding = {
        "binding_id": "rb-paper-existing-1",
        "runtime_id": "rt-paper-existing-1",
        "capital_pool_id": pool_id,
        "deployment_mode": "paper",
        "status": "active",
    }
    summary = {
        "runtime_id": "rt-paper-existing-1",
        "binding_id": "rb-paper-existing-1",
        "ledger_fill_count": 0,
        "executed_trade_count": 0,
        "positions": None,
    }

    # 1. Unapproved existing pool fails closed across resolve_pool_risk_policy_binding,
    # resolve_bound_pool_risk_policy, ExpressionInputsProvider, and PaperExecutionAlgorithm
    direct_res = resolve_pool_risk_policy_binding(
        capital_pool_id=pool_id,
        pool=unapproved_existing_pool,
    )
    assert direct_res.resolved is False
    assert direct_res.reason_code == "risk_policy_profile_mapping_unapproved"

    with pytest.raises(RiskPolicyError, match="risk_policy_profile_mapping_unapproved"):
        resolve_bound_pool_risk_policy(
            capital_pool_id=pool_id,
            binding=runtime_binding,
            capital_pool_reader=lambda _pid: unapproved_existing_pool,
        )

    provider_unapproved = ExpressionInputsProvider(
        capital_pool_reader=lambda _pid: unapproved_existing_pool,
        runtime_summary_reader=lambda _rid: summary,
    )
    with pytest.raises(ExpressionInputsUnavailable) as exc_info:
        provider_unapproved(runtime_binding)
    assert exc_info.value.missing == "risk_policy"
    assert "risk_policy_profile_mapping_unapproved" in exc_info.value.detail

    algo_unapproved = PaperExecutionAlgorithm(
        capital_pool_id=pool_id,
        capital_pool_reader=lambda _pid: unapproved_existing_pool,
    )
    with pytest.raises(RiskPolicyError, match="risk_policy_profile_mapping_unapproved"):
        algo_unapproved._resolve_active_risk_policy()

    # 2. Once canonical ApprovalEvidence exists and is bound to the pool's approval_digest,
    # all runtime entry points resolve the approved 2.0 policy
    approved_existing_pool = {
        **unapproved_existing_pool,
        "metadata": {
            **unapproved_existing_pool["metadata"],
            "requested_risk_profile": "low",
            "require_approved_profile_mapping": True,
            "profile_mapping_approval_id": "apv-runtime-low-001",
        },
    }
    approved_existing_pool["approval_digest"] = capital_pool_approval_digest(approved_existing_pool)
    valid_decision = approval_snapshot(
        decision_id="apv-runtime-low-001",
        tenant_id="tenant-dev",
        target_type="capital_pool_activation",
        target_id=pool_id,
        target_version=approved_existing_pool["approval_digest"],
        owner_user_id="proposer-1",
        risk_level="low",
        metadata={
            "subject": {
                "pool_id": pool_id,
                "risk_direction": "increase",
                "requested_risk": "low",
                "risk_policy_id": baseline_v1["risk_policy_id"],
                "risk_policy_version": "1.0",
                "risk_policy_digest": risk_policy_digest(baseline_v1),
            },
            "approvals": [
                {"actor_id": "reviewer-1", "actor_role": "governance_reviewer"},
                {"actor_id": "reviewer-2", "actor_role": "governance_reviewer"},
            ],
        },
    )
    reader = SnapshotApprovalReader(valid_decision)
    monkeypatch.setattr(approval_authority, "configured_approval_reader", lambda domain="capital", **kw: reader)

    bound_policy = resolve_bound_pool_risk_policy(
        capital_pool_id=pool_id,
        binding=runtime_binding,
        capital_pool_reader=lambda _pid: approved_existing_pool,
    )
    assert bound_policy is not None
    assert bound_policy.risk_policy_id == f"rp-{pool_id}"
    assert bound_policy.gross_limit == 2.0

    provider_approved = ExpressionInputsProvider(
        capital_pool_reader=lambda _pid: approved_existing_pool,
        runtime_summary_reader=lambda _rid: summary,
    )
    expr_inputs = provider_approved(runtime_binding)
    assert expr_inputs["risk_budget"]["gross_limit"] == 200000.0
    assert expr_inputs["risk_budget"]["max_leverage"] == 2.0

    algo_approved = PaperExecutionAlgorithm(
        capital_pool_id=pool_id,
        capital_pool_reader=lambda _pid: approved_existing_pool,
    )
    algo_policy = algo_approved._resolve_active_risk_policy()
    assert algo_policy.risk_policy_id == f"rp-{pool_id}"
    assert algo_policy.gross_limit == 2.0

    # 3. Fixture pool IDs (pool-1/rp-pool-1, pool-rb-expr/rp-pool-rb-expr, pool-paper-env/rp-pool-paper-env)
    # have no hardcoded bypass: without approval, both resolve_pool_risk_policy_binding and resolve_bound_pool_risk_policy fail closed
    for fixture_pool_id in ("pool-1", "pool-rb-expr", "pool-paper-env"):
        unapproved_fixture_pool = {
            "pool_id": fixture_pool_id,
            "status": "active",
            "currency": "TWD",
            "budget": 100000.0,
            "single_runtime_enforced": True,
            "metadata": {
                "risk_policy": {
                    "risk_policy_id": f"rp-{fixture_pool_id}",
                    "version": "1.0",
                    "status": "active",
                    "allowed_stages": ["paper"],
                    "gross_limit": 2.0,
                    "net_limit": 2.0,
                    "max_leverage": 2.0,
                    "allowed_asset_classes": ["equity", "future"],
                },
            },
        }
        fixture_res = resolve_pool_risk_policy_binding(
            capital_pool_id=fixture_pool_id,
            pool=unapproved_fixture_pool,
        )
        assert fixture_res.resolved is False
        assert fixture_res.reason_code == "risk_policy_profile_mapping_unapproved"
        with pytest.raises(RiskPolicyError, match="risk_policy_profile_mapping_unapproved"):
            resolve_bound_pool_risk_policy(
                capital_pool_id=fixture_pool_id,
                pool=unapproved_fixture_pool,
            )


def test_capital_owner_approval_transport_resolves_json_pool_in_compose_producer_and_reconciler_envs(
    tmp_path, monkeypatch
) -> None:
    import json
    import os
    import pytest
    import urllib.error
    import yaml
    from fastapi.testclient import TestClient

    from services.capital import main as capital_main
    from services.capital import risk_policy as rp_mod
    from services.capital.risk_policy import (
        CapitalOwnerApprovalReader,
        RiskPolicyError,
        capital_pool_approval_digest,
        configured_capital_owner_approval_reader,
        resolve_bound_pool_risk_policy,
        resolve_pool_risk_policy_binding,
        risk_policy_digest,
    )
    from services.execution.lean_runtime import expression_inputs as ei_mod
    from services.execution.lean_runtime.expression_inputs import (
        ExpressionInputsProvider,
        ExpressionInputsUnavailable,
        build_production_expression_inputs,
    )
    from services.execution.lean_runtime.paper_runtime import PaperExecutionAlgorithm
    from services.execution.lean_runtime.test_expression_inputs import _compose_environment
    from services.governance.test_approval_authority import (
        SnapshotApprovalReader,
        approval_snapshot,
    )

    policy = {
        "risk_policy_id": "rp-pool-review",
        "version": "1.0",
        "status": "active",
        "gross_limit": 2.0,
        "net_limit": 2.0,
        "max_leverage": 2.0,
        "allowed_stages": ["paper"],
        "allowed_asset_classes": ["equity", "future"],
    }
    pool = {
        "pool_id": "pool-review",
        "tenant_id": "tenant-dev",
        "name": "Review paper pool",
        "owner_id": "tenant-dev",
        "owner_type": "org",
        "currency": "USD",
        "budget": 100000.0,
        "single_runtime_enforced": True,
        "status": "active",
        "metadata": {
            "internal": True,
            "execution_context": "paper",
            "tenant_id": "tenant-dev",
            "risk_policy": dict(policy),
            "requested_risk_profile": "low",
            "require_approved_profile_mapping": True,
            "profile_mapping_approval_id": "apv-review",
        },
    }
    pool["approval_digest"] = capital_pool_approval_digest(pool)
    # Must be pure JSON with no callable metadata
    pool_json = json.loads(json.dumps(pool))
    decision = approval_snapshot(
        decision_id="apv-review",
        tenant_id="tenant-dev",
        owner_user_id="proposer-1",
        target_type="capital_pool_activation",
        target_id="pool-review",
        target_version=pool_json["approval_digest"],
        metadata={
            "subject": {
                "pool_id": "pool-review",
                "risk_direction": "increase",
                "risk_policy_id": policy["risk_policy_id"],
                "risk_policy_version": "1.0",
                "risk_policy_digest": risk_policy_digest(policy),
                "requested_risk": "low",
            },
            "approvals": [
                {"actor_id": "reviewer-1", "actor_role": "governance_reviewer"},
                {"actor_id": "reviewer-2", "actor_role": "governance_reviewer"},
            ],
        },
    )
    decision_json = json.loads(json.dumps(decision))

    # 1. Callable approval_reader stuffed into pool metadata is ignored and never used
    from services.capital.conftest import _ApprovesWhatIsAsked

    metadata_callable_pool = {
        **pool_json,
        "metadata": {
            **pool_json["metadata"],
            "approval_reader": _ApprovesWhatIsAsked(),
        },
    }
    with monkeypatch.context() as m:
        m.setattr(os, "environ", {})
        ignored_res = resolve_pool_risk_policy_binding(pool=metadata_callable_pool)
        assert ignored_res.resolved is False
        assert ignored_res.reason_code == "risk_policy_profile_mapping_unapproved"

    # 2. Wire the real Capital owner FastAPI routes (/api/capital-pools/{id} and /api/capital-pools/{id}/approvals/{decision_id})
    CapitalPool = capital_main.CapitalPool
    CapitalPoolStore = capital_main.CapitalPoolStore

    test_pool_store = CapitalPoolStore(tmp_path / "capital_pools.json")
    stored_pool = CapitalPool(
        pool_id="pool-review",
        name="Review paper pool",
        owner_id="tenant-dev",
        owner_type="org",
        status="active",
        created_at="2026-11-21T00:00:00Z",
        currency="USD",
        budget=100000.0,
        risk_policy_ref=None,
        single_runtime_enforced=True,
        metadata=dict(pool_json["metadata"]),
    )
    object.__setattr__(stored_pool, "tenant_id", "tenant-dev")
    test_pool_store.create(stored_pool)

    foreign_pool = CapitalPool(
        pool_id="pool-foreign",
        name="Foreign pool",
        owner_id="tenant-other",
        owner_type="org",
        status="active",
        created_at="2026-11-21T00:00:00Z",
        currency="USD",
        budget=100000.0,
        risk_policy_ref=None,
        single_runtime_enforced=True,
        metadata={"tenant_id": "tenant-other", "execution_context": "paper"},
    )
    object.__setattr__(foreign_pool, "tenant_id", "tenant-other")
    test_pool_store.create(foreign_pool)

    decisions_by_id = {"apv-review": decision_json}

    class _GovernanceStoreReader:
        def get(self, dec_id: str):
            return decisions_by_id.get(dec_id)

    monkeypatch.setattr(capital_main, "pool_store", test_pool_store)
    monkeypatch.setattr(capital_main.capital_guard, "_approval_reader", _GovernanceStoreReader())
    monkeypatch.setenv("CAPITAL_AUTH_DISABLED", "true")
    capital_client = TestClient(capital_main.app)

    # Verify Capital owner GET endpoints directly
    pool_resp = capital_client.get(
        "/api/capital-pools/pool-review",
        headers={"X-Tenant-Id": "tenant-dev", "X-Pantheon-Service": "runtime-manager"},
    )
    assert pool_resp.status_code == 200
    owner_pool_json = pool_resp.json()
    assert owner_pool_json["approval_digest"] == pool_json["approval_digest"]

    apv_resp = capital_client.get(
        "/api/capital-pools/pool-review/approvals/apv-review",
        headers={"X-Tenant-Id": "tenant-dev", "X-Pantheon-Service": "runtime-manager"},
    )
    assert apv_resp.status_code == 200
    assert apv_resp.json()["decision_id"] == "apv-review"

    apv_direct_resp = capital_client.get(
        "/api/capital/approvals/apv-review",
        headers={"X-Tenant-Id": "tenant-dev", "X-Pantheon-Service": "runtime-manager"},
    )
    assert apv_direct_resp.status_code == 200
    assert apv_direct_resp.json()["decision_id"] == "apv-review"

    # Wrong tenant or wrong pool is denied (404 fail-closed)
    wrong_tenant_resp = capital_client.get(
        "/api/capital-pools/pool-review/approvals/apv-review",
        headers={"X-Tenant-Id": "tenant-other", "X-Pantheon-Service": "runtime-manager"},
    )
    assert wrong_tenant_resp.status_code == 404
    wrong_pool_resp = capital_client.get(
        "/api/capital-pools/pool-foreign/approvals/apv-review",
        headers={"X-Tenant-Id": "tenant-dev", "X-Pantheon-Service": "runtime-manager"},
    )
    assert wrong_pool_resp.status_code == 404

    def forward_to_capital_or_telemetry(url: str, headers, timeout: float):
        if url.startswith("http://capital:8092/"):
            path = url[len("http://capital:8092") :]
            with monkeypatch.context() as cap_env:
                cap_env.setenv("CAPITAL_AUTH_DISABLED", "true")
                resp = capital_client.get(path, headers=dict(headers))
            if resp.status_code != 200:
                raise urllib.error.HTTPError(url, resp.status_code, resp.text, {}, None)
            return resp.json()
        if "/api/telemetry/runtime-summaries/" in url:
            return {"runtime_id": "rt-review", "binding_id": "rb-review", "ledger_fill_count": 0}
        raise AssertionError(f"Unexpected URL: {url}")

    # 3. Verify both paper-fleet-reconciler and paper-signal-producer Compose environments end-to-end
    token_path = tmp_path / "DEPLOYMENT_CAPITAL_SERVICE_TOKEN"
    token_path.write_text("issued-capital-reader-token", encoding="utf-8")
    token_path.chmod(0o600)

    for service in ("paper-fleet-reconciler", "paper-signal-producer"):
        env = _compose_environment(service, str(token_path))
        env["PANTHEON_TENANT_ID"] = "tenant-dev"
        env["PANTHEON_PAPER_EXPRESSION_LAYER"] = "on"
        assert [key for key in env if "GOVERNANCE" in key] == []

        with monkeypatch.context() as m:
            m.setattr(os, "environ", dict(env))
            m.setattr(ei_mod, "_http_get_json", forward_to_capital_or_telemetry)
            m.setattr(rp_mod, "_capital_owner_http_get_json", forward_to_capital_or_telemetry)

            reader_instance = configured_capital_owner_approval_reader()
            assert isinstance(reader_instance, CapitalOwnerApprovalReader)

            res = resolve_pool_risk_policy_binding(pool=owner_pool_json)
            assert res.resolved is True, f"{service}: {res.reason_code}: {res.reason}"
            assert res.profile_mapping_status == "approved_profile_mapping"
            assert res.profile_mapping_approval_id == "apv-review"

            provider = build_production_expression_inputs(env)
            expr_out = provider({"capital_pool_id": "pool-review", "runtime_id": "rt-review"})
            assert expr_out["risk_budget"]["gross_limit"] == 200000.0

            lambda_provider = ExpressionInputsProvider(
                capital_pool_reader=lambda _: owner_pool_json,
                runtime_summary_reader=lambda _: {"ledger_fill_count": 0},
            )
            lambda_out = lambda_provider({"capital_pool_id": "pool-review", "runtime_id": "rt-review"})
            assert lambda_out["risk_budget"]["gross_limit"] == 200000.0

            algo = PaperExecutionAlgorithm(
                capital_pool_id="pool-review",
                capital_pool_reader=ei_mod.capital_pool_reader_from_env(env),
            )
            algo_policy = algo._resolve_active_risk_policy()
            assert algo_policy.risk_policy_id == "rp-pool-review"
            assert algo_policy.gross_limit == 2.0


def test_profile_policy_autoresolve_arbitrary_decision_id_catalog_ambiguity_and_fail_closed_sources(
    tmp_path, monkeypatch
) -> None:
    import json
    from services.capital.risk_policy import (
        capital_pool_approval_digest,
        discover_approved_profile_risk_policy,
        resolve_bound_pool_risk_policy,
        resolve_pool_risk_policy_binding,
        risk_policy_digest,
    )
    from services.governance.test_approval_authority import SnapshotApprovalReader, approval_snapshot

    policy_dir = tmp_path / "risk_policies"
    policy_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CAPITAL_RISK_POLICY_DIR", str(policy_dir))

    pool_id = "pool-persona-paper-06d4775a9b6a8f63b311"
    base_pool = {
        "pool_id": pool_id,
        "tenant_id": "tenant-dev",
        "owner_id": "tenant-dev",
        "owner_type": "org",
        "status": "active",
        "currency": "TWD",
        "budget": 100000.0,
        "single_runtime_enforced": True,
        "metadata": {
            "internal": True,
            "execution_context": "paper",
            "tenant_id": "tenant-dev",
        },
    }

    # 1. Label-only policy (no metadata.requested_profile="low") and generic 2x policy are NEVER inferred as "low"
    label_only_policy = {
        "risk_policy_id": "rp-low-label-only",
        "name": "Low Risk Generic Label Policy",
        "version": "1.0",
        "status": "active",
        "allowed_stages": ["paper"],
        "gross_limit": 0.8,
        "net_limit": 0.6,
        "max_leverage": 1.0,
        "metadata": {"tenant_id": "tenant-dev", "description": "low risk label only"},
    }
    generic_2x_policy = {
        "risk_policy_id": "rp-generic-2x",
        "name": "Default Paper 2x Policy",
        "version": "1.0",
        "status": "active",
        "allowed_stages": ["paper"],
        "gross_limit": 2.0,
        "net_limit": 2.0,
        "max_leverage": 2.0,
        "metadata": {"tenant_id": "tenant-dev", "default_paper_baseline": True, "requested_profile": "low"},
    }
    (policy_dir / "rp-low-label-only.json").write_text(json.dumps(label_only_policy), encoding="utf-8")
    (policy_dir / "rp-generic-2x.json").write_text(json.dumps(generic_2x_policy), encoding="utf-8")

    no_infer_res = discover_approved_profile_risk_policy(
        resolved_pool_id=pool_id,
        effective_tenant_id="tenant-dev",
        clean_stage="paper",
        clean_profile="low",
        base_pool=base_pool,
        approval_reader=SnapshotApprovalReader([]),
    )
    assert no_infer_res.resolved is False
    assert no_infer_res.status == "unresolved_profile_mapping"
    assert no_infer_res.absence_source == "policy_definition_and_approval_absent"

    # 2. Cross-tenant catalog policy fails closed with cross_tenant_policy_or_approval_rejected
    cross_tenant_low = {
        "risk_policy_id": "rp-foreign-low",
        "version": "1.0",
        "status": "active",
        "allowed_stages": ["paper"],
        "gross_limit": 0.7,
        "net_limit": 0.5,
        "max_leverage": 1.0,
        "metadata": {"tenant_id": "tenant-foreign", "requested_profile": "low"},
    }
    (policy_dir / "rp-foreign-low.json").write_text(json.dumps(cross_tenant_low), encoding="utf-8")
    cross_res = discover_approved_profile_risk_policy(
        resolved_pool_id=pool_id,
        effective_tenant_id="tenant-dev",
        clean_stage="paper",
        clean_profile="low",
        base_pool=base_pool,
        approval_reader=SnapshotApprovalReader([]),
    )
    assert cross_res.resolved is False
    assert cross_res.absence_source == "cross_tenant_policy_or_approval_rejected"
    (policy_dir / "rp-foreign-low.json").unlink()

    # 3. Expired catalog policy fails closed with policy_expired_or_stale
    expired_low = {
        "risk_policy_id": "rp-expired-low",
        "version": "1.0",
        "status": "active",
        "allowed_stages": ["paper"],
        "gross_limit": 0.7,
        "net_limit": 0.5,
        "max_leverage": 1.0,
        "expires_at": "2020-01-01T00:00:00Z",
        "metadata": {"tenant_id": "tenant-dev", "requested_profile": "low"},
    }
    (policy_dir / "rp-expired-low.json").write_text(json.dumps(expired_low), encoding="utf-8")
    exp_pol_res = discover_approved_profile_risk_policy(
        resolved_pool_id=pool_id,
        effective_tenant_id="tenant-dev",
        clean_stage="paper",
        clean_profile="low",
        base_pool=base_pool,
        approval_reader=SnapshotApprovalReader([]),
        as_of="2026-11-22T00:00:00Z",
    )
    assert exp_pol_res.resolved is False
    assert exp_pol_res.absence_source == "policy_expired_or_stale"
    (policy_dir / "rp-expired-low.json").unlink()

    # 4. Valid catalog policy for (tenant-dev, low, paper) without Governance approval fails closed with policy_approval_missing
    valid_low_policy = {
        "risk_policy_id": "rp-tenant-dev-paper-low-v1",
        "version": "1.0",
        "status": "active",
        "allowed_stages": ["paper"],
        "allowed_asset_classes": ["equity"],
        "gross_limit": 0.8,
        "net_limit": 0.6,
        "max_leverage": 1.0,
        "max_single_name_weight": 0.25,
        "metadata": {"tenant_id": "tenant-dev", "requested_profile": "low"},
    }
    (policy_dir / "rp-tenant-dev-paper-low-v1.json").write_text(json.dumps(valid_low_policy), encoding="utf-8")
    unapproved_cat_res = resolve_pool_risk_policy_binding(
        capital_pool_id=pool_id,
        pool=base_pool,
        expected_tenant_id="tenant-dev",
        expected_stage="paper",
        requested_profile="low",
        require_approved_profile_mapping=True,
        approval_reader=SnapshotApprovalReader([]),
        as_of="2026-11-22T00:00:00Z",
    )
    assert unapproved_cat_res.resolved is False
    assert unapproved_cat_res.status == "unresolved_profile_mapping"
    assert unapproved_cat_res.reason_code == "risk_policy_profile_mapping_unapproved"
    assert unapproved_cat_res.owner_decision_request["absence_source"] == "policy_approval_missing"
    assert unapproved_cat_res.owner_decision_request["disposition_task_id"] == "PAPER-RISK-LOW-POLICY-OWNER-DISPOSITION-20261122"

    # 5. Lesson-derived pool_policy_parameter_proposal (governance/contract.md:70-76) is a parameter proposal record only
    # and is NEVER accepted as capital_pool_activation authority (with or without explicit decision_id).
    valid_low_digest = risk_policy_digest(valid_low_policy)
    param_proposal_only = approval_snapshot(
        decision_id="apv-parameter-only",
        tenant_id="tenant-dev",
        target_type="pool_policy_parameter_proposal",
        target_id="rp-tenant-dev-paper-low-v1",
        target_version="1.0",
        owner_user_id="proposer-1",
        persona_id="persona-2448c7053e33ea48da47",
        risk_level="high",
        actor_role="risk_owner",
        metadata={
            "subject": {
                "proposal_kind": "pool_policy",
                "lesson_ids": "lesson-review",
                "case_file_id": "case-review",
                "proposal_digest": "a" * 64,
                "requested_risk": "low",
                "risk_policy_id": "rp-tenant-dev-paper-low-v1",
                "risk_policy_version": "1.0",
                "risk_policy_digest": valid_low_digest,
            }
        },
    )
    for explicit_param_id in (None, "apv-parameter-only"):
        param_res = resolve_pool_risk_policy_binding(
            capital_pool_id=pool_id,
            pool={"pool_id": pool_id, "tenant_id": "tenant-dev", "metadata": {}},
            expected_tenant_id="tenant-dev",
            expected_stage="paper",
            requested_profile="low",
            require_approved_profile_mapping=True,
            approval_decision_id=explicit_param_id,
            approval_reader=SnapshotApprovalReader([param_proposal_only]),
            as_of="2026-11-22T00:00:00Z",
        )
        assert param_res.resolved is False
        assert param_res.status == "unresolved_profile_mapping"
        assert param_res.profile_mapping_approval_id is None

    # 6. Revoked and expired Governance decisions fail closed with exact absence_source
    policy_scoped_decision = approval_snapshot(
        decision_id="apv-arbitrary-gov-low-901",
        tenant_id="tenant-dev",
        target_type="capital_pool_activation",
        target_id="rp-tenant-dev-paper-low-v1",
        target_version=valid_low_digest,
        owner_user_id="proposer-1",
        risk_level="low",
        metadata={
            "subject": {
                "pool_id": "rp-tenant-dev-paper-low-v1",
                "risk_direction": "increase",
                "requested_risk": "low",
                "risk_policy_id": "rp-tenant-dev-paper-low-v1",
                "risk_policy_version": "1.0",
                "risk_policy_digest": valid_low_digest,
            },
            "approvals": [
                {"actor_id": "reviewer-1", "actor_role": "governance_reviewer"},
                {"actor_id": "reviewer-2", "actor_role": "governance_reviewer"},
            ],
        },
    )
    revoked_dec = {**policy_scoped_decision, "revoked_at": "2026-11-21T00:00:00Z", "decision_state": "revoked"}
    revoked_res = resolve_pool_risk_policy_binding(
        capital_pool_id=pool_id,
        pool=base_pool,
        expected_tenant_id="tenant-dev",
        expected_stage="paper",
        requested_profile="low",
        require_approved_profile_mapping=True,
        approval_reader=SnapshotApprovalReader([revoked_dec]),
        as_of="2026-11-22T00:00:00Z",
    )
    assert revoked_res.resolved is False
    assert revoked_res.owner_decision_request["absence_source"] == "policy_approval_revoked"

    expired_dec = {**policy_scoped_decision, "expires_at": "2020-01-01T00:00:00Z"}
    expired_res = resolve_pool_risk_policy_binding(
        capital_pool_id=pool_id,
        pool=base_pool,
        expected_tenant_id="tenant-dev",
        expected_stage="paper",
        requested_profile="low",
        require_approved_profile_mapping=True,
        approval_reader=SnapshotApprovalReader([expired_dec]),
        as_of="2026-11-22T00:00:00Z",
    )
    assert expired_res.resolved is False
    assert expired_res.owner_decision_request["absence_source"] == "policy_approval_expired"

    # 7. Uniquely established catalog policy + arbitrary valid Governance decision_id auto-resolves without caller supplying internal IDs
    resolved_cat = resolve_pool_risk_policy_binding(
        capital_pool_id=pool_id,
        pool=base_pool,
        expected_tenant_id="tenant-dev",
        expected_stage="paper",
        requested_profile="low",
        require_approved_profile_mapping=True,
        approval_reader=SnapshotApprovalReader([policy_scoped_decision]),
        as_of="2026-11-22T00:00:00Z",
    )
    assert resolved_cat.resolved is True
    assert resolved_cat.status == "valid"
    assert resolved_cat.risk_policy_id == "rp-tenant-dev-paper-low-v1"
    assert resolved_cat.risk_policy_version == "1.0"
    assert resolved_cat.profile_mapping_approval_id == "apv-arbitrary-gov-low-901"
    assert resolved_cat.effective_limits["gross_limit"] == 0.8
    assert resolved_cat.effective_limits["max_single_name_weight"] == 0.25

    # 8. Ambiguous multiple candidate policies or multiple valid approval decisions fail closed and never select an arbitrary candidate
    second_low_policy = {
        **valid_low_policy,
        "risk_policy_id": "rp-tenant-dev-paper-low-v2-alt",
        "gross_limit": 0.9,
    }
    (policy_dir / "rp-tenant-dev-paper-low-v2-alt.json").write_text(json.dumps(second_low_policy), encoding="utf-8")
    second_decision = approval_snapshot(
        decision_id="apv-arbitrary-gov-low-902",
        tenant_id="tenant-dev",
        target_type="capital_pool_activation",
        target_id="rp-tenant-dev-paper-low-v2-alt",
        target_version=risk_policy_digest(second_low_policy),
        owner_user_id="proposer-1",
        risk_level="low",
        metadata={
            "subject": {
                "pool_id": "rp-tenant-dev-paper-low-v2-alt",
                "risk_direction": "increase",
                "requested_risk": "low",
                "risk_policy_id": "rp-tenant-dev-paper-low-v2-alt",
                "risk_policy_version": "1.0",
                "risk_policy_digest": risk_policy_digest(second_low_policy),
            },
            "approvals": [
                {"actor_id": "reviewer-1", "actor_role": "governance_reviewer"},
                {"actor_id": "reviewer-2", "actor_role": "governance_reviewer"},
            ],
        },
    )
    ambig_disc = discover_approved_profile_risk_policy(
        resolved_pool_id=pool_id,
        effective_tenant_id="tenant-dev",
        clean_stage="paper",
        clean_profile="low",
        base_pool=base_pool,
        approval_reader=SnapshotApprovalReader([policy_scoped_decision, second_decision]),
        as_of="2026-11-22T00:00:00Z",
    )
    assert ambig_disc.resolved is False
    assert ambig_disc.status == "ambiguous_profile_mapping"
    assert ambig_disc.candidate_policy is None
    assert ambig_disc.ambiguous_policy_ids == ("rp-tenant-dev-paper-low-v1", "rp-tenant-dev-paper-low-v2-alt")
    assert ambig_disc.ambiguous_decision_ids == ("apv-arbitrary-gov-low-901", "apv-arbitrary-gov-low-902")

    ambig_res = resolve_pool_risk_policy_binding(
        capital_pool_id=pool_id,
        pool=base_pool,
        expected_tenant_id="tenant-dev",
        expected_stage="paper",
        requested_profile="low",
        require_approved_profile_mapping=True,
        approval_reader=SnapshotApprovalReader([policy_scoped_decision, second_decision]),
        as_of="2026-11-22T00:00:00Z",
    )
    assert ambig_res.resolved is False
    assert ambig_res.status == "ambiguous_profile_mapping"
    assert ambig_res.reason_code == "risk_policy_profile_mapping_ambiguous"
    assert ambig_res.owner_decision_request["absence_source"] == "policy_candidates_ambiguous"
    assert ambig_res.allow_new_or_increased_risk is False

    # 9. Bounded lookup on runtime order evaluation: once profile_mapping_approval_id is persisted on the pool,
    # resolve_bound_pool_risk_policy performs 1 exact-ID get() call and 0 list_for_target() scans.
    (policy_dir / "rp-tenant-dev-paper-low-v2-alt.json").unlink()
    get_calls: list[str] = []
    list_calls: list[tuple[str, str]] = []

    class CountingReader:
        def get(self, decision_id: str):
            get_calls.append(decision_id)
            return SnapshotApprovalReader([policy_scoped_decision]).get(decision_id)

        def list_for_target(self, *, target_type: str, target_id: str, **kwargs):
            list_calls.append((target_type, target_id))
            return SnapshotApprovalReader([policy_scoped_decision]).list_for_target(
                target_type=target_type,
                target_id=target_id,
                **kwargs,
            )

    converged_pool = {
        **base_pool,
        "risk_policy_ref": "rp-tenant-dev-paper-low-v1",
        "metadata": {
            **base_pool["metadata"],
            "requested_risk_profile": "low",
            "require_approved_profile_mapping": True,
            "profile_mapping_approval_id": "apv-arbitrary-gov-low-901",
            "risk_policy": valid_low_policy,
        },
    }
    converged_pool["approval_digest"] = capital_pool_approval_digest(converged_pool)
    bound_pol = resolve_bound_pool_risk_policy(
        capital_pool_id=pool_id,
        pool=converged_pool,
        expected_tenant_id="tenant-dev",
        expected_stage="paper",
        approval_reader=CountingReader(),
        as_of="2026-11-22T00:00:00Z",
    )
    assert bound_pol is not None
    assert bound_pol.risk_policy_id == "rp-tenant-dev-paper-low-v1"
    assert bound_pol.gross_limit == 0.8
    assert get_calls == ["apv-arbitrary-gov-low-901"]
    assert list_calls == []

    # 10. P1-1 overflow regressions:
    # (a) 65 catalog policies (> MAX_POLICY_CATALOG_FILES=64) with valid approvals on files 1 and 65
    # must fail closed with ambiguous_profile_mapping and allow_new_or_increased_risk=False while
    # keeping catalog file reads bounded to MAX_POLICY_CATALOG_FILES (64).
    (policy_dir / "rp-low-label-only.json").unlink()
    (policy_dir / "rp-generic-2x.json").unlink()
    (policy_dir / "rp-tenant-dev-paper-low-v1.json").unlink()

    overflow_policies = [
        {
            "risk_policy_id": f"rp-low-{i:03d}",
            "version": "1.0",
            "status": "active",
            "allowed_stages": ["paper"],
            "gross_limit": 0.8,
            "net_limit": 0.6,
            "max_leverage": 1.0,
            "metadata": {"tenant_id": "tenant-dev", "requested_profile": "low"},
        }
        for i in range(65)
    ]
    for pol_item in overflow_policies:
        (policy_dir / f"{pol_item['risk_policy_id']}.json").write_text(json.dumps(pol_item), encoding="utf-8")

    def _make_policy_decision(did: str, pol_dict: dict, *, pool_scoped: bool = False) -> dict:
        target = pool_id if pool_scoped else pol_dict["risk_policy_id"]
        cand_pool = {
            **base_pool,
            "risk_policy_ref": pol_dict["risk_policy_id"],
            "metadata": {
                **base_pool["metadata"],
                "requested_risk_profile": "low",
                "require_approved_profile_mapping": True,
                "risk_policy": pol_dict,
            },
        }
        ver = capital_pool_approval_digest(cand_pool) if pool_scoped else risk_policy_digest(pol_dict)
        return approval_snapshot(
            decision_id=did,
            tenant_id="tenant-dev",
            target_type="capital_pool_activation",
            target_id=target,
            target_version=ver,
            owner_user_id="proposer-1",
            risk_level="medium",
            metadata={
                "subject": {
                    "pool_id": target,
                    "risk_direction": "increase",
                    "requested_risk": "low",
                    "risk_policy_id": pol_dict["risk_policy_id"],
                    "risk_policy_version": "1.0",
                    "risk_policy_digest": risk_policy_digest(pol_dict),
                },
                "approvals": [
                    {"actor_id": "reviewer-1", "actor_role": "risk_owner"},
                    {"actor_id": "reviewer-2", "actor_role": "governance_reviewer"},
                ],
            },
        )

    cat_overflow_decisions = [
        _make_policy_decision("valid-first", overflow_policies[0]),
        _make_policy_decision("valid-last", overflow_policies[-1]),
    ]
    loaded_policy_count = 0

    def _counting_loader(pid: str):
        nonlocal loaded_policy_count
        loaded_policy_count += 1
        return json.loads((policy_dir / f"{pid}.json").read_text(encoding="utf-8"))

    cat_65_disc = discover_approved_profile_risk_policy(
        resolved_pool_id=pool_id,
        effective_tenant_id="tenant-dev",
        clean_stage="paper",
        clean_profile="low",
        base_pool=base_pool,
        policy_dir=policy_dir,
        risk_policy_loader=_counting_loader,
        approval_reader=SnapshotApprovalReader(cat_overflow_decisions),
    )
    assert loaded_policy_count == 64
    assert cat_65_disc.resolved is False
    assert cat_65_disc.status == "ambiguous_profile_mapping"
    assert cat_65_disc.approval_decision_id is None

    cat_65_bind = resolve_pool_risk_policy_binding(
        pool=base_pool,
        expected_tenant_id="tenant-dev",
        requested_profile="low",
        require_approved_profile_mapping=True,
        approval_reader=SnapshotApprovalReader(cat_overflow_decisions),
    )
    assert cat_65_bind.resolved is False
    assert cat_65_bind.status == "ambiguous_profile_mapping"
    assert cat_65_bind.allow_new_or_increased_risk is False
    assert cat_65_bind.profile_mapping_approval_id is None

    for p in policy_dir.glob("*.json"):
        p.unlink()

    # (b) 17 same-target decisions [valid-first, 15 rejected history records, valid-last]
    # (> MAX_BOUNDED_APPROVAL_CANDIDATES=16) fail closed with ambiguous_profile_mapping and
    # allow_new_or_increased_risk=False (matching [valid-first, valid-last, *history]).
    dedicated_low = {
        "risk_policy_id": "rp-dedicated-low",
        "version": "1.0",
        "status": "active",
        "allowed_stages": ["paper"],
        "gross_limit": 0.8,
        "net_limit": 0.6,
        "max_leverage": 1.0,
        "metadata": {"tenant_id": "tenant-dev", "requested_profile": "low"},
    }
    (policy_dir / "rp-dedicated-low.json").write_text(json.dumps(dedicated_low), encoding="utf-8")
    dec_first = _make_policy_decision("valid-first", dedicated_low)
    dec_last = _make_policy_decision("valid-last", dedicated_low)
    rejected_history = [
        {**dec_first, "decision_id": f"rejected-{i:02d}", "decision": "rejected"}
        for i in range(15)
    ]

    for dec_list in ([dec_first, *rejected_history, dec_last], [dec_first, dec_last, *rejected_history]):
        apv_17_disc = discover_approved_profile_risk_policy(
            resolved_pool_id=pool_id,
            effective_tenant_id="tenant-dev",
            clean_stage="paper",
            clean_profile="low",
            base_pool=base_pool,
            policy_dir=policy_dir,
            approval_reader=SnapshotApprovalReader(dec_list),
        )
        assert apv_17_disc.resolved is False
        assert apv_17_disc.status == "ambiguous_profile_mapping"
        assert apv_17_disc.approval_decision_id is None

        apv_17_bind = resolve_pool_risk_policy_binding(
            pool=base_pool,
            expected_tenant_id="tenant-dev",
            requested_profile="low",
            require_approved_profile_mapping=True,
            approval_reader=SnapshotApprovalReader(dec_list),
        )
        assert apv_17_bind.resolved is False
        assert apv_17_bind.status == "ambiguous_profile_mapping"
        assert apv_17_bind.allow_new_or_increased_risk is False
        assert apv_17_bind.profile_mapping_approval_id is None

    # 11. P1-2 pre-existing approval then policy revocation / unapproved veto regression across
    # discovered, exact-ID, embedded, explicit, and persisted paths:
    pool_scoped_apv = _make_policy_decision("pool-approved-revoked-policy", dedicated_low, pool_scoped=True)
    pre_revocation_digest = risk_policy_digest(dedicated_low)
    revoked_low = json.loads(json.dumps(dedicated_low))
    revoked_low["metadata"]["approval_status"] = "revoked"
    (policy_dir / "rp-dedicated-low.json").write_text(json.dumps(revoked_low), encoding="utf-8")
    assert risk_policy_digest(revoked_low) == pre_revocation_digest

    # (a) Discovered path via pool-targeted decision referencing catalog policy ID
    rev_disc = discover_approved_profile_risk_policy(
        resolved_pool_id=pool_id,
        effective_tenant_id="tenant-dev",
        clean_stage="paper",
        clean_profile="low",
        base_pool=base_pool,
        policy_dir=policy_dir,
        approval_reader=SnapshotApprovalReader([pool_scoped_apv]),
    )
    assert rev_disc.resolved is False
    assert rev_disc.status == "unresolved_profile_mapping"
    assert rev_disc.absence_source == "policy_approval_revoked"
    assert rev_disc.approval_decision_id is None

    rev_bind_disc = resolve_pool_risk_policy_binding(
        pool=base_pool,
        expected_tenant_id="tenant-dev",
        requested_profile="low",
        require_approved_profile_mapping=True,
        approval_reader=SnapshotApprovalReader([pool_scoped_apv]),
    )
    assert rev_bind_disc.resolved is False
    assert rev_bind_disc.status == "unresolved_profile_mapping"
    assert rev_bind_disc.allow_new_or_increased_risk is False
    assert rev_bind_disc.owner_decision_request["absence_source"] == "policy_approval_revoked"

    # (b) Exact-ID path with explicit risk_policy_ref and explicit approval_decision_id
    rev_bind_exact = resolve_pool_risk_policy_binding(
        pool=base_pool,
        risk_policy_ref="rp-dedicated-low",
        expected_tenant_id="tenant-dev",
        requested_profile="low",
        require_approved_profile_mapping=True,
        approval_decision_id="pool-approved-revoked-policy",
        approval_reader=SnapshotApprovalReader([pool_scoped_apv]),
    )
    assert rev_bind_exact.resolved is False
    assert rev_bind_exact.status == "unresolved_profile_mapping"
    assert rev_bind_exact.allow_new_or_increased_risk is False
    assert rev_bind_exact.owner_decision_request["absence_source"] == "policy_approval_revoked"

    # (c) Embedded path: decision embeds unrevoked snapshot of rp-dedicated-low while catalog is revoked,
    # or embeds a standalone revoked policy
    apv_with_embedded_snapshot = json.loads(json.dumps(pool_scoped_apv))
    apv_with_embedded_snapshot["metadata"]["subject"]["risk_policy"] = dedicated_low
    rev_disc_emb = discover_approved_profile_risk_policy(
        resolved_pool_id=pool_id,
        effective_tenant_id="tenant-dev",
        clean_stage="paper",
        clean_profile="low",
        base_pool=base_pool,
        policy_dir=policy_dir,
        approval_reader=SnapshotApprovalReader([apv_with_embedded_snapshot]),
    )
    assert rev_disc_emb.resolved is False
    assert rev_disc_emb.absence_source == "policy_approval_revoked"

    embedded_only_revoked = {
        **revoked_low,
        "risk_policy_id": "rp-embedded-only-revoked",
    }
    apv_embedded_only = _make_policy_decision("apv-emb-only", embedded_only_revoked, pool_scoped=True)
    apv_embedded_only["metadata"]["subject"]["risk_policy"] = embedded_only_revoked
    rev_disc_emb_only = discover_approved_profile_risk_policy(
        resolved_pool_id=pool_id,
        effective_tenant_id="tenant-dev",
        clean_stage="paper",
        clean_profile="low",
        base_pool=base_pool,
        policy_dir=policy_dir,
        approval_reader=SnapshotApprovalReader([apv_embedded_only]),
    )
    assert rev_disc_emb_only.resolved is False
    assert rev_disc_emb_only.absence_source == "policy_approval_revoked"

    # (d) Explicit path (require_approved_profile_mapping=False, both via risk_policy_ref and inline risk_policy)
    rev_bind_explicit_ref = resolve_pool_risk_policy_binding(
        pool={**base_pool, "risk_policy_ref": "rp-dedicated-low"},
        expected_tenant_id="tenant-dev",
        require_approved_profile_mapping=False,
    )
    assert rev_bind_explicit_ref.resolved is False
    assert rev_bind_explicit_ref.allow_new_or_increased_risk is False
    assert rev_bind_explicit_ref.owner_decision_request["absence_source"] == "policy_approval_revoked"

    rev_bind_explicit_inline = resolve_pool_risk_policy_binding(
        pool={**base_pool, "risk_policy": revoked_low},
        expected_tenant_id="tenant-dev",
        require_approved_profile_mapping=False,
    )
    assert rev_bind_explicit_inline.resolved is False
    assert rev_bind_explicit_inline.allow_new_or_increased_risk is False
    assert rev_bind_explicit_inline.owner_decision_request["absence_source"] == "policy_approval_revoked"

    # (e) Persisted path: pool persisted pre-revocation inline snapshot + approval ID, then catalog file was revoked
    persisted_pre_revocation_pool = {
        **base_pool,
        "risk_policy_ref": "rp-dedicated-low",
        "metadata": {
            **base_pool["metadata"],
            "requested_risk_profile": "low",
            "require_approved_profile_mapping": True,
            "profile_mapping_approval_id": "pool-approved-revoked-policy",
            "risk_policy": dedicated_low,
        },
    }
    persisted_pre_revocation_pool["approval_digest"] = capital_pool_approval_digest(persisted_pre_revocation_pool)
    for persisted_variant in (
        persisted_pre_revocation_pool,
        {**persisted_pre_revocation_pool, "risk_policy_ref": None},
    ):
        rev_bind_persisted = resolve_pool_risk_policy_binding(
            pool=persisted_variant,
            expected_tenant_id="tenant-dev",
            requested_profile="low",
            require_approved_profile_mapping=True,
            approval_reader=SnapshotApprovalReader([pool_scoped_apv]),
        )
        assert rev_bind_persisted.resolved is False
        assert rev_bind_persisted.status == "unresolved_profile_mapping"
        assert rev_bind_persisted.allow_new_or_increased_risk is False
        assert rev_bind_persisted.owner_decision_request["absence_source"] == "policy_approval_revoked"

    # (f) Persisted auto-resolved catalog policy with normal coordinator metadata
    # (require_approved_profile_mapping=True, profile_mapping_approval_id set, no profile_auto_resolved)
    # must fail closed across resolve_pool_risk_policy_binding and resolve_bound_pool_risk_policy
    # when the owner ApprovalDecision is subsequently revoked, expired, rejected, cross-tenant, or removed.
    import copy
    import pytest
    from services.capital.risk_policy import RiskPolicyError

    owner_low_pol = {
        "risk_policy_id": "rp-owner-low",
        "version": "1.0",
        "status": "active",
        "allowed_stages": ["paper"],
        "gross_limit": 0.8,
        "net_limit": 0.6,
        "max_leverage": 1.0,
        "metadata": {"tenant_id": "tenant-dev", "requested_profile": "low"},
    }
    (policy_dir / "rp-owner-low.json").write_text(json.dumps(owner_low_pol), encoding="utf-8")
    (policy_dir / "rp-dedicated-low.json").unlink()

    persisted_review_pool = {
        "pool_id": "pool-review",
        "tenant_id": "tenant-dev",
        "owner_id": "tenant-dev",
        "owner_type": "org",
        "currency": "USD",
        "budget": 100000.0,
        "single_runtime_enforced": True,
        "status": "active",
        "metadata": {"tenant_id": "tenant-dev", "execution_context": "paper"},
    }
    arbitrary_low_dec = approval_snapshot(
        decision_id="arbitrary-approved-low",
        tenant_id="tenant-dev",
        target_type="capital_pool_activation",
        target_id=owner_low_pol["risk_policy_id"],
        target_version=risk_policy_digest(owner_low_pol),
        owner_user_id="proposer",
        risk_level="medium",
        metadata={
            "subject": {
                "pool_id": owner_low_pol["risk_policy_id"],
                "risk_direction": "increase",
                "requested_risk": "low",
                "risk_policy_id": owner_low_pol["risk_policy_id"],
                "risk_policy_version": "1.0",
                "risk_policy_digest": risk_policy_digest(owner_low_pol),
            },
            "approvals": [
                {"actor_id": "reviewer-1", "actor_role": "risk_owner"},
                {"actor_id": "reviewer-2", "actor_role": "governance_reviewer"},
            ],
        },
    )
    auto_owner_low = discover_approved_profile_risk_policy(
        resolved_pool_id=persisted_review_pool["pool_id"],
        effective_tenant_id="tenant-dev",
        clean_profile="low",
        base_pool=persisted_review_pool,
        approval_reader=SnapshotApprovalReader([arbitrary_low_dec]),
    )
    assert auto_owner_low.resolved is True
    assert auto_owner_low.risk_policy_ref == "rp-owner-low"
    assert auto_owner_low.approval_decision_id == "arbitrary-approved-low"

    persisted_review_pool["risk_policy_ref"] = auto_owner_low.risk_policy_ref
    persisted_review_pool["metadata"].update(
        risk_policy=auto_owner_low.policy.to_dict(),
        requested_risk_profile="low",
        require_approved_profile_mapping=True,
        profile_mapping_approval_id=auto_owner_low.approval_decision_id,
    )
    assert "profile_auto_resolved" not in persisted_review_pool["metadata"]

    valid_persisted_reader = SnapshotApprovalReader([copy.deepcopy(arbitrary_low_dec)])
    valid_persisted_res = resolve_pool_risk_policy_binding(
        pool=persisted_review_pool,
        expected_tenant_id="tenant-dev",
        expected_stage="paper",
        approval_reader=valid_persisted_reader,
    )
    assert valid_persisted_res.resolved is True
    assert valid_persisted_res.status == "valid"
    assert valid_persisted_res.profile_mapping_status == "approved_profile_mapping"
    assert valid_persisted_res.profile_mapping_approval_id == "arbitrary-approved-low"
    assert valid_persisted_res.allow_new_or_increased_risk is True
    valid_bound_pol = resolve_bound_pool_risk_policy(
        pool=persisted_review_pool,
        expected_tenant_id="tenant-dev",
        expected_stage="paper",
        approval_reader=valid_persisted_reader,
    )
    assert valid_bound_pol is not None
    assert valid_bound_pol.risk_policy_id == "rp-owner-low"
    assert valid_bound_pol.gross_limit == 0.8

    for case_name, dec_mutation, expected_absence in [
        ("revoked", {"revoked_at": "2026-11-22T00:00:00Z"}, "policy_approval_revoked"),
        ("expired", {"expires_at": "2000-01-01T00:00:00Z"}, "policy_approval_expired"),
        ("rejected", {"decision": "rejected"}, "policy_approval_rejected"),
        ("cross_tenant", {"tenant_id": "tenant-other"}, "cross_tenant_policy_or_approval_rejected"),
    ]:
        mutated_reader = SnapshotApprovalReader([{**copy.deepcopy(arbitrary_low_dec), **dec_mutation}])
        mutated_res = resolve_pool_risk_policy_binding(
            pool=persisted_review_pool,
            expected_tenant_id="tenant-dev",
            expected_stage="paper",
            approval_reader=mutated_reader,
        )
        assert mutated_res.resolved is False, case_name
        assert mutated_res.status == "unresolved_profile_mapping", case_name
        assert mutated_res.profile_mapping_status == "unresolved_owner_decision_required", case_name
        assert mutated_res.profile_mapping_approval_id is None, case_name
        assert mutated_res.allow_new_or_increased_risk is False, case_name
        assert mutated_res.owner_decision_request["absence_source"] == expected_absence, case_name
        with pytest.raises(RiskPolicyError, match="risk_policy_profile_mapping_unapproved"):
            resolve_bound_pool_risk_policy(
                pool=persisted_review_pool,
                expected_tenant_id="tenant-dev",
                expected_stage="paper",
                approval_reader=mutated_reader,
            )

    missing_res = resolve_pool_risk_policy_binding(
        pool=persisted_review_pool,
        expected_tenant_id="tenant-dev",
        expected_stage="paper",
        approval_reader=SnapshotApprovalReader([]),
    )
    assert missing_res.resolved is False
    assert missing_res.status == "unresolved_profile_mapping"
    assert missing_res.profile_mapping_status == "unresolved_owner_decision_required"
    assert missing_res.profile_mapping_approval_id is None
    assert missing_res.allow_new_or_increased_risk is False
    assert missing_res.owner_decision_request["absence_source"] == "policy_approval_missing"
    with pytest.raises(RiskPolicyError, match="risk_policy_profile_mapping_unapproved"):
        resolve_bound_pool_risk_policy(
            pool=persisted_review_pool,
            expected_tenant_id="tenant-dev",
            expected_stage="paper",
            approval_reader=SnapshotApprovalReader([]),
        )



def test_runtime_order_risk_gate_retains_governed_reduction_and_close_for_unresolved_and_expired_owner_mapping(
    monkeypatch,
) -> None:
    import pytest
    from services.capital.risk_policy import (
        RiskPolicyError,
        capital_pool_approval_digest,
        resolve_bound_pool_risk_policy,
        risk_policy_digest,
    )
    from services.execution.lean_runtime.paper_runtime import PaperExecutionAlgorithm
    from services.governance import approval_authority
    from services.governance.test_approval_authority import (
        SnapshotApprovalReader,
        approval_snapshot,
    )

    pool_id = "pool-persona-paper-review"
    inline_policy = {
        "risk_policy_id": f"rp-{pool_id}",
        "version": "1.0",
        "status": "active",
        "allowed_stages": ["paper"],
        "allowed_asset_classes": ["equity", "future"],
        "gross_limit": 2.0,
        "net_limit": 2.0,
        "max_leverage": 2.0,
    }
    unapproved_pool = {
        "pool_id": pool_id,
        "tenant_id": "tenant-dev",
        "owner_id": "tenant-dev",
        "owner_type": "org",
        "status": "active",
        "currency": "TWD",
        "budget": 600_000.0,
        "single_runtime_enforced": True,
        "metadata": {
            "internal": True,
            "execution_context": "paper",
            "tenant_id": "tenant-dev",
            "requested_risk_profile": "low",
            "risk_policy": dict(inline_policy),
        },
    }
    unapproved_pool["approval_digest"] = capital_pool_approval_digest(unapproved_pool)

    def _seeded_algo(pool_obj: dict) -> PaperExecutionAlgorithm:
        algo = PaperExecutionAlgorithm(
            initial_cash=600_000.0,
            persona_binding_id="pcb-review",
            capital_pool_id=pool_id,
            capital_pool_reader=lambda _pid: pool_obj,
            in_process_broker=True,
        )
        algo._holding("2330").Quantity = 1000.0
        algo._instrument_positions[("pcb-review", "2330", "eq:2330")] = {
            "persona_binding_id": "pcb-review",
            "normalised_underlying": "2330",
            "instrument_id": "eq:2330",
            "symbol": "2330",
            "kind": "cash_share",
            "quantity": 1000.0,
            "multiplier": 1.0,
            "average_entry_price": 600.0,
            "mark_price": 600.0,
            "status": "open",
        }
        return algo

    # 1. Unresolved owner mapping: direct resolver still fails closed, while EvaluateOrderRiskGate
    # preserves full close and partial reduction under applicable controls and blocks opening/increase/reversal.
    with pytest.raises(RiskPolicyError, match="risk_policy_profile_mapping_unapproved"):
        resolve_bound_pool_risk_policy(
            capital_pool_id=pool_id,
            pool=unapproved_pool,
        )
    reduced_policy = resolve_bound_pool_risk_policy(
        capital_pool_id=pool_id,
        pool=unapproved_pool,
        allow_risk_reducing_actions=True,
    )
    assert reduced_policy is not None
    assert reduced_policy.risk_policy_id == f"rp-{pool_id}"

    algo = _seeded_algo(unapproved_pool)

    # Full close: 1000 -> 0
    full_close = algo.EvaluateOrderRiskGate(
        symbol="2330",
        side="sell",
        quantity=1000.0,
        price=600.0,
        instrument_id="eq:2330",
        persona_binding_id="pcb-review",
    )
    assert full_close["accepted"] is True
    assert full_close["risk_status"] == "succeeded"
    assert full_close["reason"] is None
    assert full_close["required"] == 0.0
    assert full_close["limit"] == 2.0
    close_checks = {c["check_name"]: c["observed"] for c in full_close["evaluation"]["checks"]}
    assert close_checks["gross_exposure"] == 0.0
    assert close_checks["net_exposure"] == 0.0
    assert close_checks["leverage"] == 0.0

    # Partial reduction: 1000 -> 600
    partial_reduce = algo.EvaluateOrderRiskGate(
        symbol="2330",
        side="sell",
        quantity=400.0,
        price=600.0,
        instrument_id="eq:2330",
        persona_binding_id="pcb-review",
    )
    assert partial_reduce["accepted"] is True
    assert partial_reduce["risk_status"] == "succeeded"
    assert partial_reduce["reason"] is None
    partial_checks = {c["check_name"]: c["observed"] for c in partial_reduce["evaluation"]["checks"]}
    assert 0.0 < partial_checks["gross_exposure"] <= 2.0
    assert 0.0 < partial_checks["net_exposure"] <= 2.0
    assert 0.0 < partial_checks["leverage"] <= 2.0

    # Blocked opening on a flat instrument: 0 -> +100
    blocked_open = algo.EvaluateOrderRiskGate(
        symbol="2317",
        side="buy",
        quantity=100.0,
        price=100.0,
        instrument_id="eq:2317",
        persona_binding_id="pcb-review",
    )
    assert blocked_open["accepted"] is False
    assert blocked_open["risk_status"] == "rejected"
    assert blocked_open["reason"] == "risk_policy_unavailable"
    assert "risk_policy_profile_mapping_unapproved" in str(blocked_open["available"])

    # Blocked increase on existing position: 1000 -> 1100
    blocked_increase = algo.EvaluateOrderRiskGate(
        symbol="2330",
        side="buy",
        quantity=100.0,
        price=600.0,
        instrument_id="eq:2330",
        persona_binding_id="pcb-review",
    )
    assert blocked_increase["accepted"] is False
    assert blocked_increase["risk_status"] == "rejected"
    assert blocked_increase["reason"] == "risk_policy_unavailable"
    assert "risk_policy_profile_mapping_unapproved" in str(blocked_increase["available"])

    # Blocked sign reversal on existing position: 1000 -> -200
    blocked_reversal = algo.EvaluateOrderRiskGate(
        symbol="2330",
        side="sell",
        quantity=1200.0,
        price=600.0,
        instrument_id="eq:2330",
        persona_binding_id="pcb-review",
    )
    assert blocked_reversal["accepted"] is False
    assert blocked_reversal["risk_status"] == "rejected"
    assert blocked_reversal["reason"] == "risk_policy_unavailable"
    assert "risk_policy_profile_mapping_unapproved" in str(blocked_reversal["available"])

    # 2. Expired owner mapping approval (both expired ApprovalDecision in owner reader and expired policy timestamp):
    # retains full close and partial reduction under applicable controls while blocking opening/increase/reversal.
    expired_pool = {
        **unapproved_pool,
        "metadata": {
            **unapproved_pool["metadata"],
            "require_approved_profile_mapping": True,
            "profile_mapping_approval_id": "apv-review-expired",
        },
    }
    expired_pool["approval_digest"] = capital_pool_approval_digest(expired_pool)
    expired_decision = approval_snapshot(
        decision_id="apv-review-expired",
        tenant_id="tenant-dev",
        target_type="capital_pool_activation",
        target_id=pool_id,
        target_version=expired_pool["approval_digest"],
        owner_user_id="proposer-1",
        risk_level="low",
        decided_at="2020-01-01T00:00:00Z",
        recorded_at="2020-01-01T00:00:00Z",
        expires_at="2020-01-02T00:00:00Z",
        metadata={
            "subject": {
                "pool_id": pool_id,
                "risk_direction": "increase",
                "requested_risk": "low",
                "risk_policy_id": inline_policy["risk_policy_id"],
                "risk_policy_version": "1.0",
                "risk_policy_digest": risk_policy_digest(inline_policy),
            },
            "approvals": [
                {"actor_id": "reviewer-1", "actor_role": "governance_reviewer"},
                {"actor_id": "reviewer-2", "actor_role": "governance_reviewer"},
            ],
        },
    )
    monkeypatch.setattr(
        approval_authority,
        "configured_approval_reader",
        lambda domain="capital", **kw: SnapshotApprovalReader(expired_decision),
    )

    for candidate_expired_pool in (
        expired_pool,
        {
            **unapproved_pool,
            "metadata": {
                **unapproved_pool["metadata"],
                "risk_policy_expires_at": "2020-01-02T00:00:00Z",
            },
        },
    ):
        algo_exp = _seeded_algo(candidate_expired_pool)
        exp_close = algo_exp.EvaluateOrderRiskGate(
            symbol="2330",
            side="sell",
            quantity=1000.0,
            price=600.0,
            instrument_id="eq:2330",
            persona_binding_id="pcb-review",
        )
        assert exp_close["accepted"] is True
        exp_partial = algo_exp.EvaluateOrderRiskGate(
            symbol="2330",
            side="sell",
            quantity=500.0,
            price=600.0,
            instrument_id="eq:2330",
            persona_binding_id="pcb-review",
        )
        assert exp_partial["accepted"] is True
        exp_open = algo_exp.EvaluateOrderRiskGate(
            symbol="2317",
            side="buy",
            quantity=100.0,
            price=100.0,
            instrument_id="eq:2317",
            persona_binding_id="pcb-review",
        )
        assert exp_open["accepted"] is False
        assert exp_open["reason"] == "risk_policy_unavailable"
        exp_inc = algo_exp.EvaluateOrderRiskGate(
            symbol="2330",
            side="buy",
            quantity=100.0,
            price=600.0,
            instrument_id="eq:2330",
            persona_binding_id="pcb-review",
        )
        assert exp_inc["accepted"] is False
        assert exp_inc["reason"] == "risk_policy_unavailable"
        exp_rev = algo_exp.EvaluateOrderRiskGate(
            symbol="2330",
            side="sell",
            quantity=1500.0,
            price=600.0,
            instrument_id="eq:2330",
            persona_binding_id="pcb-review",
        )
        assert exp_rev["accepted"] is False
        assert exp_rev["reason"] == "risk_policy_unavailable"

    # 3. Applicable controls remain enforced during reduction/close (asset class, single-name weight, settlement_pending,
    # and invalid/inactive/wrong-tenant policies never bypass fail-closed checks).
    futures_only_pool = {
        **unapproved_pool,
        "metadata": {
            **unapproved_pool["metadata"],
            "risk_policy": {
                **inline_policy,
                "allowed_asset_classes": ["future"],
            },
        },
    }
    algo_asset_ctrl = _seeded_algo(futures_only_pool)
    asset_rejected = algo_asset_ctrl.EvaluateOrderRiskGate(
        symbol="2330",
        side="sell",
        quantity=1000.0,
        price=600.0,
        instrument_id="eq:2330",
        persona_binding_id="pcb-review",
    )
    assert asset_rejected["accepted"] is False
    assert asset_rejected["reason"] == "asset_class_not_allowed"

    tight_single_name_pool = {
        **unapproved_pool,
        "metadata": {
            **unapproved_pool["metadata"],
            "risk_policy": {
                **inline_policy,
                "max_single_name_weight": 0.10,
            },
        },
    }
    algo_weight_ctrl = _seeded_algo(tight_single_name_pool)
    partial_weight_preserved = algo_weight_ctrl.EvaluateOrderRiskGate(
        symbol="2330",
        side="sell",
        quantity=400.0,
        price=600.0,
        instrument_id="eq:2330",
        persona_binding_id="pcb-review",
    )
    assert partial_weight_preserved["accepted"] is True
    assert partial_weight_preserved["risk_reducing_exit_preserved"] is True
    full_close_weight_ok = algo_weight_ctrl.EvaluateOrderRiskGate(
        symbol="2330",
        side="sell",
        quantity=1000.0,
        price=600.0,
        instrument_id="eq:2330",
        persona_binding_id="pcb-review",
    )
    assert full_close_weight_ok["accepted"] is True

    for non_eligible_pool in (
        {
            **unapproved_pool,
            "metadata": {
                **unapproved_pool["metadata"],
                "risk_policy": {**inline_policy, "status": "draft"},
            },
        },
        {
            **unapproved_pool,
            "metadata": {
                **unapproved_pool["metadata"],
                "risk_policy": {**inline_policy, "gross_limit": -1.0},
            },
        },
        {
            **unapproved_pool,
            "metadata": {
                **unapproved_pool["metadata"],
                "risk_policy": {**inline_policy, "tenant_id": "tenant-foreign"},
            },
        },
    ):
        algo_bad = _seeded_algo(non_eligible_pool)
        bad_close = algo_bad.EvaluateOrderRiskGate(
            symbol="2330",
            side="sell",
            quantity=1000.0,
            price=600.0,
            instrument_id="eq:2330",
            persona_binding_id="pcb-review",
        )
        assert bad_close["accepted"] is False
        assert bad_close["reason"] == "risk_policy_unavailable"

    # 4. End-to-end SubmitTaiwanBrokerOrder execution on unresolved/expired owner mapping:
    # blocks buy increase/open, executes partial reduction and full close fills.
    events = []
    algo_exec = PaperExecutionAlgorithm(
        initial_cash=600_000.0,
        event_sink=events.append,
        persona_binding_id="pcb-review",
        capital_pool_id=pool_id,
        capital_pool_reader=lambda _pid: unapproved_pool,
        in_process_broker=True,
    )
    algo_exec._cash = 0.0
    algo_exec._holding("2330.TW").Quantity = 1000.0
    algo_exec._instrument_positions[("pcb-review", "2330", "eq:2330")] = {
        "persona_binding_id": "pcb-review",
        "normalised_underlying": "2330",
        "instrument_id": "eq:2330",
        "symbol": "2330.TW",
        "kind": "cash_share",
        "quantity": 1000.0,
        "multiplier": 1.0,
        "average_entry_price": 600.0,
        "mark_price": 600.0,
        "status": "open",
    }
    algo_exec.SetCurrentSignalContext(
        {
            "market_price": 600.0,
            "market_price_as_of": "2026-11-21T05:30:00Z",
            "market_price_source": "source-ingest://snapshots/mss-2330",
        }
    )
    algo_exec.SubmitTaiwanBrokerOrder(
        "2330.TW",
        signal_id="sig-inc-blocked",
        side="buy",
        quantity=100.0,
        quantity_type="SHARES",
        action="BUY",
    )
    assert events[-1].event_type == "order_rejection"
    assert events[-1].metadata["reason"] == "risk_policy_unavailable"

    algo_exec.SubmitTaiwanBrokerOrder(
        "2330.TW",
        signal_id="sig-partial-reduce",
        side="sell",
        quantity=400.0,
        quantity_type="SHARES",
        action="SELL",
    )
    assert events[-1].event_type == "paper_fill_simulated"
    assert algo_exec._holding("2330.TW").Quantity == 600.0

    algo_exec.SubmitTaiwanBrokerOrder(
        "2330.TW",
        signal_id="sig-full-close",
        side="sell",
        quantity=600.0,
        quantity_type="SHARES",
        action="SELL",
    )
    assert events[-1].event_type == "paper_fill_simulated"
    assert algo_exec._holding("2330.TW").Quantity == 0.0


def test_cash_reserve_policy_limits_digest_binding_and_evaluator() -> None:
    """PAPER-SIZING-BOUND-RISK-POLICY-20261121: governed cash-reserve rules on RiskPolicy."""
    from services.capital.risk_policy import (
        resolve_pool_risk_policy_binding,
        risk_policy_digest,
    )

    baseline = RiskPolicy.from_mapping(
        {
            "risk_policy_id": "rp-baseline",
            "version": "1.0",
            "status": "active",
            "allowed_stages": ["paper"],
            "gross_limit": 2.0,
            "net_limit": 2.0,
            "max_leverage": 2.0,
        }
    )
    assert baseline.min_cash_reserve_pct is None
    assert baseline.min_cash_reserve is None
    assert "min_cash_reserve_pct" not in baseline.to_dict()
    assert "min_cash_reserve" not in baseline.to_dict()

    with_reserve = RiskPolicy.from_mapping(
        {
            **baseline.to_dict(),
            "version": "1.1",
            "min_cash_reserve_pct": 0.15,
            "min_cash_reserve": 10000.0,
        }
    )
    assert with_reserve.min_cash_reserve_pct == 0.15
    assert with_reserve.min_cash_reserve == 10000.0
    assert risk_policy_digest(with_reserve) != risk_policy_digest(baseline)

    # Binding resolution validates min_cash_reserve_pct in [0, 1)
    valid_res = resolve_pool_risk_policy_binding(
        capital_pool_id="pool-reserve",
        pool={"pool_id": "pool-reserve", "budget": 100000.0, "risk_policy": with_reserve.to_dict()},
        require_approved_profile_mapping=False,
    )
    assert valid_res.resolved is True
    assert valid_res.effective_limits["min_cash_reserve_pct"] == 0.15
    assert valid_res.effective_limits["min_cash_reserve"] == 10000.0

    invalid_res = resolve_pool_risk_policy_binding(
        capital_pool_id="pool-reserve",
        pool={
            "pool_id": "pool-reserve",
            "budget": 100000.0,
            "risk_policy": {**with_reserve.to_dict(), "min_cash_reserve_pct": 1.0},
        },
        require_approved_profile_mapping=False,
    )
    assert invalid_res.resolved is False
    assert invalid_res.reason_code == "risk_policy_invalid_limit"

    evaluator = RiskPolicyEvaluator()
    allowed_eval = evaluator.evaluate(
        with_reserve,
        {
            "target_type": "runtime_action",
            "target_id": "ord-1",
            "capital_pool_id": "pool-reserve",
            "stage": "paper",
            "gross_exposure": 0.8,
            "net_exposure": 0.8,
            "leverage": 0.8,
            "cash_reserve_pct": 0.20,
            "cash_reserve": 20000.0,
        },
    )
    assert allowed_eval.allowed is True

    rejected_eval = evaluator.evaluate(
        with_reserve,
        {
            "target_type": "runtime_action",
            "target_id": "ord-2",
            "capital_pool_id": "pool-reserve",
            "stage": "paper",
            "gross_exposure": 0.85,
            "net_exposure": 0.85,
            "leverage": 0.85,
            "cash_reserve_pct": 0.12,
            "cash_reserve": 12000.0,
        },
    )
    assert rejected_eval.rejected is True
    assert any(c.code == "cash_reserve_breach" for c in rejected_eval.checks)

