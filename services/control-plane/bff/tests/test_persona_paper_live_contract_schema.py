from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[4]
SCHEMA_PATH = ROOT / "services/control-plane/specs/persona_paper_live.schema.json"
BFF_CONTRACT_PATH = ROOT / "services/control-plane/bff/BFF_API_CONTRACT.md"
OLD_WIZARD_PATH = (
    ROOT
    / "docs/04/pantheon_persona_onboarding_wizard_2026-05-28/PERSONA_ONBOARDING_WIZARD_SPEC.md"
)


def _schema() -> dict:
    with SCHEMA_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _validator_for(def_name: str) -> Draft202012Validator:
    schema = _schema()
    return Draft202012Validator(
        {
            "$schema": schema["$schema"],
            "$defs": schema["$defs"],
            "$ref": f"#/$defs/{def_name}",
        }
    )


def _enum(def_name: str) -> list[str]:
    return list(_schema()["$defs"][def_name]["enum"])


def test_persona_paper_live_schema_is_valid_and_defines_contract_objects() -> None:
    schema = _schema()
    Draft202012Validator.check_schema(schema)

    expected_defs = {
        "PaperPersonaLaunch",
        "PersonaReadinessProjection",
        "PaperEvaluationSnapshot",
        "PromotionScoreSnapshot",
        "CohortRankingSnapshot",
        "CompetitionStandingSnapshot",
        "HumanReviewRequest",
        "QuarterlyRebalanceProposal",
        "RiskGuardrailEvent",
    }
    assert expected_defs.issubset(schema["$defs"])


def test_paper_launch_contract_is_paper_only_and_repairable() -> None:
    schema = _schema()
    launch = schema["$defs"]["PaperPersonaLaunch"]
    selection = schema["$defs"]["PaperCapitalPoolSelection"]

    assert set(_enum("LaunchStatus")) == {
        "paper_provisioning",
        "paper_running",
        "paper_warming_up",
        "setup_failed",
        "repair_required",
    }
    assert "completed_steps" in launch["required"]
    assert "retryable" in launch["required"]
    assert "trace_id" in launch["required"]
    assert selection["properties"]["capital_scope"]["const"] == "paper"
    assert "live" not in selection["properties"]["mode"]["enum"]

    sample = {
        "launch_id": "launch-001",
        "name": "US Equity Paper Persona",
        "mandate": "Paper trade US equity momentum with governed data sources.",
        "strategy_family": ["momentum"],
        "market_scope": ["US"],
        "source_scope": ["polygon"],
        "risk_profile_id": "risk-paper-default",
        "paper_capital_pool": {
            "mode": "create_from_template",
            "capital_scope": "paper",
            "capital_pool_id": None,
            "template_id": "paper-pool-default",
        },
        "paper_budget": 100000,
        "artifact_id": "artifact-us-momo-v1",
        "operator_note": None,
        "status": "paper_running",
        "persona_id": "persona-us-paper",
        "capital_pool_id": "pool-us-paper",
        "binding_id": "pcb-us-paper",
        "deployment_plan_id": "dp-us-paper",
        "approval_decision_id": "approval-paper-auto",
        "runtime_binding_id": "rb-us-paper",
        "runtime_id": "runtime-us-paper",
        "completed_steps": [
            "persona_identity_created",
            "paper_capital_pool_ready",
            "paper_binding_active",
            "paper_deployment_plan_created",
            "paper_approval_recorded",
            "paper_runtime_binding_created",
            "paper_runtime_started",
            "telemetry_heartbeat_verified",
        ],
        "failed_step": None,
        "retryable": False,
        "repair_url": None,
        "trace_id": "trace-paper-launch",
        "created_at": "2026-07-02T00:00:00Z",
    }
    _validator_for("PaperPersonaLaunch").validate(sample)


def test_unified_competition_and_human_review_gates_are_schema_required() -> None:
    schema = _schema()
    standing = schema["$defs"]["CompetitionStandingSnapshot"]
    ranking = schema["$defs"]["CohortRankingSnapshot"]
    review = schema["$defs"]["HumanReviewRequest"]
    quarterly = schema["$defs"]["QuarterlyRebalanceProposal"]

    assert set(_enum("CompetitionTrack")) == {
        "paper_challenger",
        "canary_challenger",
        "live_incumbent",
        "watchlist_incumbent",
        "risk_off_excluded",
    }
    assert "competition_track" in standing["required"]
    assert "cohort_rank" in standing["required"]
    assert "product_lifecycle_state" in standing["required"]
    assert ranking["properties"]["human_review_required_for_actions"]["const"] is True

    review_types = set(review["properties"]["review_type"]["enum"])
    assert {
        "promotion_to_canary",
        "canary_to_live",
        "quarterly_rebalance",
        "resume_after_incident",
        "retire",
    }.issubset(review_types)
    assert review["properties"]["decision_required"]["const"] is True
    assert review["properties"]["system_authority"]["const"] == "advisory_only"
    assert quarterly["properties"]["human_review_required"]["const"] is True


def test_risk_guardrail_event_cannot_promote_or_increase_allocation() -> None:
    risk_event = _schema()["$defs"]["RiskGuardrailEvent"]
    assert set(risk_event["properties"]["automatic_action"]["enum"]) == {
        "pause_new_orders",
        "reduce_exposure",
        "risk_off",
        "frozen",
    }
    assert risk_event["properties"]["review_required"]["const"] is True
    assert risk_event["properties"]["may_promote"]["const"] is False
    assert risk_event["properties"]["may_increase_allocation"]["const"] is False

    sample = {
        "event_id": "guardrail-001",
        "persona_id": "persona-live-incumbent",
        "runtime_binding_id": "rb-live",
        "capital_pool_id": "pool-live",
        "trigger_name": "daily_loss_budget",
        "observed_value": -0.04,
        "threshold": -0.03,
        "automatic_action": "pause_new_orders",
        "effective_at": "2026-07-02T00:00:00Z",
        "incident_id": "incident-001",
        "review_required": True,
        "may_promote": False,
        "may_increase_allocation": False,
        "trace_id": "trace-risk-001",
    }
    _validator_for("RiskGuardrailEvent").validate(sample)


def test_bff_contract_and_old_wizard_document_paper_first_invariants() -> None:
    bff_contract = BFF_CONTRACT_PATH.read_text(encoding="utf-8")
    old_wizard = OLD_WIZARD_PATH.read_text(encoding="utf-8")

    assert "POST" in bff_contract
    assert "/bff/management/personas/paper-launch" in bff_contract
    assert "/bff/management/personas/competition-standings" in bff_contract
    assert "competition_track" in bff_contract
    assert "setup_failed" in bff_contract
    assert "Automatic guardrails cannot promote or increase allocation" in bff_contract
    assert "Do not show `啟動精靈` for an already runnable persona" in bff_contract

    assert "2026-07-02 supersession note" in old_wizard
    assert "paper runtime" in old_wizard
    assert "`setup_failed` / `repair_required`" in old_wizard
