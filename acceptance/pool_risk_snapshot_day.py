#!/usr/bin/env python3
"""Daily, read-only check: every trading paper pool has a risk snapshot for the last completed session and a known health.

Owner: PAPER-POOL-RISK-SNAPSHOT-20261125 (SD_PAPER_RISK_MANAGEMENT_2026-11-24 section 3.1).

GET only, viewer identity only, after the viewer token exchange. Writes nothing but its stdout.
A pool's health window is built from its snapshot rows, so ``health.window_end`` is the date of the
newest snapshot the pool health consumed. Failures, per judged trading persona:
  - ``no_snapshot_for_last_session``: window_end is missing or older than the last completed session
    of the governed trading calendar (never weekday arithmetic);
  - ``pool_health_unknown``: any of gross_utilisation, drawdown_utilisation, idle_day_share is not known.
effective_bets is judged separately by pool health (it needs member virtual books) and is not failed here.
A pool with no position reads idle (known, zero utilisation), not unknown.

Exit: 0 passed, 1 failed, 77 only when ACCEPTANCE_BFF_URL is unset. See acceptance/README.md.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

_spec = importlib.util.spec_from_file_location("persona_day_for_pool_risk", Path(__file__).with_name("persona_day.py"))
persona_day = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(persona_day)

CALENDAR_PATH = "/bff/market/trading-calendar"  # overridable with ACCEPTANCE_TRADING_CALENDAR_PATH
MEASURES = ("gross_utilisation", "drawdown_utilisation", "idle_day_share")


def _timestamp(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def last_completed_session(calendar: Any, now: datetime) -> Optional[date]:
    """Date of the newest trading session whose close is at or before ``now``; None when unreadable."""
    body = calendar.get("data", calendar) if isinstance(calendar, Mapping) else None
    rows = body.get("sessions") if isinstance(body, Mapping) else None
    if not isinstance(rows, list):
        return None
    ended = []
    for row in rows:
        if not isinstance(row, Mapping) or row.get("is_trading_day") is False or row.get("complete") is False:
            continue
        try:
            closes = _timestamp(row["closes_at"])
            day = date.fromisoformat(str(row.get("date") or row.get("session_date"))[:10])
        except (KeyError, ValueError, TypeError):
            return None
        if closes <= now:
            ended.append(day)
    return max(ended) if ended else None


def pool_health_of(detail: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    pool = (detail.get("data") or {}).get("strategyPool") or {}
    health = pool.get("health")
    if not isinstance(health, Mapping):
        health = ((pool.get("current") or {}).get("decision") or {}).get("health")
    return health if isinstance(health, Mapping) else None


def judge_pool(persona_id: str, health: Optional[Mapping[str, Any]], session: Optional[date]) -> dict[str, Any]:
    failures: list[str] = []
    end = None
    if not health:
        failures += ["no_snapshot_for_last_session", "pool_health_unknown"]
    else:
        try:
            end = date.fromisoformat(str(health.get("window_end")))
        except (TypeError, ValueError):
            end = None
        if session is None:
            failures.append("trading_calendar_unreadable")
        elif end is None or end < session:
            failures.append("no_snapshot_for_last_session")
        unknown = [m for m in MEASURES if (health.get(m) or {}).get("status") != "known"]
        if unknown:
            failures.append("pool_health_unknown")
    reasons = {m: (health.get(m) or {}).get("reason") for m in MEASURES
               if health and (health.get(m) or {}).get("status") != "known"}
    return {"persona_id": persona_id, "passed": not failures, "failures": failures,
            "window_end": end.isoformat() if end else None,
            "last_completed_session": session.isoformat() if session else None, "unknown_reasons": reasons}


def run(fetch: Callable[[str], Any], now: Optional[datetime] = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    listing = fetch("/bff/personas?page_size=200")
    trading, left_out = persona_day.partition_personas(listing.get("items") or listing.get("data") or [])
    session, calendar_error = None, None
    try:
        session = last_completed_session(fetch(os.environ.get("ACCEPTANCE_TRADING_CALENDAR_PATH") or CALENDAR_PATH), now)
    except Exception as exc:  # noqa: BLE001 - reason only, never a body
        calendar_error = type(exc).__name__
    results = []
    for persona in trading:
        pid = persona.get("persona_id") or persona.get("id")
        try:
            results.append(judge_pool(pid, pool_health_of(fetch(f"/bff/personas/{pid}")), session))
        except Exception as exc:  # noqa: BLE001
            results.append({"persona_id": pid, "passed": False, "failures": ["detail_unreadable:" + type(exc).__name__]})
    ok = bool(results) and all(r["passed"] for r in results)
    return {"verdict": "passed" if ok else "failed", "pools": results, "left_out_count": len(left_out),
            "reason": None if results else "no_active_trading_persona", "calendar_error": calendar_error,
            "last_completed_session": session.isoformat() if session else None}


def main() -> int:
    base = os.environ.get("ACCEPTANCE_BFF_URL", "").strip()
    if not base:
        print("skipped: ACCEPTANCE_BFF_URL not set")
        return 77
    client_id = os.environ.get("ACCEPTANCE_VIEWER_CLIENT_ID", "").strip()
    secret = os.environ.get("ACCEPTANCE_VIEWER_CLIENT_SECRET", "")
    if not client_id or not secret:
        print(json.dumps({"verdict": "failed", "reason": "credential_missing:viewer_client"}))
        return 1
    try:
        token = persona_day._login(base, client_id, secret)
        report = run(lambda path: persona_day._get(base, path, token))
    except persona_day.ProbeHttpError as exc:
        print(json.dumps({"verdict": "failed", "reason": str(exc)}))
        return 1
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"verdict": "failed", "reason": "read_failed:" + type(exc).__name__}))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0 if report["verdict"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
