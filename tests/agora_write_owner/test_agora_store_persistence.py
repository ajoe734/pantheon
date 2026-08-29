"""
Test write-then-fresh-read persistence proof for AgoraStore.

Verifies that all Agora resources are durably stored in the backing store and that
a completely fresh AgoraStore instance successfully recovers and reads all records
without relying on any in-memory state.
"""
from __future__ import annotations

import uuid
import pytest

from services.agora.store import AgoraStore, DictRecord


def test_agora_session_and_committee_persistence() -> None:
    session_id = f"sess-{uuid.uuid4().hex[:8]}"

    # 1. Write with Store Instance 1
    store1 = AgoraStore()
    created_session = store1.create_session(
        session_id=session_id,
        title="Strategy Review Committee",
        actor_id="operator-alice",
        payload={
            "mode": "committee",
            "participants": [{"id": "persona-a", "role": "expert"}],
            "contextRefs": [{"type": "strategy", "id": "strat-001"}],
            "targetEntity": {"type": "strategy", "id": "strat-001"},
        },
    )
    assert created_session.status == "active"

    # Append message
    msg = store1.append_session_message(
        session_id,
        message_id=f"msg-{uuid.uuid4().hex[:8]}",
        content="Welcome to the committee session.",
        actor_id="operator-alice",
        payload={"language": "zh-TW", "attachments": []},
    )
    assert msg is not None

    # Open committee
    opened = store1.open_committee_session(session_id)
    assert opened is not None
    assert opened.status == "open"

    # Submit memo
    memo_id = f"memo-{uuid.uuid4().hex[:8]}"
    memo = store1.submit_committee_memo(
        session_id,
        memo_id=memo_id,
        actor_id="operator-alice",
        payload={
            "memoType": "committee_summary",
            "summary": "Consensus reached on drawdown limits.",
            "recommendations": [{"action": "approve", "target": "strat-001"}],
            "evidenceRefs": ["ev-101", "ev-102"],
        },
    )
    assert memo is not None
    assert memo.status == "draft"

    # Publish memo
    published = store1.publish_committee_memo(session_id, memo_id, actor_id="approver-bob")
    assert published is not None
    assert published.status == "published"
    assert published.published_by == "approver-bob"

    # Close committee
    closed = store1.close_committee_session(session_id, outcome="approved", memo_ids=[memo_id])
    assert closed is not None
    assert closed.status == "closed"
    assert closed.outcome == "approved"

    # 2. Destroy Store Instance 1
    del store1

    # 3. Read with Fresh Store Instance 2
    store2 = AgoraStore()
    fresh_session = store2.get_session(session_id)
    assert fresh_session is not None
    assert fresh_session.sessionId == session_id
    assert fresh_session.title == "Strategy Review Committee"
    assert fresh_session.mode == "committee"
    assert fresh_session.status == "closed"
    assert fresh_session.outcome == "approved"
    assert len(fresh_session.messages) == 1
    assert fresh_session.messages[0]["content"] == "Welcome to the committee session."

    fresh_memo = store2.get_committee_memo(session_id, memo_id)
    assert fresh_memo is not None
    assert fresh_memo.memo_id == memo_id
    assert fresh_memo.status == "published"
    assert fresh_memo.published_by == "approver-bob"
    assert fresh_memo.summary == "Consensus reached on drawdown limits."
    assert fresh_memo.evidence_refs == ["ev-101", "ev-102"]

    listed_memos = store2.list_committee_memos(session_id)
    assert len(listed_memos) == 1
    assert listed_memos[0].memo_id == memo_id


def test_agora_evidence_pack_persistence() -> None:
    session_id = f"sess-{uuid.uuid4().hex[:8]}"

    # Write with Instance 1
    store1 = AgoraStore()
    pack = store1.create_evidence_pack(
        session_id=session_id,
        payload={
            "packId": f"pack-{uuid.uuid4().hex[:8]}",
            "targetEntityType": "strategy",
            "targetEntityId": "strat-999",
            "linkedEntities": [{"type": "model", "id": "m-1"}],
            "notes": "Initial evidence for review",
        },
        actor_id="operator-alice",
    )
    assert pack.sessionId == session_id

    # Append files
    store1.append_evidence_files(
        session_id=session_id,
        files=[
            {"fileId": "f-001", "filename": "backtest.pdf", "sizeBytes": 1024, "mimeType": "application/pdf"},
            {"fileId": "f-002", "filename": "metrics.csv", "sizeBytes": 2048, "mimeType": "text/csv"},
        ],
        actor_id="operator-alice",
    )
    del store1

    # Read with Fresh Instance 2
    store2 = AgoraStore()
    fresh_pack = store2.get_evidence_pack(session_id)
    assert fresh_pack is not None
    assert fresh_pack.sessionId == session_id
    assert fresh_pack.targetEntityType == "strategy"
    assert fresh_pack.targetEntityId == "strat-999"
    assert len(fresh_pack.uploadedFiles) == 2
    assert fresh_pack.uploadedFiles[0]["filename"] == "backtest.pdf"
    assert fresh_pack.uploadedFiles[1]["filename"] == "metrics.csv"


