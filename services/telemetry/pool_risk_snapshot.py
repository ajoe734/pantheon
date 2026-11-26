"""Daily PoolRiskSnapshot: one measured risk row per paper pool and trading day.

Design: docs/04/pantheon_paper_risk_management_2026-11-24/SD_PAPER_RISK_MANAGEMENT_2026-11-24.md 3.1.

Measurement only. Nothing here is read by an order, sizing, admission, allocation, approval
or policy path. ``risk_budget_factor`` is recorded with its inputs and applied by nothing
(operator decision Q4: first quarter record-only).

Every value comes from facts the telemetry owner already holds: the paper valuation event
(equity, cash, positions at the marks), the governed calendar evidence carried on the marks,
and the limits of the bound risk policy stamped on the valuation event. A value that cannot
be computed is ``None`` with a reason in ``nulls``; it is never zero and never carried
forward. History dependent fields are recomputed from the whole retained series after every
write, so replays, duplicates, out-of-order events and restarts converge on the same rows.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

SCHEMA_VERSION = "pool_risk_snapshot.v1"
RETENTION_TRADING_DAYS = 400
WINDOW_DAYS = 20
OBSERVATION_ROWS = 20  # the most a consumer or a daily-cycle prompt is given
PERIODS_PER_YEAR = 252
CVAR_TAIL = 0.05
# Governed close per market. A row is written only after this local close.
_CLOSES = {"TW": ("Asia/Taipei", time(13, 30))}
_DEFAULT_MARKET = "TW"
_BASE_FIELDS = (
    "as_of", "pool_id", "persona_capital_binding_id", "policy_ref", "equity", "initial_cash",
    "gross_notional", "net_notional", "cash", "top_name_notional", "gross_limit",
    "drawdown_limit", "equity_basis", "calendar_basis", "nulls_base",
)


def _finite(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _parse(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


def holidays_from_marks(mark_refs: Sequence[Mapping[str, Any]]) -> tuple[set[date], Optional[str]]:
    """Holidays and market from the governed calendar evidence on the marks, if any."""
    holidays: set[date] = set()
    market = None
    for ref in mark_refs or ():
        evidence = ref.get("calendar_evidence") if isinstance(ref, Mapping) else None
        if not isinstance(evidence, Mapping):
            continue
        market = market or evidence.get("market")
        for day in (evidence.get("holidays") or {}):
            try:
                holidays.add(date.fromisoformat(day))
            except (TypeError, ValueError):
                continue
    return holidays, market


def is_trading_day(day: date, holidays: set[date]) -> bool:
    from services.execution.lean_runtime.instrument_candidates import is_trading_day as governed
    return bool(governed(day, holidays))


def _market_close(day: date, market: str) -> Optional[datetime]:
    zone = _CLOSES.get(market)
    if zone is None:
        return None
    return datetime.combine(day, zone[1], tzinfo=ZoneInfo(zone[0])).astimezone(timezone.utc)


def _digest(base: Mapping[str, Any]) -> str:
    text = json.dumps({k: base.get(k) for k in _BASE_FIELDS}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode()).hexdigest()


def build_base(*, valuation: Mapping[str, Any], metadata: Mapping[str, Any], pool_id: Any,
               binding: Any, event_created_at: Any) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Base facts of a closing row from one known owner valuation, or (None, reason).

    ``valuation`` is the retained quarter-equity observation (status known); ``metadata`` the
    valuation event metadata that carries position_facts and risk_policy_facts.
    """
    if not pool_id:
        return None, "pool_id_missing"
    refs = valuation.get("mark_refs") or []
    holidays, market = holidays_from_marks(refs)
    market = market or _DEFAULT_MARKET
    calendar_basis = "governed_calendar_evidence" if holidays or any(
        isinstance(r, Mapping) and r.get("calendar_evidence") for r in refs) else "weekday_default_no_evidence"
    mark_days = [_parse(r.get("as_of")) for r in refs if isinstance(r, Mapping)]
    mark_days = [m for m in mark_days if m]
    as_of = _parse(valuation.get("as_of"))
    if as_of is None:
        return None, "valuation_as_of_missing"
    day = (max(mark_days) if mark_days else as_of).date()
    if not is_trading_day(day, holidays):
        return None, "not_a_trading_day"
    close = _market_close(day, market)
    created = _parse(event_created_at)
    if close is None:
        return None, "market_close_unknown"
    if created is None or created < close:
        return None, "before_market_close"
    return _base_row(day, valuation, metadata, pool_id, binding, calendar_basis, "owner_valuation"), None


