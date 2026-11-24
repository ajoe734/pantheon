"""Read-only two-meeting execution-loop proof (MEETING-ACTION-TWO-MEETING-PROOF-20261123).

``check_chain`` verifies, with GET-only reads, that two distinct meetings of the same
series (tenant + meeting_type + subject_ref) form a real work loop:

  M1 adopted actions -> canonical Research tickets (owner receipts) -> owner evidence
  -> M2 was actually presented the prior work and its decision cites or reviews it.

The proof is deliberately hard to satisfy by accident. Every one of these fails:
  * the same meeting twice, or the legacy W47 MT-1 (``mtg-0f7fa4993625ca47``) as M1;
  * different tenant, meeting type or subject (series) between M1 and M2;
  * an action without a canonical ticket, or a ticket whose origin names another meeting/tenant/series;
  * a submitted/accepted/returned ticket with no resolvable owner evidence (nonexistent, empty,
    checksum-mismatched, or a non-owner reference such as a generic closed demand or inbox ack);
  * submitter equal to verifier, or no recorded submitter/verifier;
  * M2 prior_actions that omit an M1 action (missing opportunity) or an M2 decision that neither
    reviews nor cites a presented submitted action.

It is a product-flow observation (acceptance/README.md): a natural second meeting that has not
happened yet is reported as ``pending`` and is never counted as passed. Nothing is written.
Never prints tokens, case files, prompts or private memory text; only ids and verdicts.
"""
from __future__ import annotations

import urllib.parse
from typing import Any, Callable, Mapping

LEGACY_W47_MEETING_ID = "mtg-0f7fa4993625ca47"
MEETING_PATH = "/bff/management/meetings/{}"
TICKET_PATH = "/api/v1/research/tickets/{}"
OWNER_EVIDENCE_KINDS = frozenset({"research_note", "research_artifact"})
EVIDENCE_STATES = frozenset({"submitted", "accepted", "returned"})
OPEN_STATES = frozenset({"open", "in_progress", "waiting_for_input", "returned"})
MAX_ACTIONS = 24  # matches consultation's bounded prior-action snapshot

Fetch = Callable[[str], Any]


def _data(body: Any) -> dict:
    if not isinstance(body, dict):
        return {}
    inner = body.get("data")
    return inner if isinstance(inner, dict) else body


def _series(meeting: Mapping[str, Any]) -> tuple:
    s = meeting.get("meeting_series") if isinstance(meeting.get("meeting_series"), Mapping) else {}
    tenant = s.get("tenant_id") or meeting.get("tenant_id")
    return (tenant, s.get("meeting_type") or meeting.get("meeting_type"), s.get("subject_ref") or meeting.get("series_subject_ref"))


def _safe(fetch: Fetch, path: str) -> tuple[Any, str | None]:
    try:
        return fetch(path), None
    except Exception as exc:  # message is a type name or http_NNN only; never a URL or body
        return None, f"{type(exc).__name__}:{str(exc)[:40]}"


def _actor(entry: Mapping[str, Any]) -> str:
    for key in ("transitioned_by", "actor_id", "actor", "submitted_by", "reviewed_by"):
        if isinstance(entry.get(key), str) and entry[key].strip():
            return entry[key].strip()
    return ""


def _history_actor(ticket: Mapping[str, Any], action: str) -> str:
    for entry in reversed(ticket.get("lifecycle_history") or []):
        if isinstance(entry, Mapping) and entry.get("action") == action:
            return _actor(entry)
    return ""


