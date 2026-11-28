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
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

try:
    from services.source_ingestion.hypothesis_card import (
        FIRST_RUN_RITUAL_MARKERS,
        has_first_run_ritual_marker,
    )
except ModuleNotFoundError:  # pragma: no cover - stdlib-only direct load if package __init__ lacks optional deps
    import importlib.util
    from pathlib import Path

    _HC_PATH = Path(__file__).resolve().parents[1] / "services" / "source_ingestion" / "hypothesis_card.py"
    _HC_SPEC = importlib.util.spec_from_file_location("_pantheon_hypothesis_card_stdlib", _HC_PATH)
    if _HC_SPEC is None or _HC_SPEC.loader is None:
        raise
    _hc_mod = importlib.util.module_from_spec(_HC_SPEC)
    sys.modules[_HC_SPEC.name] = _hc_mod
    _HC_SPEC.loader.exec_module(_hc_mod)
    FIRST_RUN_RITUAL_MARKERS = _hc_mod.FIRST_RUN_RITUAL_MARKERS
    has_first_run_ritual_marker = _hc_mod.has_first_run_ritual_marker

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
# Paid L2 calls per distinct news source today. One call per source and
# unchanged extraction input leaves room for a revised article or a retry; an
# article re-paid on every hourly collector refetch shows up as ~3 or more.
MAX_CALLS_PER_SOURCE = 1.5
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


def count_ritual_events_since_switch(
    events: Iterable[Any], switched_at: Any = None
) -> int:
    """Count L2 ledger events at or after ``switched_at`` that carry first-run ritual markers."""
    cutoff = switched_at if isinstance(switched_at, datetime) else _parse_time(switched_at)
    if cutoff is None:
        raise ValueError("release_switch_time_invalid")
    if events is None or isinstance(events, (str, bytes, Mapping)) or not isinstance(events, Iterable):
        raise ValueError("ledger_events_invalid")
    matched = 0
    for item in events:
        if not isinstance(item, Mapping):
            raise ValueError("ledger_event_not_mapping")
        ts = _parse_time(item.get("ts") or item.get("at") or item.get("since") or item.get("created_at"))
        if ts is None:
            raise ValueError("ledger_event_timestamp_invalid")
        if ts < cutoff:
            continue
        if str(item.get("outcome") or "").strip() == "openclaw_workspace_first_run_ritual":
            matched += 1
            continue
        text = " ".join(
            str(item.get(k) or "")
            for k in ("detail", "abstention_reason", "reason", "skip_reason", "claim", "title", "outcome")
            if item.get(k)
        )
        if has_first_run_ritual_marker(text):
            matched += 1
    return matched


def _count_ritual_timestamps_since_switch(timestamps: Any, cutoff: datetime) -> int:
    """Count anonymous ritual event UTC timestamps at or after ``cutoff``."""
    if timestamps is None or isinstance(timestamps, (str, bytes, Mapping)) or not isinstance(timestamps, Iterable):
        raise ValueError("ledger_events_invalid")
    matched = 0
    for item in timestamps:
        if not isinstance(item, str):
            raise ValueError("ledger_event_timestamp_invalid")
        ts = _parse_time(item)
        if ts is None:
            raise ValueError("ledger_event_timestamp_invalid")
        if ts >= cutoff:
            matched += 1
    return matched


