#!/usr/bin/env python3
"""Daily, read-only check: every trading paper pool has a risk snapshot for the last completed session.

Owner: PAPER-POOL-RISK-SNAPSHOT-20261125 (SD_PAPER_RISK_MANAGEMENT_2026-11-24 section 3.1);
sources: POOL-RISK-SNAPSHOT-DAILY-CHECK-READS-REAL-SOURCES-20261125.

GET only, viewer identity only, after the viewer token exchange. Writes nothing but its stdout.
Reads (all existing routes, each response capped at 5 MiB):
  - /bff/personas, /bff/personas/{id}  trading personas and the served pool health;
  - /api/v1/runtime-bindings           persona -> paper runtime;
  - /api/v1/telemetry/{runtime_id}/summary  the served ``pool_risk_snapshots`` rows;
  - /bff/management/trade-journeys     released decisions: the last completed session is the Taipei trading
    day of the newest released decision (stage trade_decision succeeded) of the trading personas; when none
    exists, the newest mark date (as_of) on the snapshot rows of the other pools. Never a calendar route,
    never weekday arithmetic.
Per persona: no row for the session is failed; a row whose equity or gross_exposure is null is failed with the
row's own reason; pool health unknown with fewer than 20 rows is pending (exit 0, names the row count), unknown
with 20 or more rows is failed. An unreadable source is failed with its name.

Exit: 0 passed or pending, 1 failed, 77 only when ACCEPTANCE_BFF_URL is unset. See acceptance/README.md.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

_spec = importlib.util.spec_from_file_location("persona_day_for_pool_risk", Path(__file__).with_name("persona_day.py"))
persona_day = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(persona_day)

MEASURES = ("gross_utilisation", "drawdown_utilisation", "idle_day_share")
HEALTH_WINDOW_ROWS = 20
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
TAIPEI = timezone(timedelta(hours=8))
# Route templates this check reads; a test resolves each in the BFF application.
READ_ROUTES = (
    "/bff/personas", "/bff/personas/{persona_id}", "/api/v1/runtime-bindings",
    "/api/v1/telemetry/{runtime_id}/summary", "/bff/management/trade-journeys",
)


class SourceUnreadable(Exception):
    def __init__(self, source: str, cause: Any):
        super().__init__(f"{source}_unreadable:{cause}")
        self.source = source


def _read(fetch: Callable[[str], Any], source: str, path: str) -> Any:
    try:
        body = fetch(path)
    except Exception as exc:  # noqa: BLE001 - reason only, never a body
        raise SourceUnreadable(source, str(exc) if isinstance(exc, persona_day.ProbeHttpError) else type(exc).__name__) from None
    meta = body.get("meta") if isinstance(body, Mapping) else None
    if isinstance(meta, Mapping) and (meta.get("status") in {"unavailable", "degraded"} or meta.get("read_state") == "unavailable"):
        raise SourceUnreadable(source, "source_reports_unavailable")
    if not isinstance(body, Mapping):
        raise SourceUnreadable(source, "not_an_object")
    return body


def _day(value: Any) -> Optional[date]:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(TAIPEI).date()


def decision_day(journey: Mapping[str, Any]) -> Optional[date]:
    """Taipei trading day of a released decision: stage trade_decision succeeded."""
    stage = (journey.get("stages") or {}).get("trade_decision")
    if not isinstance(stage, Mapping) or stage.get("status") != "succeeded":
        return None
    return _day(stage.get("updated_at") or journey.get("created_at"))


def pool_health_of(detail: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    pool = (detail.get("data") or {}).get("strategyPool") or {}
    health = pool.get("health")
    if not isinstance(health, Mapping):
        health = ((pool.get("current") or {}).get("decision") or {}).get("health")
    return health if isinstance(health, Mapping) else None


def persona_rows(fetch: Callable[[str], Any], persona_id: str, bindings: list) -> tuple[dict[str, Mapping[str, Any]], int]:
    """({as_of: row}, retained row count) from the served summaries of the persona's paper runtimes."""
    rows: dict[str, Mapping[str, Any]] = {}
    count = 0
    for binding in bindings:
        if binding.get("persona_id") != persona_id or binding.get("retired_at"):
            continue
        if str(binding.get("status") or "").strip().lower() in {"retired", "failed"}:
            continue
        if (binding.get("deployment_stage") or binding.get("deployment_mode")) != "paper" or not binding.get("runtime_id"):
            continue
        runtime = urllib.parse.quote(str(binding["runtime_id"]), safe="")
        summary = _read(fetch, "telemetry_summary", f"/api/v1/telemetry/{runtime}/summary").get("data")
        if not isinstance(summary, Mapping):
            raise SourceUnreadable("telemetry_summary", "no_data")
        served = summary.get("pool_risk_snapshots")
        served = served if isinstance(served, list) else []
        for row in served:
            if isinstance(row, Mapping) and row.get("as_of"):
                rows[str(row["as_of"])[:10]] = row
        retained = summary.get("pool_risk_snapshot_retained_days")
        count = max(count, retained if isinstance(retained, int) and not isinstance(retained, bool) else len(served))
    return rows, count


