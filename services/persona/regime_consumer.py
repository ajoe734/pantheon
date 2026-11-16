"""First-wave regime consumer for the daily cycle (SD-REGIME-STATE-ENGINE R13).

Observe calls the registry's idempotent ``POST /api/registry/regime/evaluate``, then reads the
active ``RegimeState`` record of each pool strategy and the shared market state. It only reads
stored records: a refusal, a missing record or an unconfigured registry yields an explicit
``unavailable`` reason and never a value. The persona's own interpretation is never written back
to the registry (this module issues exactly one mutating call, the evaluate request).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

REF_PREFIX = "regime-state://"
MARKET = "TW"
Transport = Callable[[str, str, "dict | None"], "tuple[int, Any]"]  # (method, path, body) -> (status, json)


def session_for_run(run: Mapping[str, Any]) -> str:
    """Taipei calendar date of the cron run; the registry refuses a session without complete data."""
    ms = run.get("runAtMs")
    at = datetime.fromtimestamp(ms / 1000, timezone.utc) if isinstance(ms, (int, float)) else datetime.now(timezone.utc)
    return (at + timedelta(hours=8)).date().isoformat()


def http_transport(base_url: str, token: str, tenant: str, *, timeout: float = 20) -> Transport:
    def call(method: str, path: str, body: dict | None = None) -> tuple[int, Any]:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Authorization": f"Bearer {token}", "X-Tenant-Id": tenant}
        if data is not None:
            headers["Content-Type"] = "application/json"
        try:
            with urlopen(Request(base_url.rstrip("/") + path, data=data, headers=headers, method=method),
                         timeout=timeout) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            try:
                return exc.code, json.load(exc)
            except Exception:
                return exc.code, None
    return call


def transport_from_env(env: Mapping[str, str], tenant: str) -> Transport | None:
    base = next((env[k] for k in ("PANTHEON_REGISTRY_API_URL", "REGISTRY_URL") if env.get(k)), "")
    token = next((env[k] for k in ("PANTHEON_REGISTRY_SERVICE_TOKEN", "PERSONA_REGISTRY_SERVICE_TOKEN") if env.get(k)), "")
    return http_transport(base, token, tenant) if base and token else None


def _unavailable(reason: str, session: str) -> dict[str, Any]:
    return {"status": "unavailable", "reason": reason, "session": session, "regime_state_ref": None,
            "regime_tag": "", "records": {}, "macro": None}


def observe_regime(transport: Transport | None, *, session: str, strategy_ids: Sequence[str]) -> dict[str, Any]:
    """Evaluate for the session, then read the active record of each strategy. Never raises."""
    if transport is None:
        return _unavailable("registry_unconfigured", session)
    try:
        status, body = transport("POST", "/api/registry/regime/evaluate", {"session": session, "market": MARKET})
        if status != 200:
            code = ((body or {}).get("detail") or {}).get("code") if isinstance(body, dict) else None
            detail = (body or {}).get("detail") if isinstance(body, dict) else None
            code = code or (detail if isinstance(detail, str) else None)
            return _unavailable(f"evaluate_refused:{status}:{code or 'unknown'}"[:160], session)
        records: dict[str, dict[str, Any]] = {}
        missing: list[str] = []
        for sid in dict.fromkeys(strategy_ids):
            code, record = transport("GET", f"/api/registry/regime/strategies/{quote(sid, safe='')}/active", None)
            if code == 200 and isinstance(record, dict) and record.get("regime_id"):
                records[sid] = record
            else:
                missing.append(sid)
        macro = None
        code, ms = transport("GET", f"/api/registry/regime/market-state/{MARKET}/{session}", None)
        if code == 200 and isinstance(ms, dict):
            for row in ms.get("market_states") or []:
                axis = (row.get("axes") or {}).get("macro") or {}
                if axis.get("available") and axis.get("state"):
                    macro = {"state": axis["state"], "session": session, "as_of": axis.get("as_of_session")}
        if not records:
            out = _unavailable("no_active_record_for_pool_strategies" if strategy_ids else "no_pool_strategies", session)
            out["macro"] = macro
            return out
        first = next((s for s in strategy_ids if s in records), None)
        rec = records[first]
        return {"status": "observed", "reason": None, "session": session,
                "regime_state_ref": REF_PREFIX + rec["regime_id"], "regime_tag": str(rec.get("regime_class") or ""),
                "records": {s: {"regime_id": r["regime_id"], "regime_class": r.get("regime_class"),
                                "confidence": r.get("confidence")} for s, r in records.items()},
                "missing_strategies": missing, "macro": macro}
    except Exception as exc:  # the cycle runs without a regime rather than inventing one
        return _unavailable(f"registry_unavailable:{type(exc).__name__}", session)


def previous_macro(snapshots: Mapping[str, Mapping[str, Any]], persona: Mapping[str, Any], session: str) -> dict | None:
    """Latest macro state this persona stored in an earlier session's cycle packet."""
    best = None
    for p in snapshots.values():
        if p.get("tenant_id") != persona["tenant_id"] or persona["persona_id"] not in p.get("persona_ids", []):
            continue
        macro = ((p.get("persona_cycle") or {}).get("regime") or {}).get("macro")
        if macro and macro.get("session") and macro["session"] < session and (
                best is None or macro["session"] > best["session"]):
            best = macro
    return best
