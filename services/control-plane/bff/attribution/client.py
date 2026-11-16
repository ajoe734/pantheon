"""Attribution-owner client and shaping for outlier, attribution and shadow reads.

The BFF keeps no attribution store. Every read forwards to the attribution
owner (``services/evaluation``, service-token auth, contract C3) and the shadow
comparison additionally reads the Agora decision lock from the Agora owner.
No attribution number is computed here: values are forwarded as received.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Mapping, Optional

from services.control_plane.bff.core.owner_reads import remaining_management_read_budget

_ENV_URLS = ("PANTHEON_EVALUATION_API_URL", "PANTHEON_EVALUATION_SERVICE_URL", "EVALUATION_URL")
_ENV_TOKENS = ("EVALUATION_SERVICE_TOKEN", "EVALUATION_API_TOKEN")
SUBJECT_KINDS = ("trader", "persona", "pool_member")
DIRECTIONS = ("outstanding", "underperform", "disagreement")
SHADOW_ARMS = ("trader", "base_strategy", "private_servant", "institutional_view")
_IDENTITY_KEYS = frozenset({"subject_id", "trader_id", "agora_user_id", "user_id", "account_id", "broker_ids"})


class AttributionOwnerUnavailableError(RuntimeError):
    def __init__(self, reason: str, *, status_code: int = 503) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


class AttributionOwnerNotFoundError(RuntimeError):
    def __init__(self, ref: str, *, reason: str = "attribution_not_found") -> None:
        super().__init__(reason)
        self.ref = ref
        self.reason = reason
        self.status_code = 404


class AttributionOwnerValidationError(RuntimeError):
    def __init__(self, reason: str, *, status_code: int = 422) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


class AttributionOwnerClient:
    """Thin read-only adapter over the attribution owner's C3 routes."""

    def __init__(
        self,
        tenant_id: str,
        actor_id: str,
        *,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> None:
        self.tenant_id = str(tenant_id or "").strip()
        self.actor_id = str(actor_id or "operator").strip()
        self.base_url = (base_url or next((os.getenv(n, "").strip() for n in _ENV_URLS if os.getenv(n, "").strip()), "")).rstrip("/")
        self.token = (token if token is not None else next((os.getenv(n, "").strip() for n in _ENV_TOKENS if os.getenv(n, "").strip()), "")).strip()
        budget = remaining_management_read_budget(timeout_seconds or 5.0)
        self.timeout = max(0.05, float(budget if budget is not None else (timeout_seconds or 5.0)))

    def _get(self, path: str, query: Optional[Mapping[str, Any]] = None, *, ref: str = "") -> Any:
        rem = remaining_management_read_budget()
        if rem is not None and rem <= 0.0:
            raise AttributionOwnerUnavailableError("read_timeout", status_code=504)
        if not self.base_url:
            raise AttributionOwnerUnavailableError("attribution_owner_unconfigured")
        if not self.token:
            raise AttributionOwnerUnavailableError("attribution_service_credential_unconfigured")
        qs = urllib.parse.urlencode({k: v for k, v in (query or {}).items() if v not in (None, "")})
        url = f"{self.base_url}/{path.lstrip('/')}" + (f"?{qs}" if qs else "")
        req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
        req.add_unredirected_header("Authorization", f"Bearer {self.token}")
        req.add_unredirected_header("X-Pantheon-Service-Actor", f"bff:{self.actor_id}")
        if self.tenant_id:
            req.add_unredirected_header("X-Pantheon-Tenant-Id", self.tenant_id)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                text = resp.read().decode("utf-8").strip()
                return json.loads(text) if text else None
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise AttributionOwnerNotFoundError(ref or path.rsplit("/", 1)[-1]) from exc
            if exc.code in (400, 409, 422):
                raise AttributionOwnerValidationError(f"attribution_owner_http_{exc.code}", status_code=int(exc.code)) from exc
            raise AttributionOwnerUnavailableError(
                f"attribution_owner_http_{exc.code}", status_code=exc.code if exc.code in (401, 403) else 503
            ) from exc
        except (OSError, ValueError) as exc:
            raise AttributionOwnerUnavailableError("attribution_owner_unavailable") from exc

    def list_records(
        self,
        *,
        since: Optional[str] = None,
        subject_kind: Optional[str] = None,
        direction: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> Any:
        return self._get(
            "/api/attribution/records",
            {"since": since, "subject_kind": subject_kind, "direction": direction, "cursor": cursor, "limit": limit},
        )

    def get_record(self, record_id: str) -> Any:
        return self._get(f"/api/attribution/records/{urllib.parse.quote(record_id, safe='')}", ref=record_id)

    def get_shadow(self, decision_id: str) -> Any:
        return self._get(f"/api/attribution/shadow/{urllib.parse.quote(decision_id, safe='')}", ref=decision_id)


def _text(v: Any) -> str:
    return str(v or "").strip()


def record_visible(record: Mapping[str, Any], *, is_operator: bool, caller_user_id: str) -> bool:
    """Operators see every record; a trader sees only records about himself."""
    if is_operator:
        return True
    return (
        _text(record.get("subject_kind")) == "trader"
        and bool(caller_user_id)
        and _text(record.get("subject_id")) == caller_user_id
    )


def shape_record(record: Mapping[str, Any], *, detail: bool = False) -> Dict[str, Any]:
    """Forward owner numbers untouched; add the MT-4 meeting link and hypothesis state."""
    out: Dict[str, Any] = dict(record)
    out["decomposition"] = dict(record.get("decomposition") or {})
    out["luck_interval"] = record.get("luck_interval")
    meeting_id = _text(record.get("meeting_id"))
    out["meeting"] = (
        {"meeting_id": meeting_id, "meeting_type": "MT-4", "href": f"/bff/management/meetings/{urllib.parse.quote(meeting_id, safe='')}"}
        if meeting_id
        else None
    )
    hyp = record.get("hypothesis_validation")
    if hyp is None:
        hyp = record.get("hypotheses")
    out["hypothesis_validation"] = hyp if hyp is not None else {"status": "unavailable", "reason": "no_hypothesis_recorded"}
    return out


def shape_record_list(
    payload: Any,
    *,
    is_operator: bool,
    caller_user_id: str,
    snapshot_at: str,
    limit: int,
) -> Dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise AttributionOwnerUnavailableError("attribution_owner_invalid_payload")
    rows = [r for r in (payload.get("items") or []) if isinstance(r, Mapping)]
    items = [shape_record(r) for r in rows if record_visible(r, is_operator=is_operator, caller_user_id=caller_user_id)]
    next_cursor = payload.get("next_cursor")
    return {
        "status": "ok" if items else "empty",
        "owner": "attribution",
        "items": items,
        "empty": not items,
        "page_info": {"next_cursor": next_cursor, "has_more": bool(next_cursor), "page_size": limit, "returned": len(items)},
        "meta": {"snapshot_at": snapshot_at, "source": "attribution", "status": "ok"},
    }


def shape_record_detail(
    record: Any,
    *,
    is_operator: bool,
    caller_user_id: str,
    snapshot_at: str,
    meeting: Optional[Mapping[str, Any]] = None,
    meeting_unavailable_reason: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return None when the caller may not see the record (route answers 404)."""
    if not isinstance(record, Mapping):
        raise AttributionOwnerUnavailableError("attribution_owner_invalid_payload")
    if not record_visible(record, is_operator=is_operator, caller_user_id=caller_user_id):
        return None
    shaped = shape_record(record, detail=True)
    if shaped["meeting"] is not None:
        if meeting is not None:
            shaped["meeting"].update(
                {k: meeting.get(k) for k in ("status", "outcome", "topic") if meeting.get(k) is not None}
            )
            hyp = meeting.get("hypothesis_validation", meeting.get("hypotheses"))
            if hyp is not None and record.get("hypothesis_validation") is None and record.get("hypotheses") is None:
                shaped["hypothesis_validation"] = hyp
        else:
            shaped["meeting"]["unavailable_reason"] = meeting_unavailable_reason or "meeting_not_read"
    return {
        "status": "ok",
        "owner": "attribution",
        "data": shaped,
        "meta": {"snapshot_at": snapshot_at, "source": "attribution", "status": "ok"},
    }


def _lock_arm(lock: Optional[Mapping[str, Any]], arm: str) -> Dict[str, Any]:
    if lock is None:
        return {"available": False, "unavailable_reason": "agora_lock_unavailable"}
    if arm == "trader":
        return {
            "available": True,
            "decision": lock.get("decision"),
            "trading_room_verdict": lock.get("trading_room_verdict"),
            "execution_observation_kind": lock.get("execution_observation_kind"),
            "execution_display_label": lock.get("execution_display_label"),
            "execution_verified": lock.get("execution_verified"),
            "decision_time": lock.get("decision_time"),
        }
    arms = lock.get("arms") if isinstance(lock.get("arms"), Mapping) else {}
    value = arms.get(arm)
    return dict(value) if isinstance(value, Mapping) else {"available": False, "unavailable_reason": f"{arm}_arm_not_recorded"}


def shape_shadow(
    decision_id: str,
    *,
    attribution: Optional[Mapping[str, Any]],
    attribution_unavailable_reason: Optional[str],
    lock: Optional[Mapping[str, Any]],
    lock_unavailable_reason: Optional[str],
    is_operator: bool,
    snapshot_at: str,
) -> Dict[str, Any]:
    """Four arms, each with lock detail (Agora owner) and settlement (attribution owner)."""
    owner_arms: Dict[str, Mapping[str, Any]] = {}
    if attribution is not None:
        for a in attribution.get("arms") or []:
            if isinstance(a, Mapping) and _text(a.get("arm")):
                owner_arms[_text(a["arm"])] = a
    names = list(SHADOW_ARMS) + [n for n in owner_arms if n not in SHADOW_ARMS]
    arms: List[Dict[str, Any]] = []
    for name in names:
        settle = owner_arms.get(name)
        if settle is not None:
            settlement = {
                "settlement_source": settle.get("settlement_source"),
                "outcome": settle.get("outcome"),
                "settled_at": settle.get("settled_at"),
                "unavailable_reason": settle.get("unavailable_reason"),
            }
        else:
            settlement = {
                "settlement_source": None,
                "outcome": None,
                "settled_at": None,
                "unavailable_reason": attribution_unavailable_reason or "arm_not_reported_by_attribution_owner",
            }
        arm_lock = _lock_arm(lock, name)
        if lock is None and lock_unavailable_reason:
            arm_lock["unavailable_reason"] = lock_unavailable_reason
        arms.append({"arm": name, "decision_detail": arm_lock, **settlement})
    degraded = attribution_unavailable_reason or lock_unavailable_reason
    data: Dict[str, Any] = {"decision_id": decision_id, "arms": arms}
    if lock is not None and is_operator:
        data["trader_identity"] = lock.get("trader_identity")
        data["account_id"] = lock.get("account_id")
    elif lock is not None:
        data["account_id"] = lock.get("account_id")
    return {
        "status": "degraded" if degraded else "ok",
        "owner": "attribution+agora",
        "data": data,
        "meta": {
            "snapshot_at": snapshot_at,
            "sources": {
                "attribution": attribution_unavailable_reason or "ok",
                "agora": lock_unavailable_reason or "ok",
            },
        },
    }


def unavailable_attribution_envelope(surface_key: str, reason: str, *, snapshot_at: str) -> Dict[str, Any]:
    return {
        "status": "unavailable",
        "owner": "attribution",
        "reason": reason,
        "empty": False,
        "items": [],
        "data": {"id": surface_key.replace("_", "-"), "status": "unavailable", "source": "attribution", "reason": reason, "items": []},
        "page_info": {"next_cursor": None, "has_more": False, "page_size": 0, "returned": 0},
        "meta": {
            "snapshot_at": snapshot_at,
            "status": "unavailable",
            "source": "attribution",
            "reason": reason,
            "surfaces": {surface_key: {"status": "unavailable", "source": "attribution", "reason": reason, "snapshot_at": snapshot_at}},
        },
    }