def _base_row(day: date, valuation: Mapping[str, Any], metadata: Mapping[str, Any], pool_id: Any,
              binding: Any, calendar_basis: str, equity_basis: str) -> dict[str, Any]:
    nulls: dict[str, str] = {}
    equity = _finite(valuation.get("equity")) if valuation else None
    if equity is None or equity <= 0:
        equity = None
        nulls["equity"] = "no_known_owner_valuation"
    facts = (metadata or {}).get("position_facts")
    gross = net = top = None
    if isinstance(facts, list):
        notionals = [_finite(f.get("notional")) for f in facts if isinstance(f, Mapping)]
        if all(n is not None for n in notionals):
            gross = round(sum(abs(n) for n in notionals), 6)
            net = round(sum(notionals), 6)
            top = round(max((abs(n) for n in notionals), default=0.0), 6)
        else:
            for key in ("gross_notional", "net_notional", "top_name_notional"):
                nulls[key] = "position_mark_missing"
    elif valuation and not valuation.get("mark_refs"):
        gross = net = top = 0.0  # a flat book holds nothing: a fact, not a default
    else:
        for key in ("gross_notional", "net_notional", "top_name_notional"):
            nulls[key] = "position_facts_missing"
    policy = (metadata or {}).get("risk_policy_facts")
    policy = policy if isinstance(policy, Mapping) else {}
    ref = {k: policy.get(k) for k in ("risk_policy_id", "risk_policy_version", "risk_policy_digest")}
    if not all(ref.values()):
        ref = {"unavailable_reason": policy.get("unavailable_reason") or "risk_policy_facts_missing"}
    elif policy.get("_basis"):
        ref["basis"] = policy["_basis"]
    gross_limit = _finite(policy.get("gross_limit"))
    gross_limit = gross_limit if gross_limit and gross_limit > 0 else None
    if gross_limit is None:
        nulls["gross_limit"] = "policy_gross_limit_missing"
    dd_limit = _finite(policy.get("drawdown_risk_off"))
    dd_limit = dd_limit if dd_limit and dd_limit > 0 else None
    if dd_limit is None:
        nulls["drawdown_limit"] = "policy_drawdown_risk_off_missing"
    cash = _finite((valuation or {}).get("cash"))
    if cash is None:
        cash = _finite((metadata or {}).get("ledger_cash"))
    if cash is None:
        nulls["cash"] = "cash_missing"
    return {
        "as_of": day.isoformat(), "pool_id": str(pool_id),
        "persona_capital_binding_id": binding, "policy_ref": ref,
        "equity": equity, "initial_cash": _finite((valuation or {}).get("initial_cash")),
        "gross_notional": gross, "net_notional": net, "cash": cash, "top_name_notional": top,
        "gross_limit": gross_limit, "drawdown_limit": dd_limit,
        "equity_basis": equity_basis, "calendar_basis": calendar_basis, "nulls_base": nulls,
        "valuation_as_of": (valuation or {}).get("as_of"),
    }