def test_agora_notes_and_insights_persistence() -> None:
    note_id = f"note-{uuid.uuid4().hex[:8]}"
    insight_id = f"ins-{uuid.uuid4().hex[:8]}"

    # Write with Instance 1
    store1 = AgoraStore()
    store1.create_note(
        note_id=note_id,
        title="Key Market Observation",
        content="Regime shift observed in tech sector.",
        actor_id="analyst-carol",
        payload={"tags": ["tech", "regime"], "category": "market_notes"},
    )
    store1.create_insight(
        insight_id=insight_id,
        title="High Volatility Ahead",
        content="Implied volatility spread widening.",
        actor_id="analyst-carol",
        payload={"confidence": 0.85, "impact": "high"},
    )
    del store1

    # Read with Fresh Instance 2
    store2 = AgoraStore()
    fresh_note = store2.get_note(note_id)
    assert fresh_note is not None
    assert fresh_note.noteId == note_id
    assert fresh_note.title == "Key Market Observation"
    assert fresh_note.content == "Regime shift observed in tech sector."
    assert fresh_note.tags == ["tech", "regime"]

    fresh_insight = store2.get_insight(insight_id)
    assert fresh_insight is not None
    assert fresh_insight.insightId == insight_id
    assert fresh_insight.title == "High Volatility Ahead"
    assert fresh_insight.confidence == 0.85
    assert fresh_insight.impact == "high"


def test_agora_training_examples_persistence() -> None:
    example_id = f"ex-{uuid.uuid4().hex[:8]}"

    # Write with Instance 1
    store1 = AgoraStore()
    store1.create_training_example(
        example_id=example_id,
        topic="macro_risk_assessment",
        input_data={"cpi_yoy": 3.4, "unemployment": 4.1},
        expected_output={"risk_level": "moderate", "action": "hedge_tail"},
        actor_id="trainer-dave",
        payload={"status": "approved", "targetPersonaId": "persona-macro"},
    )
    del store1

    # Read with Fresh Instance 2
    store2 = AgoraStore()
    fresh_ex = store2.get_training_example(example_id)
    assert fresh_ex is not None
    assert fresh_ex.exampleId == example_id
    assert fresh_ex.topic == "macro_risk_assessment"
    assert fresh_ex.inputData == {"cpi_yoy": 3.4, "unemployment": 4.1}
    assert fresh_ex.expectedOutput == {"risk_level": "moderate", "action": "hedge_tail"}
    assert fresh_ex.targetPersonaId == "persona-macro"


def test_agora_signals_and_feedback_persistence() -> None:
    signal_id = f"sig-{uuid.uuid4().hex[:8]}"
    feedback_id = f"fb-{uuid.uuid4().hex[:8]}"

    # Write with Instance 1
    store1 = AgoraStore()
    store1.create_signal(
        signal_id=signal_id,
        symbol="NVDA",
        action="BUY",
        confidence=0.92,
        actor_id="researcher-eve",
        payload={"horizon": "medium", "rationale": "Strong earnings acceleration"},
    )
    store1.record_signal_feedback(
        signal_id=signal_id,
        rating=5,
        comments="Excellent entry timing.",
        actor_id="trader-frank",
    )
    store1.create_feedback(
        feedback_id=feedback_id,
        target_id=signal_id,
        content="Great signal performance.",
        actor_id="trader-frank",
        payload={"targetType": "signal", "score": 95},
    )
    del store1

    # Read with Fresh Instance 2
    store2 = AgoraStore()
    fresh_signal = store2.get_signal(signal_id)
    assert fresh_signal is not None
    assert fresh_signal.signalId == signal_id
    assert fresh_signal.symbol == "NVDA"
    assert fresh_signal.action == "BUY"
    assert fresh_signal.confidence == 0.92
    assert fresh_signal.feedback is not None
    assert fresh_signal.feedback["rating"] == 5
    assert fresh_signal.feedback["reviewer"] == "trader-frank"

    fresh_fb = store2.get_feedback(feedback_id)
    assert fresh_fb is not None
    assert fresh_fb.feedbackId == feedback_id
    assert fresh_fb.targetId == signal_id
    assert fresh_fb.score == 95