def _ritual_events_since_switch(
    board: Dict[str, Any],
    *,
    ledger_events: Optional[Iterable[Any]] = None,
    switched_at: Any = None,
) -> Tuple[Optional[int], Optional[str], Optional[str]]:
    data = _dict(board.get("data"))
    summary = _dict(data.get("discovery_summary"))
    llm_calls = _dict(summary.get("llm_calls"))
    meta = _dict(board.get("meta"))
    surfaces = _dict(meta.get("surfaces"))
    if "alpha_factory_llm_calls" in surfaces:
        llm_surface_reason = _surface(board, "alpha_factory_llm_calls")
        if llm_surface_reason:
            return None, llm_surface_reason, None

    if llm_calls.get("status") not in (None, "ok"):
        return (
            None,
            f"unreadable:ritual_events_since_switch:{llm_calls.get('reason') or 'llm_calls_unavailable'}",
            None,
        )
    if llm_calls.get("ritual_events_status") not in (None, "ok"):
        return (
            None,
            f"unreadable:ritual_events_since_switch:{llm_calls.get('ritual_events_reason') or 'observation_unavailable'}",
            None,
        )

    board_switch_raw = None
    for candidate in (
        llm_calls.get("release_switched_at"),
        llm_calls.get("switched_at"),
        summary.get("release_switched_at"),
        summary.get("switched_at"),
        meta.get("release_switched_at"),
        meta.get("switched_at"),
        meta.get("deployed_at"),
    ):
        if candidate is not None:
            board_switch_raw = candidate
            break

    board_cutoff: Optional[datetime] = None
    if board_switch_raw is not None:
        if isinstance(board_switch_raw, str) and not board_switch_raw.strip():
            return None, "unreadable:ritual_events_since_switch:release_switch_time_missing", None
        board_cutoff = (
            board_switch_raw
            if isinstance(board_switch_raw, datetime)
            else _parse_time(board_switch_raw)
        )
        if board_cutoff is None:
            return None, "unreadable:ritual_events_since_switch:release_switch_time_invalid", None

    env_switch = os.environ.get("ACCEPTANCE_RELEASE_SWITCHED_AT")
    override_raw = (
        switched_at
        if switched_at is not None
        else (env_switch if env_switch is not None else None)
    )
    if override_raw is not None:
        if isinstance(override_raw, str) and not override_raw.strip():
            return None, "unreadable:ritual_events_since_switch:release_switch_time_missing", None
        cutoff = override_raw if isinstance(override_raw, datetime) else _parse_time(override_raw)
        if cutoff is None:
            return None, "unreadable:ritual_events_since_switch:release_switch_time_invalid", None
    else:
        cutoff = board_cutoff

    if cutoff is None:
        return None, "unreadable:ritual_events_since_switch:release_switch_time_missing", None

    cutoff_iso = _iso(cutoff)
    if llm_calls.get("ritual_events_truncated") is True or summary.get("ritual_events_truncated") is True:
        return None, "unreadable:ritual_events_since_switch:observation_truncated", cutoff_iso

    cutoff_overridden = board_cutoff is None or cutoff != board_cutoff

    explicit_counts: List[int] = []
    counts_map = _dict(data.get("counts"))
    if "ritual_events_since_switch" in counts_map:
        c_val, c_reason = _count(board, "ritual_events_since_switch")
        if c_reason or c_val is None or c_val < 0:
            return None, c_reason or "unreadable:ritual_events_since_switch:count_invalid", cutoff_iso
        explicit_counts.append(c_val)

    for container in (llm_calls, summary):
        if "ritual_events_since_switch" in container:
            val = _int(container.get("ritual_events_since_switch"))
            if val is None or val < 0:
                return None, "unreadable:ritual_events_since_switch:count_invalid", cutoff_iso
            explicit_counts.append(val)

    event_lists: List[Tuple[str, Any]] = []
    for label, candidate_list in (
        ("ledger_events", ledger_events),
        ("llm_calls.ritual_event_timestamps", llm_calls.get("ritual_event_timestamps")),
        ("llm_calls.ritual_events", llm_calls.get("ritual_events")),
        ("llm_calls.events_since_switch", llm_calls.get("events_since_switch")),
        ("llm_calls.events", llm_calls.get("events")),
        ("llm_calls.recent_events", llm_calls.get("recent_events")),
        ("discovery_summary.events_since_switch", summary.get("events_since_switch")),
        ("discovery_summary.events", summary.get("events")),
        ("discovery_summary.l2_events", summary.get("l2_events")),
    ):
        if candidate_list is not None:
            event_lists.append((label, candidate_list))

    covered_list_counts: List[int] = []
    positive_observed_counts: List[int] = []
    ritual_scan_truncated = False
    ritual_sample_truncated = False

    scan_hit_cap = llm_calls.get("ritual_events_scan_hit_cap")
    oldest_scanned_raw = llm_calls.get("ritual_events_oldest_scanned_at")
    oldest_scanned: Optional[datetime] = None
    if oldest_scanned_raw is not None:
        oldest_scanned = (
            oldest_scanned_raw
            if isinstance(oldest_scanned_raw, datetime)
            else _parse_time(oldest_scanned_raw)
        )
        if oldest_scanned is None:
            return None, "unreadable:ritual_events_since_switch:ledger_event_timestamp_invalid", cutoff_iso
    if scan_hit_cap is True and (oldest_scanned is None or oldest_scanned >= cutoff):
        ritual_scan_truncated = True

    for label, event_list in event_lists:
        try:
            if label == "llm_calls.ritual_event_timestamps":
                c = _count_ritual_timestamps_since_switch(event_list, cutoff)
            else:
                c = count_ritual_events_since_switch(event_list, cutoff)
        except ValueError as exc:
            return None, f"unreadable:ritual_events_since_switch:{exc}", cutoff_iso
        if c > 0:
            positive_observed_counts.append(c)

        if label in ("llm_calls.ritual_event_timestamps", "llm_calls.ritual_events"):
            if llm_calls.get("ritual_events_sample_truncated") is True:
                ritual_sample_truncated = True
                continue
            if ritual_scan_truncated:
                continue
            if scan_hit_cap is False and llm_calls.get("ritual_events_truncated") is False:
                covered_list_counts.append(c)
            elif (
                scan_hit_cap is True
                and oldest_scanned is not None
                and oldest_scanned < cutoff
                and llm_calls.get("ritual_events_truncated") is False
            ):
                covered_list_counts.append(c)
            elif (
                scan_hit_cap is None
                and board_cutoff is not None
                and cutoff >= board_cutoff
                and llm_calls.get("ritual_events_truncated") is False
            ):
                covered_list_counts.append(c)
            continue

        if label in ("llm_calls.events_since_switch", "discovery_summary.events_since_switch"):
            if board_cutoff is not None and cutoff >= board_cutoff:
                covered_list_counts.append(c)
            continue

        covered_list_counts.append(c)

    if not covered_list_counts:
        if ritual_scan_truncated or (ritual_sample_truncated and cutoff_overridden):
            return None, "unreadable:ritual_events_since_switch:observation_truncated", cutoff_iso
        if cutoff_overridden:
            fallback_positive = list(positive_observed_counts)
            if explicit_counts and max(explicit_counts) > 0 and board_cutoff is not None and cutoff <= board_cutoff:
                fallback_positive.append(max(explicit_counts))
            if fallback_positive:
                return max(fallback_positive), None, cutoff_iso
            return None, "unreadable:ritual_events_since_switch:observation_missing_for_switch_override", cutoff_iso
        if not explicit_counts and not positive_observed_counts:
            return None, "unreadable:ritual_events_since_switch:observation_missing", cutoff_iso
        return max([*explicit_counts, *positive_observed_counts]), None, cutoff_iso

    if cutoff_overridden:
        total = max([*covered_list_counts, *positive_observed_counts])
        if explicit_counts and board_cutoff is not None and cutoff <= board_cutoff:
            total = max(total, max(explicit_counts))
        return total, None, cutoff_iso

    total = max([*explicit_counts, *covered_list_counts, *positive_observed_counts])
    return total, None, cutoff_iso