def build_idle_base(*, day: date, valuation: Optional[Mapping[str, Any]], metadata: Mapping[str, Any],
                    pool_id: Any, binding: Any, holidays: set[date], never_filled: bool) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """A flat book after the close: nothing held, equity is the cash ledger's own value."""
    if not pool_id:
        return None, "pool_id_missing"
    if not is_trading_day(day, holidays):
        return None, "not_a_trading_day"
    basis = "flat_cash_ledger"
    base_valuation: dict[str, Any] = dict(valuation or {})
    if never_filled and base_valuation.get("initial_cash") is not None:
        base_valuation.setdefault("equity", base_valuation["initial_cash"])
        base_valuation["equity"] = base_valuation["initial_cash"]
        base_valuation["cash"] = base_valuation["initial_cash"]
        basis = "flat_never_filled_initial_cash"
    elif base_valuation.get("equity") is None and base_valuation.get("cash") is not None:
        base_valuation["equity"] = base_valuation["cash"]
    base_valuation["mark_refs"] = []
    row = _base_row(day, base_valuation, {**(metadata or {}), "position_facts": []}, pool_id, binding,
                    "weekday_default_no_evidence" if not holidays else "governed_calendar_evidence", basis)
    return row, None


def _returns(rows: Sequence[Mapping[str, Any]], holidays: set[date]) -> list[tuple[float, float]]:
    """(return, pnl) between consecutive trading days that both have equity; gaps break the series."""
    out = []
    for prev, cur in zip(rows, rows[1:]):
        if prev.get("equity") is None or cur.get("equity") is None:
            out.append(None)
            continue
        expected = date.fromisoformat(prev["as_of"]) + timedelta(days=1)
        while not is_trading_day(expected, holidays):
            expected += timedelta(days=1)
        if expected != date.fromisoformat(cur["as_of"]):
            out.append(None)
            continue
        out.append((cur["equity"] / prev["equity"] - 1.0, cur["equity"] - prev["equity"]))
    return out


def _stdev(values: Sequence[float]) -> float:
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))


def finalize(rows: list[dict[str, Any]], *, holidays: Optional[set[date]] = None,
             budget_full: Optional[float] = None) -> list[dict[str, Any]]:
    """Recompute every history dependent field from the retained series (deterministic)."""
    holidays = holidays or set()
    rows.sort(key=lambda r: r["as_of"])
    returns = _returns(rows, holidays)
    peak = None
    for index, row in enumerate(rows):
        nulls = dict(row.get("nulls_base") or {})
        equity = row.get("equity")
        if equity is not None:
            seed = [row.get("initial_cash")] if row.get("initial_cash") else []
            peak = max([p for p in (peak, equity, *seed) if p is not None])
        row["peak_equity"] = peak if equity is not None else None
        if equity is None or peak is None:
            row["drawdown_pct"] = None
            nulls["drawdown_pct"] = nulls.get("equity", "equity_missing")
        else:
            row["drawdown_pct"] = round(max(0.0, (peak - equity) / peak), 10)
        for key, field in (("gross_exposure", "gross_notional"), ("net_exposure", "net_notional"),
                           ("top_name_weight", "top_name_notional")):
            if equity is None or row.get(field) is None:
                row[key] = None
                nulls[key] = nulls.get(field) or nulls.get("equity", "equity_missing")
            else:
                row[key] = round(row[field] / equity, 10)
        row["leverage"] = row["gross_exposure"]
        if row["leverage"] is None:
            nulls["leverage"] = nulls.get("gross_exposure", "gross_exposure_missing")
        cash = row.get("cash")
        if cash is None or equity is None:
            row["cash_reserve_pct"] = None
            nulls["cash_reserve_pct"] = nulls.get("cash") or nulls.get("equity", "equity_missing")
        else:
            row["cash_reserve_pct"] = round(cash / equity, 10)
        gl, dl = row.get("gross_limit"), row.get("drawdown_limit")
        row["gross_utilisation"] = (round(row["gross_exposure"] / gl, 10)
                                    if row["gross_exposure"] is not None and gl else None)
        if row["gross_utilisation"] is None:
            nulls["gross_utilisation"] = nulls.get("gross_limit") or nulls.get("gross_exposure", "gross_exposure_missing")
        row["drawdown_utilisation"] = (round(row["drawdown_pct"] / dl, 10)
                                       if row["drawdown_pct"] is not None and dl else None)
        if row["drawdown_utilisation"] is None:
            nulls["drawdown_utilisation"] = nulls.get("drawdown_limit") or nulls.get("drawdown_pct", "drawdown_pct_missing")
        window = returns[max(0, index - WINDOW_DAYS):index]  # the WINDOW_DAYS returns ending at this row
        full = len(window) == WINDOW_DAYS and all(item is not None for item in window)
        if full:
            row["realized_vol_20d"] = round(_stdev([r for r, _ in window]) * math.sqrt(PERIODS_PER_YEAR), 10)
            pnls = sorted(p for _, p in window)
            tail = max(1, math.ceil(CVAR_TAIL * len(pnls)))
            row["cvar_95"] = round(sum(pnls[:tail]) / tail, 6)
        else:
            row["realized_vol_20d"] = row["cvar_95"] = None
            nulls["realized_vol_20d"] = nulls["cvar_95"] = "under_20_consecutive_returns"
        # Not derivable from the valuation event alone; named honestly rather than zero-filled.
        row["effective_bets"] = None
        nulls["effective_bets"] = "owned_by_pool_health_member_books"
        row["turnover_20d"] = None
        nulls["turnover_20d"] = "no_fill_notional_on_event"
        row["ladder_state"] = None
        nulls["ladder_state"] = "ladder_is_phase_2"
        row["breaches"] = None
        nulls["breaches"] = "no_rejections_retained"
        row["nulls"] = nulls
        row["digest"] = _digest(row)
        row["schema_version"] = SCHEMA_VERSION
    attach_risk_budget_factor(rows, budget_full=budget_full)
    return rows


