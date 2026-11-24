#!/usr/bin/env python3
"""Read-only hosted/component probe; emits no credential or product content.

BFF_BASE_URL, BFF_AUTH_TOKEN, BFF_TENANT_ID and MEETING_ID select an existing
meeting. Exit 77 means real action evidence is absent, never accepted proof.
The chain's integrated acceptance must separately exercise owner transitions.
"""
import json
import os
import sys
import urllib.parse
import urllib.request


def main():
    names = ("BFF_BASE_URL", "BFF_AUTH_TOKEN", "BFF_TENANT_ID", "MEETING_ID")
    if any(not os.getenv(name) for name in names):
        print(json.dumps({"status": "skipped", "reason": "probe_configuration_missing"}))
        return 77
    headers = {"Authorization": "Bearer " + os.environ["BFF_AUTH_TOKEN"].removeprefix("Bearer "),
               "X-Tenant-Id": os.environ["BFF_TENANT_ID"]}
    sizes = []

    def read(path):
        request = urllib.request.Request(os.environ["BFF_BASE_URL"].rstrip("/") + path, headers=headers)
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("probe_reply_limit")
        sizes.append(len(raw))
        return json.loads(raw)

    try:
        meeting = read("/bff/management/meetings/" + urllib.parse.quote(os.environ["MEETING_ID"], safe=""))
        tickets = read("/api/v1/research/tickets?meeting_linked=true&human_actionable=true&page_size=100")
        inbox = read("/bff/management/human-inbox?source_type=meeting&page_size=200")
        assert meeting.get("status") == "ok", "meeting_owner_unavailable"
        assert isinstance(meeting.get("actions"), list), "action_projection_absent"
        assert isinstance(meeting.get("prior_actions"), list), "prior_action_projection_absent"
        assert isinstance(meeting.get("participant_capabilities"), list), "capabilities_absent"
        assert isinstance(tickets.get("page_info"), dict), "owner_cursor_absent"
        work = [item for item in inbox["data"]["items"] if item.get("kind") == "meeting_action"]
        assert len({item["source_id"] for item in work}) == len(work), "duplicate_original_ticket"
        for item in work:
            assert item.get("ticket_id") and item.get("meeting_id") and item.get("required_actor"), "unlinked_input"
            assert item.get("resumes_with") and item.get("reason"), "input_context_absent"
            assert item.get("response_action") in {"submit", "provide_input"}, "human_transition_absent"
            assert type(item.get("can_respond")) is bool, "caller_response_permission_absent"
        if not meeting["actions"] and not meeting["prior_actions"]:
            print(json.dumps({"status": "skipped", "reason": "no_real_meeting_actions", "reply_bytes": sizes}))
            return 77
        assert meeting["participant_capabilities"], "recorded_capabilities_absent"
        assert meeting.get("series_subject_ref"), "series_subject_absent"
        assert isinstance(meeting.get("prior_action_reviews"), list), "action_reviews_absent"
        assert isinstance(meeting.get("action_page_info"), dict), "action_owner_pages_absent"
        assert isinstance(meeting.get("action_read"), dict), "action_owner_receipt_absent"
        assert meeting["action_read"].get("status") not in {None, "not_recorded"}, "action_source_receipt_lost"
        print(json.dumps({"status": "passed_read_projection", "reply_bytes": sizes,
                          "action_count": len(meeting["actions"]), "prior_count": len(meeting["prior_actions"]),
                          "human_input_count": len(work), "writes": 0}))
        return 0
    except Exception as exc:
        # Never print response bodies, URLs with object IDs, or bearer credentials.
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
