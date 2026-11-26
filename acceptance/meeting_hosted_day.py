#!/usr/bin/env python3
# acceptance-owner: MEETING-HOSTED-ACCEPTANCE-20261115
"""Daily read-only observation of natural persona meetings on hosted dev (MEETING-HOSTED-ACCEPTANCE-20261115).

A daily state probe (acceptance/README.md "Release probes only"): whether natural meetings happened does not
depend on a release, so it runs from persona-day-acceptance.yml, not from the release stage.

Logs in as the read-only viewer (ACCEPTANCE_VIEWER_CLIENT_ID/SECRET via /bff/auth/dev-login; the token stays in
memory) and issues GET only. It reports, per acceptance item, ``observed`` / ``missing`` (with reason) /
``violated``; it never creates, convenes, or synthesises a meeting. Output holds ids, counts and verdicts only:
no topics, positions, case-file text or memory text.

  AC1 a closed MT-1/MT-2/MT-3 meeting with >=2 persona participants ran >=2 rounds; its proposals appear in the
      proposals entry point with a downstream status.
  AC2 each persona participant's writeback entry is in that persona's own memory; no private memory id is cited
      in any round or case file.
  AC3 an agora_advisor participated in a meeting; a meeting was refused with private_servant_not_allowed.
  AC4 (data half) every closed meeting is listed in the persona meetings filter of its chair.

Exit 0: every item observed (passed) or some natural event missing (pending). Exit 1: a violation or read failure.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Iterable, Mapping, Optional

NATURAL_TYPES = frozenset({"MT-1", "MT-2", "MT-3"})
CLOSED = frozenset({"closed", "completed"})
PRIVATE_SCOPES = frozenset({"persona_private", "private", "private_user"})
SERVANT_REFUSAL = "private_servant_not_allowed"
MAX_PAGES = 20
MAX_DETAILS = 60
Fetch = Callable[[str], Any]


def _rows(body: Any) -> list[dict]:
    if not isinstance(body, dict):
        return []
    data = body.get("data")
    for c in (body.get("items"), data.get("items") if isinstance(data, dict) else None, data if isinstance(data, list) else None):
        if isinstance(c, list):
            return [r for r in c if isinstance(r, dict)]
    return []


def _data(body: Any) -> dict:
    if not isinstance(body, dict):
        return {}
    return body["data"] if isinstance(body.get("data"), dict) else body


def _cursor(body: Any) -> Optional[str]:
    if not isinstance(body, dict):
        return None
    for h in (body, body.get("data"), body.get("page_info")):
        if isinstance(h, dict):
            t = h.get("next_cursor") or h.get("next_page_token")
            if isinstance(t, str) and t.strip():
                return t.strip()
    return None


def _paged(fetch: Fetch, path: str) -> list[dict]:
    out: list[dict] = []
    cursor = None
    sep = "&" if "?" in path else "?"
    for _ in range(MAX_PAGES):
        body = fetch(path + (sep + "cursor=" + urllib.parse.quote(cursor, safe="") if cursor else ""))
        if isinstance(body, dict) and str(body.get("status") or "").lower() == "unavailable":
            raise RuntimeError(f"surface_unavailable:{path.split('?')[0]}:{body.get('reason') or 'unknown'}")
        out.extend(_rows(body))
        cursor = _cursor(body)
        if not cursor:
            return out
    raise RuntimeError("pagination_exceeded:" + path.split("?")[0])


def _mid(m: Mapping[str, Any]) -> str:
    return str(m.get("meeting_id") or m.get("id") or "")


def _is_private(entry: Mapping[str, Any]) -> bool:
    if entry.get("is_private") is True or entry.get("private") is True:
        return True
    return any(str(entry.get(k) or "").strip().lower() in PRIVATE_SCOPES
               for k in ("relevance_scope", "memory_layer", "scope", "visibility_scope", "memory_scope"))


def _entry_id(entry: Mapping[str, Any]) -> str:
    return str(entry.get("entry_id") or entry.get("memory_id") or entry.get("id") or "")


def _cited_ids(detail: Mapping[str, Any]) -> set[str]:
    ids: set[str] = set()
    for item in detail.get("case_file_items") or []:
        if isinstance(item, Mapping):
            ids.update(str(item[k]) for k in ("item_id", "source_id", "entry_id") if item.get(k))
    for rnd in detail.get("rounds") or []:
        for pos in (rnd.get("positions") or []) if isinstance(rnd, Mapping) else []:
            for c in (pos.get("citations") or []) if isinstance(pos, Mapping) else []:
                if isinstance(c, str):
                    ids.add(c.strip())
                elif isinstance(c, Mapping):
                    ids.update(str(c[k]) for k in ("item_id", "source_id", "entry_id", "id") if c.get(k))
    return ids


def summarize(meeting: Mapping[str, Any], detail: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    d = detail or {}
    participants = d.get("participants") or meeting.get("participants") or []
    rounds = d.get("rounds") or []
    convened = meeting.get("convened_by") if isinstance(meeting.get("convened_by"), Mapping) else {}
    return {
        "meeting_id": _mid(meeting),
        "meeting_type": meeting.get("meeting_type"),
        "status": meeting.get("status"),
        "outcome": meeting.get("outcome"),
        "refusal_reason": meeting.get("refusal_reason"),
        "convened_by_kind": convened.get("kind"),
        "chair_persona_id": meeting.get("chair_persona_id"),
        "created_at": meeting.get("created_at"),
        "closed_at": meeting.get("closed_at"),
        "participants": sorted({f"{p.get('origin') or 'unknown'}:{p.get('persona_id')}" for p in participants
                                if isinstance(p, Mapping)}),
        "rounds": len([r for r in rounds if isinstance(r, Mapping) and r.get("positions")]) if detail else None,
        "proposals": [{"proposal_id": p.get("proposal_id"), "kind": p.get("kind"),
                       "downstream": {k: (p.get("downstream") or {}).get(k) for k in ("target", "id", "status", "href")}}
                      for p in (d.get("proposals") or []) if isinstance(p, Mapping)],
        "writebacks": [{k: w.get(k) for k in ("persona_id", "entry_id", "status")}
                       for w in (d.get("writebacks") or []) if isinstance(w, Mapping)],
        "private_items_withheld": d.get("private_items_withheld"),
    }


def _persona_ids(summary: Mapping[str, Any]) -> list[str]:
    return [p.split(":", 1)[1] for p in summary["participants"] if p.split(":", 1)[1].startswith("persona-")]


def run(fetch: Fetch) -> dict[str, Any]:
    """``fetch(path)`` performs one GET and returns decoded JSON; no other verb is used."""
    meetings = _paged(fetch, "/bff/management/meetings?page_size=200&include_failed=true")
    closed = [m for m in meetings if str(m.get("status") or "").lower() in CLOSED]
    closed.sort(key=lambda m: str(m.get("closed_at") or ""), reverse=True)
    summaries: dict[str, dict] = {}
    details: dict[str, dict] = {}
    for m in closed[:MAX_DETAILS]:
        detail = _data(fetch("/bff/management/meetings/" + urllib.parse.quote(_mid(m), safe="")))
        details[_mid(m)] = detail
        summaries[_mid(m)] = summarize(m, detail)
    for m in meetings:
        summaries.setdefault(_mid(m), summarize(m, None))

    proposal_index = {str(p.get("proposal_id") or p.get("id") or ""): p
                      for p in _paged(fetch, "/bff/management/meetings/proposals")}

    memory: dict[str, dict] = {}

    def persona_memory(pid: str) -> dict:
        if pid not in memory:
            body = fetch("/bff/personas/" + urllib.parse.quote(pid, safe="") + "/memory")
            meta = body.get("meta") if isinstance(body, dict) and isinstance(body.get("meta"), dict) else {}
            src = meta.get("memory_source") if isinstance(meta.get("memory_source"), dict) else {}
            entries = _rows(body)
            memory[pid] = {"available": bool(src.get("available", bool(entries))), "reason": src.get("reason"),
                           "ids": {_entry_id(e) for e in entries} - {""},
                           "private_ids": {_entry_id(e) for e in entries if _is_private(e)} - {""}}
        return memory[pid]

    violations: list[str] = []
    ac1_hits: list[str] = []
    ac2_results: dict[str, dict] = {}
    for mid, s in summaries.items():
        if s["rounds"] is None:
            continue
        personas = _persona_ids(s)
        if s["meeting_type"] in NATURAL_TYPES and len(personas) >= 2 and s["rounds"] >= 2 and s["proposals"]:
            at_entry = all(p["proposal_id"] in proposal_index and p["downstream"].get("status") for p in s["proposals"])
            if at_entry:
                ac1_hits.append(mid)
            else:
                violations.append(f"proposal_not_at_entry_point:{mid}")
        if s["meeting_type"] in NATURAL_TYPES and s["rounds"] >= 2:
            per: dict[str, str] = {}
            wb_by_persona = {w["persona_id"]: w for w in s["writebacks"] if w.get("persona_id")}
            for pid in personas:
                wb = wb_by_persona.get(pid)
                if not wb or not wb.get("entry_id"):
                    per[pid] = "no_writeback_recorded"
                    continue
                mem = persona_memory(pid)
                if not mem["available"]:
                    per[pid] = "memory_unreadable:" + str(mem["reason"] or "unknown")
                elif wb["entry_id"] in mem["ids"]:
                    per[pid] = "in_own_memory"
                else:
                    per[pid] = "not_in_latest_100_entries"
            ac2_results[mid] = {"participants": per, "private_items_withheld": s["private_items_withheld"]}
    # Second pass: compare every checked meeting's citations with every private id read from any participant.
    all_private = set().union(*(m["private_ids"] for m in memory.values())) if memory else set()
    for mid, result in ac2_results.items():
        leaked = sorted(_cited_ids(details.get(mid) or {}) & all_private)
        result["private_ids_cited"] = leaked
        if leaked:
            violations.append(f"private_memory_cited:{mid}:{','.join(leaked[:5])}")

    advisor_meetings = sorted(mid for mid, s in summaries.items()
                              if any(p.startswith("agora_advisor:") for p in s["participants"]))
    servant_refusals = sorted(mid for mid, s in summaries.items() if s["refusal_reason"] == SERVANT_REFUSAL)
    agora_rows = len(_paged(fetch, "/bff/agora/committee/meetings"))

    tab_missing: list[str] = []
    for chair in sorted({s["chair_persona_id"] for mid, s in summaries.items() if s["rounds"] is not None and s["chair_persona_id"]}):
        listed = {_mid(m) for m in _paged(fetch, "/bff/management/meetings?page_size=200&persona_id="
                                          + urllib.parse.quote(chair, safe=""))}
        tab_missing += [mid for mid, s in summaries.items()
                        if s["rounds"] is not None and s["chair_persona_id"] == chair and mid not in listed]
    if tab_missing:
        violations.append("persona_tab_missing:" + ",".join(sorted(tab_missing)[:5]))

    ac2_ok = [mid for mid in ac1_hits
              if all(v == "in_own_memory" for v in ac2_results.get(mid, {}).get("participants", {}).values())
              and ac2_results.get(mid, {}).get("participants")]
    items = {
        "AC1_natural_two_round_meeting_with_proposals": (
            {"status": "observed", "meetings": ac1_hits} if ac1_hits else
            {"status": "missing", "reason": "no closed MT-1/MT-2/MT-3 with >=2 persona participants, >=2 rounds and proposals"}),
        "AC2_outcome_in_own_memory_no_private_leak": (
            {"status": "observed", "meetings": ac2_ok} if ac2_ok else
            {"status": "missing", "reason": "no AC1 meeting whose every persona writeback was read back in its own memory"}),
        "AC3a_agora_advisor_participated": (
            {"status": "observed", "meetings": advisor_meetings} if advisor_meetings else
            {"status": "missing", "reason": "no meeting lists an agora_advisor participant"}),
        "AC3b_foreign_servant_refused": (
            {"status": "observed", "meetings": servant_refusals} if servant_refusals else
            {"status": "missing", "reason": f"no meeting refused with {SERVANT_REFUSAL}"}),
    }
    if violations:
        verdict = "failed"
    elif all(v["status"] == "observed" for v in items.values()):
        verdict = "passed"
    else:
        verdict = "pending"
    return {
        "verdict": verdict,
        "violations": violations,
        "items": items,
        "counts": {"meetings": len(meetings), "closed": len(closed), "details_read": len(details),
                   "proposals_entry_point": len(proposal_index), "agora_committee_rows": agora_rows,
                   "by_type_status": _tally(summaries.values())},
        "memory_outcomes": ac2_results,
        "meetings": sorted(summaries.values(), key=lambda s: str(s.get("created_at") or ""), reverse=True),
    }


def _tally(summaries: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for s in summaries:
        key = f"{s.get('meeting_type')}:{s.get('status')}"
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


def _call(request: urllib.request.Request) -> Any:
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - operator-supplied BFF URL
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"http_{exc.code}") from None
    except Exception as exc:  # never echo URLs, headers, or bodies
        raise RuntimeError(f"request_failed:{type(exc).__name__}") from None


def main() -> int:
    base = os.environ.get("ACCEPTANCE_BFF_URL", "").strip().rstrip("/")
    cid = os.environ.get("ACCEPTANCE_VIEWER_CLIENT_ID", "").strip()
    secret = os.environ.get("ACCEPTANCE_VIEWER_CLIENT_SECRET", "")
    if not base:
        print("skipped: ACCEPTANCE_BFF_URL not set")
        return 77
    if not cid or not secret:
        print(json.dumps({"verdict": "failed", "reason": "credential_missing:viewer_client"}))
        return 1
    try:
        login = json.dumps({"grant_type": "client_credentials", "client_id": cid, "client_secret": secret}).encode()
        token = str((_call(urllib.request.Request(base + "/bff/auth/dev-login", data=login, method="POST",
                                                  headers={"Content-Type": "application/json"})) or {}).get("access_token") or "")
        if not token:
            raise RuntimeError("no_access_token")
        report = run(lambda path: _call(urllib.request.Request(base + path, method="GET",
                                                               headers={"Authorization": "Bearer " + token})))
    except RuntimeError as exc:
        print(json.dumps({"verdict": "failed", "reason": str(exc)[:120]}))
        return 1
    print(json.dumps(report, sort_keys=True, ensure_ascii=False))
    return 1 if report["verdict"] == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