def stage_hypothesis_cards(
    board: Dict[str, Any],
    now: datetime,
    *,
    ledger_events: Optional[Iterable[Any]] = None,
    switched_at: Any = None,
) -> Dict[str, Any]:
    name = "hypothesis_cards"
    reason = _surface(board, "alpha_factory_hypothesis_cards")
    if reason:
        return _failed(name, reason)
    calls, calls_reason = _count(board, "llm_calls_today")
    llm = _dict(_dict(_dict(board.get("data")).get("counts")).get("llm_calls_today"))
    cap = _int(llm.get("cap"))
    owner_calls = _dict(_dict(_dict(board.get("data")).get("discovery_summary")).get("llm_calls"))
    unsent = _int(owner_calls.get("unsent_calls"))
    closed = _closed_reasons(board)
    if calls_reason:
        return _failed(name, calls_reason, closed_last_24h_by_reason=closed)
    if cap is None or cap <= 0:
        return _failed(name, "llm_call_cap_missing", calls_today=calls, unsent_calls_today=unsent, cap=cap)
    if calls > cap:
        return _failed(name, "llm_calls_over_cap", calls_today=calls, unsent_calls_today=unsent, cap=cap)
    sources = _int(llm.get("distinct_sources"))
    per_source = round(calls / sources, 3) if sources else None
    # The distinct-source count comes from the ledger through the owner summary
    # and the BFF board. Until that read is plumbed the stage still reports the
    # calls it can read and says the ratio is not measurable (not a pass on it).
    if per_source is not None and per_source > MAX_CALLS_PER_SOURCE:
        return _failed(name, "llm_calls_per_source_over_limit", calls_today=calls, unsent_calls_today=unsent, cap=cap,
                       distinct_sources=sources, calls_per_source=per_source, limit=MAX_CALLS_PER_SOURCE)
    recent = _newest_cards(board, now)
    verified = [c for c in recent if c.get("claim_status") == "ok"]
    today, _ = _count(board, "hypothesis_cards_today")
    ritual_events, ritual_reason, switch_iso = _ritual_events_since_switch(
        board, ledger_events=ledger_events, switched_at=switched_at
    )
    values = {
        "verified_last_24h": len(verified),
        "drafts_last_24h": len(recent),
        "cards_today": today,
        "calls_today": calls,
        "unsent_calls_today": unsent,
        "cap": cap,
        "distinct_sources": sources,
        "calls_per_source": per_source,
        "calls_per_source_status": "measured" if per_source is not None else "distinct_sources_not_reported",
        "release_switched_at": switch_iso,
        "ritual_events_since_switch": ritual_events,
        "closed_last_24h_by_reason": closed,
    }
    if ritual_reason:
        return _failed(name, ritual_reason, **values)
    if ritual_events != 0:
        return _failed(name, f"ritual_marked_events_since_switch:{ritual_events}", **values)
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
              "demand_id": latest.get("demand_id"),
              "templates_attempted": _int(latest.get("templates_attempted")) or 0,
              "excluded_by_reason": _dict(latest.get("excluded_by_reason"))}
    if not values["templates_attempted"] and any(
        (_int(values["excluded_by_reason"].get(code)) or 0) > 0
        for code in ("template_checksum_mismatch", "template_tenant_mismatch")
    ):
        return _failed(name, "last_search_attempted_nothing", **values)
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
            if (_int(item.get("templates_attempted")) or 0) <= 0:
                add("last_search_attempted_nothing", False)
            elif code in GATE_REFUSALS:
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