def attach_risk_budget_factor(rows: list[dict[str, Any]], *, budget_full: Optional[float]) -> None:
    """RECORD ONLY (Q4). Same definition as the allocation consumer: min(1, utilisation / budget_full).

    Computed from the trailing observation window; nothing that sizes, admits or allocates reads it.
    """
    for index, row in enumerate(rows):
        window = rows[max(0, index - OBSERVATION_ROWS + 1):index + 1]
        values = [r.get("gross_utilisation") for r in window]
        known = len(window) == OBSERVATION_ROWS and all(v is not None for v in values)
        # Owner-valued rows only: a placeholder row (no valuation) is heartbeat-derived and does not survive
        # heartbeat retention, so it must not shape the recorded inputs of a row that does.
        inputs = {"window_rows": sum(1 for r in window if r.get("equity") is not None), "budget_full": budget_full, "applied": False}
        if known and budget_full and budget_full > 0:
            mean = sum(values) / len(values)
            inputs["mean_gross_utilisation"] = round(mean, 10)
            row["risk_budget_factor"] = round(min(1.0, mean / budget_full), 8)
        else:
            row["risk_budget_factor"] = None
            inputs["reason"] = ("budget_full_unknown" if not budget_full else
                                "fewer_than_20_known_gross_utilisation_days")
        row["risk_budget_factor_inputs"] = inputs


def upsert(rows: list[dict[str, Any]], base: dict[str, Any], *, rank: int) -> tuple[list[dict[str, Any]], str]:
    """Insert or replace the one row of (pool, day). Returns (rows, outcome).

    Outcomes: inserted, unchanged (duplicate/replay), replaced (late correction, previous digest kept),
    ignored_stale (an older or weaker observation of a day that already has a better one).
    ``rank``: 2 owner valuation, 1 flat/idle derivation. A better-ranked or later valuation wins
    regardless of arrival order.
    """
    base = {**base, "_rank": rank}
    new_digest = _digest(base)
    for index, row in enumerate(rows):
        if row["as_of"] != base["as_of"] or row["pool_id"] != base["pool_id"]:
            continue
        if row.get("digest") == new_digest or _digest(row) == new_digest:
            return rows, "unchanged"
        old_key = (row.get("_rank", 1), row.get("valuation_as_of") or "")
        new_key = (rank, base.get("valuation_as_of") or "")
        if new_key < old_key:
            return rows, "ignored_stale"
        if row.get("_rank", 1) == 0 and rank > 0:
            rows[index] = {**base, "revision": 1}
            return rows, "replaced"
        replaced = {**base, "previous_digest": row.get("digest"), "revision": int(row.get("revision") or 1) + 1}
        rows[index] = replaced
        return rows, "replaced"
    rows.append({**base, "revision": 1})
    return rows, "inserted"


