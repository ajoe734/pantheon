from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest

from scripts import verify_e2e_producer_chain as verifier


def _policy() -> verifier.ProducerPolicy:
    return verifier.ProducerPolicy(
        threshold={
            "metric_name": "rolling_drawdown_multiple",
            "summary_field": "drawdown",
            "ratio_baseline_key": "expected_drawdown",
            "telemetry_event_type": "drawdown_snapshot",
            "comparator": "gt",
            "threshold_value": 1.25,
            "window": "paper-daily-sweep",
            "policy_source": "governed-test-policy",
        },
        baselines={
            "artifact-governed": {
                "expected_drawdown": 0.12,
                "policy_source": "governed-test-baseline",
            }
        },
    )


def _binding() -> dict[str, str]:
    return {
        "binding_id": "rb-canonical",
        "runtime_id": "runtime-paper",
        "capital_pool_id": "pool-paper",
        "artifact_id": "artifact-governed",
        "artifact_version": "1.2.3",
        "deployment_stage": "paper",
        "deployment_plan_id": "plan-canonical",
        "plan_id": "plan-canonical",
        "persona_capital_binding_id": "pcb-canonical",
        "persona_id": "persona-paper",
    }


def _identity() -> verifier.ProducerIdentity:
    return verifier._producer_identity(
        "rb-canonical",
        _policy().threshold,
        moment=datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc),
    )


def test_select_binding_joins_bff_runtime_to_canonical_summary_and_governed_baseline() -> None:
    runtimes = [
        {
            "runtime_id": "runtime-paper",
            "binding_id": "bff-local-alias",
            "status": "running",
            "deployment_mode": "paper",
            "persona_id": "persona-paper",
            "capital_pool_id": "pool-bff",
            "persona_capital_binding_id": "pcb-bff",
            "plan_id": "plan-bff",
        },
        {
            "runtime_id": "runtime-other",
            "binding_id": "rb-other",
            "status": "active",
            "deployment_mode": "paper",
            "persona_id": "persona-other",
        },
    ]
    summaries = [
        {
            "runtime_id": "runtime-paper",
            "binding_id": "rb-canonical",
            "deployment_stage": "paper",
            "artifact_id": "artifact-governed",
            "artifact_version": "1.2.3",
            "capital_pool_id": "pool-paper",
            "persona_capital_binding_id": "pcb-canonical",
            "deployment_plan_id": "plan-canonical",
        }
    ]

    selected, baseline = verifier._select_binding(
        runtimes,
        summaries,
        _policy(),
        requested_runtime_id="runtime-paper",
    )

    assert {field: selected[field] for field in _binding()} == _binding()
    assert baseline == 0.12
    assert verifier._breach_value(baseline, _policy().threshold) > baseline * 1.25


def test_select_binding_fails_closed_without_governed_baseline() -> None:
    policy = verifier.ProducerPolicy(threshold=_policy().threshold, baselines={})
    with pytest.raises(verifier.VerificationFailure, match="governed baseline") as exc:
        verifier._select_binding(
            [
                {
                    "runtime_id": "runtime-paper",
                    "status": "active",
                    "deployment_mode": "paper",
                    "persona_id": "persona-paper",
                }
            ],
            [
                {
                    "runtime_id": "runtime-paper",
                    "binding_id": "rb-canonical",
                    "deployment_stage": "paper",
                    "artifact_id": "artifact-ungoverned",
                }
            ],
            policy,
            requested_runtime_id="runtime-paper",
        )
    assert exc.value.stage == "binding_resolution"


def test_producer_identity_matches_worker_and_incident_consumer_algorithms() -> None:
    identity = _identity()
    expected_key = json.dumps(
        [
            "rb-canonical",
            "rolling_drawdown_multiple",
            "paper-daily-sweep:2026-07-15",
        ],
        separators=(",", ":"),
    )
    expected_event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, expected_key))
    expected_incident_id = (
        "inc-threshold-"
        + uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{expected_event_id}:rolling_drawdown_multiple",
        ).hex[:12]
    )

    assert identity.dedupe_key == expected_key
    assert identity.event_id == expected_event_id
    assert identity.incident_id == expected_incident_id


def test_incident_assertion_rejects_old_same_binding_drawdown_incident() -> None:
    identity = _identity()
    old_incident = {
        **_binding(),
        "incident_id": "inc-threshold-old",
        "status": "open",
        "title": "Threshold breach: rolling_drawdown_multiple",
        "telemetry_event_ids": ["old-event"],
        "evidence_summary": "dedupe_key=old-key",
    }

    with pytest.raises(verifier.VerificationFailure) as exc:
        verifier._assert_incident(old_incident, _binding(), identity)

    assert exc.value.stage == "incident_dedupe"


