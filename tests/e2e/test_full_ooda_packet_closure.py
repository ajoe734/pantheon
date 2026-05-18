"""OODA-E2E-007: close the full paper OodaLoopPacket evidence chain."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
OODA_DIR = REPO_ROOT / "services" / "control-plane" / "ooda"
if str(OODA_DIR) not in sys.path:
    sys.path.insert(0, str(OODA_DIR))

from ooda_loop_packet import (  # noqa: E402
    ActBundle,
    DecideBundle,
    LearnBundle,
    LoopEnvironment,
    LoopStatus,
    LoopType,
    ObservationWindow,
    ObserveBundle,
    OodaLoopPacket,
    OrientBundle,
    validate_packet,
)
from stage_transition import assert_complete_replay_path, validate_status_transition  # noqa: E402


EVIDENCE_DIR = REPO_ROOT / "support" / "evidence" / "OODA-E2E-PROOF"
FULL_PACKET_PATH = EVIDENCE_DIR / "full_packet.json"
CLOSURE_SUMMARY_PATH = EVIDENCE_DIR / "closure_summary.md"
GENERATED_AT = "2026-05-18T03:10:00Z"
PACKET_ID = "ooda-e2e-007-full-packet"

TRANSITION_TESTS = [
    {
        "task_id": "OODA-E2E-001",
        "stage": "observe",
        "test_file": "tests/e2e/test_source_to_strategy_spec.py",
        "evidence_path": "support/evidence/OODA-E2E-001/closeout_note.md",
    },
    {
        "task_id": "OODA-E2E-002",
        "stage": "orient",
        "test_file": "tests/e2e/test_strategy_spec_to_experiment_run.py",
        "evidence_path": "support/evidence/OODA-E2E-002/closeout.md",
    },
    {
        "task_id": "OODA-E2E-003",
        "stage": "orient",
        "test_file": "tests/e2e/test_experiment_run_to_admission.py",
        "evidence_path": "support/evidence/OODA-E2E-003/closeout.md",
    },
    {
        "task_id": "OODA-E2E-004",
        "stage": "decide",
        "test_file": "tests/e2e/test_admission_to_deployment_plan.py",
        "evidence_path": "support/evidence/OODA-E2E-004/closeout.md",
    },
    {
        "task_id": "OODA-E2E-005",
        "stage": "act",
        "test_file": "tests/e2e/test_deployment_plan_to_paper_run.py",
        "evidence_path": "support/evidence/OODA-E2E-005/closeout_summary.md",
    },
    {
        "task_id": "OODA-E2E-006",
        "stage": "learn",
        "test_file": "tests/e2e/test_paper_run_to_evolution_decision.py",
        "evidence_path": "ai-task-archive/tasks/OODA-E2E-006.json",
    },
]


def test_full_ooda_packet_closure_runs_transition_tests_and_writes_evidence() -> None:
    transition_results = _run_transition_tests()
    full_packet = build_full_packet(transition_results)

    ooda_packet = full_packet["ooda_packet"]
    assert full_packet["packet_id"] == PACKET_ID
    assert full_packet["loop_type"] == "paper_strategy"
    assert full_packet["status"] == "closed"
    assert ooda_packet["observe"]["source_refs"]
    assert ooda_packet["orient"]["allocation_proposal_refs"]
    assert ooda_packet["decide"]["deployment_plan_id"]
    assert ooda_packet["act"]["runtime_binding_id"]
    assert ooda_packet["learn"]["evolution_followthrough_refs"]
    assert ooda_packet["act"]["live_capital_side_effects"] is False
    assert full_packet["validation_errors"] == []
    assert all(result["passed"] for result in transition_results)
    assert len(full_packet["sub_test_evidence"]) == 6
    assert len(full_packet["artifact_ids"]) >= 12

    write_evidence(full_packet)
    written = json.loads(FULL_PACKET_PATH.read_text(encoding="utf-8"))
    assert written["ooda_packet"] == ooda_packet
    assert CLOSURE_SUMMARY_PATH.exists()


def _run_transition_tests() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PANTHEON_VECTORBT_BACKEND"] = "stub"

    for step in TRANSITION_TESTS:
        command = [sys.executable, "-m", "pytest", "-q", "-x", step["test_file"]]
        recorded_command = f"python3 -m pytest -q -x {step['test_file']}"
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        result = {
            "task_id": step["task_id"],
            "stage": step["stage"],
            "test_file": step["test_file"],
            "command": recorded_command,
            "returncode": completed.returncode,
            "passed": completed.returncode == 0,
        }
        results.append(result)
        assert completed.returncode == 0, (
            f"{step['task_id']} transition test failed with exit {completed.returncode}\n"
            f"Command: {' '.join(command)}\n"
            f"{completed.stdout}"
        )
    return results


def build_full_packet(transition_results: list[dict[str, Any]]) -> dict[str, Any]:
    strategy_spec = _read_json("tests/e2e/fixtures/strategy_spec_for_experiment.json")
    experiment_fixture = _read_json("tests/e2e/fixtures/experiment_run_for_admission.json")
    decision_fixture = _read_json("tests/e2e/fixtures/candidate_artifact_for_decision.json")
    runtime_plan = _read_json("tests/e2e/fixtures/deployment_plan_for_runtime.json")
    telemetry = _read_json("tests/e2e/fixtures/synthetic_incident_telemetry.json")

    source_record_ref = "source-record:ooda-e2e-001-internal-note"
    strategy_artifact_id = strategy_spec["metadata"]["strategy_spec_artifact_id"]
    experiment_task_id = strategy_spec["metadata"]["experiment_task_id"]
    experiment_run_id = experiment_fixture["experiment_run"]["run_id"]
    candidate_artifact_id = experiment_fixture["artifact"]["artifact_id"]
    registry_candidate_id = decision_fixture["candidate_artifact"]["registry_id"]
    approval_decision_id = runtime_plan["approval_decision_id"]
    deployment_plan_id = runtime_plan["plan_id"]
    runtime_binding_id = "rtb-ooda-e2e-007-paper-closure"
    incident_id = "inc-ooda-e2e-006-001"
    postmortem_id = "pm-ooda-e2e-006-001"
    evolution_proposal_id = f"evolution-proposal:{postmortem_id}:rollback"

    packet = OodaLoopPacket(
        packet_id=PACKET_ID,
        loop_type=LoopType.PAPER_STRATEGY,
        status=LoopStatus.CLOSED,
        environment=LoopEnvironment.PAPER,
        created_at=GENERATED_AT,
        updated_at=GENERATED_AT,
        closed_at=GENERATED_AT,
        capital_pool_id=runtime_plan["capital_pool_id"],
        strategy_id=runtime_plan["strategy_id"],
        persona_ids=[decision_fixture["persona_capital_binding"]["persona_id"]],
        observe=ObserveBundle(
            source_refs=[
                source_record_ref,
                "file://tests/e2e/fixtures/sample_internal_research_note.md",
            ],
            telemetry_refs=[telemetry["event_id"]],
        ),
        orient=OrientBundle(
            allocation_proposal_refs=[
                "allocation-proposal://OODA-E2E-002/vectorbt-sma-cross-paper",
                f"candidate-artifact:{candidate_artifact_id}",
            ],
            signal_inference_refs=[
                strategy_artifact_id,
                experiment_run_id,
            ],
            evidence_bundle_refs=[
                "support/evidence/OODA-E2E-002/closeout.md",
                "support/evidence/OODA-E2E-003/closeout.md",
            ],
        ),
        decide=DecideBundle(
            approval_decision_id=approval_decision_id,
            deployment_plan_id=deployment_plan_id,
            decision_rationale_ref="support/evidence/OODA-E2E-004/closeout.md",
            policy_decision_refs=[
                decision_fixture["approval_decision"]["decision_id"],
                decision_fixture["deployment_plan"]["plan_id"],
            ],
        ),
        act=ActBundle(
            runtime_binding_id=runtime_binding_id,
            command_receipt_refs=[
                f"runtime-manager://deploy/{deployment_plan_id}",
                "lean-smoke://OODA-E2E-005/five-day-paper-run",
            ],
            broker_evidence_refs=[
                "paper-broker://no-live-submission/OODA-E2E-005",
            ],
            live_capital_side_effects=False,
        ),
        learn=LearnBundle(
            telemetry_refs=[telemetry["event_id"]],
            postmortem_refs=[postmortem_id],
            evolution_followthrough_refs=[
                evolution_proposal_id,
                "postmortem-bridge://OODA-E2E-006/high-severity-rollback",
            ],
            observation_window=ObservationWindow(
                start_at=telemetry["timestamp"],
                end_at=GENERATED_AT,
                duration_hours=27.1667,
            ),
        ),
        audit_refs=[
            "support/evidence/OODA-E2E-PROOF/closure_summary.md",
        ],
    )

    transitions = _stage_transitions(packet.packet_id)
    assert_complete_replay_path(transitions)
    packet_payload = packet.to_dict()
    validation_errors = validate_packet(packet_payload)

    artifact_ids = [
        _artifact("observe", "OODA-E2E-001", source_record_ref, "normalized SourceRecord"),
        _artifact("orient", "OODA-E2E-002", strategy_artifact_id, "StrategySpec artifact"),
        _artifact("orient", "OODA-E2E-002", experiment_task_id, "ExperimentTask"),
        _artifact("orient", "OODA-E2E-003", experiment_run_id, "ExperimentRun"),
        _artifact("orient", "OODA-E2E-003", candidate_artifact_id, "CandidateArtifact payload"),
        _artifact("decide", "OODA-E2E-004", registry_candidate_id, "candidate registry entry"),
        _artifact("decide", "OODA-E2E-004", decision_fixture["approval_decision"]["decision_id"], "ApprovalDecision"),
        _artifact("decide", "OODA-E2E-004", decision_fixture["deployment_plan"]["plan_id"], "governance paper DeploymentPlan"),
        _artifact("decide", "OODA-E2E-005", deployment_plan_id, "runtime-ready paper DeploymentPlan"),
        _artifact("act", "OODA-E2E-005", runtime_binding_id, "RuntimeBinding closure ref"),
        _artifact("act", "OODA-E2E-005", runtime_plan["artifact_id"], "paper execution artifact"),
        _artifact("learn", "OODA-E2E-006", telemetry["event_id"], "paper anomaly telemetry"),
        _artifact("learn", "OODA-E2E-006", incident_id, "IncidentCase"),
        _artifact("learn", "OODA-E2E-006", postmortem_id, "Postmortem"),
        _artifact("learn", "OODA-E2E-006", evolution_proposal_id, "EvolutionDecisionProposal"),
    ]

    return {
        "task_id": "OODA-E2E-007",
        "generated_at": GENERATED_AT,
        "packet_id": packet_payload["packet_id"],
        "loop_type": packet_payload["loop_type"],
        "status": packet_payload["status"],
        "transition_test_run": transition_results,
        "sub_test_evidence": [
            {
                "task_id": step["task_id"],
                "stage": step["stage"],
                "test_file": step["test_file"],
                "evidence_path": step["evidence_path"],
            }
            for step in TRANSITION_TESTS
        ],
        "stage_transitions": transitions,
        "artifact_ids": artifact_ids,
        "ooda_packet": packet_payload,
        "validation_errors": validation_errors,
        "acceptance_assertions": {
            "all_transition_tests_passed": all(result["passed"] for result in transition_results),
            "packet_closed": packet_payload["status"] == "closed",
            "packet_loop_type_paper_strategy": packet_payload["loop_type"] == "paper_strategy",
            "observe_source_refs_non_null": bool(packet_payload["observe"]["source_refs"]),
            "orient_allocation_proposal_refs_non_null": bool(packet_payload["orient"]["allocation_proposal_refs"]),
            "decide_deployment_plan_id_non_null": bool(packet_payload["decide"]["deployment_plan_id"]),
            "act_runtime_binding_id_non_null": bool(packet_payload["act"]["runtime_binding_id"]),
            "learn_evolution_followthrough_refs_non_null": bool(packet_payload["learn"]["evolution_followthrough_refs"]),
            "act_live_capital_side_effects_false": packet_payload["act"]["live_capital_side_effects"] is False,
            "validation_errors_empty": validation_errors == [],
        },
    }


def write_evidence(full_packet: dict[str, Any]) -> None:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    FULL_PACKET_PATH.write_text(
        json.dumps(full_packet, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    CLOSURE_SUMMARY_PATH.write_text(_render_summary(full_packet), encoding="utf-8")


def _stage_transitions(packet_id: str) -> list[dict[str, Any]]:
    statuses = [
        ("open", "observing", "observe"),
        ("observing", "oriented", "orient"),
        ("oriented", "decided", "decide"),
        ("decided", "acted", "act"),
        ("acted", "evolving", "learn"),
        ("evolving", "closed", "close"),
    ]
    transitions: list[dict[str, Any]] = []
    for from_status, to_status, event in statuses:
        transition = validate_status_transition(from_status, to_status).to_dict()
        transition.update(
            {
                "packet_id": packet_id,
                "event": event,
                "transitioned_at": GENERATED_AT,
            }
        )
        transitions.append(transition)
    return transitions


def _artifact(stage: str, task_id: str, artifact_id: str, description: str) -> dict[str, str]:
    return {
        "stage": stage,
        "task_id": task_id,
        "artifact_id": artifact_id,
        "description": description,
    }


def _read_json(relative_path: str) -> dict[str, Any]:
    return json.loads((REPO_ROOT / relative_path).read_text(encoding="utf-8"))


def _render_summary(full_packet: dict[str, Any]) -> str:
    evidence_rows = "\n".join(
        "| {task_id} | {stage} | [{test_file}](../../../{test_file}) | [{evidence_path}](../../../{evidence_path}) |".format(
            **entry
        )
        for entry in full_packet["sub_test_evidence"]
    )
    artifact_rows = "\n".join(
        f"| {item['stage']} | {item['task_id']} | `{item['artifact_id']}` | {item['description']} |"
        for item in full_packet["artifact_ids"]
    )
    assertions = full_packet["acceptance_assertions"]
    assertion_rows = "\n".join(
        f"| `{key}` | {str(value).lower()} |"
        for key, value in assertions.items()
    )
    return (
        "# OODA-E2E-007 Closure Summary\n\n"
        "Task: OODA E2E #7 - full OodaLoopPacket closure + evidence chain\n"
        f"Generated: {full_packet['generated_at']}\n\n"
        "## Packet\n\n"
        f"- Packet ID: `{full_packet['packet_id']}`\n"
        f"- Loop type: `{full_packet['loop_type']}`\n"
        f"- Status: `{full_packet['status']}`\n"
        "- Environment: `paper`\n"
        "- Live capital side effects: `false`\n"
        f"- Evidence packet: [`full_packet.json`](full_packet.json)\n\n"
        "## Transition Test Evidence\n\n"
        "| Task | Stage | Test | Evidence |\n"
        "|---|---|---|---|\n"
        f"{evidence_rows}\n\n"
        "## Artifact IDs\n\n"
        "| Stage | Task | Artifact ID | Description |\n"
        "|---|---|---|---|\n"
        f"{artifact_rows}\n\n"
        "## Acceptance Assertions\n\n"
        "| Assertion | Value |\n"
        "|---|---|\n"
        f"{assertion_rows}\n"
    )