def enforce_retention(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cap per pool: delete the oldest days beyond RETENTION_TRADING_DAYS on write."""
    rows.sort(key=lambda r: (r["pool_id"], r["as_of"]))
    kept: list[dict[str, Any]] = []
    by_pool: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_pool.setdefault(row["pool_id"], []).append(row)
    for pool_rows in by_pool.values():
        kept.extend(pool_rows[-RETENTION_TRADING_DAYS:])
    return kept


def observations(rows: Sequence[Mapping[str, Any]], *, limit: int = OBSERVATION_ROWS) -> list[dict[str, Any]]:
    """pool_observations in the shape pool health consumes; at most the last ``limit`` rows."""
    out = []
    for row in list(rows)[-limit:]:
        out.append({
            "date": row["as_of"],
            "source_ref": f"pool_risk_snapshot:{row['pool_id']}:{row['as_of']}:{str(row.get('digest'))[:12]}",
            "gross_notional": row.get("gross_exposure"), "gross_limit": row.get("gross_limit"),
            "drawdown": row.get("drawdown_pct"), "drawdown_limit": row.get("drawdown_limit"),
        })
    return out


# ---------------------------------------------------------------------------
# Projection hook: called by the runtime summary store after an event is folded in.
# ---------------------------------------------------------------------------

ROWS_KEY = "_pool_risk_snapshot_rows"          # retained store, <= 400 days per pool, never served whole
HOLIDAYS_KEY = "_pool_risk_holidays"
POLICY_KEY = "_pool_risk_policy_facts"         # last bound-policy facts a valuation or state snapshot stamped
ACCOUNT_KEY = "_pool_risk_account_facts"       # last account facts (initial_cash, cash, fill_count)
LAST_WRITE_KEY = "_pool_risk_snapshot_last_write"
_POLICY_FACT_KEYS = (
    "risk_policy_id", "risk_policy_version", "risk_policy_digest",
    "gross_limit", "max_leverage", "drawdown_risk_off", "drawdown_warn",
    "unavailable_reason",
)


def _taipei_date(created_at: Any) -> Optional[date]:
    created = _parse(created_at)
    if created is None:
        return None
    return created.astimezone(ZoneInfo(_CLOSES[_DEFAULT_MARKET][0])).date()


def _budget_full() -> Optional[float]:
    """budget_full of the pool policy the allocation consumer already uses; unknown -> None."""
    import os
    path = os.environ.get("PANTHEON_POOL_POLICY_PATH") or os.path.join(
        os.path.dirname(__file__), "..", "control-plane", "persona", "config", "pool_policy.json")
    try:
        with open(path, encoding="utf-8") as handle:
            return _finite(json.load(handle).get("budget_full"))
    except (OSError, ValueError):
        return None


def _taipei_day_after_close(created_at: Any) -> Optional[date]:
    created = _parse(created_at)
    if created is None:
        return None
    local = created.astimezone(ZoneInfo(_CLOSES[_DEFAULT_MARKET][0]))
    return local.date() if local.time() >= _CLOSES[_DEFAULT_MARKET][1] else None


def apply_event(current: dict[str, Any], event: Mapping[str, Any], *, observation: Optional[Mapping[str, Any]],
                metadata: Mapping[str, Any]) -> None:
    """Fold one event into the summary's snapshot rows. Never raises into the projection path."""
    try:
        _apply_event(current, event, observation=observation, metadata=metadata)
    except Exception as exc:  # noqa: BLE001 - measurement must never break telemetry ingest
        current["pool_risk_snapshot_error"] = type(exc).__name__


def _apply_event(current, event, *, observation, metadata) -> None:
    if str(current.get("deployment_stage") or "").lower() != "paper":
        return
    pool_id = current.get("capital_pool_id")
    binding = current.get("persona_capital_binding_id")
    rows: list[dict[str, Any]] = [dict(r) for r in current.get(ROWS_KEY) or []]
    holidays = {date.fromisoformat(d) for d in current.get(HOLIDAYS_KEY) or []}
    outcomes: list[tuple[str, str]] = []
    event_type = str(event.get("event_type") or "")

    if event_type in ("runtime_state_snapshot", "heartbeat"):
        raw_account = metadata.get("account_facts")
        if isinstance(raw_account, Mapping):
            init_cash = _finite(raw_account.get("initial_cash"))
            cash = _finite(raw_account.get("cash"))
            raw_fc = raw_account.get("fill_count")
            fc = (
                int(raw_fc)
                if isinstance(raw_fc, int) and not isinstance(raw_fc, bool) and raw_fc >= 0
                else None
            )
            if init_cash is not None and init_cash > 0 and cash is not None and fc is not None:
                event_day = _taipei_date(event.get("created_at"))
                event_day_iso = event_day.isoformat() if event_day else None
                prev_acc = current.get(ACCOUNT_KEY)
                prev_fc = prev_acc.get("fill_count") if isinstance(prev_acc, Mapping) else None
                if prev_fc is None or fc >= prev_fc:
                    if (
                        isinstance(prev_acc, Mapping)
                        and prev_fc == fc
                        and prev_acc.get("initial_cash") == init_cash
                        and prev_acc.get("cash") == cash
                    ):
                        prev_as_of = prev_acc.get("as_of")
                        candidates = [d for d in (prev_as_of, event_day_iso) if isinstance(d, str) and d]
                        as_of = min(candidates) if candidates else None
                    else:
                        as_of = event_day_iso
                    new_acc: dict[str, Any] = {
                        "initial_cash": init_cash,
                        "cash": cash,
                        "fill_count": fc,
                    }
                    if as_of:
                        new_acc["as_of"] = as_of
                    current[ACCOUNT_KEY] = new_acc
        raw_policy = metadata.get("risk_policy_facts")
        if isinstance(raw_policy, Mapping) and (
            raw_policy.get("risk_policy_digest") or raw_policy.get("unavailable_reason")
        ):
            basis_name = "runtime_state_snapshot" if event_type == "runtime_state_snapshot" else "last_valuation_event"
            current[POLICY_KEY] = {
                **{k: raw_policy[k] for k in _POLICY_FACT_KEYS if k in raw_policy},
                "_basis": basis_name,
            }

    if (event_type == "pnl_snapshot" and observation and observation.get("status") == "known"):
        new_holidays, _ = holidays_from_marks(observation.get("mark_refs") or [])
        holidays |= new_holidays
        facts = metadata.get("risk_policy_facts")
        if isinstance(facts, Mapping) and (facts.get("risk_policy_digest") or facts.get("unavailable_reason")):
            current[POLICY_KEY] = {
                **{k: facts[k] for k in _POLICY_FACT_KEYS if k in facts},
                "_basis": "last_valuation_event",
            }
        base, reason = build_base(valuation=observation, metadata=metadata, pool_id=pool_id,
                                  binding=binding, event_created_at=event.get("created_at"))
        if base is not None:
            rows, outcome = upsert(rows, base, rank=2)
            if outcome in ("inserted", "replaced"):
                outcomes.append((base["as_of"], outcome))
        else:
            current["pool_risk_snapshot_last_skip"] = reason

    day = _taipei_day_after_close(event.get("created_at"))
    if day is not None and pool_id and is_trading_day(day, holidays) and not any(
            r["as_of"] == day.isoformat() and r["pool_id"] == str(pool_id) and r.get("_rank", 0) >= 2 for r in rows):
        known = [o for o in current.get("quarter_equity_observations") or [] if o.get("status") == "known"]
        valuation = known[-1] if known else None
        account = current.get(ACCOUNT_KEY) if isinstance(current.get(ACCOUNT_KEY), Mapping) else None
        stored = current.get(POLICY_KEY)
        idle_meta = dict(metadata or {})
        if isinstance(stored, Mapping):
            # Heartbeats carry no policy. The limits in force are the last bound policy a valuation or
            # runtime_state_snapshot stamped; the row says so rather than presenting it as stamped at this close.
            idle_meta["risk_policy_facts"] = {
                **stored,
                "_basis": stored.get("_basis") or "last_valuation_event",
            }
        raw_lfc = current.get("ledger_fill_count")
        acc_fc = account.get("fill_count") if account is not None else None
        fill_counts = [
            c for c in (raw_lfc, acc_fc)
            if isinstance(c, int) and not isinstance(c, bool) and c >= 0
        ]
        effective_lfc = max(fill_counts) if fill_counts else None
        executed_fills = int(current.get("executed_trade_count") or 0)
        never_filled = (
            executed_fills == 0
            and (effective_lfc == 0 or (effective_lfc is None and valuation is not None))
        )
        if current.get("positions_status") == "unavailable":
            flat = False
        elif isinstance(current.get("positions"), list):
            flat = current.get("position_count") == 0 and len(current["positions"]) == 0
        else:
            flat = never_filled and not current.get("_positions_by_symbol")

        idle_valuation: Optional[dict[str, Any]] = None
        if flat:
            if never_filled:
                init_cash = _finite((account or {}).get("initial_cash"))
                if init_cash is None or init_cash <= 0:
                    init_cash = _finite((valuation or {}).get("initial_cash"))
                if init_cash is not None and init_cash > 0:
                    idle_valuation = {
                        **(valuation or {}),
                        "initial_cash": init_cash,
                        "cash": init_cash,
                        "equity": init_cash,
                    }
            else:
                val_as_of = _parse(valuation.get("as_of")) if valuation else None
                val_day = val_as_of.astimezone(ZoneInfo(_CLOSES[_DEFAULT_MARKET][0])).date() if val_as_of else None
                usable_val = valuation if (valuation is not None and (val_day is None or val_day <= day)) else None
                acc_day = (
                    date.fromisoformat(account["as_of"])
                    if (account and isinstance(account.get("as_of"), str) and account["as_of"])
                    else None
                )
                usable_acc = account if (account is not None and (acc_day is None or acc_day <= day)) else None
                if usable_val is not None or usable_acc is not None:
                    cand: dict[str, Any] = dict(usable_val or {})
                    if usable_acc is not None:
                        val_fc = usable_val.get("fill_count") if isinstance(usable_val, Mapping) else None
                        if usable_val is None or not isinstance(val_fc, int) or usable_acc["fill_count"] >= val_fc:
                            cand["initial_cash"] = usable_acc["initial_cash"]
                            cand["cash"] = usable_acc["cash"]
                            cand["equity"] = usable_acc["cash"]
                    if _finite(cand.get("equity")) is not None and _finite(cand.get("equity")) > 0:
                        idle_valuation = cand

        if flat and (idle_valuation is not None):
            base, _ = build_idle_base(day=day, valuation=idle_valuation, metadata=idle_meta, pool_id=pool_id,
                                      binding=binding, holidays=holidays, never_filled=never_filled)
            rank = 1
            if base is not None and never_filled:
                start = _parse(
                    current.get("_binding_effective_at")
                    or current.get("pnl_period_start_at")
                    or current.get("_binding_first_event_at")
                    or (metadata or {}).get("runtime_binding_effective_at")
                    or (metadata or {}).get("binding_effective_at")
                )
                cursor = start.astimezone(ZoneInfo(_CLOSES[_DEFAULT_MARKET][0])).date() if start else day
                cursor = max(cursor, day - timedelta(days=int(RETENTION_TRADING_DAYS * 2)))
                backfill_days: list[date] = []
                while cursor < day:
                    if is_trading_day(cursor, holidays):
                        backfill_days.append(cursor)
                    cursor += timedelta(days=1)
                for b_day in backfill_days[-(RETENTION_TRADING_DAYS - 1):]:
                    back, _ = build_idle_base(day=b_day, valuation=idle_valuation, metadata=idle_meta,
                                              pool_id=pool_id, binding=binding, holidays=holidays,
                                              never_filled=True)
                    if back is not None:
                        rows, outcome = upsert(rows, back, rank=1)
                        if outcome in ("inserted", "replaced"):
                            outcomes.append((back["as_of"], outcome))
        else:
            acc_day = (
                date.fromisoformat(account["as_of"])
                if (account and isinstance(account.get("as_of"), str) and account["as_of"])
                else None
            )
            has_later_valued = any(
                r["pool_id"] == str(pool_id) and r.get("equity") is not None and r["as_of"] >= day.isoformat()
                for r in rows
            )
            before_account_facts = (not never_filled and acc_day is not None and day < acc_day)
            if has_later_valued or before_account_facts:
                base = None
                rank = 0
            else:
                base = _base_row(day, {}, {}, pool_id, binding,
                                 "weekday_default_no_evidence" if not holidays else "governed_calendar_evidence",
                                 "owner_valuation_missing")
                rank = 0
        if base is not None:
            rows, outcome = upsert(rows, base, rank=rank)
            if outcome in ("inserted", "replaced"):
                outcomes.append((base["as_of"], outcome))

    if pool_id:
        last_valued = max(
            (r["as_of"] for r in rows if r["pool_id"] == str(pool_id) and r.get("equity") is not None),
            default=None,
        )
        if last_valued is not None:
            filtered = [
                r for r in rows
                if r["pool_id"] != str(pool_id) or r.get("equity") is not None or r["as_of"] > last_valued
            ]
            if len(filtered) != len(rows):
                rows = filtered
                if not outcomes:
                    outcomes.append((last_valued, "pruned_placeholder"))

    if not outcomes:
        return
    rows = enforce_retention(rows)
    pool_rows = [r for r in rows if r["pool_id"] == str(pool_id)]
    others = [r for r in rows if r["pool_id"] != str(pool_id)]
    finalize(pool_rows, holidays=holidays, budget_full=_budget_full())
    current[ROWS_KEY] = others + pool_rows
    current[HOLIDAYS_KEY] = sorted(d.isoformat() for d in holidays)[-60:]
    current[LAST_WRITE_KEY] = [{"day": d, "outcome": o} for d, o in outcomes[-5:]]
    current.pop("pool_risk_snapshot_error", None)


def served_view(summary: Mapping[str, Any]) -> dict[str, Any]:
    """The summary as served: the retained rows are replaced by the last OBSERVATION_ROWS only.

    Bounds the response (about 20 rows, never 400) and populates pool_observations.
    """
    rows = summary.get(ROWS_KEY)
    if rows is None:
        return summary
    pool = summary.get("capital_pool_id")
    rows = [r for r in rows if not pool or r.get("pool_id") == str(pool)]
    # A no-valuation placeholder is heartbeat-derived: once a later owner-valued close exists it is
    # superseded and not served, so the served view is identical after heartbeat retention and cold rebuild.
    last_valued = max((r["as_of"] for r in rows if r.get("equity") is not None), default=None)
    rows = [r for r in rows if r.get("equity") is not None or last_valued is None or r["as_of"] > last_valued]
    out = {k: v for k, v in summary.items() if k != ROWS_KEY}
    tail = list(rows)[-OBSERVATION_ROWS:]
    out["pool_risk_snapshots"] = [{k: v for k, v in r.items() if not k.startswith("_")} for r in tail]
    out["pool_observations"] = observations(rows)
    out["pool_risk_snapshot_retained_days"] = len(rows)
    return out
