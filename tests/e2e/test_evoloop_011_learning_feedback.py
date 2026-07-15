from __future__ import annotations

import json
import tempfile
from pathlib import Path
import pytest

from services.memory.persona_memory_store import PersonaMemoryStore
from services.memory.institutional_memory_store import InstitutionalMemoryStore
from integrations.openclaw.persona_memory_bridge import materialize_openclaw_memory_context
from services.persona.learning_feedback_bridge import run_learning_feedback_bridge, parse_persona_id_from_pcb


def test_parse_persona_id_from_pcb():
    assert parse_persona_id_from_pcb("pcb-persona-tw-equity-paper") == "persona-tw-equity"
    assert parse_persona_id_from_pcb("pcb-usability-persona-crypto") == "persona-crypto"
    assert parse_persona_id_from_pcb("pcb-persona-tw-equity") == "persona-tw-equity"
    assert parse_persona_id_from_pcb("") == ""


def test_evoloop_011_learning_feedback_loop_e2e(tmp_path):
    # 1. Setup isolated memory stores
    persona_path = tmp_path / "persona_memory_entries.json"
    inst_path = tmp_path / "institutional_memory_entries.json"
    
    persona_store = PersonaMemoryStore(path=persona_path)
    inst_store = InstitutionalMemoryStore(path=inst_path)

    # 2. Setup mock decisions JSON file
    # Decision A: Executed retrain with successful outcome
    # Decision B: Executed with NO outcome summary (should not create memory)
    # Decision C: Proposed state only (should not create memory)
    decisions = {
        "decision-retrain-ok": {
            "decision_id": "decision-retrain-ok",
            "target_type": "candidate_artifact",
            "target_id": "artifact-tw-session-momentum-v1",
            "target_version": "1.0.0",
            "action_type": "retrain",
            "decision_state": "executed",
            "persona_id": "persona-tw-equity",
            "execution_result": {
                "status": "completed",
                "plane": "research",
                "executed_at": "2026-07-15T00:00:00Z",
                "execution_ref_id": "dispatch-decision-retrain-ok",
                "outcome_summary": "Retrain succeeded. Mutated parameters: lookback_bars=3. Registered artifact-tw-session-momentum-v2.",
            }
        },
        "decision-no-outcome": {
            "decision_id": "decision-no-outcome",
            "target_type": "candidate_artifact",
            "target_id": "artifact-tw-session-momentum-v1",
            "target_version": "1.0.0",
            "action_type": "retrain",
            "decision_state": "executed",
            "persona_id": "persona-tw-equity",
            "execution_result": {
                "status": "submitted",
                "plane": "research",
                "executed_at": "2026-07-15T00:00:00Z",
                "execution_ref_id": "dispatch-decision-no-outcome",
                "outcome_summary": None,  # empty
            }
        },
        "decision-proposed": {
            "decision_id": "decision-proposed",
            "target_type": "candidate_artifact",
            "target_id": "artifact-tw-session-momentum-v1",
            "target_version": "1.0.0",
            "action_type": "retrain",
            "decision_state": "proposed",
            "persona_id": "persona-tw-equity",
            "execution_result": None
        }
    }
    
    dec_file = tmp_path / "decisions.json"
    dec_file.write_text(json.dumps(decisions))

    # 3. Setup mock postmortems JSON file
    # Postmortem A: Published status
    # Postmortem B: Draft status (should not create memory)
    incidents = {
        "incidents": [],
        "postmortems": [
            {
                "postmortem_id": "pm-high-drawdown-published",
                "title": "Postmortem for high drawdown breach",
                "status": "published",
                "created_at": "2026-07-15T00:01:00Z",
                "incident_id": "inc-high-drawdown",
                "binding_id": "binding-tw-equity",
                "deployment_stage": "paper",
                "deployment_plan_id": "plan-tw-equity",
                "capital_pool_id": "pool-tw-equity",
                "persona_capital_binding_id": "pcb-persona-tw-equity-paper",
                "artifact_id": "artifact-tw-session-momentum-v1",
                "artifact_version": "1.0.0",
                "runtime_id": "rt-tw-equity",
                "trace_id": "trace-tw-equity",
                "root_cause": "SMA crossovers triggered false signals during volatile regimes.",
                "published_at": "2026-07-15T00:02:00Z"
            },
            {
                "postmortem_id": "pm-draft",
                "title": "Draft postmortem",
                "status": "draft",
                "created_at": "2026-07-15T00:01:00Z",
                "incident_id": "inc-high-drawdown",
                "binding_id": "binding-tw-equity",
                "deployment_stage": "paper",
                "deployment_plan_id": "plan-tw-equity",
                "capital_pool_id": "pool-tw-equity",
                "persona_capital_binding_id": "pcb-persona-tw-equity-paper",
                "artifact_id": "artifact-tw-session-momentum-v1",
                "artifact_version": "1.0.0",
                "runtime_id": "rt-tw-equity",
                "trace_id": "trace-tw-equity",
                "root_cause": "Unfinished analysis.",
            }
        ]
    }

    inc_file = tmp_path / "incidents.json"
    inc_file.write_text(json.dumps(incidents))

    # 4. Run the learning feedback bridge first time
    report = run_learning_feedback_bridge(
        decisions_store_path=dec_file,
        incidents_store_path=inc_file,
        persona_store=persona_store,
        institutional_store=inst_store,
        skip_openclaw_sync=True
    )

    # 5. Assertions on first execution
    assert report["processed_decisions"] == 1  # only decision-retrain-ok
    assert report["processed_postmortems"] == 1  # only pm-high-drawdown-published
    assert report["written_decisions"] == ["decision-retrain-ok"]
    assert report["written_postmortems"] == ["pm-high-drawdown-published"]
    assert len(report["errors"]) == 0

    # Verify memory contents
    # Check that exactly one persona memory entry for the decision exists
    decision_mem = persona_store.find_by_source_event(
        source_event_type="evolution_decision_approved",
        source_event_id="decision-retrain-ok",
        active_only=False
    )
    assert len(decision_mem) == 1
    assert "Retrain succeeded" in decision_mem[0].content["summary"]
    assert decision_mem[0].persona_id == "persona-tw-equity"

    # Check that exactly one persona memory entry for the postmortem exists
    pm_mem = persona_store.find_by_source_event(
        source_event_type="postmortem_published",
        source_event_id="pm-high-drawdown-published",
        active_only=False
    )
    assert len(pm_mem) == 1
    assert "SMA crossovers triggered false signals" in pm_mem[0].content["summary"]
    assert pm_mem[0].persona_id == "persona-tw-equity"

    # Fail-closed check: Make sure decision-no-outcome and pm-draft did NOT create memory entries
    no_outcome_mem = persona_store.find_by_source_event(
        source_event_type="evolution_decision_approved",
        source_event_id="decision-no-outcome",
        active_only=False
    )
    assert len(no_outcome_mem) == 0

    draft_mem = persona_store.find_by_source_event(
        source_event_type="postmortem_published",
        source_event_id="pm-draft",
        active_only=False
    )
    assert len(draft_mem) == 0

    # 6. Check Idempotency: Run again and verify no duplicate records are created
    report_again = run_learning_feedback_bridge(
        decisions_store_path=dec_file,
        incidents_store_path=inc_file,
        persona_store=persona_store,
        institutional_store=inst_store,
        skip_openclaw_sync=True
    )
    assert report_again["processed_decisions"] == 1
    assert report_again["processed_postmortems"] == 1
    assert len(report_again["written_decisions"]) == 0  # not written again
    assert len(report_again["written_postmortems"]) == 0  # not written again

    # 7. Check OpenClaw Materialization logic
    # Fetch memory entries for the persona
    retrieval_hits = []
    # Fetch persona-level entries
    hits = persona_store.retrieve(persona_id="persona-tw-equity", query="retrain and drawdown lessons", limit=10)
    for hit in hits:
        retrieval_hits.append({
            "type": "persona",
            "relevance_score": hit.relevance_score,
            "entry": hit.entry.to_dict()
        })
    # Fetch institutional entries
    inst_hits = inst_store.retrieve(query="retrain and drawdown lessons", limit=10)
    for hit in inst_hits:
        retrieval_hits.append({
            "type": "institutional",
            "relevance_score": hit.relevance_score,
            "entry": hit.entry.to_dict()
        })

    retrieval_payload = {"hits": retrieval_hits}

    # Materialize memory into a simulated agent workspace
    workspace_dir = tmp_path / "workspace_persona_tw_equity"
    workspace_dir.mkdir()
    materialize_openclaw_memory_context(
        persona_id="persona-tw-equity",
        workspace=str(workspace_dir),
        retrieval_payload=retrieval_payload,
        query="retrain and drawdown lessons",
        limit=8
    )

    # Read the materialized MEMORY.md file and verify the persona references the outcome and root cause
    memory_md_path = workspace_dir / "MEMORY.md"
    assert memory_md_path.exists()
    content = memory_md_path.read_text(encoding="utf-8")
    
    # Check for the citations and summary text
    assert "Retrain succeeded" in content
    assert "SMA crossovers triggered false signals" in content
    assert "pm-high-drawdown-published" in content
    assert "decision-retrain-ok" in content
    
    print("All E2E learning feedback assertions passed successfully!")
