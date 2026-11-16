"""Internal service-token and role-scoped Agora platform data source read router (§12.4).

Exposes Agora decision locks (C4 / A4), settled shadow arms, decision journal
entries, and servant insights (AgoraInsight) incrementally by cursor through
the Agora owner stores without cross-owner database reads.

Standing rules enforced:
- Only authenticated service principals (on /internal/agora/platform-source/*)
  or authorized operators/owners (on /bff/agora/platform-source/records) can read.
- Trader identity, account_id, broker_ids, and raw_text are preserved in full
  per operator decision (2026-11-16).
- Operators with read_all authority see tenant records; traders see only their
  own records.
- Paper proxy is never labelled as an actual fill.
- Records are facts only; nothing here executes orders, mutates capital, or
  promotes strategies/rules/lessons without validation gates.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hmac
import json
import logging
import os
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from fastapi import APIRouter, Header, HTTPException, Query

from services.agora.store import AgoraEvidenceStore, AgoraStore, DictRecord
from services.agora.write_authority import is_authorized
from services.foundation.postgres_json_store import PostgresJsonOwnerStore

from .identity.scope import AgoraScopeResolutionError, resolve_agora_user_scope

log = logging.getLogger(__name__)

PLATFORM_SOURCE_CAPABILITY = "agora.platform_source.v1"
PLATFORM_SOURCE_PROVENANCE = "agora"
LOCAL_PLATFORM_SOURCE_SERVICE_TOKEN = "pantheon-local-agora-platform-source-service-token"
EXAMPLE_PLATFORM_SOURCE_SERVICE_TOKEN = "replace-me-agora-platform-source-service-token"
DEFAULT_ALLOWED_SERVICE_ACTORS = frozenset(
    {
        "source-ingest",
        "source-ingestion",
        "source-ingestion-controller",
        "evaluation",
        "attribution",
        "consultation-svc",
        "consultation",
        "operator-bff",
    }
)
ACTUAL_FILL_OBSERVATION_KINDS = frozenset({"verified_broker_fill", "imported_broker_fill"})
RECORD_KINDS = ("decision_lock", "settled_arm", "decision_journal", "servant_insight")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _normalize_rfc3339(value: Any, fallback: Optional[str] = None) -> str:
    text = _clean(value) or _clean(fallback) or _utc_now()
    if text.endswith("Z"):
        return text
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt.isoformat().replace("+00:00", "Z")
    except ValueError:
        return text


def _string_list(value: Any) -> List[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        s = value.strip()
        return [s] if s else []
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray, Mapping)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def format_cursor(timestamp: str, record_kind: str, record_id: str) -> str:
    return f"{timestamp}|{record_kind}|{record_id}"


def parse_cursor(cursor: Optional[str]) -> Optional[Tuple[str, str, str]]:
    raw = _clean(cursor)
    if not raw:
        return None
    parts = raw.split("|")
    if len(parts) == 3:
        ts, kind, rid = parts[0].strip(), parts[1].strip(), parts[2].strip()
        if not ts or not kind or not rid:
            raise ValueError(f"Malformed cursor: {cursor!r}")
        return (_normalize_rfc3339(ts), kind, rid)
    if len(parts) == 2:
        ts, rid = parts[0].strip(), parts[1].strip()
        if not ts or not rid:
            raise ValueError(f"Malformed cursor: {cursor!r}")
        return (_normalize_rfc3339(ts), "\uffff", rid)
    if len(parts) == 1:
        # Plain ISO-8601 timestamp watermark
        try:
            datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"Malformed cursor timestamp: {cursor!r}") from exc
        return (_normalize_rfc3339(raw), "\uffff", "\uffff")
    raise ValueError(f"Malformed cursor: {cursor!r}")


def _normalize_decision_lock(raw: Mapping[str, Any], default_tenant: str) -> Dict[str, Any]:
    """Normalize a decision lock to expose full C4 and A4 fields without dropping owner keys."""
    lock = dict(raw)
    decision_id = (
        _clean(lock.get("decision_id"))
        or _clean(lock.get("human_decision_id"))
        or _clean(lock.get("decision_record_id"))
        or _clean(lock.get("decision_event_id"))
    )
    if not decision_id:
        raise ValueError("Decision lock is missing decision_id / human_decision_id")

    trader_identity = lock.get("trader_identity") if isinstance(lock.get("trader_identity"), Mapping) else {}
    tenant_id = (
        _clean(lock.get("tenant_id"))
        or _clean(trader_identity.get("tenant_id"))
        or default_tenant
    )
    agora_user_id = (
        _clean(lock.get("agora_user_id"))
        or _clean(lock.get("user_id"))
        or _clean(trader_identity.get("agora_user_id"))
        or _clean(trader_identity.get("user_id"))
    )
    account_id = _clean(lock.get("account_id")) or None
    broker_ids = _string_list(lock.get("broker_ids"))
    decision_time = _normalize_rfc3339(
        lock.get("decision_time") or lock.get("created_at")
    )
    created_at = _normalize_rfc3339(
        lock.get("created_at") or decision_time,
        fallback=decision_time,
    )
    data_cutoff = (
        _clean(lock.get("data_cutoff"))
        or _clean(lock.get("available_data_cutoff"))
        or None
    )
    raw_text = lock.get("raw_text")
    if raw_text is None:
        raw_text = lock.get("raw_rationale")
    raw_text_str = str(raw_text) if raw_text is not None else ""

    raw_arms_source = lock.get("arms") if isinstance(lock.get("arms"), Mapping) else lock.get("comparison_arms")
    raw_arms = dict(raw_arms_source or {}) if isinstance(raw_arms_source, Mapping) else {}
    obs_kind = (
        _clean((raw_arms.get("human_actual") or {}).get("execution_observation_kind"))
        if isinstance(raw_arms.get("human_actual"), Mapping)
        else ""
    ) or _clean(lock.get("execution_observation_kind")) or "pantheon_paper_proxy"

    # Never label paper proxy as an actual fill (A4 §1).
    actual_fill_claimed = obs_kind in ACTUAL_FILL_OBSERVATION_KINDS

    human_actual_raw = raw_arms.get("human_actual") if isinstance(raw_arms.get("human_actual"), Mapping) else {}
    human_actual = {
        "action": (
            _clean(human_actual_raw.get("action"))
            or _clean(lock.get("decision"))
            or _clean(lock.get("trading_room_verdict"))
            or "hold"
        ),
        "quantity": (
            human_actual_raw.get("quantity")
            if "quantity" in human_actual_raw
            else lock.get("requested_quantity")
        ),
        "price": (
            human_actual_raw.get("price")
            if "price" in human_actual_raw
            else lock.get("requested_price_constraint")
        ),
        "execution_observation_kind": obs_kind,
        "actual_fill_claimed": False if obs_kind == "pantheon_paper_proxy" else actual_fill_claimed,
        **{
            k: v
            for k, v in human_actual_raw.items()
            if k not in {"action", "quantity", "price", "execution_observation_kind", "actual_fill_claimed"}
        },
    }

    unavailable_arms: List[Dict[str, str]] = []
    raw_unavail = lock.get("unavailable_arms")
    if isinstance(raw_unavail, Mapping):
        for arm_key, reason_val in raw_unavail.items():
            if _clean(arm_key):
                unavailable_arms.append(
                    {
                        "arm": _clean(arm_key),
                        "reason": _clean(reason_val) or "unavailable",
                    }
                )
    elif isinstance(raw_unavail, Sequence) and not isinstance(raw_unavail, (str, bytes)):
        for item in raw_unavail or []:
            if isinstance(item, Mapping) and _clean(item.get("arm")):
                unavailable_arms.append(
                    {
                        "arm": _clean(item.get("arm")),
                        "reason": _clean(item.get("reason") or item.get("unavailable_reason") or "unavailable"),
                    }
                )

    def _record_unavailable(arm_name: str, arm_val: Any) -> None:
        if isinstance(arm_val, Mapping) and arm_val.get("available") is False:
            reason = _clean(arm_val.get("unavailable_reason") or arm_val.get("reason") or "unavailable")
            if not any(u["arm"] == arm_name for u in unavailable_arms):
                unavailable_arms.append({"arm": arm_name, "reason": reason})

    private_servant_raw = raw_arms.get("private_servant")
    _record_unavailable("private_servant", private_servant_raw)
    if isinstance(private_servant_raw, Mapping) and private_servant_raw.get("available") is not False:
        private_servant: Optional[Dict[str, Any]] = {
            "suggestion": (
                private_servant_raw.get("suggestion")
                or private_servant_raw.get("suggested_action")
                or private_servant_raw.get("action")
            ),
            "persona_id": (
                private_servant_raw.get("persona_id")
                or private_servant_raw.get("servant_persona_id")
            ),
            **{k: v for k, v in private_servant_raw.items() if k not in {"suggestion", "persona_id"}},
        }
    else:
        private_servant = dict(private_servant_raw) if isinstance(private_servant_raw, Mapping) else None

    base_strategy_raw = raw_arms.get("base_strategy")
    _record_unavailable("base_strategy", base_strategy_raw)
    if isinstance(base_strategy_raw, Mapping) and base_strategy_raw.get("available") is not False:
        base_strategy: Optional[Dict[str, Any]] = {
            "suggestion": (
                base_strategy_raw.get("suggestion")
                or base_strategy_raw.get("suggested_action")
                or base_strategy_raw.get("action")
            ),
            "strategy_ref": (
                base_strategy_raw.get("strategy_ref")
                or base_strategy_raw.get("strategy_version_id")
                or base_strategy_raw.get("strategy_id")
                or lock.get("strategy_id")
            ),
            **{k: v for k, v in base_strategy_raw.items() if k not in {"suggestion", "strategy_ref"}},
        }
    else:
        base_strategy = dict(base_strategy_raw) if isinstance(base_strategy_raw, Mapping) else None

    committee_raw = (
        raw_arms.get("committee_variant")
        if "committee_variant" in raw_arms
        else raw_arms.get("institutional_view")
    )
    _record_unavailable("committee_variant", committee_raw)
    if isinstance(committee_raw, Mapping) and committee_raw.get("available") is not False:
        committee_variant: Optional[Dict[str, Any]] = {
            "suggestion": (
                committee_raw.get("suggestion")
                or committee_raw.get("suggested_action")
                or committee_raw.get("verdict")
            ),
            "meeting_id": (
                committee_raw.get("meeting_id")
                or committee_raw.get("session_id")
            ),
            **{k: v for k, v in committee_raw.items() if k not in {"suggestion", "meeting_id"}},
        }
    else:
        committee_variant = None

    normalized_arms: Dict[str, Any] = {
        **raw_arms,
        "human_actual": human_actual,
        "private_servant": private_servant,
        "base_strategy": base_strategy,
        "committee_variant": committee_variant,
    }

    cursor = format_cursor(decision_time, "decision_lock", decision_id)
    return {
        **lock,
        "record_kind": "decision_lock",
        "record_type": "AgoraDecisionLock",
        "record_id": decision_id,
        "decision_id": decision_id,
        "human_decision_id": _clean(lock.get("human_decision_id")) or decision_id,
        "tenant_id": tenant_id,
        "agora_user_id": agora_user_id,
        "user_id": agora_user_id,
        "trader_identity": {
            **dict(trader_identity),
            "tenant_id": tenant_id,
            "user_id": agora_user_id,
            "agora_user_id": agora_user_id,
        },
        "account_id": account_id,
        "broker_ids": broker_ids,
        "decision_time": decision_time,
        "data_cutoff": data_cutoff,
        "available_data_cutoff": data_cutoff,
        "raw_text": raw_text_str,
        "raw_rationale": raw_text_str,
        "execution_observation_kind": obs_kind,
        "actual_fill_claimed": False if obs_kind == "pantheon_paper_proxy" else actual_fill_claimed,
        "arms": normalized_arms,
        "unavailable_arms": unavailable_arms,
        "immutable": True,
        "created_at": created_at,
        "event_time": decision_time,
        "available_time": created_at if created_at >= decision_time else decision_time,
        "provenance": PLATFORM_SOURCE_PROVENANCE,
        "cursor": cursor,
    }


def _extract_settled_arms_from_lock(normalized_lock: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Extract settled arm records associated with a decision lock."""
    results: List[Dict[str, Any]] = []
    decision_id = str(normalized_lock["decision_id"])
    tenant_id = str(normalized_lock["tenant_id"])
    agora_user_id = str(normalized_lock["agora_user_id"])
    decision_time = str(normalized_lock["decision_time"])
    data_cutoff = normalized_lock.get("data_cutoff")

    explicit_settled = normalized_lock.get("settled_arms")
    raw_items: List[Mapping[str, Any]] = []
    if isinstance(explicit_settled, Sequence) and not isinstance(explicit_settled, (str, bytes)):
        for item in explicit_settled:
            if isinstance(item, Mapping):
                raw_items.append(item)
    elif isinstance(explicit_settled, Mapping):
        for arm_name, item in explicit_settled.items():
            if isinstance(item, Mapping):
                raw_items.append({"arm": str(arm_name), **dict(item)})

    # Also inspect individual arms or lock-level outcome if settled
    arms = normalized_lock.get("arms") if isinstance(normalized_lock.get("arms"), Mapping) else {}
    for arm_name in ("human_actual", "private_servant", "base_strategy", "committee_variant"):
        arm_val = arms.get(arm_name)
        if isinstance(arm_val, Mapping):
            outcome = arm_val.get("outcome")
            if (
                (isinstance(outcome, Mapping) and outcome.get("available") is not False and outcome)
                or arm_val.get("settled_at")
                or arm_val.get("return_pct") is not None
                or arm_val.get("realized_pnl") is not None
            ):
                if not any(_clean(r.get("arm")) == arm_name for r in raw_items):
                    raw_items.append(
                        {
                            "arm": arm_name,
                            "settlement_source": (
                                arm_val.get("settlement_source")
                                or arm_val.get("execution_observation_kind")
                                or ("pantheon_paper_proxy" if arm_name != "human_actual" else normalized_lock.get("execution_observation_kind"))
                            ),
                            "outcome": outcome if isinstance(outcome, Mapping) else {
                                k: arm_val[k]
                                for k in ("return_pct", "realized_pnl", "unrealized_pnl", "slippage_bps", "max_drawdown_pct")
                                if k in arm_val
                            },
                            "settled_at": arm_val.get("settled_at") or normalized_lock.get("settled_at") or normalized_lock["created_at"],
                            "unavailable_reason": arm_val.get("unavailable_reason"),
                        }
                    )

    for item in raw_items:
        arm_name = _clean(item.get("arm")) or "human_actual"
        record_id = _clean(item.get("settled_arm_id") or item.get("record_id")) or f"{decision_id}:{arm_name}"
        settlement_source = _clean(
            item.get("settlement_source")
            or item.get("execution_observation_kind")
            or (normalized_lock.get("execution_observation_kind") if arm_name == "human_actual" else "pantheon_paper_proxy")
        )
        obs_kind = _clean(item.get("execution_observation_kind")) or settlement_source
        settled_at = _normalize_rfc3339(
            item.get("settled_at") or item.get("created_at") or normalized_lock["created_at"],
            fallback=decision_time,
        )
        if settled_at < decision_time:
            settled_at = decision_time
        cursor = format_cursor(settled_at, "settled_arm", record_id)
        results.append(
            {
                **dict(item),
                "record_kind": "settled_arm",
                "record_type": "AgoraSettledArm",
                "record_id": record_id,
                "settled_arm_id": record_id,
                "decision_id": decision_id,
                "arm": arm_name,
                "tenant_id": tenant_id,
                "agora_user_id": agora_user_id,
                "user_id": agora_user_id,
                "trader_identity": dict(normalized_lock["trader_identity"]),
                "account_id": normalized_lock.get("account_id"),
                "broker_ids": list(normalized_lock.get("broker_ids") or []),
                "raw_text": normalized_lock.get("raw_text", ""),
                "decision_time": decision_time,
                "data_cutoff": data_cutoff,
                "settlement_source": settlement_source,
                "execution_observation_kind": obs_kind,
                "actual_fill_claimed": (
                    False
                    if obs_kind == "pantheon_paper_proxy" or settlement_source == "pantheon_paper_proxy"
                    else (obs_kind in ACTUAL_FILL_OBSERVATION_KINDS or settlement_source in ACTUAL_FILL_OBSERVATION_KINDS)
                ),
                "outcome": dict(item.get("outcome") or {}) if isinstance(item.get("outcome"), Mapping) else item.get("outcome"),
                "unavailable_reason": item.get("unavailable_reason"),
                "settled_at": settled_at,
                "created_at": settled_at,
                "event_time": decision_time,
                "available_time": settled_at,
                "provenance": PLATFORM_SOURCE_PROVENANCE,
                "cursor": cursor,
            }
        )
    return results