def test_agora_handoffs_and_audit_events_persistence() -> None:
    handoff_id = f"hnd-{uuid.uuid4().hex[:8]}"
    audit_id = f"aud-{uuid.uuid4().hex[:8]}"

    # Write with Instance 1
    store1 = AgoraStore()
    store1.create_handoff(
        handoff_id=handoff_id,
        source_lane="research",
        target_lane="execution",
        summary="Model parameters ready for paper deployment.",
        actor_id="operator-alice",
        payload={"status": "delivered", "artifacts": ["art-001", "art-002"]},
    )
    store1.record_audit_event(
        event_type="agora.session.create",
        actor_id="operator-alice",
        target_id="sess-001",
        payload={"client_ip": "10.0.0.1", "action": "create"},
        event_id=audit_id,
    )
    del store1

    # Read with Fresh Instance 2
    store2 = AgoraStore()
    fresh_handoff = store2.get_handoff(handoff_id)
    assert fresh_handoff is not None
    assert fresh_handoff.handoffId == handoff_id
    assert fresh_handoff.sourceLane == "research"
    assert fresh_handoff.targetLane == "execution"
    assert fresh_handoff.artifacts == ["art-001", "art-002"]

    fresh_audit = store2.get_audit_event(audit_id)
    assert fresh_audit is not None
    assert fresh_audit.eventId == audit_id
    assert fresh_audit.eventType == "agora.session.create"
    assert fresh_audit.actorId == "operator-alice"
    assert fresh_audit.details["client_ip"] == "10.0.0.1"


def test_agora_workshops_proposals_and_interactions_persistence() -> None:
    ws_id = f"ws-{uuid.uuid4().hex[:8]}"
    prop_id = f"prop-{uuid.uuid4().hex[:8]}"
    act_id = f"act-{uuid.uuid4().hex[:8]}"

    # Write with Instance 1
    store1 = AgoraStore()
    store1.create_workshop(
        workshop_id=ws_id,
        initial_message="Kickoff for alpha brainstorming",
        created_by="operator-alice",
    )
    store1.append_workshop_message(
        workshop_id=ws_id,
        content="Idea: pair momentum with value filter",
        actor_id="persona-quant",
    )

    store1.create_proposal(
        proposal_id=prop_id,
        payload={
            "proposal_type": "parameter_tuning",
            "target_kind": "strategy",
            "target_id": "strat-momentum",
            "target_version": "v1.2",
            "current_value": {"lookback": 20},
            "proposed_value": {"lookback": 30},
            "rationale": "Reduced turnover during sideways markets",
            "confidence": 0.88,
        },
        created_by="operator-alice",
    )
    store1.modify_proposal(
        proposal_id=prop_id,
        action="approve",
        reason="Backtest results confirm reduced turnover",
        proposed_value=None,
        actor_id="lead-reviewer",
    )

    store1.create_interaction(
        interaction_id=act_id,
        payload={
            "workshop_id": ws_id,
            "mode": "debate",
            "environment": "paper",
            "topic": "execution_slippage_model",
            "participant_persona_ids": ["persona-1", "persona-2"],
        },
        created_by="operator-alice",
    )
    del store1

    # Read with Fresh Instance 2
    store2 = AgoraStore()
    fresh_ws = store2.get_workshop(ws_id)
    assert fresh_ws is not None
    assert fresh_ws.workshop_id == ws_id
    assert len(fresh_ws.messages) == 2
    assert fresh_ws.messages[0]["content"] == "Kickoff for alpha brainstorming"
    assert fresh_ws.messages[1]["content"] == "Idea: pair momentum with value filter"

    fresh_prop = store2.get_proposal(prop_id)
    assert fresh_prop is not None
    assert fresh_prop.proposal_id == prop_id
    assert fresh_prop.status == "approved"
    assert fresh_prop.revision == 2
    assert len(fresh_prop.change_history) == 1
    assert fresh_prop.change_history[0]["action"] == "approve"

    fresh_act = store2.get_interaction(act_id)
    assert fresh_act is not None
    assert fresh_act.interaction_id == act_id
    assert fresh_act.workshop_id == ws_id
    assert fresh_act.topic == "execution_slippage_model"


def test_cross_instance_mutation_and_reread() -> None:
    """Proves that a write by Instance B is immediately observable by Instance A without caching."""
    session_id = f"sess-{uuid.uuid4().hex[:8]}"

    instance_a = AgoraStore()
    instance_b = AgoraStore()

    # Instance A creates session
    instance_a.create_session(
        session_id=session_id,
        title="Cross Instance Session",
        actor_id="user-a",
        payload={"mode": "general"},
    )

    # Instance B reads and mutates
    session_at_b = instance_b.get_session(session_id)
    assert session_at_b is not None
    assert session_at_b.status == "active"

    instance_b.close_session(session_id, outcome="completed")

    # Instance A re-reads from DB and observes Instance B's update
    session_at_a = instance_a.get_session(session_id)
    assert session_at_a is not None
    assert session_at_a.status == "closed"
    assert session_at_a.outcome == "completed"
