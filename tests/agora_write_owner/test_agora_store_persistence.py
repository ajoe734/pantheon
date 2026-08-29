"""
Test write-then-fresh-read persistence proof for AgoraStore.

Verifies that all Agora resources are durably stored in the backing store and that
a completely fresh AgoraStore instance successfully recovers and reads all records
without relying on any in-memory state.
"""
from __future__ import annotations

from pathlib import Path
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

    all_memos = store2.list_committee_memos(session_id)
    assert len(all_memos) == 1
    assert all_memos[0].memo_id == memo_id


def test_agora_evidence_pack_persistence() -> None:
    session_id = f"sess-ev-{uuid.uuid4().hex[:8]}"

    # Store 1: Create Pack and Append Files
    store1 = AgoraStore()
    store1.create_session(
        session_id=session_id,
        title="Evidence Session",
        actor_id="operator-alice",
        payload={"mode": "committee", "targetEntity": {"type": "artifact", "id": "art-001"}},
    )
    pack = store1.create_evidence_pack(
        session_id=session_id,
        payload={"notes": "Initial evidence notes", "linkedEntities": [{"type": "signal", "id": "sig-1"}]},
        actor_id="operator-alice",
    )
    assert pack is not None
    assert pack.targetEntityType == "artifact"

    append_res = store1.append_evidence_files(
        session_id=session_id,
        files=[
            {"fileName": "chart.png", "mimeType": "image/png", "sizeBytes": 1024},
            {"fileName": "report.pdf", "mimeType": "application/pdf", "sizeBytes": 4096},
        ],
        actor_id="operator-alice",
    )
    assert append_res is not None
    assert len(append_res.uploadedFiles) == 2

    # Destroy Store 1
    del store1

    # Store 2: Fresh Read
    store2 = AgoraStore()
    fresh_pack = store2.get_evidence_pack(session_id)
    assert fresh_pack is not None
    assert fresh_pack.sessionId == session_id
    assert fresh_pack.notes == "Initial evidence notes"
    assert len(fresh_pack.uploadedFiles) == 2
    assert fresh_pack.uploadedFiles[0]["fileName"] == "chart.png"
    assert fresh_pack.uploadedFiles[1]["fileName"] == "report.pdf"


def test_agora_notes_insights_training_examples_persistence() -> None:
    store1 = AgoraStore()
    suffix = uuid.uuid4().hex[:8]

    # Note
    note_id = f"note-{suffix}"
    note = store1.create_note(
        note_id=note_id,
        title="Macro Regime Shift",
        body="Transitioning from inflation regime to stagflation regime.",
        actor_id="analyst-carol",
        payload={"tags": ["macro", "regime"], "linked_evidence_refs": ["ev-99"]},
    )
    assert note.title == "Macro Regime Shift"

    # Insight
    insight_id = f"ins-{suffix}"
    insight = store1.create_insight(
        insight_id=insight_id,
        summary="High volatility expected in tech earnings.",
        actor_id="analyst-carol",
        payload={"scope": "sector", "tags": ["tech", "volatility"], "confidence": {"score": 0.85}},
    )
    assert insight.summary == "High volatility expected in tech earnings."

    # Training Example
    example_id = f"train-{suffix}"
    ex = store1.create_training_example(
        example_id=example_id,
        payload={
            "personaId": "persona-trader",
            "input": {"prompt": "Analyze orderbook skew"},
            "expected": {"decision": "buy", "confidence": 0.9},
            "labels": ["supervised", "orderbook"],
        },
        actor_id="trainer-dave",
    )
    assert ex.trainingExampleId == example_id

    # Destroy Store 1
    del store1

    # Fresh Read with Store 2
    store2 = AgoraStore()
    fresh_note = store2.get_note(note_id)
    assert fresh_note is not None
    assert fresh_note.note_id == note_id
    assert fresh_note.title == "Macro Regime Shift"
    assert fresh_note.tags == ["macro", "regime"]

    fresh_insight = store2.get_insight(insight_id)
    assert fresh_insight is not None
    assert fresh_insight.insight_id == insight_id
    assert fresh_insight.summary == "High volatility expected in tech earnings."
    assert fresh_insight.confidence.get("score") == 0.85

    fresh_ex = store2.get_training_example(example_id)
    assert fresh_ex is not None
    assert fresh_ex.trainingExampleId == example_id
    assert fresh_ex.expected.get("decision") == "buy"
    assert fresh_ex.labels == ["supervised", "orderbook"]


def test_agora_signals_feedback_persistence() -> None:
    store1 = AgoraStore()
    suffix = uuid.uuid4().hex[:8]

    # Create Signal
    signal_id = f"sig-{suffix}"
    signal = store1.create_signal(
        signal_id=signal_id,
        title="Breakout on Sector ETF",
        body="Volume breakout confirmed above 200 EMA.",
        actor_id="researcher-eve",
        payload={"market": "US_EQUITIES", "severity": "medium", "tags": ["breakout", "etf"]},
    )
    assert signal.signal_id == signal_id
    assert signal.reviewStatus == "pending_trader_review"

    # Record Signal Feedback
    sig_fb = store1.record_signal_feedback(
        signal_id=signal_id,
        decision="approved",
        confidence=95.0,
        reason="Consistent with institutional volume flows",
        actor_id="trader-frank",
    )
    assert sig_fb is not None
    assert sig_fb.decision == "approved"

    # Create General Feedback
    fb = store1.create_feedback(
        signal_id=signal_id,
        verdict="pass",
        memo="High conviction signal",
        actor_id="trader-frank",
    )
    assert fb is not None

    # Destroy Store 1
    del store1

    # Fresh Read with Store 2
    store2 = AgoraStore()
    fresh_signal = store2.get_signal(signal_id)
    assert fresh_signal is not None
    assert fresh_signal.signal_id == signal_id
    assert fresh_signal.title == "Breakout on Sector ETF"
    assert fresh_signal.reviewStatus == "approved"
    assert fresh_signal.latestFeedbackId is not None

    signals_list = store2.list_signals(review_status="approved")
    assert any(s.signal_id == signal_id for s in signals_list)


