"""On-demand synthetic page measurement; prints counts/bytes, writes nothing."""
import json

from services.control_plane.bff.meetings.client import _human_input_ticket_items


def measure():
    rows = []
    for index in range(100):
        direct = index >= 50
        row = {
            "ticket_id": f"rt-action-{index:032x}", "tenant_id": "tenant-a",
            "title": "Supply licensed observations", "priority": "normal",
            "owner": "op-inbox" if direct else "persona-alpha", "reviewer_id": "chair-reviewer",
            "required_actor": "op-inbox", "revision": 3, "status": "in_progress",
            "action_state": "returned" if direct else "waiting_for_input",
            "meeting_action": {"meeting_id": "mtg-001", "meeting_type": "MT-1",
                "subject_ref": "pool-alpha", "assignee_kind": "human" if direct else "persona",
                "deliverable": "D" * 2000, "acceptance_criterion": "C" * 2000,
                "due_at": "2026-11-22T00:00:00Z", "next_review": "W48-MT1"},
            "evidence_refs": [], "created_at": "2026-11-21T01:00:00Z",
            "updated_at": "2026-11-23T01:00:00Z",
        }
        if direct:
            row.update(description="H" * 2000, review_decision={"decision": "return", "reason": "R" * 2000})
        else:
            row["input_request"] = {"reason": "I" * 2000, "human_actor_id": "op-inbox",
                                    "requested_at": "2026-11-23T01:00:00Z"}
        rows.append(row)
    page = {"data": rows, "page_info": {"next_page_token": "opaque-owner-next"}}
    items = _human_input_ticket_items(page, tenant_id="tenant-a", snapshot_at="2026-11-23T00:00:00Z")
    return {"ticket_count": len(rows), "projected_count": len(items),
            "owner_reply_bytes": len(json.dumps(page).encode()),
            "projected_inbox_rows_bytes": len(json.dumps(items).encode())}


if __name__ == "__main__":
    print(json.dumps(measure(), sort_keys=True))