def _normalize_journal_entry(raw: Mapping[str, Any], default_tenant: str) -> Dict[str, Any]:
    entry = dict(raw)
    entry_id = _clean(entry.get("entry_id") or entry.get("id") or entry.get("entryId"))
    if not entry_id:
        raise ValueError("Decision journal entry is missing id / entry_id")
    tenant_id = _clean(entry.get("tenant_id") or entry.get("tenantId")) or default_tenant
    agora_user_id = (
        _clean(entry.get("agora_user_id"))
        or _clean(entry.get("user_id"))
        or _clean(entry.get("userId"))
        or _clean(entry.get("createdBy"))
        or _clean(entry.get("actor_id"))
    )
    created_at = _normalize_rfc3339(
        entry.get("createdAt") or entry.get("created_at") or entry.get("event_time")
    )
    updated_at = _normalize_rfc3339(
        entry.get("updatedAt") or entry.get("updated_at") or entry.get("available_time") or created_at,
        fallback=created_at,
    )
    if updated_at < created_at:
        updated_at = created_at
    raw_text = (
        entry.get("raw_text")
        if entry.get("raw_text") is not None
        else (entry.get("body") if entry.get("body") is not None else entry.get("content", ""))
    )
    raw_text_str = str(raw_text or "")
    account_id = _clean(entry.get("account_id") or entry.get("accountId")) or None
    broker_ids = _string_list(entry.get("broker_ids") or entry.get("brokerIds"))
    cursor = format_cursor(updated_at, "decision_journal", entry_id)
    return {
        **entry,
        "record_kind": "decision_journal",
        "record_type": "AgoraDecisionJournalEntry",
        "record_id": entry_id,
        "entry_id": entry_id,
        "tenant_id": tenant_id,
        "agora_user_id": agora_user_id,
        "user_id": agora_user_id,
        "trader_identity": {
            "tenant_id": tenant_id,
            "user_id": agora_user_id,
            "agora_user_id": agora_user_id,
        },
        "account_id": account_id,
        "broker_ids": broker_ids,
        "title": _clean(entry.get("title")) or f"Decision Journal {entry_id}",
        "body": raw_text_str,
        "raw_text": raw_text_str,
        "tags": _string_list(entry.get("tags")),
        "linked_strategy_ids": _string_list(entry.get("linkedStrategyIds") or entry.get("linked_strategy_ids")),
        "linked_persona_ids": _string_list(entry.get("linkedPersonaIds") or entry.get("linked_persona_ids")),
        "context_refs": _string_list(entry.get("contextRefs") or entry.get("context_refs")),
        "created_at": created_at,
        "updated_at": updated_at,
        "event_time": created_at,
        "available_time": updated_at,
        "provenance": PLATFORM_SOURCE_PROVENANCE,
        "cursor": cursor,
    }


