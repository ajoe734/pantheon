"""Two-meeting loop against the REAL Research write owner (MEETING-ACTION-TWO-MEETING-PROOF-20261123).

Consultation's normal dispatch/read entry points talk to the real Research HTTP routes (TestClient bridged at the
HTTP transport) backed by a real PostgresJsonOwnerStore. Requires RESEARCH_MEETING_TEST_DSN (isolated database;
CI may use its provisioned DATABASE_URL) and is skipped otherwise: a skip is NOT a pass. This is component/integration
validation on local owners, not hosted acceptance.
"""
from __future__ import annotations

import copy
import urllib.parse

import pytest

from services.consultation import meeting_outputs as mo
from services.research.tests.test_meeting_action_tickets import (  # noqa: F401  (world is a pytest fixture)
    URL, advance, command, headers, note, world,
)

TENANT = "tenant-a"
SERIES = {"tenant_id": TENANT, "meeting_type": "MT-1", "subject_ref": "pool-1"}


def _bridge(client):
    calls = []

    def transport(method, url, hdrs, body, **_):
        parsed = urllib.parse.urlsplit(url)
        calls.append((method, parsed.path))
        params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        resp = client.request(method, parsed.path, params=params or None, json=body,
                              headers={k: v for k, v in hdrs.items()})
        return resp.status_code, resp.json()

    return transport, calls


def _adopted(source, assignee, kind, **extra):
    row = {"source_action_id": source, "assignee": assignee, "assignee_kind": kind,
           "deliverable": f"Deliverable {source}", "acceptance_criterion": "Compare A and B with sources",
           "due_at": "2026-11-20T00:00:00Z", "source_refs": ["facts:source"]}
    row.update(extra)
    return row


def test_two_meeting_loop_real_owner_idempotent_and_series_scoped(world, tmp_path, monkeypatch):
    monkeypatch.setenv("PANTHEON_RESEARCH_JWT_SECRET", "meeting-action-test-only-secret")
    transport, calls = _bridge(world[1])
    ledger = tmp_path / "dispatch.json"
    m1 = {"tenant_id": TENANT, "meeting_id": "mtg-m1", "meeting_series": dict(SERIES),
          "synthesis": {"actions": [_adopted("p1", "persona-1", "persona"),
                                    _adopted("h1", "operator", "human", human_requirement="Supply licensed excerpt")]}}
    first = mo.dispatch_meeting_actions(m1, transport=transport, store_path=ledger)
    assert all(a["ticket_id"] and not a["dispatch_error"] for a in first), first
    posts = len([c for c in calls if c[0] == "POST"])
    # Restart (fresh process state, same ledger/owner): no duplicate dispatch, same canonical ids.
    again = mo.dispatch_meeting_actions(copy.deepcopy(m1), transport=transport, store_path=ledger)
    assert [a["ticket_id"] for a in again] == [a["ticket_id"] for a in first]
    assert len([c for c in calls if c[0] == "POST"]) == posts
    pid, hid = first[0]["ticket_id"], first[1]["ticket_id"]

    # Persona executes and submits owner evidence; the human item stays outstanding.
    ticket = advance(world[1].get(f"{URL}/{pid}", headers=headers()))
    submitted = advance(command(world, ticket, "submit", evidence_refs=[note(world, ticket)]))
    assert submitted["action_state"] == "submitted"

    # Next same-series meeting: zero semantic memory is consulted; work comes from the owner by stable series.
    m2 = {"tenant_id": TENANT, "meeting_id": "mtg-m2", "meeting_series": dict(SERIES)}
    read = mo.read_meeting_actions(m2, transport=transport)
    by_id = {i["action_id"]: i for i in read["items"]}
    assert set(by_id) == {pid, hid} and by_id[pid]["state"] == "submitted" and by_id[hid]["state"] in {"open", "waiting_for_input"}
    assert all(i["meeting_id"] == "mtg-m1" for i in by_id.values())
    ok = [e for e in read["evidence"] if e["action_id"] == pid]
    assert ok and ok[0]["status"] == "ok" and ok[0]["content"]["body"]

    # Another series / tenant never sees M1's work.
    other = mo.read_meeting_actions({**m2, "meeting_series": {**SERIES, "subject_ref": "pool-2"}}, transport=transport)
    assert other["items"] == []
    foreign = mo.read_meeting_actions({**m2, "tenant_id": "tenant-b", "meeting_series": {**SERIES, "tenant_id": "tenant-b"}},
                                      transport=transport)
    assert all(i["action_id"] not in {pid, hid} for i in foreign["items"])

    # The same-series read agrees with the acceptance proof's ticket checks.
    from acceptance import meeting_action_followthrough as maf
    summary, fails = maf.check_ticket({"action_id": pid}, advance(world[1].get(f"{URL}/{pid}", headers=headers())),
                                      meeting_id="mtg-m1", series=(TENANT, "MT-1", "pool-1"))
    assert not fails and summary["state"] == "submitted" and summary["submitter"]