def judge(
    board: Any,
    health: Any,
    now: datetime,
    *,
    ledger_events: Optional[Iterable[Any]] = None,
    switched_at: Any = None,
) -> Dict[str, Any]:
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
        if fn is stage_hypothesis_cards:
            stages.append(fn(board, now, ledger_events=ledger_events, switched_at=switched_at))
        else:
            stages.append(fn(board, now))
    if isinstance(health, ReadFailed):
        stages.append(_failed("distillation_dead_letters", f"unreadable:loop_health:{health}"))
    elif not isinstance(health, dict):
        stages.append(_failed("distillation_dead_letters", "loop_health_payload_invalid"))
    else:
        stages.append(stage_distillation_dead_letters(health, now))
    failed = [s["stage"] for s in stages if s["status"] == "failed"]
    gate = next(s for s in stages if s["stage"] == "gate_attempts")
    cards_stage = next(s for s in stages if s["stage"] == "hypothesis_cards")
    passes = _dict(gate.get("values")).get("passes")
    ritual_events = _dict(cards_stage.get("values")).get("ritual_events_since_switch")
    return {
        "owner": OWNER,
        "judged_at": _iso(now),
        "verdict": "failed" if failed else "passed",
        "failed_stages": failed,
        "ritual_events_since_switch": ritual_events if isinstance(ritual_events, int) else None,
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