def _normalize_servant_insight(raw: Mapping[str, Any], default_tenant: str) -> Dict[str, Any]:
    insight = dict(raw)
    insight_id = _clean(
        insight.get("insight_id")
        or insight.get("insightId")
        or insight.get("evidence_id")
        or insight.get("id")
    )
    if not insight_id:
        raise ValueError("Servant insight is missing insight_id / id")
    content_obj = insight.get("content") if isinstance(insight.get("content"), Mapping) else {}
    tenant_id = (
        _clean(insight.get("tenant_id"))
        or _clean(content_obj.get("tenant_id"))
        or default_tenant
    )
    agora_user_id = (
        _clean(insight.get("agora_user_id"))
        or _clean(insight.get("user_id"))
        or _clean(insight.get("author"))
        or _clean(content_obj.get("agora_user_id"))
        or _clean(content_obj.get("user_id"))
        or _clean(((content_obj.get("agora_human_evidence") or {}) if isinstance(content_obj.get("agora_human_evidence"), Mapping) else {}).get("actor_id"))
    )
    persona_id = (
        _clean(insight.get("persona_id"))
        or _clean(content_obj.get("persona_id"))
        or None
    )
    account_id = (
        _clean(insight.get("account_id"))
        or _clean(content_obj.get("account_id"))
        or None
    )
    broker_ids = _string_list(insight.get("broker_ids") or content_obj.get("broker_ids"))
    raw_text_val = insight.get("raw_text")
    if raw_text_val is None:
        if isinstance(insight.get("content"), str):
            raw_text_val = insight.get("content")
        elif content_obj:
            raw_text_val = (
                content_obj.get("raw_text")
                or content_obj.get("text")
                or content_obj.get("summary")
                or content_obj.get("insight")
                or json.dumps(
                    {k: v for k, v in content_obj.items() if k != "agora_human_evidence"},
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
    raw_text_str = str(raw_text_val or "")
    title = (
        _clean(insight.get("title"))
        or _clean(content_obj.get("title"))
        or f"AgoraInsight {insight_id}"
    )
    captured_at = _normalize_rfc3339(
        insight.get("captured_at") or insight.get("createdAt") or insight.get("created_at")
    )
    created_at = _normalize_rfc3339(
        insight.get("createdAt") or insight.get("created_at") or captured_at,
        fallback=captured_at,
    )
    if created_at < captured_at:
        created_at = captured_at
    cursor = format_cursor(created_at, "servant_insight", insight_id)
    return {
        **insight,
        "record_kind": "servant_insight",
        "record_type": "AgoraInsight",
        "record_id": insight_id,
        "insight_id": insight_id,
        "tenant_id": tenant_id,
        "agora_user_id": agora_user_id,
        "user_id": agora_user_id,
        "trader_identity": {
            "tenant_id": tenant_id,
            "user_id": agora_user_id,
            "agora_user_id": agora_user_id,
        },
        "persona_id": persona_id,
        "account_id": account_id,
        "broker_ids": broker_ids,
        "title": title,
        "content": insight.get("content", raw_text_str),
        "raw_text": raw_text_str,
        "category": _clean(insight.get("category") or content_obj.get("category")) or "market",
        "confidence": float(
            insight.get("confidence")
            if insight.get("confidence") is not None
            else (content_obj.get("confidence") if content_obj.get("confidence") is not None else 0.8)
        ),
        "impact": _clean(insight.get("impact") or content_obj.get("impact")) or "medium",
        "context_refs": _string_list(
            insight.get("contextRefs")
            or insight.get("context_refs")
            or insight.get("source_refs")
            or content_obj.get("context_refs")
        ),
        "captured_at": captured_at,
        "created_at": created_at,
        "event_time": captured_at,
        "available_time": created_at,
        "provenance": PLATFORM_SOURCE_PROVENANCE,
        "cursor": cursor,
    }


def is_record_visible_to_caller(
    record: Mapping[str, Any],
    *,
    tenant_id: str,
    user_id: str,
    roles: Sequence[str] | str,
) -> bool:
    """Enforce operator and owner role access on Agora platform source records.

    Operators / governance / admin roles with read_all authority can read all
    records in their tenant. Traders / owner roles can read only their own
    records in their tenant.
    """
    rec_tenant = _clean(record.get("tenant_id"))
    if not tenant_id or rec_tenant != _clean(tenant_id):
        return False
    role_set = (
        {r.strip().lower() for r in roles.split(",") if r.strip()}
        if isinstance(roles, str)
        else {str(r).strip().lower() for r in (roles or ()) if str(r).strip()}
    )
    if (
        role_set & {"operator", "admin", "governance", "system", "service"}
        or is_authorized("AgoraInsight", "read", role_set) and ("operator" in role_set or "admin" in role_set)
    ):
        return True
    rec_user = _clean(record.get("agora_user_id") or record.get("user_id"))
    return bool(user_id and rec_user and rec_user == _clean(user_id))


class AgoraPlatformSourceOwner:
    """Owner-side reader assembling C4 decision locks, settled arms, journals, and AgoraInsights."""

    def __init__(
        self,
        *,
        agora_owner: Optional[Any] = None,
        store: Optional[Any] = None,
        dataset_store: Optional[Any] = None,
        journal_write_owner: Optional[Any] = None,
        data_dir: Optional[Any] = None,
        backend: Optional[str] = None,
    ) -> None:
        self._agora_owner = agora_owner if agora_owner is not None else store
        self._dataset_store = dataset_store
        self._journal_write_owner = journal_write_owner
        self._data_dir = data_dir
        self._backend = backend
        self._decision_locks_fallback_store: Optional[PostgresJsonOwnerStore] = None
        self._recorded_decision_locks: List[Dict[str, Any]] = []
        self._recorded_settled_arms: List[Dict[str, Any]] = []
        self._recorded_journal_entries: List[Dict[str, Any]] = []
        self._recorded_servant_insights: List[Dict[str, Any]] = []

    @property
    def store(self) -> Optional[Any]:
        return self._resolve_agora_store()

    def record_decision_lock(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        row = dict(payload)
        self._recorded_decision_locks.append(row)
        return _normalize_decision_lock(row, _clean(row.get("tenant_id")) or "default")

    def record_settled_arm(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        row = dict(payload)
        if "arm" not in row and "arm_name" in row:
            row["arm"] = row["arm_name"]
        self._recorded_settled_arms.append(row)
        return row

    def record_decision_journal_entry(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        row = dict(payload)
        self._recorded_journal_entries.append(row)
        return _normalize_journal_entry(row, _clean(row.get("tenant_id")) or "default")

    def record_servant_insight(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        row = dict(payload)
        self._recorded_servant_insights.append(row)
        return _normalize_servant_insight(row, _clean(row.get("tenant_id")) or "default")

    def _resolve_agora_store(self) -> Optional[Any]:
        owner = self._agora_owner() if callable(self._agora_owner) else self._agora_owner
        if owner is not None:
            return getattr(owner, "store", owner)
        dsn = (os.getenv("AGORA_STORE_DSN") or os.getenv("DATABASE_URL") or "").strip()
        if not dsn:
            return None
        schema = (os.getenv("AGORA_STORE_SCHEMA") or "agora").strip() or "agora"
        self._agora_owner = AgoraStore(dsn=dsn, schema=schema, bootstrap=True)
        return self._agora_owner

    def _resolve_journal_owner(self) -> Optional[Any]:
        owner = (
            self._journal_write_owner()
            if callable(self._journal_write_owner)
            else self._journal_write_owner
        )
        return owner

    def list_raw_decision_locks(self, *, tenant_id: str) -> List[Mapping[str, Any]]:
        owner = self._agora_owner() if callable(self._agora_owner) else self._agora_owner
        store = self._resolve_agora_store()
        rows: List[Mapping[str, Any]] = list(self._recorded_decision_locks)
        if owner is not None and hasattr(owner, "list_decision_locks"):
            try:
                rows.extend(
                    owner.list_decision_locks(
                        tenant_id=tenant_id,
                        user_id="",
                        actor_roles=("operator",),
                    )
                )
            except TypeError:
                rows.extend(owner.list_decision_locks())
        elif store is not None and hasattr(store, "list_decision_locks"):
            rows.extend(store.list_decision_locks())
        elif store is not None and hasattr(store, "dsn") and hasattr(store, "schema"):
            if self._decision_locks_fallback_store is None:
                self._decision_locks_fallback_store = PostgresJsonOwnerStore(
                    dsn=store.dsn,
                    table=f"{store.schema}.decision_locks",
                    owner_service="agora-svc",
                    bootstrap=True,
                )
            rows.extend(DictRecord(r) for r in self._decision_locks_fallback_store.list_all())
        return [
            r
            for r in rows
            if isinstance(r, Mapping)
            and _clean(r.get("tenant_id") or default_tenant_for_row(r, tenant_id)) == tenant_id
        ]

    def list_raw_settled_arms(self, *, tenant_id: str) -> List[Mapping[str, Any]]:
        owner = self._agora_owner() if callable(self._agora_owner) else self._agora_owner
        store = self._resolve_agora_store()
        rows: List[Mapping[str, Any]] = list(self._recorded_settled_arms)
        for target in (owner, store):
            if target is not None and hasattr(target, "list_settled_arms"):
                try:
                    rows.extend(target.list_settled_arms(tenant_id=tenant_id))
                except TypeError:
                    rows.extend(target.list_settled_arms())
                break
        return [
            r
            for r in rows
            if isinstance(r, Mapping)
            and _clean(r.get("tenant_id") or tenant_id) == tenant_id
        ]

    def list_raw_journal_entries(self, *, tenant_id: str) -> List[Mapping[str, Any]]:
        rows: List[Mapping[str, Any]] = [
            r
            for r in self._recorded_journal_entries
            if isinstance(r, Mapping) and _clean(r.get("tenant_id") or r.get("tenantId")) == tenant_id
        ]
        journal_owner = self._resolve_journal_owner()
        if journal_owner is None:
            return rows
        if hasattr(journal_owner, "stores") and getattr(journal_owner.stores, "entries", None) is not None:
            all_rows = journal_owner.stores.entries.list_all()
            rows.extend(
                r
                for r in all_rows
                if isinstance(r, Mapping)
                and r.get("_creation_outbox") is None
                and _clean(r.get("tenant_id") or r.get("tenantId")) == tenant_id
            )
            return rows
        if hasattr(journal_owner, "list_decision_journal_entries"):
            rows.extend(
                r for r in journal_owner.list_decision_journal_entries(tenant_id=tenant_id) if isinstance(r, Mapping)
            )
        return rows

    def list_raw_servant_insights(self, *, tenant_id: str) -> List[Mapping[str, Any]]:
        rows_by_id: Dict[str, Mapping[str, Any]] = {}
        for item in self._recorded_servant_insights:
            if not isinstance(item, Mapping):
                continue
            if _clean(item.get("tenant_id") or tenant_id) == tenant_id:
                iid = _clean(item.get("insight_id") or item.get("insightId") or item.get("id"))
                if iid:
                    rows_by_id[iid] = item
        store = self._resolve_agora_store()
        if store is not None and hasattr(store, "list_insights"):
            for item in store.list_insights():
                if not isinstance(item, Mapping):
                    continue
                item_tenant = _clean(
                    item.get("tenant_id")
                    or ((item.get("payload") or {}) if isinstance(item.get("payload"), Mapping) else {}).get("tenant_id")
                    or ((item.get("content") or {}) if isinstance(item.get("content"), Mapping) else {}).get("tenant_id")
                    or tenant_id
                )
                if item_tenant == tenant_id:
                    iid = _clean(item.get("insight_id") or item.get("insightId") or item.get("id"))
                    if iid:
                        rows_by_id[iid] = item

        ds = self._dataset_store() if callable(self._dataset_store) else self._dataset_store
        if ds is not None and hasattr(ds, "_list_inbox"):
            try:
                for status in ("pending", "processing", "processed"):
                    for entry in ds._list_inbox(tenant_id=tenant_id, status=status):
                        if not isinstance(entry, Mapping):
                            continue
                        if _clean(entry.get("interaction_kind")) != "insight":
                            continue
                        if not entry.get("consent_granted", True):
                            continue
                        iid = _clean(entry.get("evidence_id") or entry.get("id"))
                        if iid and iid not in rows_by_id:
                            rows_by_id[iid] = entry
            except Exception:
                log.debug("Dataset inbox insight scan skipped", exc_info=True)
        return list(rows_by_id.values())

    def read_incremental(
        self,
        *,
        tenant_id: str,
        cursor: Optional[str] = None,
        limit: int = 100,
        kind: Optional[str] = None,
        viewer_user_id: Optional[str] = None,
        viewer_roles: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        clean_tenant = _clean(tenant_id)
        if not clean_tenant:
            raise ValueError("tenant_id is required")
        parsed_cursor = parse_cursor(cursor)
        requested_kind = _clean(kind).lower() or None
        if requested_kind and requested_kind not in RECORD_KINDS:
            raise ValueError(f"Unsupported record kind: {requested_kind!r}")

        records: List[Dict[str, Any]] = []
        seen_settled_ids: set[str] = set()

        if requested_kind in (None, "decision_lock", "settled_arm"):
            for raw_lock in self.list_raw_decision_locks(tenant_id=clean_tenant):
                norm_lock = _normalize_decision_lock(raw_lock, clean_tenant)
                if requested_kind in (None, "decision_lock"):
                    records.append(norm_lock)
                if requested_kind in (None, "settled_arm"):
                    for settled in _extract_settled_arms_from_lock(norm_lock):
                        seen_settled_ids.add(settled["record_id"])
                        records.append(settled)

        if requested_kind in (None, "settled_arm"):
            for raw_settled in self.list_raw_settled_arms(tenant_id=clean_tenant):
                decision_id = _clean(raw_settled.get("decision_id") or raw_settled.get("human_decision_id")) or "unknown"
                arm_name = _clean(raw_settled.get("arm")) or "human_actual"
                rid = _clean(raw_settled.get("settled_arm_id") or raw_settled.get("record_id")) or f"{decision_id}:{arm_name}"
                if rid in seen_settled_ids:
                    continue
                synthetic_lock = _normalize_decision_lock(
                    {
                        "decision_id": decision_id,
                        "tenant_id": clean_tenant,
                        "agora_user_id": raw_settled.get("agora_user_id") or raw_settled.get("user_id") or "",
                        "account_id": raw_settled.get("account_id"),
                        "broker_ids": raw_settled.get("broker_ids") or [],
                        "decision_time": raw_settled.get("decision_time") or raw_settled.get("settled_at") or _utc_now(),
                        "data_cutoff": raw_settled.get("data_cutoff"),
                        "raw_text": raw_settled.get("raw_text") or "",
                        "settled_arms": [dict(raw_settled)],
                    },
                    clean_tenant,
                )
                for settled in _extract_settled_arms_from_lock(synthetic_lock):
                    seen_settled_ids.add(settled["record_id"])
                    records.append(settled)

        if requested_kind in (None, "decision_journal"):
            for raw_entry in self.list_raw_journal_entries(tenant_id=clean_tenant):
                records.append(_normalize_journal_entry(raw_entry, clean_tenant))

        if requested_kind in (None, "servant_insight"):
            for raw_insight in self.list_raw_servant_insights(tenant_id=clean_tenant):
                records.append(_normalize_servant_insight(raw_insight, clean_tenant))

        if viewer_roles is not None:
            records = [
                rec
                for rec in records
                if is_record_visible_to_caller(
                    rec,
                    tenant_id=clean_tenant,
                    user_id=viewer_user_id or "",
                    roles=viewer_roles,
                )
            ]

        records.sort(key=lambda r: (str(r["available_time"]), str(r["record_kind"]), str(r["record_id"])))

        if parsed_cursor is not None:
            records = [
                r
                for r in records
                if (str(r["available_time"]), str(r["record_kind"]), str(r["record_id"])) > parsed_cursor
            ]

        bounded_limit = max(1, min(int(limit), 1000))
        has_more = len(records) > bounded_limit
        page = records[:bounded_limit]
        next_cursor = page[-1]["cursor"] if page else (_clean(cursor) or None)

        return {
            "status": "ok",
            "provenance": PLATFORM_SOURCE_PROVENANCE,
            "tenant_id": clean_tenant,
            "cursor": _clean(cursor) or None,
            "next_cursor": next_cursor,
            "has_more": has_more,
            "count": len(page),
            "total": len(page),
            "items": page,
            "records": page,
            "decision_locks": [r for r in page if r["record_kind"] == "decision_lock"],
            "settled_arms": [r for r in page if r["record_kind"] == "settled_arm"],
            "decision_journal_entries": [r for r in page if r["record_kind"] == "decision_journal"],
            "servant_insights": [r for r in page if r["record_kind"] == "servant_insight"],
            "governance": {
                "facts_only": True,
                "direct_execution_allowed": False,
                "validation_gates_required": True,
                "environment_ceiling": "paper",
            },
        }


def default_tenant_for_row(row: Mapping[str, Any], fallback: str) -> str:
    trader_id = row.get("trader_identity") if isinstance(row.get("trader_identity"), Mapping) else {}
    return _clean(row.get("tenant_id") or trader_id.get("tenant_id") or fallback)


def _authenticate_service_caller(
    authorization: Optional[str],
    service_actor: Optional[str],
    requested_tenant_id: Optional[str],
) -> Tuple[str, str]:
    """Authenticate service-token callers on /internal/agora/platform-source/*."""
    if "AGORA_PLATFORM_SOURCE_SERVICE_TOKEN" in os.environ:
        expected_token = os.environ["AGORA_PLATFORM_SOURCE_SERVICE_TOKEN"].strip()
    else:
        expected_token = LOCAL_PLATFORM_SOURCE_SERVICE_TOKEN

    posture = (
        os.getenv("PANTHEON_PERSISTENCE_POSTURE")
        or os.getenv("PANTHEON_ENV")
        or "dev"
    ).strip().lower()
    enforced = posture in {
        "stage",
        "staging",
        "staging-live",
        "prod",
        "production",
    } or posture.startswith(("staging", "prod"))

    if not expected_token or (
        enforced
        and expected_token in {LOCAL_PLATFORM_SOURCE_SERVICE_TOKEN, EXAMPLE_PLATFORM_SOURCE_SERVICE_TOKEN}
    ):
        raise HTTPException(
            status_code=503,
            detail={
                "status": "unavailable",
                "reason": "agora_platform_source_service_boundary_unconfigured",
            },
        )

    scheme, _, supplied_token = str(authorization or "").partition(" ")
    if (
        scheme.lower() != "bearer"
        or not supplied_token
        or not hmac.compare_digest(supplied_token.strip(), expected_token)
    ):
        raise HTTPException(
            status_code=401,
            detail={
                "status": "unauthorized",
                "reason": "agora_platform_source_invalid_service_credential",
            },
        )

    raw_allowed_actors = (os.getenv("AGORA_PLATFORM_SOURCE_ALLOWED_SERVICE_ACTORS") or "").strip()
    allowed_actors = (
        {a.strip() for a in raw_allowed_actors.split(",") if a.strip()}
        if raw_allowed_actors
        else set(DEFAULT_ALLOWED_SERVICE_ACTORS)
    )
    actor = _clean(service_actor) or "source-ingest"
    if actor not in allowed_actors:
        raise HTTPException(
            status_code=403,
            detail={
                "status": "forbidden",
                "reason": "agora_platform_source_actor_forbidden",
            },
        )

    tenant_id = (
        _clean(requested_tenant_id)
        or _clean(os.getenv("PANTHEON_TENANT_ID"))
        or _clean(os.getenv("PANTHEON_BFF_TENANT_ID"))
        or "default"
    )
    raw_allowed_tenants = (os.getenv("AGORA_PLATFORM_SOURCE_SERVICE_TENANTS") or "").strip()
    if raw_allowed_tenants:
        allowed_tenants = {t.strip() for t in raw_allowed_tenants.split(",") if t.strip()}
        if "*" not in allowed_tenants and tenant_id not in allowed_tenants:
            raise HTTPException(
                status_code=403,
                detail={
                    "status": "forbidden",
                    "reason": "agora_platform_source_tenant_forbidden",
                },
            )

    return actor, tenant_id


def create_platform_source_router(
    *,
    owner: Optional[AgoraPlatformSourceOwner] = None,
    agora_owner: Optional[Any] = None,
    dataset_store: Optional[Any] = None,
    journal_write_owner: Optional[Any] = None,
    extract_identity: Optional[Callable[..., Any]] = None,
    require_read_role: Optional[Callable[..., None]] = None,
    bff_error: Optional[Callable[..., HTTPException]] = None,
    utc_now: Callable[[], str] = _utc_now,
) -> APIRouter:
    """Create the Agora platform data source router for internal service and operator/owner reads."""
    router = APIRouter(tags=["agora-platform-source"])
    source_owner = (
        owner
        if isinstance(owner, AgoraPlatformSourceOwner)
        else AgoraPlatformSourceOwner(
            agora_owner=agora_owner if agora_owner is not None else owner,
            dataset_store=dataset_store,
            journal_write_owner=journal_write_owner,
        )
    )

    @router.get("/internal/agora/platform-source/records")
    @router.get("/internal/agora/platform-source")
    def list_internal_platform_source_records(
        cursor: Optional[str] = Query(default=None),
        limit: int = Query(default=100, ge=1, le=1000),
        kind: Optional[str] = Query(default=None),
        tenant_id: Optional[str] = Query(default=None),
        authorization: Optional[str] = Header(default=None),
        service_actor: Optional[str] = Header(default=None, alias="X-Pantheon-Service-Actor"),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    ) -> Dict[str, Any]:
        actor, resolved_tenant = _authenticate_service_caller(
            authorization,
            service_actor,
            tenant_id or x_tenant_id or x_pantheon_tenant,
        )
        try:
            payload = source_owner.read_incremental(
                tenant_id=resolved_tenant,
                cursor=cursor,
                limit=limit,
                kind=kind,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={"status": "invalid_request", "reason": str(exc)},
            ) from exc
        except Exception as exc:
            log.exception("Agora platform source owner read failed")
            raise HTTPException(
                status_code=503,
                detail={
                    "status": "unavailable",
                    "reason": "agora_platform_source_owner_unavailable",
                },
            ) from exc

        payload["meta"] = {
            "snapshot_at": utc_now(),
            "capability": PLATFORM_SOURCE_CAPABILITY,
            "audience": f"tenant:{resolved_tenant}:service:{actor}",
            "provenance": PLATFORM_SOURCE_PROVENANCE,
            "no_promote_proof": "agora_facts_only_validation_gated",
            "no_runtime_mutation_proof": "agora_platform_source_read_only",
        }
        return payload

    @router.get("/bff/agora/platform-source/records")
    def list_scoped_platform_source_records(
        cursor: Optional[str] = Query(default=None),
        limit: int = Query(default=100, ge=1, le=1000),
        kind: Optional[str] = Query(default=None),
        user_id: Optional[str] = Query(default=None),
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
        x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
        x_pantheon_role: Optional[str] = Header(default=None, alias="X-Pantheon-Role"),
    ) -> Dict[str, Any]:
        if extract_identity is not None and require_read_role is not None:
            identity = extract_identity(authorization)
            require_read_role(identity)
            try:
                scope = resolve_agora_user_scope(
                    identity,
                    utc_now=utc_now,
                    requested_tenant_id=x_tenant_id or x_pantheon_tenant,
                )
            except AgoraScopeResolutionError as exc:
                if bff_error is not None:
                    from ..models import ErrorCode

                    code = ErrorCode.AUTH_REQUIRED if exc.status_code == 401 else ErrorCode.FORBIDDEN
                    raise bff_error(
                        exc.status_code,
                        code,
                        exc.message,
                        exc.reason,
                        precondition_failed="agora_user_scope",
                        details_extra=exc.details,
                    ) from exc
                raise HTTPException(
                    status_code=exc.status_code,
                    detail={"status": "forbidden", "reason": exc.reason},
                ) from exc
            scope_tenant_id = scope.tenant_id
            scope_user_id = scope.user_id
            scope_roles = list(scope.roles or [])
        elif x_tenant_id or x_pantheon_tenant or x_user_id or x_pantheon_role:
            scope_tenant_id = _clean(x_tenant_id or x_pantheon_tenant) or "default"
            scope_user_id = _clean(x_user_id)
            scope_roles = [r.strip() for r in str(x_pantheon_role or "trader").split(",") if r.strip()]
            if not scope_user_id and not (set(scope_roles) & {"operator", "admin", "governance"}):
                raise HTTPException(
                    status_code=401,
                    detail={"status": "unauthorized", "reason": "agora_user_identity_required"},
                )
        else:
            raise HTTPException(
                status_code=503,
                detail={"status": "unavailable", "reason": "agora_auth_policy_unconfigured"},
            )

        role_set = {str(r).strip().lower() for r in (scope_roles or []) if str(r).strip()}
        is_operator = bool(role_set & {"operator", "admin", "governance"})
        if user_id and not is_operator and _clean(user_id) != _clean(scope_user_id):
            raise HTTPException(
                status_code=403,
                detail={"status": "forbidden", "reason": "cross_user_read_forbidden"},
            )

        try:
            payload = source_owner.read_incremental(
                tenant_id=scope_tenant_id,
                cursor=cursor,
                limit=limit,
                kind=kind,
                viewer_user_id=scope_user_id,
                viewer_roles=scope_roles,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={"status": "invalid_request", "reason": str(exc)},
            ) from exc
        except Exception as exc:
            log.exception("Agora scoped platform source read failed")
            raise HTTPException(
                status_code=503,
                detail={
                    "status": "unavailable",
                    "reason": "agora_platform_source_owner_unavailable",
                },
            ) from exc

        if user_id:
            target_uid = _clean(user_id)
            filtered = [r for r in payload["items"] if _clean(r.get("agora_user_id")) == target_uid]
            payload["items"] = filtered
            payload["records"] = filtered
            payload["total"] = len(filtered)
            payload["decision_locks"] = [r for r in filtered if r["record_kind"] == "decision_lock"]
            payload["settled_arms"] = [r for r in filtered if r["record_kind"] == "settled_arm"]
            payload["decision_journal_entries"] = [r for r in filtered if r["record_kind"] == "decision_journal"]
            payload["servant_insights"] = [r for r in filtered if r["record_kind"] == "servant_insight"]

        payload["meta"] = {
            "snapshot_at": utc_now(),
            "capability": PLATFORM_SOURCE_CAPABILITY,
            "audience": f"tenant:{scope_tenant_id}:user:{scope_user_id}",
            "provenance": PLATFORM_SOURCE_PROVENANCE,
            "no_promote_proof": "agora_facts_only_validation_gated",
            "no_runtime_mutation_proof": "agora_platform_source_read_only",
        }
        return payload

    router.platform_source_owner = source_owner
    return router


__all__ = [
    "AgoraPlatformSourceOwner",
    "DEFAULT_ALLOWED_SERVICE_ACTORS",
    "LOCAL_PLATFORM_SOURCE_SERVICE_TOKEN",
    "PLATFORM_SOURCE_CAPABILITY",
    "PLATFORM_SOURCE_PROVENANCE",
    "RECORD_KINDS",
    "create_platform_source_router",
    "format_cursor",
    "is_record_visible_to_caller",
    "parse_cursor",
]
