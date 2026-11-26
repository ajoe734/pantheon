"""Discovery pipeline acceptance: does every stage of strategy discovery produce output?

Task DISCOVERY-PIPELINE-ACCEPTANCE-PROBE-20261125 (RC2: done means merged, not working).
Run by the release probe ``acceptance/probes/discovery-pipeline``.

Read-only. The viewer identity (ACCEPTANCE_VIEWER_CLIENT_ID/SECRET) is exchanged once at
``/bff/auth/dev-login`` (the token stays in memory, is never printed or written); then exactly
two GETs:

* ``GET /bff/alpha-factory?page_size=1`` - the owner-backed discovery board
  (BFF-ALPHA-FACTORY-PIPELINE-TRUTH-20261125): source-ingest discovery summary counts, newest
  hypothesis cards with their ledger claim, template evaluations, template-search runs,
  alpha-replication admissions and their gate results, each with its own surface status;
* ``GET /bff/v5/loop-health/strategy_distillation`` - the distillation controller's last
  recorded downstream state (unresolved dead letters) and last success / failure.

Each stage is reported separately as ``passed``, ``failed`` or ``not_due`` with its reason.
A stage whose input could not be read is ``failed`` with the read's reason; it is never
skipped or passed. Survivorship or threshold refusals are working gate output; the report
says ``no strategy passed`` when none did.

Exit 0: no stage failed. 1: at least one stage failed (or the login failed). 77: a required
variable is missing.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

OWNER = "DISCOVERY-PIPELINE-ACCEPTANCE-PROBE-20261125"
REQUIRED = ("ACCEPTANCE_BFF_URL", "ACCEPTANCE_VIEWER_CLIENT_ID", "ACCEPTANCE_VIEWER_CLIENT_SECRET")
ALPHA_FACTORY = "/bff/alpha-factory?page_size=1"
DISTILLATION_HEALTH = "/bff/v5/loop-health/strategy_distillation"
REQUEST_TIMEOUT_SECONDS = 30
MAX_RESPONSE_BYTES = 8 * 1024 * 1024

STAGES = (
    "external_records",
    "hypothesis_cards",
    "no_fallback_drafts",
    "governed_market_dataset",
    "template_search",
    "templates_evaluated",
    "gate_attempts",
    "distillation_dead_letters",
)

# Windows. Each is the cadence of the owner plus slack, never a pass threshold on results.
RECORDS_WINDOW = timedelta(hours=24)  # owner summary window
CARDS_WINDOW = timedelta(hours=24)
SEARCH_STALE_AFTER = timedelta(hours=48)  # template search runs once a day per open demand
DEMAND_GRACE = timedelta(hours=24)  # a demand opened less than a day ago may not be searched yet
SNAPSHOT_STALE_AFTER = timedelta(days=10)  # weekly evaluation cycle over the newest trade day
EVALUATION_STALE_AFTER = timedelta(days=14)
GATE_WINDOW = timedelta(days=7)
DISTILLATION_TICK_STALE_AFTER = timedelta(hours=6)

FALLBACK_PREFIX = "evidence suggests"
# Typed outcomes that mean the gate (or the search reaching it) ran and answered.
GATE_REFUSALS = ("survivorship_limited", "below_threshold", "no_passing_gate_result")
# Outcomes that mean the stage could not do its job (never working output).
UNAVAILABLE_CODES = ("mandate_universe_missing", "dataset_unavailable")
DEAD_LETTER_RE = re.compile(r"(\d+)\s+(?:unresolved\s+)?dead letters")


class ReadFailed(Exception):
    """A read that could not be answered; the message is a reason code, never a body or URL."""


# --------------------------------------------------------------------------- helpers


def _parse_time(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if len(text) == 10:
            parsed = datetime.strptime(text, "%Y-%m-%d")
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(moment: Optional[datetime]) -> Optional[str]:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if moment else None


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _stage(name: str, status: str, reason: str, **values: Any) -> Dict[str, Any]:
    return {"stage": name, "status": status, "reason": reason, "values": values}


def _failed(name: str, reason: str, **values: Any) -> Dict[str, Any]:
    return _stage(name, "failed", reason, **values)


def _count(board: Dict[str, Any], key: str) -> Tuple[Optional[int], Optional[str]]:
    """(value, None) for an ok board count, (None, reason) for one the owner could not answer."""
    counts = _dict(_dict(board.get("data")).get("counts"))
    entry = counts.get(key)
    if not isinstance(entry, dict):
        return None, f"count_missing:{key}"
    if entry.get("status") != "ok" or _int(entry.get("value")) is None:
        return None, f"unreadable:{key}:{entry.get('reason') or 'owner_read_failed'}"
    return int(entry["value"]), None


def _surface(board: Dict[str, Any], key: str) -> Optional[str]:
    """None when the surface is ok, else its unavailable reason."""
    surface = _dict(_dict(board.get("meta")).get("surfaces")).get(key)
    if not isinstance(surface, dict):
        return f"surface_missing:{key}"
    if surface.get("status") != "ok":
        return f"unreadable:{key}:{surface.get('reason') or 'unavailable'}"
    return None


def _within(moment: Optional[datetime], now: datetime, window: timedelta) -> bool:
    return moment is not None and now - window <= moment <= now + timedelta(minutes=5)


# --------------------------------------------------------------------------- stages


def stage_external_records(board: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    name = "external_records"
    value, reason = _count(board, "collected_text_records")
    if reason:
        return _failed(name, reason)
    summary = _dict(_dict(board.get("data")).get("discovery_summary"))
    by_family = _dict(summary.get("collected_by_source_family"))
    if value == 0:
        return _failed(name, "no_external_records_last_24h", last_24h=0)
    return _stage(name, "passed", "external_records_last_24h", last_24h=value, by_source_family=by_family)


def _newest_cards(board: Dict[str, Any], now: datetime) -> List[Dict[str, Any]]:
    data = _dict(board.get("data"))
    cards = [c for c in _list(data.get("discovered")) if isinstance(c, dict) and c.get("kind") == "hypothesis_card"]
    return [c for c in cards if _within(_parse_time(c.get("since")), now, CARDS_WINDOW)]


def _closed_reasons(board: Dict[str, Any]) -> Dict[str, Any]:
    summary = _dict(_dict(board.get("data")).get("discovery_summary"))
    return _dict(_dict(summary.get("closed_last_24h")).get("by_reason"))


def stage_hypothesis_cards(board: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    name = "hypothesis_cards"
    reason = _surface(board, "alpha_factory_hypothesis_cards")
    if reason:
        return _failed(name, reason)
    calls, calls_reason = _count(board, "llm_calls_today")
    llm = _dict(_dict(_dict(board.get("data")).get("counts")).get("llm_calls_today"))
    cap = _int(llm.get("cap"))
    closed = _closed_reasons(board)
    if calls_reason:
        return _failed(name, calls_reason, closed_last_24h_by_reason=closed)
    if cap is None or cap <= 0:
        return _failed(name, "llm_call_cap_missing", calls_today=calls, cap=cap)
    if calls > cap:
        return _failed(name, "llm_calls_over_cap", calls_today=calls, cap=cap)
    recent = _newest_cards(board, now)
    verified = [c for c in recent if c.get("claim_status") == "ok"]
    today, _ = _count(board, "hypothesis_cards_today")
    values = {"verified_last_24h": len(verified), "drafts_last_24h": len(recent), "cards_today": today,
              "calls_today": calls, "cap": cap, "closed_last_24h_by_reason": closed}
    if not verified:
        why = "no_verified_hypothesis_card_last_24h"
        disabled = _int(closed.get("l2_extraction_disabled"))
        if disabled:
            why += f":l2_extraction_disabled={disabled}"
        return _failed(name, why, **values)
    return _stage(name, "passed", "verified_cards_within_cap", **values)


def stage_no_fallback_drafts(board: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    """No external-text draft in the last 24 h without a verified card (the old `Evidence suggests` path)."""
    name = "no_fallback_drafts"
    reason = _surface(board, "alpha_factory_hypothesis_cards")
    if reason:
        return _failed(name, reason)
    recent = _newest_cards(board, now)
    fallback = [c for c in recent
                if str(c.get("title") or "").strip().lower().startswith(FALLBACK_PREFIX)
                or str(c.get("claim") or "").strip().lower().startswith(FALLBACK_PREFIX)
                or c.get("claim_status") in ("card_not_in_ledger", "claim_missing")]
    unverifiable = [c for c in recent if c not in fallback and c.get("claim_status") != "ok"]
    values = {"drafts_last_24h": len(recent), "examined": "newest owner card sample (at most 50)"}
    if fallback:
        return _failed(name, "external_text_drafts_without_verified_card", count=len(fallback),
                       source_ids=[str(c.get("source_id")) for c in fallback[:5]], **values)
    if unverifiable:
        statuses = sorted({str(c.get("claim_status")) for c in unverifiable})
        return _failed(name, "drafts_not_verifiable:" + ",".join(statuses), count=len(unverifiable), **values)
    return _stage(name, "passed", "zero_new_fallback_drafts", count=0, **values)


def _governed_snapshots(board: Dict[str, Any]) -> List[Tuple[datetime, str]]:
    found = []
    for template in _list(_dict(board.get("data")).get("templates")):
        context = _dict(_dict(_dict(template).get("evaluation")).get("evaluation_context"))
        snapshot = str(context.get("governed_snapshot_id") or "").strip()
        day = _parse_time(context.get("window_end"))
        if snapshot and day:
            found.append((day, snapshot))
    return sorted(found)


def stage_governed_market_dataset(board: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    name = "governed_market_dataset"
    reason = _surface(board, "alpha_factory_templates")
    if reason:
        return _failed(name, reason)
    snapshots = _governed_snapshots(board)
    if not snapshots:
        return _failed(name, "no_governed_snapshot_recorded")
    day, snapshot = snapshots[-1]
    values = {"snapshot_id": snapshot, "day": day.strftime("%Y-%m-%d")}
    if not snapshot.startswith("gmd-"):
        return _failed(name, "snapshot_not_governed_market", **values)
    if now - day > SNAPSHOT_STALE_AFTER:
        return _failed(name, "governed_snapshot_stale", **values)
    return _stage(name, "passed", "governed_snapshot_available", **values)


def _scaffolded(board: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [i for i in _list(_dict(board.get("data")).get("scaffolded")) if isinstance(i, dict)]


def _searches(board: Dict[str, Any]) -> List[Dict[str, Any]]:
    return sorted((i for i in _scaffolded(board) if i.get("kind") == "template_search"),
                  key=lambda i: str(i.get("latest_at") or ""))


def _search_unavailable(item: Dict[str, Any]) -> bool:
    raw = str(item.get("reason") or "").lower()
    return "unavailable" in raw or item.get("reason_code") in UNAVAILABLE_CODES


def stage_template_search(board: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    name = "template_search"
    for key in ("alpha_factory_demands", "alpha_factory_runs"):
        reason = _surface(board, key)
        if reason:
            return _failed(name, reason)
    searches = _searches(board)
    demands = [d for d in _list(_dict(board.get("data")).get("discovered"))
               if isinstance(d, dict) and d.get("kind") == "research_demand"]
    if not searches:
        if not demands:
            return _stage(name, "not_due", "no_open_demand")
        oldest = min((_parse_time(d.get("since")) for d in demands if _parse_time(d.get("since"))), default=None)
        if oldest is not None and now - oldest < DEMAND_GRACE:
            return _stage(name, "not_due", "open_demands_younger_than_24h", open_demands=len(demands))
        return _failed(name, "open_demands_never_searched", open_demands=len(demands))
    latest = searches[-1]
    at = _parse_time(latest.get("latest_at"))
    values = {"latest_at": _iso(at), "outcome": latest.get("outcome"), "reason_code": latest.get("reason_code"),
              "owner_reason": latest.get("reason"), "attempts": sum(_int(s.get("attempt_count")) or 0 for s in searches),
              "demand_id": latest.get("demand_id")}
    if _search_unavailable(latest):
        return _failed(name, "last_search_unavailable:" + str(latest.get("reason_code") or "reason_not_recorded"),
                       **values)
    if at is None or now - at > SEARCH_STALE_AFTER:
        return _failed(name, "last_search_stale", **values)
    return _stage(name, "passed", "last_search_answered", **values)


def stage_templates_evaluated(board: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    name = "templates_evaluated"
    evaluated, reason = _count(board, "templates_evaluated")
    if reason:
        return _failed(name, reason)
    registered, _ = _count(board, "templates_registered")
    snapshots = _governed_snapshots(board)
    newest = snapshots[-1][0] if snapshots else None
    values = {"evaluated": evaluated, "registered": registered,
              "newest_evaluation_day": newest.strftime("%Y-%m-%d") if newest else None}
    if not evaluated:
        return _failed(name, "no_template_evaluated", **values)
    if newest is None:
        return _failed(name, "evaluation_without_governed_context", **values)
    if now - newest > EVALUATION_STALE_AFTER:
        return _failed(name, "evaluation_stale", **values)
    return _stage(name, "passed", "templates_evaluated", **values)


def _card_time(card: Dict[str, Any]) -> Optional[datetime]:
    for key in ("last_revalidation_at", "enqueued_at", "approved_at", "created_at"):
        moment = _parse_time(card.get(key))
        if moment:
            return moment
    return None


def stage_gate_attempts(board: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    name = "gate_attempts"
    reason = _surface(board, "alpha_factory_runs")
    if reason:
        return _failed(name, reason)
    passes, reason = _count(board, "passes")
    if reason:
        return _failed(name, reason)
    data = _dict(board.get("data"))
    by_reason: Dict[str, int] = {}
    working = 0
    errors = 0

    def add(code: str, is_working: bool) -> None:
        nonlocal working, errors
        by_reason[code] = by_reason.get(code, 0) + 1
        if is_working:
            working += 1
        else:
            errors += 1

    for card in _list(data.get("replicated")):
        if isinstance(card, dict) and _within(_card_time(card), now, GATE_WINDOW):
            add("passed", True)
    for card in _list(data.get("document_checked")):
        if isinstance(card, dict) and _within(_card_time(card), now, GATE_WINDOW):
            code = str(card.get("document_check_reason") or "reason_not_recorded")
            add(code, code in GATE_REFUSALS)
    for item in _scaffolded(board):
        when = _parse_time(item.get("latest_at")) if item.get("kind") == "template_search" else _card_time(item)
        if not _within(when, now, GATE_WINDOW):
            continue
        code = str(item.get("reason_code") or "")
        if item.get("kind") == "template_search":
            if code in GATE_REFUSALS:
                add(code, True)
            elif str(item.get("outcome") or "").lower() == "completed" and not _search_unavailable(item):
                add("search_completed", True)
            else:
                add(code or str(item.get("outcome") or "reason_not_recorded"), False)
        else:
            add(code or f"in_progress:{item.get('queue_status') or 'unknown'}", code in GATE_REFUSALS)
    verdict = "no strategy passed" if not passes else f"{passes} strategy(ies) passed"
    values = {"window_days": GATE_WINDOW.days, "by_reason": dict(sorted(by_reason.items())),
              "working_results": working, "unavailable_or_unfinished": errors, "passes": passes, "verdict": verdict}
    if not working:
        why = "no_gate_result_in_window" if not by_reason else "gate_attempts_without_result"
        return _failed(name, why, **values)
    return _stage(name, "passed", "gate_answered:" + verdict.replace(" ", "_"), **values)


def stage_distillation_dead_letters(health: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    name = "distillation_dead_letters"
    row = _dict(health.get("data"))
    if not row:
        return _failed(name, "loop_health_payload_invalid")
    downstream = _dict(row.get("downstream_actual_state"))
    checked = _parse_time(downstream.get("checked_at"))
    last_success = _parse_time(_dict(row.get("last_success")).get("at") or _dict(row.get("last_success")).get("observed_at"))
    failure = _dict(row.get("last_failure"))
    values = {"checked_at": _iso(checked), "last_success_at": _iso(last_success),
              "last_failure_at": failure.get("at") or failure.get("observed_at"),
              "last_failure_reason": failure.get("reason") or failure.get("summary")}
    if not downstream.get("authoritative") or checked is None:
        return _failed(name, "distillation_state_unobserved", **values)
    if now - checked > DISTILLATION_TICK_STALE_AFTER:
        return _failed(name, "distillation_state_stale", **values)
    match = DEAD_LETTER_RE.search(str(downstream.get("summary") or ""))
    if not match:
        return _failed(name, "dead_letter_count_not_reported", summary=downstream.get("summary"), **values)
    unresolved = int(match.group(1))
    if unresolved:
        return _failed(name, "unresolved_dead_letters", unresolved=unresolved, **values)
    return _stage(name, "passed", "no_unresolved_dead_letters", unresolved=0, **values)


BOARD_STAGES: Tuple[Callable[[Dict[str, Any], datetime], Dict[str, Any]], ...] = (
    stage_external_records,
    stage_hypothesis_cards,
    stage_no_fallback_drafts,
    stage_governed_market_dataset,
    stage_template_search,
    stage_templates_evaluated,
    stage_gate_attempts,
)


def judge(board: Any, health: Any, now: datetime) -> Dict[str, Any]:
    """Judge every stage. ``board``/``health`` are the parsed bodies, or a ReadFailed for an unread route."""
    stages: List[Dict[str, Any]] = []
    for fn in BOARD_STAGES:
        stage_name = fn.__name__.replace("stage_", "", 1)
        if isinstance(board, ReadFailed):
            stages.append(_failed(stage_name, f"unreadable:alpha_factory:{board}"))
            continue
        board_reason = _surface(board, "alpha_factory") if isinstance(board, dict) else "alpha_factory_payload_invalid"
        if board_reason:
            stages.append(_failed(stage_name, board_reason))
            continue
        stages.append(fn(board, now))
    if isinstance(health, ReadFailed):
        stages.append(_failed("distillation_dead_letters", f"unreadable:loop_health:{health}"))
    elif not isinstance(health, dict):
        stages.append(_failed("distillation_dead_letters", "loop_health_payload_invalid"))
    else:
        stages.append(stage_distillation_dead_letters(health, now))
    failed = [s["stage"] for s in stages if s["status"] == "failed"]
    gate = next(s for s in stages if s["stage"] == "gate_attempts")
    passes = _dict(gate.get("values")).get("passes")
    return {
        "owner": OWNER,
        "judged_at": _iso(now),
        "verdict": "failed" if failed else "passed",
        "failed_stages": failed,
        "strategies_passed": passes if isinstance(passes, int) else None,
        "strategy_verdict": "no strategy passed" if not passes else f"{passes} strategy(ies) passed",
        "stages": stages,
    }


def check(fetch: Callable[[str], Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    """Two GETs; a read that fails becomes the failure reason of every stage it feeds."""
    moment = now or datetime.now(timezone.utc)
    results: Dict[str, Any] = {}
    for key, path in (("board", ALPHA_FACTORY), ("health", DISTILLATION_HEALTH)):
        try:
            results[key] = fetch(path)
        except ReadFailed as exc:
            results[key] = exc
    return judge(results["board"], results["health"], moment)


def render(report: Dict[str, Any]) -> str:
    lines = []
    for stage in report["stages"]:
        lines.append(f"{stage['status'].upper()} {stage['stage']} reason={stage['reason']} "
                     f"values={json.dumps(stage['values'], sort_keys=True, ensure_ascii=False)}")
    lines.append(f"STRATEGIES {report['strategy_verdict']}")
    lines.append(f"VERDICT {report['verdict']} failed={','.join(report['failed_stages']) or '-'}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- transport


def _request(request: urllib.request.Request) -> Any:
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:  # noqa: S310
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise ReadFailed(f"http_{exc.code}") from None
    except Exception as exc:  # never echo URLs, headers or bodies
        raise ReadFailed(f"request_failed:{type(exc).__name__}") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ReadFailed("response_too_large")
    try:
        return json.loads(raw.decode())
    except ValueError:
        raise ReadFailed("response_not_json") from None


def main(argv: Optional[List[str]] = None, now: Optional[datetime] = None) -> int:
    del argv
    missing = [name for name in REQUIRED if not os.environ.get(name, "").strip()]
    if missing:
        print("skipped: missing " + ", ".join(missing))
        return 77
    base = os.environ["ACCEPTANCE_BFF_URL"].rstrip("/")
    try:
        body = json.dumps({"grant_type": "client_credentials",
                           "client_id": os.environ["ACCEPTANCE_VIEWER_CLIENT_ID"],
                           "client_secret": os.environ["ACCEPTANCE_VIEWER_CLIENT_SECRET"]}).encode()
        login = _request(urllib.request.Request(base + "/bff/auth/dev-login", data=body, method="POST",
                                                headers={"Content-Type": "application/json"}))
        token = str(_dict(login).get("access_token") or "")
        if not token:
            raise ReadFailed("no_access_token")
    except ReadFailed as exc:
        print(f"failed: auth_failed:{exc}")
        return 1
    report = check(lambda path: _request(urllib.request.Request(
        base + path, method="GET", headers={"Authorization": "Bearer " + token})), now)
    # The stage keeps the tail of the output: the JSON first, the per-stage lines last.
    print(json.dumps(report, sort_keys=True, ensure_ascii=False))
    print(render(report))
    return 1 if report["failed_stages"] else 0


if __name__ == "__main__":
    sys.exit(main())
