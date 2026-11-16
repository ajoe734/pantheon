"""Attribution owner store and governed upstream readers (C3, §12.4, §12.5).

Owns persistence for:
  - weekly and event attribution runs (idempotent per tenant + ISO week)
  - outlier and disagreement attribution records
  - settled shadow decision arms (preserving A4 §3 outcome superseding history)

Reads trader C4 decision locks and journal entries exclusively through the Agora
platform source owner route (``/internal/agora/platform-source/records``) — never
by reading the Agora database directly.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
import urllib.error
import urllib.parse
import urllib.request

from services.foundation.postgres_json_store import PostgresJsonOwnerStore

from .outliers import DIRECTIONS, SUBJECT_KINDS
from .settlement import SETTLEMENT_SOURCE_PRIORITY, ShadowDecisionSettlement

log = logging.getLogger(__name__)

LOCAL_AGORA_PLATFORM_SOURCE_TOKEN = "pantheon-local-agora-platform-source-service-token"
DEFAULT_AGORA_PLATFORM_SOURCE_URL = "http://operator-bff:8001/internal/agora/platform-source/records"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean(value: Any) -> str:
    return str(value or "").strip()


class _KeyValueTable:
    """In-memory / optional JSON-file table with the same interface as PostgresJsonOwnerStore."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path
        self._rows: Dict[str, Dict[str, Any]] = {}
        if self._path is not None and self._path.is_file():
            try:
                loaded = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(loaded, Mapping):
                    self._rows = {str(k): dict(v) for k, v in loaded.items() if isinstance(v, Mapping)}
            except (OSError, json.JSONDecodeError):
                self._rows = {}

    def _flush(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._rows, ensure_ascii=False, sort_keys=True), encoding="utf-8")

    def put(self, record_id: str, payload: Dict[str, Any]) -> None:
        if not record_id:
            raise ValueError("record_id is required")
        self._rows[str(record_id)] = copy.deepcopy(dict(payload))
        self._flush()

    def insert_if_absent(
        self,
        record_id: str,
        payload: Dict[str, Any],
        *,
        unique_fields: Tuple[str, ...] = (),
    ) -> Tuple[bool, Dict[str, Any]]:
        if not record_id:
            raise ValueError("record_id is required")
        if record_id in self._rows:
            return False, copy.deepcopy(self._rows[record_id])
        if unique_fields:
            for existing in self._rows.values():
                if all(existing.get(f) == payload.get(f) for f in unique_fields):
                    return False, copy.deepcopy(existing)
        stored = copy.deepcopy(dict(payload))
        self._rows[str(record_id)] = stored
        self._flush()
        return True, copy.deepcopy(stored)

    def get(self, record_id: str) -> Optional[Dict[str, Any]]:
        row = self._rows.get(str(record_id))
        return copy.deepcopy(row) if row is not None else None

    def list_all(self) -> List[Dict[str, Any]]:
        return [copy.deepcopy(v) for v in self._rows.values()]