def check_ticket(action: Mapping[str, Any], ticket_body: Any, *, meeting_id: str, series: tuple) -> tuple[dict, list[str]]:
    """Canonical owner ticket for one adopted action."""
    aid = str(action.get("action_id") or "")
    fails: list[str] = []
    t = ticket_body if isinstance(ticket_body, dict) else {}
    if not t or t.get("ticket_id") != aid or action.get("ticket_id") not in (None, aid):
        return {}, [f"ticket_not_canonical:{aid}"]
    origin = t.get("meeting_action") if isinstance(t.get("meeting_action"), Mapping) else {}
    if t.get("tenant_id") != series[0]:
        fails.append(f"ticket_wrong_tenant:{aid}")
    if origin.get("meeting_id") != meeting_id:
        fails.append(f"ticket_wrong_meeting:{aid}")
    if (origin.get("meeting_type"), origin.get("subject_ref")) != series[1:]:
        fails.append(f"ticket_wrong_series:{aid}")
    for key in ("deliverable", "acceptance_criterion"):
        if not str(origin.get(key) or "").strip():
            fails.append(f"ticket_{key}_missing:{aid}")
    if not (origin.get("due_at") or origin.get("next_review")):
        fails.append(f"ticket_due_or_review_missing:{aid}")
    if not str(t.get("owner") or "").strip() or t.get("owner") != t.get("assignee", t.get("owner")):
        fails.append(f"ticket_owner_missing:{aid}")
    # The minutes snapshot is frozen; the live owner state may be newer and is the one reported.
    if not t.get("action_state"):
        fails.append(f"ticket_state_missing:{aid}")
    state = str(t.get("action_state") or "")
    summary = {"ticket_id": aid, "state": state, "owner": t.get("owner"),
               "assignee_kind": origin.get("assignee_kind"), "revision": t.get("revision")}
    if state in EVIDENCE_STATES:
        refs = t.get("evidence_refs") or []
        notes = {n.get("note_id"): n for n in (t.get("evidence_notes") or []) if isinstance(n, Mapping)}
        if not refs:
            fails.append(f"evidence_missing:{aid}")
        for ref in refs:
            rid = ref.get("artifact_id") if isinstance(ref, Mapping) else None
            if not isinstance(ref, Mapping) or ref.get("kind") not in OWNER_EVIDENCE_KINDS or not rid:
                fails.append(f"evidence_not_owner_artifact:{aid}")  # generic demand, inbox ack, membership ack
                continue
            if ref["kind"] == "research_note":
                note = notes.get(rid)
                if (not note or not str(note.get("body") or "").strip() or note.get("ticket_id") != aid
                        or note.get("tenant_id") != series[0]
                        or not ref.get("checksum") or note.get("checksum") != ref.get("checksum")):
                    fails.append(f"evidence_unresolved:{aid}:{rid}")
            else:
                fails.append(f"evidence_artifact_requires_owner_read:{aid}:{rid}")
        submitter = _history_actor(t, "submit")
        summary["submitter"] = submitter
        if not submitter:
            fails.append(f"submitter_missing:{aid}")
        if state in {"accepted", "returned"}:
            verifier = _history_actor(t, "accept" if state == "accepted" else "return")
            decision = t.get("review_decision") if isinstance(t.get("review_decision"), Mapping) else {}
            summary["verifier"] = verifier
            if not verifier:
                fails.append(f"verifier_missing:{aid}")
            elif verifier == submitter:
                fails.append(f"submitter_is_verifier:{aid}")
            if not str(decision.get("reason") or "").strip() or not decision.get("review_source"):
                fails.append(f"review_reason_or_source_missing:{aid}")
        summary["evidence_ids"] = [r.get("artifact_id") for r in refs if isinstance(r, Mapping)]
    return summary, fails


def _cited_ids(meeting: Mapping[str, Any]) -> set[str]:
    cited: set[str] = set()
    for rnd in meeting.get("rounds") or []:
        for pos in (rnd.get("positions") if isinstance(rnd, Mapping) else None) or []:
            for cit in (pos.get("citations") if isinstance(pos, Mapping) else None) or []:
                cid = cit.get("item_id") or cit.get("source_id") if isinstance(cit, Mapping) else cit
                if isinstance(cid, str):
                    cited.add(cid.removeprefix("meeting_action:"))
    return cited