def test_incident_assertion_requires_exact_dedupe_marker_and_event() -> None:
    identity = _identity()
    incident = {
        **_binding(),
        "incident_id": identity.incident_id,
        "telemetry_event_ids": [identity.event_id],
        "evidence_summary": f"evidence; dedupe_key={identity.dedupe_key}; baseline=0.12",
    }

    verifier._assert_incident(incident, _binding(), identity)


def test_sweep_item_uses_exact_incident_instead_of_first_item() -> None:
    payload = {
        "items": [
            {
                "incident_id": "inc-unrelated",
                "status": "created",
                "decision_id": "decision-unrelated",
            },
            {
                "incident_id": _identity().incident_id,
                "status": "existing",
                "decision_id": "decision-exact",
            },
        ]
    }

    item = verifier._sweep_item(payload, _identity().incident_id)

    assert item["decision_id"] == "decision-exact"


def test_sweep_item_reports_cooldown_as_proposal_stage_failure() -> None:
    with pytest.raises(verifier.VerificationFailure) as exc:
        verifier._sweep_item(
            {
                "items": [
                    {
                        "incident_id": _identity().incident_id,
                        "status": "cooldown_blocked",
                        "active_decision_id": "decision-other",
                    }
                ]
            },
            _identity().incident_id,
        )
    assert exc.value.stage == "proposal_sweep"


def test_proposal_assertion_requires_incident_threshold_and_evidence_lineage() -> None:
    identity = _identity()
    proposal = {
        "linked_incident_id": identity.incident_id,
        "target_id": _binding()["artifact_id"],
        "target_version": _binding()["artifact_version"],
        "threshold_snapshots": [
            {
                "metric_name": _policy().threshold["metric_name"],
                "window": identity.window,
            }
        ],
        "evidence_refs": [
            {"ref_id": identity.incident_id},
            {"ref_id": identity.event_id},
        ],
    }

    verifier._assert_proposal(proposal, _binding(), identity, _policy().threshold)

    proposal["linked_incident_id"] = "inc-wrong"
    with pytest.raises(verifier.VerificationFailure) as exc:
        verifier._assert_proposal(proposal, _binding(), identity, _policy().threshold)
    assert exc.value.stage == "proposal_sweep"


def test_formal_journal_requires_exact_live_mutation_review_not_substring() -> None:
    decision_id = "decision-123"
    items = [
        {
            "entry_type": "evolution_decision",
            "source_id": decision_id,
            "origin": "live",
        },
        {
            "entry_type": "mutation_review",
            "source_id": f"prefix-{decision_id}",
            "origin": "live",
        },
    ]
    with pytest.raises(verifier.VerificationFailure) as exc:
        verifier._formal_journal_entry(items, decision_id)
    assert exc.value.stage == "formal_journal"

    items.append(
        {
            "id": f"mutation_review:{decision_id}",
            "entry_type": "mutation_review",
            "source_id": decision_id,
            "origin": "live",
        }
    )
    assert verifier._formal_journal_entry(items, decision_id)["source_id"] == decision_id


def test_fleet_link_requires_exact_journal_navigation() -> None:
    persona = {
        "id": "persona-paper",
        "last_mutation_kind": "formal_mutation",
        "mutation_entry_id": "decision-123",
        "evolution_entry_id": "decision-123",
        "mutation_confidence": "formal",
        "evolution_href": (
            "/management/evolution-journal"
            "?persona=persona-paper&mutation_review=decision-123"
        ),
    }
    verifier._assert_fleet_link(persona, "persona-paper", "decision-123")

    persona["evolution_href"] = "/management/evolution-journal?persona=persona-paper"
    with pytest.raises(verifier.VerificationFailure) as exc:
        verifier._assert_fleet_link(persona, "persona-paper", "decision-123")
    assert exc.value.stage == "persona_fleet"


def test_main_requires_explicit_mutating_opt_in(monkeypatch, capsys) -> None:
    monkeypatch.delenv("ALLOW_MUTATING_E2E", raising=False)
    monkeypatch.delenv("EVOCHAIN_VERIFY_RUNTIME_ID", raising=False)

    assert verifier.main() == 1
    assert "FAIL [mutation_opt_in]" in capsys.readouterr().err