class AttributionStore:
    """Owner store for attribution runs, attribution records, and shadow arm settlements."""

    def __init__(
        self,
        *,
        dsn: Optional[str] = None,
        schema: str = "evaluation",
        data_dir: Optional[Path | str] = None,
        backend: Optional[str] = None,
    ) -> None:
        resolved_backend = _clean(backend or os.getenv("EVALUATION_STORE_BACKEND") or "auto").lower()
        resolved_dsn = _clean(dsn or os.getenv("EVALUATION_STORE_DSN"))
        base_dir = Path(data_dir) if data_dir else None

        self._runs: Any = None
        self._records: Any = None
        self._shadows: Any = None
        self.backend = "memory"

        if resolved_backend == "postgres" or (resolved_backend == "auto" and resolved_dsn):
            try:
                self._runs = PostgresJsonOwnerStore(
                    dsn=resolved_dsn,
                    table=f"{schema}.attribution_runs",
                    owner_service="evaluation-svc",
                    bootstrap=True,
                )
                self._records = PostgresJsonOwnerStore(
                    dsn=resolved_dsn,
                    table=f"{schema}.attribution_records",
                    owner_service="evaluation-svc",
                    bootstrap=True,
                )
                self._shadows = PostgresJsonOwnerStore(
                    dsn=resolved_dsn,
                    table=f"{schema}.shadow_settlements",
                    owner_service="evaluation-svc",
                    bootstrap=True,
                )
                self.backend = "postgres"
            except Exception:
                if resolved_backend == "postgres":
                    raise
                log.debug("Falling back to local AttributionStore table", exc_info=True)

        if self._runs is None:
            self._runs = _KeyValueTable(base_dir / "attribution_runs.json" if base_dir else None)
            self._records = _KeyValueTable(base_dir / "attribution_records.json" if base_dir else None)
            self._shadows = _KeyValueTable(base_dir / "shadow_settlements.json" if base_dir else None)
            self.backend = "file" if base_dir else "memory"

    @staticmethod
    def weekly_run_id(tenant_id: str, iso_week: str) -> str:
        clean_t = _clean(tenant_id) or "default"
        clean_w = _clean(iso_week)
        digest = hashlib.sha256(f"{clean_t}|weekly|{clean_w}".encode("utf-8")).hexdigest()[:12]
        return f"attr-run-{clean_w}-{digest}"

    def get_weekly_run(self, tenant_id: str, iso_week: str) -> Optional[Dict[str, Any]]:
        run_id = self.weekly_run_id(tenant_id, iso_week)
        existing = self._runs.get(run_id)
        if existing is not None:
            return existing
        for row in self._runs.list_all():
            if (
                _clean(row.get("tenant_id")) == (_clean(tenant_id) or "default")
                and _clean(row.get("kind")) == "weekly"
                and _clean(row.get("iso_week")) == _clean(iso_week)
            ):
                return row
        return None

    def record_or_replay_weekly_run(
        self,
        *,
        tenant_id: str,
        iso_week: str,
        window_end: str,
        compute_fn: Callable[[], Dict[str, Any]],
    ) -> Tuple[Dict[str, Any], bool]:
        """Execute a weekly attribution run idempotently per (tenant_id, iso_week)."""
        clean_tenant = _clean(tenant_id) or "default"
        existing = self.get_weekly_run(clean_tenant, iso_week)
        if existing is not None:
            return {**existing, "idempotent_replay": True}, True

        computed = compute_fn()
        records = computed.get("records") or []
        settlements = computed.get("settlements") or []

        for settlement in settlements:
            if isinstance(settlement, (Mapping, ShadowDecisionSettlement)):
                self.put_shadow_settlement(settlement)

        record_ids: List[str] = []
        for rec in records:
            if isinstance(rec, Mapping):
                saved = self.put_record(rec)
                record_ids.append(saved["record_id"])

        run_id = self.weekly_run_id(clean_tenant, iso_week)
        run_payload: Dict[str, Any] = {
            "run_id": run_id,
            "tenant_id": clean_tenant,
            "kind": "weekly",
            "iso_week": iso_week,
            "window_end": _clean(window_end)[:10],
            "window": computed.get("window") or {"end": _clean(window_end)[:10], "iso_week": iso_week},
            "status": "completed",
            "record_ids": record_ids,
            "records_created": len(record_ids),
            "settlements_recorded": len(settlements),
            "created_at": _clean(computed.get("created_at")) or _utc_now(),
        }
        inserted, canonical = self._runs.insert_if_absent(
            run_id,
            run_payload,
            unique_fields=("tenant_id", "kind", "iso_week"),
        )
        return {**canonical, "idempotent_replay": not inserted}, not inserted

    def put_record(self, record: Mapping[str, Any]) -> Dict[str, Any]:
        row = copy.deepcopy(dict(record))
        rid = _clean(row.get("record_id"))
        if not rid:
            raise ValueError("Attribution record requires record_id")
        row.setdefault("tenant_id", "default")
        row.setdefault("created_at", _utc_now())
        row.setdefault("meeting_id", None)
        self._records.put(rid, row)
        return row

    def get_record(self, record_id: str, *, tenant_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        rid = _clean(record_id)
        if not rid:
            return None
        row = self._records.get(rid)
        if row is None:
            return None
        if tenant_id and _clean(row.get("tenant_id")) not in ("", _clean(tenant_id)):
            return None
        return row

    def attach_meeting(
        self,
        record_id: str,
        meeting_id: str,
        *,
        tenant_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        row = self.get_record(record_id, tenant_id=tenant_id)
        if row is None:
            return None
        row["meeting_id"] = _clean(meeting_id) or None
        self._records.put(row["record_id"], row)
        return row

    def list_records(
        self,
        *,
        tenant_id: Optional[str] = None,
        since: Optional[str] = None,
        subject_kind: Optional[str] = None,
        subject_id: Optional[str] = None,
        direction: Optional[str] = None,
        window_start: Optional[str] = None,
        window_end: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        clean_kind = _clean(subject_kind).lower() or None
        if clean_kind is not None and clean_kind not in SUBJECT_KINDS:
            raise ValueError(f"Invalid subject_kind {subject_kind!r}; expected one of {SUBJECT_KINDS}")

        clean_dir = _clean(direction).lower() or None
        if clean_dir is not None and clean_dir not in DIRECTIONS:
            raise ValueError(f"Invalid direction {direction!r}; expected one of {DIRECTIONS}")

        clean_tenant = _clean(tenant_id) or None
        clean_subject_id = _clean(subject_id) or None
        clean_since = _clean(since) or None
        clean_w_start = _clean(window_start) or None
        clean_w_end = _clean(window_end) or None

        all_rows = self._records.list_all()
        filtered: List[Dict[str, Any]] = []
        for row in all_rows:
            if not isinstance(row, Mapping):
                continue
            if clean_tenant and _clean(row.get("tenant_id")) not in ("", clean_tenant):
                continue
            if clean_kind and _clean(row.get("subject_kind")) != clean_kind:
                continue
            if clean_subject_id and _clean(row.get("subject_id")) != clean_subject_id:
                continue
            if clean_dir and _clean(row.get("direction")) != clean_dir:
                continue
            win = row.get("window") if isinstance(row.get("window"), Mapping) else {}
            row_win_end = _clean(win.get("end"))
            row_win_start = _clean(win.get("start"))
            row_created = _clean(row.get("created_at"))
            if clean_since:
                since_day = clean_since[:10]
                if not (
                    (row_win_end and row_win_end >= since_day)
                    or (row_created and row_created >= clean_since)
                    or (row_created and row_created[:10] >= since_day)
                ):
                    continue
            if clean_w_start and row_win_start and row_win_start < clean_w_start[:10]:
                continue
            if clean_w_end and row_win_end and row_win_end > clean_w_end[:10]:
                continue
            filtered.append(dict(row))

        filtered.sort(key=lambda r: (str(r.get("created_at") or ""), str(r.get("record_id") or "")))

        bounded_limit = max(1, min(int(limit or 50), 500))
        raw_cursor = _clean(cursor)
        if raw_cursor:
            if raw_cursor.startswith("offset-"):
                try:
                    start_idx = max(0, int(raw_cursor.removeprefix("offset-")))
                except ValueError as exc:
                    raise ValueError(f"Malformed cursor: {cursor!r}") from exc
                remaining = filtered[start_idx:]
            elif "|" in raw_cursor:
                parts = raw_cursor.split("|", 1)
                c_key = (parts[0].strip(), parts[1].strip())
                remaining = [
                    r
                    for r in filtered
                    if (str(r.get("created_at") or ""), str(r.get("record_id") or "")) > c_key
                ]
            else:
                # Match record_id watermark
                remaining = [
                    r
                    for r in filtered
                    if str(r.get("record_id") or "") > raw_cursor
                ]
        else:
            remaining = filtered

        page = remaining[:bounded_limit]
        has_more = len(remaining) > bounded_limit
        next_cursor: Optional[str] = None
        if has_more and page:
            last = page[-1]
            next_cursor = f"{last.get('created_at') or ''}|{last.get('record_id') or ''}"

        return {
            "items": page,
            "next_cursor": next_cursor,
            "total": len(filtered),
        }

    def put_shadow_settlement(
        self,
        settlement: ShadowDecisionSettlement | Mapping[str, Any],
    ) -> Dict[str, Any]:
        payload = (
            settlement.to_dict()
            if isinstance(settlement, ShadowDecisionSettlement)
            else copy.deepcopy(dict(settlement))
        )
        decision_id = _clean(payload.get("decision_id"))
        if not decision_id:
            raise ValueError("Shadow settlement requires decision_id")
        payload.setdefault("tenant_id", "default")
        payload.setdefault("settled_at", _utc_now())

        existing = self._shadows.get(decision_id)
        if isinstance(existing, Mapping):
            # Preserve superseded outcome records in history without overwriting prior outcomes (A4 §3)
            history = list(existing.get("outcome_history") or [])
            prev_arms = {
                _clean(a.get("arm")): dict(a)
                for a in (existing.get("arms") or [])
                if isinstance(a, Mapping) and _clean(a.get("arm"))
            }
            merged_arms: List[Dict[str, Any]] = []
            for new_arm in payload.get("arms") or []:
                if not isinstance(new_arm, Mapping):
                    continue
                arm_d = dict(new_arm)
                arm_name = _clean(arm_d.get("arm"))
                prev_arm = prev_arms.get(arm_name)
                if isinstance(prev_arm, Mapping) and prev_arm.get("settlement_source"):
                    prev_src = _clean(prev_arm.get("settlement_source"))
                    new_src = _clean(arm_d.get("settlement_source"))
                    prev_rank = SETTLEMENT_SOURCE_PRIORITY.get(prev_src, 999)
                    new_rank = SETTLEMENT_SOURCE_PRIORITY.get(new_src, 999)
                    if new_rank < prev_rank:
                        prev_oid = _clean(
                            prev_arm.get("outcome_id")
                            or ((prev_arm.get("outcome") or {}) if isinstance(prev_arm.get("outcome"), Mapping) else {}).get("outcome_id")
                        )
                        if prev_oid:
                            arm_d["supersedes_outcome_id"] = prev_oid
                            if isinstance(arm_d.get("outcome"), Mapping):
                                arm_d["outcome"] = {
                                    **dict(arm_d["outcome"]),
                                    "supersedes_outcome_id": prev_oid,
                                }
                        history.append(prev_arm)
                    elif new_rank > prev_rank:
                        # Do not downgrade a higher-authority settled outcome
                        arm_d = prev_arm
                merged_arms.append(arm_d)
            payload["arms"] = merged_arms
            if history:
                payload["outcome_history"] = history

        self._shadows.put(decision_id, payload)
        return payload

    def get_shadow_settlement(
        self,
        decision_id: str,
        *,
        tenant_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        did = _clean(decision_id)
        if not did:
            return None
        row = self._shadows.get(did)
        if row is None:
            return None
        if tenant_id and _clean(row.get("tenant_id")) not in ("", _clean(tenant_id)):
            return None
        return row


class AgoraPlatformSourceClient:
    """Service-token reader pulling C4 decision locks and journals from the Agora owner route (§12.4)."""

    def __init__(
        self,
        *,
        api_url: Optional[str] = None,
        service_token: Optional[str] = None,
        service_actor: str = "attribution",
        timeout_seconds: float = 10.0,
        transport: Optional[Callable[[str, Mapping[str, str]], Mapping[str, Any]]] = None,
    ) -> None:
        self.api_url = (
            _clean(api_url)
            or _clean(os.getenv("AGORA_PLATFORM_SOURCE_URL"))
            or DEFAULT_AGORA_PLATFORM_SOURCE_URL
        )
        self.service_token = (
            _clean(service_token)
            or _clean(os.getenv("AGORA_PLATFORM_SOURCE_SERVICE_TOKEN"))
            or LOCAL_AGORA_PLATFORM_SOURCE_TOKEN
        )
        self.service_actor = (
            _clean(service_actor)
            or _clean(os.getenv("AGORA_PLATFORM_SOURCE_SERVICE_ACTOR"))
            or "attribution"
        )
        self.timeout_seconds = float(timeout_seconds)
        self._transport = transport

    def pull_records(
        self,
        *,
        tenant_id: str = "default",
        kind: Optional[str] = None,
        limit: int = 200,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Pull all incremental pages from /internal/agora/platform-source/records."""
        cursor: Optional[str] = None
        decision_locks: List[Dict[str, Any]] = []
        settled_arms: List[Dict[str, Any]] = []
        journal_entries: List[Dict[str, Any]] = []
        servant_insights: List[Dict[str, Any]] = []

        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.service_token}",
            "X-Pantheon-Service-Actor": self.service_actor,
            "X-Tenant-Id": _clean(tenant_id) or "default",
        }

        for _ in range(20):
            params: Dict[str, Any] = {"tenant_id": _clean(tenant_id) or "default", "limit": limit}
            if kind:
                params["kind"] = kind
            if cursor:
                params["cursor"] = cursor
            qs = urllib.parse.urlencode(params)
            sep = "&" if "?" in self.api_url else "?"
            url = f"{self.api_url}{sep}{qs}"

            if self._transport is not None:
                body = self._transport(url, headers)
            else:
                req = urllib.request.Request(url, headers=headers, method="GET")
                with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                    body = json.loads(resp.read().decode("utf-8"))

            if not isinstance(body, Mapping):
                break
            for item in body.get("items") or body.get("records") or ():
                if not isinstance(item, Mapping):
                    continue
                rk = _clean(item.get("record_kind"))
                if rk == "decision_lock" or item.get("record_type") == "AgoraDecisionLock":
                    decision_locks.append(dict(item))
                elif rk == "settled_arm" or item.get("record_type") == "AgoraSettledArm":
                    settled_arms.append(dict(item))
                elif rk == "decision_journal" or item.get("record_type") == "AgoraDecisionJournalEntry":
                    journal_entries.append(dict(item))
                elif rk == "servant_insight" or item.get("record_type") == "AgoraInsight":
                    servant_insights.append(dict(item))

            next_cursor = _clean(body.get("next_cursor"))
            if not body.get("has_more") or not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor

        return {
            "decision_locks": decision_locks,
            "settled_arms": settled_arms,
            "journal_entries": journal_entries,
            "servant_insights": servant_insights,
        }


__all__ = [
    "AgoraPlatformSourceClient",
    "AttributionStore",
    "DEFAULT_AGORA_PLATFORM_SOURCE_URL",
    "LOCAL_AGORA_PLATFORM_SOURCE_TOKEN",
]