def check_next_meeting(m1_actions: list[Mapping[str, Any]], m2: Mapping[str, Any], *, m1_id: str, m2_id: str,
                       ticket_states: Mapping[str, str]) -> tuple[dict, list[str]]:
    """Was M1's work actually presented to, and answered by, M2's decision?"""
    fails: list[str] = []
    prior = {str(p.get("action_id")): p for p in (m2.get("prior_actions") or []) if isinstance(p, Mapping)}
    synth = m2.get("synthesis") if isinstance(m2.get("synthesis"), Mapping) else {}
    reviews = {str(r.get("action_id")): r for r in (synth.get("action_reviews") or m2.get("prior_action_reviews") or [])
               if isinstance(r, Mapping)}
    cited = _cited_ids(m2)
    evidence = {str(e.get("action_id")): e for e in (m2.get("prior_action_evidence") or []) if isinstance(e, Mapping)}
    presented, answered = [], []
    for a in m1_actions:
        aid = str(a["action_id"])
        live = ticket_states.get(aid)
        if live == "accepted" and aid not in prior:
            continue  # accepted before M2's review window; disposition lives on the ticket, not re-presented
        if aid not in prior:
            fails.append(f"missing_opportunity:{aid}")  # silent drop of outstanding/overdue/submitted work
            continue
        if prior[aid].get("meeting_id") != m1_id:
            fails.append(f"prior_action_wrong_origin:{aid}")
        presented.append(aid)
        if prior[aid].get("state") == "submitted":
            ev = evidence.get(aid)
            if not ev or ev.get("status") != "ok":
                fails.append(f"submitted_evidence_not_presented:{aid}")
            if aid not in reviews and aid not in cited:
                fails.append(f"decision_neither_reviews_nor_cites:{aid}")
            else:
                answered.append(aid)
        elif aid in cited or aid in reviews:
            answered.append(aid)
    if not presented and not fails:
        fails.append("no_prior_action_presented")
    if any(p.get("meeting_id") == m2_id for p in prior.values()):
        fails.append("prior_actions_include_current_meeting")
    return {"presented": sorted(presented), "decision_answered": sorted(answered),
            "reviews": {k: v.get("decision") for k, v in reviews.items()}}, fails


def check_chain(fetch: Fetch, m1_id: str | None, m2_id: str | None) -> dict:
    """GET-only verdict. ``pending`` = the natural observation has not happened; it never passes."""
    if not m1_id or not m2_id:
        return {"passed": False, "pending": True, "failed": [],
                "pending_reason": "second_meeting_or_human_input_not_yet_observed"}
    if m1_id == m2_id:
        return {"passed": False, "pending": False, "failed": ["same_meeting_twice"]}
    if LEGACY_W47_MEETING_ID in (m1_id, m2_id):
        return {"passed": False, "pending": False, "failed": ["legacy_w47_is_historical_context_not_proof"]}
    fails: list[str] = []
    m1_body, err1 = _safe(fetch, MEETING_PATH.format(urllib.parse.quote(m1_id, safe="")))
    m2_body, err2 = _safe(fetch, MEETING_PATH.format(urllib.parse.quote(m2_id, safe="")))
    for label, err in (("m1", err1), ("m2", err2)):
        if err:
            fails.append(f"{label}_unreadable:{err}")
    if fails:
        return {"passed": False, "pending": False, "failed": fails}
    m1, m2 = _data(m1_body), _data(m2_body)
    s1, s2 = _series(m1), _series(m2)
    if not all(s1) or s1 != s2:
        fails.append("series_mismatch_or_unstable")
    actions = [a for a in (m1.get("actions") or (m1.get("synthesis") or {}).get("actions") or []) if isinstance(a, Mapping)]
    if not actions:
        fails.append("m1_has_no_adopted_actions")
    kinds = {a.get("assignee_kind") for a in actions}
    if "persona" not in kinds or "human" not in kinds:
        fails.append("m1_missing_persona_or_human_item")
    tickets, ticket_states = [], {}
    for a in actions[:MAX_ACTIONS]:
        aid = str(a.get("action_id") or "")
        body, err = _safe(fetch, TICKET_PATH.format(urllib.parse.quote(aid, safe="")))
        if err:
            fails.append(f"ticket_unreadable:{aid}:{err}")
            continue
        summary, tfails = check_ticket(a, body, meeting_id=m1_id, series=s1)
        fails.extend(tfails)
        if summary:
            tickets.append(summary)
            ticket_states[aid] = summary["state"]
    follow, nfails = check_next_meeting([a for a in actions if str(a.get("action_id")) in ticket_states], m2,
                                        m1_id=m1_id, m2_id=m2_id, ticket_states=ticket_states)
    fails.extend(nfails)
    open_or_returned = [t["ticket_id"] for t in tickets if t["state"] in OPEN_STATES]
    if not any(t["state"] in EVIDENCE_STATES for t in tickets):
        fails.append("no_ticket_has_owner_evidence")
    return {"passed": not fails, "pending": False, "failed": fails, "m1": m1_id, "m2": m2_id,
            "series": {"tenant_id": s1[0], "meeting_type": s1[1], "subject_ref": s1[2]},
            "tickets": tickets, "next_meeting": follow, "open_or_returned": open_or_returned}
