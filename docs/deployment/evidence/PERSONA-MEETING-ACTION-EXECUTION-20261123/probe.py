#!/usr/bin/env python3
"""Read-only acceptance: owner submission, genuine run and later decision citation.

Use a copied hosted packet store and the exact hosted owner origins. This probe
never creates tickets, writes notes, ticks a cycle or changes task state.
"""
import argparse
import json
import os
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen


def credential(name):
    path = os.getenv(name + "_FILE")
    return Path(path).read_text().strip() if path else os.environ[name]


def read_owner(base, path, token, tenant):
    req = Request(base.rstrip("/") + path, headers={"Authorization": "Bearer " + token, "X-Tenant-Id": tenant})
    with urlopen(req, timeout=10) as response:
        data = response.read(1048577)
    if len(data) > 1048576:
        raise ValueError("owner_response_exceeds_budget")
    body = json.loads(data)
    return body.get("data", body)


def verify(ticket, note, packets, persona, tenant):
    tid = ticket["ticket_id"]
    assert ticket["tenant_id"] == tenant and ticket["owner"] == persona
    assert ticket["action_id"] == tid and ticket["meeting_action"]["assignee_kind"] == "persona"
    assert ticket["action_state"] in {"submitted", "accepted"}, "delivery has not reached Research"
    assert note["_meeting_binding"] == {"tenant_id": tenant, "ticket_id": tid, "actor_id": "persona-pool-cycle"}
    assert note.get("body") and note.get("_meeting_digest")
    assert any(r["artifact_id"] == note["note_id"] and r["checksum"] == note["_meeting_digest"]
               for r in ticket["evidence_refs"])
    owned = [p for p in packets.values() if p.get("tenant_id") == tenant and p.get("persona_ids") == [persona]]
    deliveries = [(p, a) for p in owned for a in p.get("persona_cycle", {}).get("meeting_action_attempts", [])
                  if a["ticket_id"] == tid and a["command"]["action"] == "submit" and a.get("run_id")]
    assert deliveries, "no journaled broker execution for this original ticket"
    original, plan = next((p, a) for p, a in deliveries if a.get("note", {}).get("body") == note["body"])
    tool = original["persona_cycle"]["tool_use"]
    assert tool["run_id"] == plan["run_id"] and tool["run_ref"] == plan["run_ref"]
    acknowledged = [a["result"] for _, a in deliveries if (a.get("result") or {}).get("status") == "submitted"]
    acknowledged += [r for p in owned for r in p.get("persona_cycle", {}).get("meeting_action_recovery", [])
                     if r.get("ticket_id") == tid and r.get("status") == "submitted"]
    assert acknowledged, "an uncertain write is not an acknowledgement"
    citation = "facts:meeting_action_result:" + tid
    later = [p for p in owned if p["created_at"] > original["created_at"] and any(
        citation in d.get("cited_case_file_item_ids", []) for d in p.get("persona_cycle", {}).get("decisions", []))]
    assert later, "no subsequent actual decision cited the result"
    for packet in later:
        assert any(i.get("item_id") == citation for case in packet["persona_cycle"]["case_files"].values()
                   for group in case["groups"].values() for i in group["items"])
    return {"status": "passed", "persona_id": persona, "ticket_id": tid, "note_id": note["note_id"],
            "run_id": plan["run_id"], "delivery_packet_id": original["packet_id"],
            "later_packet_ids": [p["packet_id"] for p in later], "owner_state": ticket["action_state"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("persona-api-url", "research-api-url", "packet-file", "persona-id", "tenant-id", "ticket-id"):
        parser.add_argument("--" + flag, required=True)
    args = parser.parse_args()
    cap = read_owner(args.persona_api_url, "/api/personas/" + quote(args.persona_id, safe="") + "/action-capability",
                     credential("PANTHEON_PERSONA_SERVICE_TOKEN"), args.tenant_id)
    assert cap["persona_id"] == args.persona_id and cap["executable"], "current access is unavailable"
    token = credential("PANTHEON_RESEARCH_SERVICE_TOKEN")
    ticket = read_owner(args.research_api_url, "/api/research/tickets/" + quote(args.ticket_id, safe=""), token, args.tenant_id)
    refs = [r for r in ticket.get("evidence_refs", []) if r.get("kind") == "research_note"]
    assert len(refs) == 1, "expected the one concrete analysis result"
    note = read_owner(args.research_api_url, "/api/research/notes/" + quote(refs[0]["artifact_id"], safe=""), token, args.tenant_id)
    raw = Path(args.packet_file).read_text()
    rows = json.loads(raw) if raw.lstrip().startswith("[") else [json.loads(line) for line in raw.splitlines() if line]
    packets = {r["packet_id"]: r["payload"] for r in rows if r.get("record_type") == "packet_snapshot"}
    print(json.dumps(verify(ticket, note, packets, args.persona_id, args.tenant_id), ensure_ascii=False))


if __name__ == "__main__":
    main()