def judge_persona(persona_id: str, rows: Mapping[str, Mapping[str, Any]], count: int,
                  health: Optional[Mapping[str, Any]], session: Optional[date]) -> dict[str, Any]:
    failures: list[str] = []
    pending: list[str] = []
    if session is None:
        failures.append("last_session_unknown")
    else:
        row = rows.get(session.isoformat())
        if row is None:
            failures.append("no_row_for_last_session")
        else:
            nulls = row.get("nulls") if isinstance(row.get("nulls"), Mapping) else {}
            for field in ("equity", "gross_exposure"):
                if row.get(field) is None:
                    failures.append(f"{field}_null:{nulls.get(field) or 'no_reason_on_row'}")
    unknown = [m for m in MEASURES if not health or (health.get(m) or {}).get("status") != "known"]
    reasons = {m: ((health or {}).get(m) or {}).get("reason") for m in unknown}
    if unknown:
        if count < HEALTH_WINDOW_ROWS:
            pending.append(f"pool_health_pending:rows={count}/{HEALTH_WINDOW_ROWS}")
        else:
            failures.append(f"pool_health_unknown:rows={count}")
    status = "failed" if failures else "pending" if pending else "passed"
    return {"persona_id": persona_id, "status": status, "passed": status != "failed", "failures": failures,
            "pending": pending, "row_count": count, "unknown_reasons": reasons}


def run(fetch: Callable[[str], Any], now: Optional[datetime] = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    report: dict[str, Any] = {"checked_at": now.isoformat(), "pools": [], "last_completed_session": None}
    try:
        listing = _read(fetch, "personas", "/bff/personas?page_size=200")
        trading, left_out = persona_day.partition_personas(listing.get("items") or listing.get("data") or [])
        report["left_out_count"] = len(left_out)
        if not trading:
            return {**report, "verdict": "failed", "reason": "no_active_trading_persona"}
        bindings = _read(fetch, "runtime_bindings", "/api/v1/runtime-bindings").get("data")
        if not isinstance(bindings, list):
            raise SourceUnreadable("runtime_bindings", "not_a_list")
        loaded: dict[str, tuple] = {}
        decisions: list[date] = []
        for persona in trading:
            pid = str(persona.get("persona_id") or persona.get("id"))
            loaded[pid] = persona_rows(fetch, pid, [b for b in bindings if isinstance(b, Mapping)])
            query = urllib.parse.urlencode({"tenant_id": persona.get("tenant_id") or persona.get("tenantId") or "",
                                            "environment": "paper", "persona_id": pid,
                                            "sort": "updated_at_desc", "page_size": 50})
            items = (_read(fetch, "trade_journeys", "/bff/management/trade-journeys?" + query).get("data") or {}).get("items") or []
            days = [d for d in (decision_day(j) for j in items if isinstance(j, Mapping)) if d]
            if days:
                decisions.append(max(days))
        details = {pid: _read(fetch, "persona_detail", f"/bff/personas/{urllib.parse.quote(pid, safe='')}") for pid in loaded}
    except SourceUnreadable as exc:
        return {**report, "verdict": "failed", "reason": str(exc), "source": exc.source}
    results = []
    for pid, (rows, count) in loaded.items():
        session = max(decisions) if decisions else max(
            (d for other, (other_rows, _) in loaded.items() if other != pid for d in other_rows), default=None)
        session = session if isinstance(session, date) else date.fromisoformat(session) if session else None
        report["last_completed_session"] = session.isoformat() if session else report["last_completed_session"]
        results.append(judge_persona(pid, rows, count, pool_health_of(details[pid]), session))
    verdict = "failed" if any(r["status"] == "failed" for r in results) else \
        "pending" if any(r["status"] == "pending" for r in results) else "passed"
    return {**report, "verdict": verdict, "pools": results,
            "session_source": "released_decisions" if decisions else "other_pool_snapshot_rows"}


def _get_capped(base: str, path: str, token: str) -> Any:
    request = urllib.request.Request(base.rstrip("/") + path, method="GET",
                                     headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - operator-supplied BFF URL
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise persona_day.ProbeHttpError(f"http_{exc.code}") from None
    except Exception as exc:
        raise persona_day.ProbeHttpError(type(exc).__name__) from None
    if len(body) > MAX_RESPONSE_BYTES:
        raise persona_day.ProbeHttpError("response_over_5MiB")
    return json.loads(body)


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
        report = run(lambda path: _get_capped(base, path, token))
    except persona_day.ProbeHttpError as exc:
        print(json.dumps({"verdict": "failed", "reason": str(exc)}))
        return 1
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"verdict": "failed", "reason": "read_failed:" + type(exc).__name__}))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0 if report["verdict"] in {"passed", "pending"} else 1


if __name__ == "__main__":
    sys.exit(main())
