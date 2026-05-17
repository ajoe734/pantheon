from __future__ import annotations

from .e2e_fixtures import consult_review_e2e


def test_ask_006_consult_committee_memo_reaches_management_review_handoff() -> None:
    correlation_id = "corr-ask006-consult-review"
    ask_session_id = "ask-006-root-session"
    committee_session_id = "committee-ask006-review"
    memo_id = "memo-ask006-management-review"

    with consult_review_e2e() as e2e:
        ask_session = e2e.create_ask_session(
            session_id=ask_session_id,
            correlation_id=correlation_id,
        )
        assert ask_session["sessionId"] == ask_session_id
        assert ask_session["mode"] == "quick_ask"

        committee = e2e.invoke_committee(
            session_id=committee_session_id,
            linked_request_id=ask_session_id,
        )
        assert committee["sessionId"] == committee_session_id
        assert committee["status"] == "open"
        assert committee["linkedRequestId"] == ask_session_id

        draft_memo = e2e.submit_committee_memo(
            session_id=committee_session_id,
            memo_id=memo_id,
            linked_request_id=ask_session_id,
            correlation_id=correlation_id,
        )
        assert draft_memo["memo_id"] == memo_id
        assert draft_memo["status"] == "draft"

        published_memo = e2e.publish_committee_memo(
            session_id=committee_session_id,
            memo_id=memo_id,
            correlation_id=correlation_id,
        )
        assert published_memo["memo_id"] == memo_id
        assert published_memo["status"] == "published"

        memo_events = [
            event for event in e2e.ask_events() if event["type"] == "consult_memo_published"
        ]
        assert len(memo_events) == 1
        assert memo_events[0]["data"]["correlation_id"] == correlation_id
        assert memo_events[0]["data"]["memo_id"] == memo_id
        assert memo_events[0]["data"]["session_id"] == committee_session_id

        handoff_id = memo_events[0]["data"]["handoff_id"]
        handoffs = e2e.get_json(
            "/bff/agora/handoffs?handoffType=consult_memo_to_management_review"
        )
        assert handoffs["page_info"]["total"] == 1
        handoff = handoffs["items"][0]
        assert handoff["handoffId"] == handoff_id
        assert handoff["handoffType"] == "consult_memo_to_management_review"
        assert handoff["destination"]["app"] == "management"
        assert handoff["destination"]["queue"] == "consult_memo_review"
        assert handoff["payload"]["memoId"] == memo_id
        assert handoff["payload"]["sessionId"] == committee_session_id
        assert handoff["payload"]["correlationId"] == correlation_id
