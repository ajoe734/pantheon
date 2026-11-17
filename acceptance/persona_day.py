"""Persona-day acceptance: did each active trading persona complete its day on hosted?

Read-only. ``evaluate_persona_day`` is a pure function shared with the BFF
Management cycle view (services/control-plane/bff/personas/cycle_views.py), so the
probe and the page cannot disagree. ``main`` is the acceptance-stage probe
(contract: acceptance/README.md): exit 0 passed, 1 failed, 77 skipped.
Output lists per persona and per precondition passed/failed with an evidence
reference. It never prints tokens, case files, prompts or memory content.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional

# acceptance-owner: PERSONA-DAY-ACCEPTANCE-PROBE-20261117

PRECONDITIONS = (
    "lifecycle_paper_owner",
    "daily_cycle_cron_job",
    "terminal_packet_today",
    "memory_read_via_broker",
    "proposal_recorded_with_citations",
    "pool_cycle_ok",
    "cycle_health_completed",
)
TAIPEI = timezone(timedelta(hours=8))
CYCLE_READY_HOUR = 12  # the cron fires 10:00; before this hour today's cycle is not yet due
CONSULT_WINDOW_DAYS = 7


def trading_day(now: datetime) -> str:
    """Taipei trading day the cycle is due for: today after CYCLE_READY_HOUR, else the previous weekday."""
    local = now.astimezone(TAIPEI)
    day = local.date() if local.hour >= CYCLE_READY_HOUR else local.date() - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day.isoformat()


def _packet_day(packet: Mapping[str, Any]) -> Optional[str]:
    ms = (packet.get("producer") or {}).get("cron_run_at_ms")
    if type(ms) is not int:
        return None
    return (datetime.fromtimestamp(ms / 1000, timezone.utc) + timedelta(hours=8)).date().isoformat()


def _latest(packets: Iterable[Mapping[str, Any]]) -> Optional[Mapping[str, Any]]:
    rows = [p for p in packets if isinstance(p.get("persona_cycle"), Mapping)]
    return max(rows, key=lambda p: ((p.get("producer") or {}).get("cron_run_at_ms") or 0,
                                    p.get("updated_at") or ""), default=None)


def _result(name: str, ok: Optional[bool], ref: str, reason: str = "") -> dict[str, Any]:
    return {"name": name, "status": "passed" if ok else ("failed" if ok is False else "not_observed"),
            "reason": reason, "evidence_ref": ref}


def _citations_ok(output: Any) -> bool:
    proposals = output.get("proposals") if isinstance(output, Mapping) else None
    if not isinstance(proposals, list) or not proposals:
        return False
    for p in proposals:
        if not isinstance(p, Mapping):
            return False
        cited = bool(p.get("case_file_item_ids"))
        reason = bool(str(p.get("none_applicable_reason") or "").strip())
        if cited == reason:  # exactly one of citation / none_applicable_reason
            return False
    return True


def evaluate_persona_day(persona_id: str, *, lifecycle_state: Optional[str], cron_jobs: Optional[int],
                         packets: Iterable[Mapping[str, Any]], now: datetime,
                         consultations: Iterable[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """Per-precondition verdict for one persona. ``cron_jobs`` None: not directly observable
    (the BFF has no cron reader); then a consumed cron run for the day is the evidence the job exists."""
    day = trading_day(now)
    mine = [p for p in packets if persona_id in (p.get("persona_ids") or [])]
    todays = [p for p in mine if _packet_day(p) == day and (p.get("persona_cycle") or {}).get("phase") == "terminal"]
    packet = _latest(todays)
    cycle = (packet or {}).get("persona_cycle") or {}
    pid_ref = f"persona:{persona_id}"
    pkt_ref = f"packet:{packet['packet_id']}" if packet and packet.get("packet_id") else f"trading_day:{day}"

    checks = []
    checks.append(_result("lifecycle_paper_owner",
                          None if lifecycle_state is None else lifecycle_state == "paper_owner",
                          pid_ref + "#lifecycle_state", f"lifecycle_state={lifecycle_state}"))
    if cron_jobs is None:
        inferred = any((p.get("producer") or {}).get("cron_run_at_ms") for p in mine)
        checks.append(_result("daily_cycle_cron_job", inferred, pid_ref + "#cron",
                              "inferred_from_consumed_cron_run" if inferred else "no_cron_run_consumed"))
    else:
        checks.append(_result("daily_cycle_cron_job", cron_jobs == 1, pid_ref + "#cron", f"jobs={cron_jobs}"))
    if packet is None:
        failed_today = [p for p in mine if _packet_day(p) == day]
        reason = "cycle_running_or_not_terminal" if failed_today else f"no_terminal_packet_for_{day}"
        checks.append(_result("terminal_packet_today", False, pkt_ref, reason))
    else:
        reason = cycle.get("failure_reason")
        checks.append(_result("terminal_packet_today", not reason, pkt_ref, str(reason or "")[:160]))
    use = cycle.get("tool_use") or {}
    checks.append(_result("memory_read_via_broker", bool(packet) and (use.get("memory_recall_ok") or 0) >= 1,
                          pkt_ref + "#persona_cycle/tool_use",
                          "" if packet else "no_packet"))
    checks.append(_result("proposal_recorded_with_citations",
                          bool(packet) and (use.get("proposals_recorded") or 0) >= 1
                          and _citations_ok(cycle.get("output")),
                          pkt_ref + "#persona_cycle/output", "" if packet else "no_packet"))
    pool = (packet or {}).get("pool_autonomy")
    pool_ok = isinstance(pool, Mapping) and pool.get("status") not in (None, "failed")
    checks.append(_result("pool_cycle_ok", bool(packet) and pool_ok, pkt_ref + "#pool_autonomy",
                          "pool_cycle_not_run" if packet and not isinstance(pool, Mapping)
                          else str((pool or {}).get("reason") or "")[:120]))
    completed = bool(packet) and cycle.get("phase") == "terminal" and not cycle.get("failure_reason")
    checks.append(_result("cycle_health_completed", completed, pkt_ref, "" if completed else "not_completed"))

    first = next((c["name"] for c in checks if c["status"] != "passed"), None)
    stamps = []
    for c in consultations:
        try:
            at = datetime.fromisoformat(str(c.get("at")).replace("Z", "+00:00"))
            stamps.append(at if at.tzinfo else at.replace(tzinfo=timezone.utc))
        except (TypeError, ValueError, AttributeError):
            continue
    last = max(stamps, default=None)
    return {"persona_id": persona_id, "trading_day": day, "passed": first is None,
            "first_failed_precondition": first, "preconditions": checks,
            "last_consultation_at": last.isoformat() if last else None,
            "no_consultation_in_7_days": last is None or last < now - timedelta(days=CONSULT_WINDOW_DAYS)}


def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    ok = bool(results) and all(r["passed"] for r in results)
    return {"verdict": "passed" if ok else "failed", "personas": results,
            "reason": None if results else "no_active_trading_persona"}


def _get(base: str, path: str, token: str) -> Any:
    request = urllib.request.Request(base.rstrip("/") + path, method="GET",
                                     headers={"Authorization": "Bearer " + token} if token else {})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - operator-supplied BFF URL
        return json.load(response)


def run(fetch, now: Optional[datetime] = None) -> dict[str, Any]:
    """``fetch(path)`` performs a GET and returns decoded JSON; the probe issues no other verb."""
    now = now or datetime.now(timezone.utc)
    listing = fetch("/bff/personas?page_size=200")
    items = listing.get("items") or listing.get("data") or []
    trading = [p for p in items if str(p.get("archetype") or p.get("kind") or "trading").lower() != "consultable"
               and str(p.get("status") or "active").lower() == "active"
               and str(p.get("state") or p.get("lifecycle_state") or "") not in ("consultable", "retired", "archived")]
    results = []
    for p in trading:
        pid = p.get("persona_id") or p.get("id")
        state = p.get("state") or p.get("lifecycle_state")
        if state != "paper_owner":  # first precondition fails; later ones need no read
            base = evaluate_persona_day(pid, lifecycle_state=state, cron_jobs=None, packets=[], now=now)
            results.append(base)
            continue
        detail = fetch(f"/bff/personas/{pid}")
        day_status = ((detail.get("data") or {}).get("personaCycle") or {}).get("dayStatus")
        results.append(day_status if isinstance(day_status, dict) and "preconditions" in day_status else
                       {"persona_id": pid, "passed": False, "first_failed_precondition": "terminal_packet_today",
                        "preconditions": [], "reason": "day_status_unavailable"})
    return aggregate(results)


def main() -> int:
    base = os.environ.get("ACCEPTANCE_BFF_URL", "").strip()
    if not base:
        print("skipped: ACCEPTANCE_BFF_URL not set")
        return 77
    token = os.environ.get("ACCEPTANCE_BFF_TOKEN", "")
    try:
        report = run(lambda path: _get(base, path, token))
    except Exception as exc:  # never echo bodies or headers
        print(json.dumps({"verdict": "failed", "reason": "read_failed:" + type(exc).__name__}))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0 if report["verdict"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