def test_agora_handoffs_and_audit_persistence() -> None:
    store1 = AgoraStore()
    suffix = uuid.uuid4().hex[:8]

    # Create Handoff
    handoff_id = f"h-{suffix}"
    handoff = store1.create_handoff(
        handoff_id=handoff_id,
        handoff_type="policy_review",
        source_route="/bff/agora/committee/sessions",
        source_entity={"type": "session", "id": f"sess-{suffix}"},
        destination_route="/bff/governance/reviews",
        destination_queue="governance_review_queue",
        priority="urgent",
        payload={"slaDueAt": "2026-08-30T00:00:00Z", "reason": "Emergency risk threshold adjustment"},
        actor_id="operator-alice",
    )
    assert handoff.handoffId == handoff_id
    assert handoff.priority == "urgent"

    # Record Audit Event
    audit_id = f"aud-{suffix}"
    audit = store1.record_audit_event({
        "auditId": audit_id,
        "action": "agora.session.create",
        "actorId": "operator-alice",
        "details": {"sessionId": f"sess-{suffix}"},
    })
    assert audit.auditId == audit_id

    # Destroy Store 1
    del store1

    # Fresh Read with Store 2
    store2 = AgoraStore()
    fresh_handoff = store2.get_handoff(handoff_id)
    assert fresh_handoff is not None
    assert fresh_handoff.handoffId == handoff_id
    assert fresh_handoff.handoffType == "policy_review"
    assert fresh_handoff.priority == "urgent"
    assert fresh_handoff.source["entity"]["id"] == f"sess-{suffix}"

    all_audits = store2.list_audit_events()
    assert any(a.auditId == audit_id for a in all_audits)


def test_agora_workshops_proposals_interactions_persistence() -> None:
    store1 = AgoraStore()
    suffix = uuid.uuid4().hex[:8]

    # Workshop
    workshop_id = f"ws-{suffix}"
    ws = store1.create_workshop(
        initial_message="Beginning alpha discovery workshop.",
        created_by="quant-lead",
        workshop_id=workshop_id,
    )
    assert ws.workshop_id == workshop_id

    store1.append_workshop_message(
        workshop_id=workshop_id,
        content="Second message with feature ideas.",
        actor_id="quant-researcher",
    )

    # Proposal
    proposal_id = f"prop-{suffix}"
    prop = store1.create_proposal(
        payload={
            "proposal_type": "strategy_parameter",
            "target_kind": "strategy",
            "target_id": "strat-vol-01",
            "target_version": "v1",
            "current_value": {"leverage": 1.5},
            "proposed_value": {"leverage": 2.0},
            "rationale": "Strong sharpe observed in backtest",
        },
        created_by="quant-lead",
        proposal_id=proposal_id,
    )
    assert prop.proposal_id == proposal_id
    assert prop.revision == 1

    modified_prop = store1.modify_proposal(
        proposal_id=proposal_id,
        action="modify_value",
        reason="Conservative leverage adjustment",
        proposed_value={"leverage": 1.75},
        actor_id="risk-officer",
    )
    assert modified_prop is not None
    assert modified_prop.revision == 2

    # Interaction
    interaction_id = f"act-{suffix}"
    interaction = store1.create_interaction(
        payload={
            "workshop_id": workshop_id,
            "mode": "brainstorm",
            "environment": "paper",
            "topic": "Volatility Arbitrage",
            "participant_persona_ids": ["persona-quant-1", "persona-quant-2"],
            "context_refs": [{"type": "strategy", "id": "strat-vol-01"}],
        },
        created_by="quant-lead",
        interaction_id=interaction_id,
    )
    assert interaction.interaction_id == interaction_id

    # Destroy Store 1
    del store1

    # Fresh Read with Store 2
    store2 = AgoraStore()
    fresh_ws = store2.get_workshop(workshop_id)
    assert fresh_ws is not None
    assert fresh_ws.workshop_id == workshop_id
    assert len(fresh_ws.messages) == 2
    assert fresh_ws.version == 2

    fresh_prop = store2.get_proposal(proposal_id)
    assert fresh_prop is not None
    assert fresh_prop.proposal_id == proposal_id
    assert fresh_prop.revision == 2
    assert fresh_prop.proposed_value == {"leverage": 1.75}
    assert len(fresh_prop.change_history) == 1

    fresh_act = store2.get_interaction(interaction_id)
    assert fresh_act is not None
    assert fresh_act.interaction_id == interaction_id
    assert fresh_act.topic == "Volatility Arbitrage"
    assert len(fresh_act.participant_persona_ids) == 2
