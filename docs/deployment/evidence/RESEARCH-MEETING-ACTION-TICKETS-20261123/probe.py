#!/usr/bin/env python3
"""Read-only hosted RW-01 acceptance; never creates or transitions product data.

Example:
  RESEARCH_ACCEPTANCE_TOKEN_FILE=/run/principal/token python3 probe.py \
    --base-url https://research-owner --tenant tenant-dev --ticket-id rt-action-...
The token must already be authorized for the original ticket and its series.
Use --require-state accepted to prove the final review of a genuine obligation.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import urllib.parse
import urllib.request


def inspect(base_url, tenant, ticket_id, token, require_state=None, fetch=None):
    def get(path, query=None):
        if fetch:
            return fetch(path, query)
        url = base_url.rstrip("/") + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        request = urllib.request.Request(url, headers={"Authorization": "Bearer " + token,
            "X-Tenant-Id": tenant, "Accept": "application/json"}, method="GET")
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read(27 * 1024 * 1024 + 1)
        if len(raw) > 27 * 1024 * 1024:
            raise ValueError("Owner response exceeds bounded page budget")
        return json.loads(raw)

    root = "/api/research/tickets"
    detail = get(root + "/" + urllib.parse.quote(ticket_id, safe=""))
    assert detail["tenant_id"] == tenant and detail["action_id"] == ticket_id
    action = detail["meeting_action"]
    assert all(action.get(k) for k in ("meeting_id", "source_action_id", "meeting_type", "subject_ref",
                                      "deliverable", "acceptance_criterion", "assignee_kind"))
    assert action.get("due_at") or action.get("next_review")
    assert detail["owner"] == detail["assignee"] and type(detail["revision"]) is int
    if require_state:
        assert detail["action_state"] == require_state
    history = detail["lifecycle_history"]
    assert 1 <= len(history) <= 256
    if detail["action_state"] == "accepted":
        assert detail["status"] == "closed" and detail["evidence_refs"]
        assert history[-1]["action"] == "accept"
        assert detail["review_decision"]["reason"] and detail["review_decision"]["review_source"]
    refs = [*detail.get("evidence_refs", []),
            *(detail.get("human_input") or {}).get("evidence_refs", [])]
    note_refs = {r["artifact_id"]: r for r in refs if r["kind"] == "research_note"}
    notes = {n["note_id"]: n for n in detail.get("evidence_notes", [])}
    assert len(note_refs) <= 16 and set(notes) == set(note_refs)
    for note_id, ref in note_refs.items():
        note = notes[note_id]
        saved = get("/api/research/notes/" + urllib.parse.quote(note_id, safe=""))
        assert note["body"].strip() and note["title"].strip()
        assert note["tenant_id"] == tenant and note["ticket_id"] == ticket_id
        assert saved["_meeting_binding"] == {"tenant_id": tenant, "ticket_id": ticket_id,
                                              "actor_id": note["actor_id"]}
        assert saved["_meeting_digest"] == note["checksum"] == ref["checksum"]
        assert all(saved[k] == note[k] for k in ("title", "body", "source_refs"))
    query = {"meeting_linked": "true", "meeting_type": action["meeting_type"],
             "subject_ref": action["subject_ref"], "owner": detail["owner"], "page_size": 100}
    ids, tokens = set(), set()
    for _ in range(100):
        page = get(root, query)
        assert isinstance(page["data"], list) and len(page["data"]) <= 100
        for row in page["data"]:
            assert row["tenant_id"] == tenant and row["owner"] == detail["owner"]
            assert row["meeting_action"]["subject_ref"] == action["subject_ref"]
            assert row["ticket_id"] not in ids
            ids.add(row["ticket_id"])
        token = page["page_info"]["next_page_token"]
        if not token:
            break
        assert token not in tokens
        tokens.add(token)
        query["page_token"] = token
    else:
        raise ValueError("Series exceeds probe's 100-page budget; no partial-success claim")
    assert ticket_id in ids
    return {"read_only": True, "ticket_id": ticket_id, "state": detail["action_state"],
            "revision": detail["revision"], "series_ticket_count": len(ids), "pages": len(tokens) + 1,
            "verified_note_count": len(note_refs)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--ticket-id", required=True)
    parser.add_argument("--require-state")
    args = parser.parse_args()
    token_path = os.getenv("RESEARCH_ACCEPTANCE_TOKEN_FILE")
    token = Path(token_path).read_text().strip() if token_path else os.environ["RESEARCH_ACCEPTANCE_TOKEN"]
    print(json.dumps(inspect(args.base_url, args.tenant, args.ticket_id, token, args.require_state), sort_keys=True))


if __name__ == "__main__":
    main()
