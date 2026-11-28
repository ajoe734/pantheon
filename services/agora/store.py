"""
Independent persistent Agora write-owner storage over Pantheon PostgresJsonOwnerStore.

Provides durable persistence for Agora write groups:
- Core Sessions, Committees & Evidence Packs
- Notes, Insights & Training Examples
- Signals & Signal Feedback
- Handoffs & Audit Events
- Decision Journal (with versioning, diff calculation, and idempotency protection)
- Workshops, Proposals & Interactions

Does not use SQLite, JSON file fallbacks, or in-memory caches.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from typing import Any, Dict, List, Optional, Sequence
import uuid

from services.foundation.postgres_json_store import PostgresJsonOwnerStore


def _utc_now_rfc3339() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_rfc3339(value: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _rfc3339_after(base: str, seconds: float) -> str:
    start = _parse_rfc3339(base) or datetime.now(timezone.utc)
    return (
        (start + timedelta(seconds=seconds))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


# AGORA-SERVANT-SESSION-DURABLE-20261127 runtime budget (SD_AGORA_SERVANT_CORE §4.3, T1).
# Servant conversations live in the Agora owner table ``agora.sessions`` (mode "servant",
# messages embedded in the session row) and their queued turns in ``agora.servant_turns``.
SERVANT_SESSION_MODE = "servant"
SERVANT_MESSAGE_CAP = 500  # messages per session (user + servant); the 501st user message gets a 409
SERVANT_MESSAGE_MAX_CHARS = 4000  # per message, about 2 KB typical; a longer answer is stored truncated
SERVANT_HISTORY_MESSAGES = 20  # messages carried into one provider turn
SERVANT_SESSION_RETENTION_DAYS = 180  # untouched sessions and their turns are purged after this
SERVANT_TURN_MAX_ATTEMPTS = 3  # claims per turn (crash recovery) before it is settled as degraded
SERVANT_TURNS_TABLE = "agora.servant_turns"  # table: one ~1 KB row per user message
# AGORA-TRADER-HABIT-PROFILE-20261127 runtime budget (SD §6): one row per trader and strategy
# in ``agora.habit_profiles`` holding the latest profile plus HABIT_PROFILE_HISTORY_VERSIONS
# superseded versions; older versions are deleted on write.
HABIT_PROFILES_TABLE = "agora.habit_profiles"
HABIT_PROFILE_HISTORY_VERSIONS = 12
HABIT_WIDGET_EVENTS_MAX = 200  # servant user messages read per profile derivation
# AGORA-SERVANT-REVISIONS-AND-REPORT-20261127 runtime budget (SD §9.3, T11):
# One weekly report per trader per strategy per ISO week (keep at most 26 weekly reports per
# trader and strategy, delete older ones on write) plus on-demand reports capped at 3 per day
# and deleted after 7 days; about 20 KB per report.
TRADER_REPORTS_TABLE = "agora.trader_reports"
TRADER_REPORT_WEEKLY_HISTORY_CAP = 26
TRADER_REPORT_ONDEMAND_DAILY_CAP = 3
TRADER_REPORT_ONDEMAND_RETENTION_DAYS = 7
TRADER_REPORT_MAX_BYTES = 20480


class TraderReportDailyCapError(Exception):
    """The trader already requested the maximum number of on-demand reports today (3/day)."""


class ServantSessionMessageCapError(Exception):
    """The servant session already holds the maximum number of messages."""


class ServantSessionConflictError(Exception):
    """A servant session id or message id is already used by a different caller or content."""


class DictRecord(dict):
    """Thin dictionary supporting attribute-style property access for convenience."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(f"DictRecord has no attribute {name!r}")

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value

    def to_dict(self) -> Dict[str, Any]:
        return dict(self)


class AgoraEvidenceStore:
    """Agora human-evidence owner over the single durable dataset inbox.

    Admission and downstream publication share the existing inbox transaction;
    there is no second notes table/outbox write that can be lost on a crash.
    """

    def __init__(self, dataset_store: Any) -> None:
        self.inbox = dataset_store

    def admit(self, evidence: Any, **scope: Any) -> Any:
        from services.control_plane.bff.agora.dataset_extraction.extractor import admit_evidence
        return admit_evidence(evidence, store=self.inbox, **scope)

    def get(self, evidence_id: str, *, tenant_id: str, user_id: str) -> Any:
        return self.inbox.get_inbox_entry(evidence_id, tenant_id=tenant_id, user_id=user_id)

    def list_persona(self, *, tenant_id: str, user_id: str, persona_id: str) -> List[Dict[str, Any]]:
        # Keep pending/failed evidence visible to Observe independently of L5.
        return [
            item
            for status in ("pending", "processing", "processed", "failed")
            for item in self.inbox._list_inbox(tenant_id=tenant_id, user_id=user_id, status=status)
            if item["persona_id"] == persona_id
        ]

    def cycle_references(self, *, tenant_id: str, persona_id: str, since: Any = None,
                         after_id: str = "") -> List[Dict[str, Any]]:
        """Purpose-bound worker read of consented teaching references only.

        The Persona owner supplies canonical tenant/persona identity. Private
        content and user identities never leave this owner through this port;
        human-facing reads retain their tenant+user predicate. This is the
        same learning consent used by the existing extraction consumer.
        """
        from datetime import datetime, timedelta, timezone
        if not tenant_id or not persona_id:
            raise ValueError("canonical persona scope is required")
        now = datetime.now(timezone.utc)
        if self.inbox.backend == "memory":
            with self.inbox._lock:
                rows = [dict(r) for r in self.inbox._inbox.values()
                        if r["tenant_id"] == tenant_id and r["persona_id"] == persona_id
                        and "agora_human_evidence" in r.get("content", {})]
        else:
            with self.inbox._connect() as conn:
                found = conn.execute(
                    f"SELECT evidence_id, created_at, consent_granted, learning_eligible, purpose, retention_days "
                    f"FROM {self.inbox._inbox_table} WHERE tenant_id = %s AND persona_id = %s "
                    "AND content ? 'agora_human_evidence' AND consent_granted = true "
                    "AND learning_eligible = true AND purpose = 'policy_learning' "
                    "AND (created_at, evidence_id) > (%s, %s) AND "
                    "created_at::timestamptz + retention_days * interval '1 day' > %s "
                    "ORDER BY created_at, evidence_id LIMIT 101",
                    (tenant_id, persona_id, since or datetime.min.replace(tzinfo=timezone.utc), after_id, now),
                ).fetchall()
            rows = [dict(zip(("evidence_id", "created_at", "consent_granted", "learning_eligible", "purpose", "retention_days"), r))
                    for r in found]
        refs = []
        for row in rows:
            created = datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00"))
            if (row.get("consent_granted") and row.get("learning_eligible")
                    and row.get("purpose") == "policy_learning"
                    and created + timedelta(days=row["retention_days"]) > now
                    and (since is None or (created, row["evidence_id"]) > (since, after_id))):
                refs.append({"evidence_id": row["evidence_id"], "created_at": created.isoformat()})
        return sorted(refs, key=lambda r: (r["created_at"], r["evidence_id"]))[:101]

    def pending_scopes(self, limit: int = 25) -> List[Any]:
        """Discover bounded durable work after restart, including expired leases."""
        if self.inbox.backend == "memory":
            with self.inbox._lock:
                return list(dict.fromkeys(
                    (item["tenant_id"], item["user_id"])
                    for item in self.inbox._inbox.values()
                    if item["status"] in {"pending", "processing"}
                ))[:limit]
        with self.inbox._connect() as conn:
            rows = conn.execute(
                f"SELECT tenant_id, user_id FROM {self.inbox._inbox_table} "
                "WHERE status = 'pending' OR (status = 'processing' AND "
                "(lease_expires_at IS NULL OR lease_expires_at <= now())) "
                "GROUP BY tenant_id, user_id ORDER BY min(created_at) LIMIT %s",
                (limit,),
            ).fetchall()
        return [(row[0], row[1]) for row in rows]


class AgoraStore:
    """
    Concrete persistent store for Agora write operations.

    Built directly on Pantheon's durable foundation: PostgresJsonOwnerStore.
    Every mutation persists durably to PostgreSQL; every read fetches fresh data
    directly from PostgreSQL without caching.
    """

    def __init__(
        self,
        *,
        dsn: Optional[str] = None,
        schema: str = "agora",
        bootstrap: bool = True,
    ) -> None:
        self.dsn = (
            dsn
            or os.getenv("AGORA_STORE_DSN")
            or os.getenv("DATABASE_URL")
            or "postgresql://postgres@localhost:5432/pantheon"
        ).strip()
        self.schema = schema.strip() or "agora"

        self._sessions = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{self.schema}.sessions", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._memos = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{self.schema}.memos", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._evidence_packs = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{self.schema}.evidence_packs", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._notes = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{self.schema}.notes", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._insights = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{self.schema}.insights", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._training_examples = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{self.schema}.training_examples", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._signals = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{self.schema}.signals", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._signal_feedback = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{self.schema}.signal_feedback", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._feedback = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{self.schema}.feedback", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._handoffs = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{self.schema}.handoffs", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._audit_events = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{self.schema}.audit_events", owner_service="agora-svc", bootstrap=bootstrap
        )
        # JOURNAL-CONSUMER-ISOLATION-CORRECTIVE-001:
        # Decision Journal writer slice is retired from AgoraStore.
        # Canonical durable owner is services.governance.decision_journal.
        self._journal = None
        self._journal_audit = None
        self._journal_idempotency = None
        self._workshops = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{self.schema}.workshops", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._proposals = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{self.schema}.proposals", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._interactions = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{self.schema}.interactions", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._decision_locks = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{self.schema}.decision_locks", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._servant_turns = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{self.schema}.servant_turns", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._habit_profiles = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{self.schema}.habit_profiles", owner_service="agora-svc", bootstrap=bootstrap
        )
        self._trader_reports = PostgresJsonOwnerStore(
            dsn=self.dsn, table=f"{self.schema}.trader_reports", owner_service="agora-svc", bootstrap=bootstrap
        )

    # -------------------------------------------------------------------------
    # Sessions & Committees
    # -------------------------------------------------------------------------
    def create_session(
        self,
        session_id: str,
        title: str,
        actor_id: str,
        payload: Dict[str, Any],
        created_at: Optional[str] = None,
    ) -> DictRecord:
        now = created_at or _utc_now_rfc3339()
        mode = str(payload.get("mode") or "general").strip() or "general"
        participants = list(payload.get("participants") or [])
        if not any(p.get("id") == actor_id for p in participants if isinstance(p, dict)):
            participants.insert(0, {"id": actor_id, "role": "operator", "joinedAt": now})

        target_entity = dict(payload.get("targetEntity") or payload.get("target_entity") or {})
        session_record = DictRecord({
            "id": session_id,
            "sessionId": session_id,
            "title": title,
            "mode": mode,
            "status": "active",
            "outcome": None,
            "targetEntity": target_entity,
            "contextRefs": list(payload.get("contextRefs") or payload.get("context_refs") or []),
            "participants": participants,
            "messages": list(payload.get("messages") or []),
            "createdAt": now,
            "updatedAt": now,
            "closedAt": None,
            "canonicalWriteAuthority": "agora_committee_service",
            "persistenceMode": "owner_store",
        })
        self._sessions.put(session_id, session_record)
        return session_record

    def get_session(self, session_id: str) -> Optional[DictRecord]:
        data = self._sessions.get(session_id)
        return DictRecord(data) if data else None

    def list_sessions(self, mode: Optional[str] = None, status: Optional[str] = None) -> List[DictRecord]:
        rows = [DictRecord(r) for r in self._sessions.list_all()]
        if mode:
            rows = [r for r in rows if r.get("mode") == mode]
        if status:
            rows = [r for r in rows if r.get("status") == status]
        rows.sort(key=lambda s: s.get("createdAt", ""), reverse=True)
        return rows

    def open_committee_session(
        self,
        session_id: str,
        opened_at: Optional[str] = None,
    ) -> Optional[DictRecord]:
        session = self.get_session(session_id)
        if not session:
            return None
        now = opened_at or _utc_now_rfc3339()
        session["status"] = "open"
        session["openedAt"] = now
        session["updatedAt"] = now
        self._sessions.put(session_id, session)
        return session

    def close_committee_session(
        self,
        session_id: str,
        closed_at: Optional[str] = None,
        outcome: Optional[str] = None,
        memo_ids: Optional[List[str]] = None,
    ) -> Optional[DictRecord]:
        session = self.get_session(session_id)
        if not session:
            return None
        now = closed_at or _utc_now_rfc3339()
        session["status"] = "closed"
        session["closedAt"] = now
        session["updatedAt"] = now
        if outcome is not None:
            session["outcome"] = outcome
        if memo_ids is not None:
            session["memoIds"] = memo_ids
        self._sessions.put(session_id, session)
        return session

    def close_session(
        self,
        session_id: str,
        closed_at: Optional[str] = None,
        outcome: Optional[str] = None,
    ) -> Optional[DictRecord]:
        session = self.get_session(session_id)
        if not session:
            return None
        now = closed_at or _utc_now_rfc3339()
        session["status"] = "closed"
        session["closedAt"] = now
        session["updatedAt"] = now
        if outcome is not None:
            session["outcome"] = outcome
        self._sessions.put(session_id, session)
        return session

    def append_session_message(
        self,
        session_id: str,
        message_id: str,
        content: str,
        actor_id: str,
        payload: Dict[str, Any],
        created_at: Optional[str] = None,
    ) -> Optional[DictRecord]:
        """Append one message; idempotent on ``message_id`` and safe against concurrent appends.

        The BFF (user message) and the turn worker (servant answer) append to the same
        session row, so the write is a compare-and-set retried on a stale snapshot.
        Servant sessions enforce the per-session cap and per-message size budget.
        """
        for _attempt in range(8):
            session = self.get_session(session_id)
            if not session:
                return None
            messages = list(session.get("messages") or [])
            for existing in messages:
                if isinstance(existing, dict) and existing.get("id") == message_id:
                    return DictRecord(existing)
            role = str(payload.get("role") or "user")
            truncated = False
            if session.get("mode") == SERVANT_SESSION_MODE:
                # A user message needs room for its answer, so it stops one short of the cap.
                room = SERVANT_MESSAGE_CAP - (1 if role == "user" else 0)
                if len(messages) >= room:
                    raise ServantSessionMessageCapError(session_id)
                if len(content) > SERVANT_MESSAGE_MAX_CHARS:
                    content = content[:SERVANT_MESSAGE_MAX_CHARS]
                    truncated = True
            now = created_at or _utc_now_rfc3339()
            msg = DictRecord({
                "id": message_id,
                "sessionId": session_id,
                "sender": dict(payload.get("sender") or {"type": "operator", "id": actor_id}),
                "role": role,
                "content": content,
                "language": str(payload.get("language") or "zh-TW"),
                "attachments": list(payload.get("attachments") or []),
                "citations": list(payload.get("citations") or []),
                "annotations": list(payload.get("annotations") or []),
                "createdAt": now,
            })
            if payload.get("metadata"):
                msg["metadata"] = dict(payload["metadata"])
            if truncated:
                msg["truncated"] = True
            updated = DictRecord(session)
            updated["messages"] = messages + [msg]
            updated["updatedAt"] = now
            swapped, _current = self._sessions.compare_and_set(session_id, dict(session), dict(updated))
            if swapped:
                return msg
        raise RuntimeError(f"agora session {session_id} append lost 8 concurrent races")

    # -------------------------------------------------------------------------
    # Servant conversations (AGORA-SERVANT-SESSION-DURABLE-20261127)
    # -------------------------------------------------------------------------
    def ensure_servant_session(
        self,
        session_id: str,
        *,
        tenant_id: str,
        user_id: str,
        operator_id: str,
        persona_id: str,
        session_type: str = "interactive",
        created_at: Optional[str] = None,
    ) -> DictRecord:
        """Create the durable servant conversation row once; a different owner is a conflict."""
        existing = self.get_session(session_id)
        if existing is None:
            now = created_at or _utc_now_rfc3339()
            record = DictRecord({
                "id": session_id,
                "sessionId": session_id,
                "title": "Agora servant conversation",
                "mode": SERVANT_SESSION_MODE,
                "status": "active",
                "outcome": None,
                "targetEntity": {},
                "contextRefs": [],
                "participants": [{"id": user_id, "role": "trader", "joinedAt": now}],
                "messages": [],
                "tenantId": tenant_id,
                "userId": user_id,
                "operatorId": operator_id,
                "personaId": persona_id,
                "sessionType": session_type,
                "createdAt": now,
                "updatedAt": now,
                "closedAt": None,
                "canonicalWriteAuthority": "agora_servant_service",
                "persistenceMode": "owner_store",
            })
            created, current = self._sessions.compare_and_set(session_id, None, dict(record))
            if created:
                return record
            existing = DictRecord(current) if current else None
        if (
            existing is None
            or existing.get("mode") != SERVANT_SESSION_MODE
            or str(existing.get("tenantId")) != str(tenant_id)
            or str(existing.get("userId")) != str(user_id)
        ):
            raise ServantSessionConflictError(session_id)
        return existing

    def get_servant_session(self, session_id: str) -> Optional[DictRecord]:
        session = self.get_session(session_id)
        if session is None or session.get("mode") != SERVANT_SESSION_MODE:
            return None
        return session

    def enqueue_servant_turn(self, turn: Dict[str, Any]) -> tuple[bool, DictRecord]:
        """Durably queue one turn; the same ``turn_id`` is queued once (idempotent replay)."""
        now = str(turn.get("created_at") or _utc_now_rfc3339())
        record = {
            **dict(turn),
            "status": "queued",
            "attempts": 0,
            "lease_owner": None,
            "lease_expires_at": None,
            "answer_message_id": None,
            "created_at": now,
            "updated_at": now,
        }
        inserted, current = self._servant_turns.insert_if_absent(str(record["turn_id"]), record)
        return inserted, DictRecord(current)

    def get_servant_turn(self, turn_id: str) -> Optional[DictRecord]:
        data = self._servant_turns.get(turn_id)
        return DictRecord(data) if data else None

    def list_servant_turns(self, session_id: str, statuses: Sequence[str] = ("queued", "running")) -> List[DictRecord]:
        rows: List[DictRecord] = []
        for status in statuses:
            rows.extend(
                DictRecord(r)
                for r in self._servant_turns.select_where(
                    "payload ->> 'session_id' = %s AND payload ->> 'status' = %s",
                    (session_id, status),
                )
            )
        rows.sort(key=lambda r: (str(r.get("created_at")), str(r.get("turn_id"))))
        return rows

    def claim_servant_turn(
        self,
        *,
        lease_owner: str,
        lease_seconds: float = 600,
        now: Optional[str] = None,
    ) -> Optional[DictRecord]:
        """Claim the oldest queued turn, or a running turn whose lease expired (crash recovery)."""
        now = now or _utc_now_rfc3339()
        for status in ("queued", "running"):
            rows = self._servant_turns.select_where(
                "payload ->> 'status' = %s",
                (status,),
                tail_sql="ORDER BY payload ->> 'created_at' ASC LIMIT 25",
            )
            for row in sorted(rows, key=lambda r: (str(r.get("created_at")), str(r.get("turn_id")))):
                if status == "running" and str(row.get("lease_expires_at") or "") > now:
                    continue
                claimed = {
                    **row,
                    "status": "running",
                    "attempts": int(row.get("attempts") or 0) + 1,
                    "lease_owner": lease_owner,
                    "lease_expires_at": _rfc3339_after(now, lease_seconds),
                    "updated_at": now,
                }
                swapped, _current = self._servant_turns.compare_and_set(str(row["turn_id"]), row, claimed)
                if swapped:
                    return DictRecord(claimed)
        return None

    def finish_servant_turn(
        self,
        turn: Dict[str, Any],
        *,
        status: str,
        answer_message_id: Optional[str] = None,
        reason: Optional[str] = None,
        finished_at: Optional[str] = None,
    ) -> Optional[DictRecord]:
        """Settle a claimed turn as completed, degraded or failed (compare-and-set on the claim)."""
        current = self._servant_turns.get(str(turn["turn_id"]))
        if current is None or current.get("lease_owner") != turn.get("lease_owner"):
            return None
        settled = {
            **current,
            "status": status,
            "answer_message_id": answer_message_id,
            "reason": reason,
            "lease_owner": None,
            "lease_expires_at": None,
            "updated_at": finished_at or _utc_now_rfc3339(),
        }
        swapped, _current = self._servant_turns.compare_and_set(str(turn["turn_id"]), current, settled)
        return DictRecord(settled) if swapped else None

    def purge_expired_servant_sessions(
        self,
        *,
        now: Optional[str] = None,
        retention_days: int = SERVANT_SESSION_RETENTION_DAYS,
    ) -> Dict[str, int]:
        """Cleanup path: delete servant sessions untouched for ``retention_days`` and their turns."""
        cutoff = _parse_rfc3339(now or _utc_now_rfc3339()) - timedelta(days=retention_days)
        removed = {"sessions": 0, "turns": 0}
        for row in self._sessions.select_where("payload ->> 'mode' = %s", (SERVANT_SESSION_MODE,)):
            touched = _parse_rfc3339(row.get("updatedAt") or row.get("createdAt"))
            if touched is None or touched >= cutoff:
                continue
            session_id = str(row.get("id"))
            for turn in self._servant_turns.select_where("payload ->> 'session_id' = %s", (session_id,)):
                if self._servant_turns.delete_if_matches(str(turn["turn_id"]), turn):
                    removed["turns"] += 1
            if self._sessions.delete_if_matches(session_id, row):
                removed["sessions"] += 1
        return removed

    # -------------------------------------------------------------------------
    # Committee Memos
    # -------------------------------------------------------------------------
    def submit_committee_memo(
        self,
        session_id: str,
        memo_id: str,
        actor_id: str,
        payload: Dict[str, Any],
        created_at: Optional[str] = None,
    ) -> Optional[DictRecord]:
        session = self.get_session(session_id)
        if not session:
            return None
        now = created_at or _utc_now_rfc3339()
        memo_type = str(payload.get("memoType") or payload.get("memo_type") or "committee_summary").strip() or "committee_summary"
        author_ref = dict(payload.get("authorRef") or payload.get("author_ref") or {"type": "operator", "id": actor_id})
        evidence_refs = list(payload.get("evidenceRefs") or payload.get("evidence_refs") or [])
        evidence_ref_ids = [str(r.get("id") or r.get("evidence_id") or r.get("ref") or "") for r in evidence_refs if isinstance(r, dict)]

        target = session.get("targetEntity") or {"type": "artifact", "id": session_id}
        memo = DictRecord({
            "id": memo_id,
            "memo_id": memo_id,
            "memo_type": memo_type,
            "status": "draft",
            "lifecycle_state": "draft",
            "linked_session_id": session_id,
            "linked_request_id": payload.get("linkedRequestId") or payload.get("linked_request_id") or session.get("linkedRequestId"),
            "author_ref": author_ref,
            "session_to_memo_mapping": {
                "session_id": session_id,
                "session_type": "committee",
                "memo_type": memo_type,
                "mapping_status": "draft",
                "evidence_ref_ids": evidence_ref_ids,
            },
            "summary": str(payload.get("summary") or "").strip() or None,
            "recommendations": list(payload.get("recommendations") or []),
            "evidence_refs": evidence_refs,
            "created_at": now,
            "published_at": None,
            "published_by": None,
            "governance_target": {
                "type": target.get("type", "artifact"),
                "id": target.get("id", session_id),
                "target_type": target.get("type", "artifact"),
                "target_id": target.get("id", session_id),
            },
        })
        self._memos.put(memo_id, memo)
        return memo

    def get_committee_memo(self, session_id: str, memo_id: str) -> Optional[DictRecord]:
        data = self._memos.get(memo_id)
        if data and data.get("linked_session_id") == session_id:
            return DictRecord(data)
        return None

    def list_committee_memos(self, session_id: str) -> List[DictRecord]:
        rows = [DictRecord(r) for r in self._memos.list_all() if r.get("linked_session_id") == session_id]
        rows.sort(key=lambda m: m.get("created_at", ""), reverse=True)
        return rows

    def publish_committee_memo(
        self,
        session_id: str,
        memo_id: str,
        actor_id: str,
        published_at: Optional[str] = None,
    ) -> Optional[DictRecord]:
        memo = self.get_committee_memo(session_id, memo_id)
        if not memo:
            return None
        now = published_at or _utc_now_rfc3339()
        memo["status"] = "published"
        memo["lifecycle_state"] = "published"
        memo["published_at"] = now
        memo["published_by"] = actor_id
        mapping = memo.get("session_to_memo_mapping")
        if isinstance(mapping, dict):
            mapping["mapping_status"] = "active"
        self._memos.put(memo_id, memo)
        return memo

    # -------------------------------------------------------------------------
    # Evidence Packs & Files
    # -------------------------------------------------------------------------
    def create_evidence_pack(
        self,
        session_id: str,
        payload: Dict[str, Any],
        actor_id: str,
        created_at: Optional[str] = None,
    ) -> DictRecord:
        existing = self.get_evidence_pack(session_id)
        pack_id = str(
            payload.get("id")
            or payload.get("packId")
            or (existing.get("packId") if existing else f"evpack-{uuid.uuid4().hex[:12]}")
        )
        session = self.get_session(session_id)
        session_target = session.get("targetEntity", {}) if session else {}
        target_type = str(payload.get("targetEntityType") or payload.get("target_entity_type") or session_target.get("type") or "artifact")
        target_id = str(payload.get("targetEntityId") or payload.get("target_entity_id") or session_target.get("id") or session_id)
        now = created_at or _utc_now_rfc3339()

        pack = DictRecord({
            "id": pack_id,
            "packId": pack_id,
            "sessionId": session_id,
            "targetEntityType": target_type,
            "targetEntityId": target_id,
            "uploadedFiles": list(existing.get("uploadedFiles", [])) if existing else [],
            "linkedEntities": list(payload.get("linkedEntities") or payload.get("linked_entities") or []),
            "notes": str(payload.get("notes") or ""),
            "createdBy": existing.get("createdBy") if existing else actor_id,
            "createdAt": existing.get("createdAt") if existing else now,
            "updatedAt": now,
            "canonicalWriteAuthority": "agora_committee_evidence_service",
            "persistenceMode": "owner_store",
        })
        self._evidence_packs.put(session_id, pack)
        return pack

    def get_evidence_pack(self, session_id: str) -> Optional[DictRecord]:
        data = self._evidence_packs.get(session_id)
        return DictRecord(data) if data else None

    def append_evidence_files(
        self,
        session_id: str,
        files: List[Dict[str, Any]],
        actor_id: str,
        uploaded_at: Optional[str] = None,
    ) -> Optional[DictRecord]:
        session = self.get_session(session_id)
        now = uploaded_at or _utc_now_rfc3339()
        pack = self.get_evidence_pack(session_id)
        if not pack:
            pack = self.create_evidence_pack(
                session_id=session_id,
                payload={},
                actor_id=actor_id,
                created_at=now,
            )

        uploaded = list(pack.get("uploadedFiles", []))
        for f in files:
            file_id = str(f.get("fileId") or f.get("id") or f"f-{uuid.uuid4().hex[:8]}")
            name = str(f.get("filename") or f.get("name") or "unnamed")
            size = int(f.get("sizeBytes") or f.get("size") or 0)
            mime = str(f.get("mimeType") or f.get("mime") or "application/octet-stream")
            s_ref = str(f.get("storageRef") or f.get("ref") or f"storage://evidence/{session_id}/{file_id}")
            c_hash = str(f.get("contentHash") or f.get("hash") or "")
            uploaded.append({
                "fileId": file_id,
                "filename": name,
                "sizeBytes": size,
                "mimeType": mime,
                "storageRef": s_ref,
                "contentHash": c_hash,
                "uploadedBy": actor_id,
                "uploadedAt": now,
            })
        pack["uploadedFiles"] = uploaded
        pack["updatedAt"] = now
        self._evidence_packs.put(session_id, pack)
        return pack

    # -------------------------------------------------------------------------
    # Notes & Insights
    # -------------------------------------------------------------------------
    def create_note(
        self,
        note_id: str,
        title: str,
        content: str,
        actor_id: str,
        payload: Optional[Dict[str, Any]] = None,
        created_at: Optional[str] = None,
    ) -> DictRecord:
        now = created_at or _utc_now_rfc3339()
        p = payload or {}
        note = DictRecord({
            "id": note_id,
            "noteId": note_id,
            "title": title,
            "content": content,
            "author": actor_id,
            "tags": list(p.get("tags") or []),
            "category": str(p.get("category") or "general"),
            "contextRefs": list(p.get("contextRefs") or p.get("context_refs") or []),
            "createdAt": now,
            "updatedAt": now,
            "canonicalWriteAuthority": "agora_notes_service",
            "persistenceMode": "owner_store",
        })
        self._notes.put(note_id, note)
        return note

    def get_note(self, note_id: str) -> Optional[DictRecord]:
        data = self._notes.get(note_id)
        return DictRecord(data) if data else None

    def list_notes(self) -> List[DictRecord]:
        rows = [DictRecord(r) for r in self._notes.list_all()]
        rows.sort(key=lambda n: n.get("createdAt", ""), reverse=True)
        return rows

    def create_insight(
        self,
        insight_id: str,
        title: str,
        content: str,
        actor_id: str,
        payload: Optional[Dict[str, Any]] = None,
        created_at: Optional[str] = None,
    ) -> DictRecord:
        now = created_at or _utc_now_rfc3339()
        p = payload or {}
        insight = DictRecord({
            "id": insight_id,
            "insightId": insight_id,
            "title": title,
            "content": content,
            "author": actor_id,
            "category": str(p.get("category") or "market"),
            "confidence": float(p.get("confidence") or 0.8),
            "impact": str(p.get("impact") or "medium"),
            "contextRefs": list(p.get("contextRefs") or p.get("context_refs") or []),
            "createdAt": now,
            "updatedAt": now,
            "canonicalWriteAuthority": "agora_insights_service",
            "persistenceMode": "owner_store",
        })
        self._insights.put(insight_id, insight)
        return insight

    def get_insight(self, insight_id: str) -> Optional[DictRecord]:
        data = self._insights.get(insight_id)
        return DictRecord(data) if data else None

    def list_insights(self) -> List[DictRecord]:
        rows = [DictRecord(r) for r in self._insights.list_all()]
        rows.sort(key=lambda i: i.get("createdAt", ""), reverse=True)
        return rows

    # -------------------------------------------------------------------------
    # Training Examples
    # -------------------------------------------------------------------------
    def create_training_example(
        self,
        example_id: str,
        topic: str,
        input_data: Dict[str, Any],
        expected_output: Dict[str, Any],
        actor_id: str,
        payload: Optional[Dict[str, Any]] = None,
        created_at: Optional[str] = None,
    ) -> DictRecord:
        now = created_at or _utc_now_rfc3339()
        p = payload or {}
        example = DictRecord({
            "id": example_id,
            "exampleId": example_id,
            "topic": topic,
            "inputData": dict(input_data),
            "expectedOutput": dict(expected_output),
            "submittedBy": actor_id,
            "status": str(p.get("status") or "curated"),
            "targetPersonaId": p.get("targetPersonaId") or p.get("target_persona_id"),
            "tags": list(p.get("tags") or []),
            "createdAt": now,
            "updatedAt": now,
            "canonicalWriteAuthority": "agora_training_example_service",
            "persistenceMode": "owner_store",
        })
        self._training_examples.put(example_id, example)
        return example

    def get_training_example(self, example_id: str) -> Optional[DictRecord]:
        data = self._training_examples.get(example_id)
        return DictRecord(data) if data else None

    def list_training_examples(self) -> List[DictRecord]:
        rows = [DictRecord(r) for r in self._training_examples.list_all()]
        rows.sort(key=lambda e: e.get("createdAt", ""), reverse=True)
        return rows

    # -------------------------------------------------------------------------
    # Signals & Feedback
    # -------------------------------------------------------------------------
    def create_signal(
        self,
        signal_id: str,
        symbol: str,
        action: str,
        confidence: float,
        actor_id: str,
        payload: Optional[Dict[str, Any]] = None,
        created_at: Optional[str] = None,
    ) -> DictRecord:
        now = created_at or _utc_now_rfc3339()
        p = payload or {}
        signal = DictRecord({
            "id": signal_id,
            "signalId": signal_id,
            "symbol": symbol,
            "action": action,
            "confidence": float(confidence),
            "source": actor_id,
            "reviewStatus": str(p.get("reviewStatus") or p.get("review_status") or "pending"),
            "horizon": str(p.get("horizon") or "short"),
            "rationale": str(p.get("rationale") or ""),
            "metadata": dict(p.get("metadata") or {}),
            "feedback": None,
            "createdAt": now,
            "updatedAt": now,
            "canonicalWriteAuthority": "agora_signal_service",
            "persistenceMode": "owner_store",
        })
        self._signals.put(signal_id, signal)
        return signal

    def get_signal(self, signal_id: str) -> Optional[DictRecord]:
        data = self._signals.get(signal_id)
        return DictRecord(data) if data else None

    def list_signals(self, review_status: Optional[str] = None) -> List[DictRecord]:
        rows = [DictRecord(r) for r in self._signals.list_all()]
        if review_status:
            rows = [r for r in rows if r.get("reviewStatus") == review_status or r.get("review_status") == review_status]
        rows.sort(key=lambda s: s.get("createdAt", ""), reverse=True)
        return rows

    def record_signal_feedback(
        self,
        signal_id: str,
        rating: int,
        comments: str,
        actor_id: str,
        recorded_at: Optional[str] = None,
    ) -> Optional[DictRecord]:
        signal = self.get_signal(signal_id)
        if not signal:
            return None
        now = recorded_at or _utc_now_rfc3339()
        feedback_id = f"fb-sig-{uuid.uuid4().hex[:8]}"
        fb = DictRecord({
            "id": feedback_id,
            "signalId": signal_id,
            "rating": rating,
            "comments": comments,
            "reviewer": actor_id,
            "recordedAt": now,
            "canonicalWriteAuthority": "agora_signal_feedback_service",
            "persistenceMode": "owner_store",
        })
        self._signal_feedback.put(feedback_id, fb)
        signal["feedback"] = dict(fb)
        signal["updatedAt"] = now
        self._signals.put(signal_id, signal)
        return fb

    def create_feedback(
        self,
        feedback_id: str,
        target_id: str,
        content: str,
        actor_id: str,
        payload: Optional[Dict[str, Any]] = None,
        created_at: Optional[str] = None,
    ) -> DictRecord:
        now = created_at or _utc_now_rfc3339()
        p = payload or {}
        fb = DictRecord({
            "id": feedback_id,
            "feedbackId": feedback_id,
            "targetId": target_id,
            "targetType": str(p.get("targetType") or p.get("target_type") or "item"),
            "content": content,
            "author": actor_id,
            "score": p.get("score"),
            "createdAt": now,
            "canonicalWriteAuthority": "agora_feedback_service",
            "persistenceMode": "owner_store",
        })
        self._feedback.put(feedback_id, fb)
        return fb

    def get_feedback(self, feedback_id: str) -> Optional[DictRecord]:
        data = self._feedback.get(feedback_id)
        return DictRecord(data) if data else None

    def list_feedback(self, target_id: Optional[str] = None) -> List[DictRecord]:
        rows = [DictRecord(r) for r in self._feedback.list_all()]
        if target_id:
            rows = [r for r in rows if r.get("targetId") == target_id or r.get("target_id") == target_id]
        rows.sort(key=lambda f: f.get("createdAt", ""), reverse=True)
        return rows

    # -------------------------------------------------------------------------
    # Handoffs
    # -------------------------------------------------------------------------
    def create_handoff(
        self,
        handoff_id: str,
        source_lane: str,
        target_lane: str,
        summary: str,
        actor_id: str,
        payload: Optional[Dict[str, Any]] = None,
        created_at: Optional[str] = None,
    ) -> DictRecord:
        now = created_at or _utc_now_rfc3339()
        p = payload or {}
        handoff = DictRecord({
            "id": handoff_id,
            "handoffId": handoff_id,
            "sourceLane": source_lane,
            "targetLane": target_lane,
            "summary": summary,
            "status": str(p.get("status") or "pending"),
            "contextRefs": list(p.get("contextRefs") or p.get("context_refs") or []),
            "artifacts": list(p.get("artifacts") or []),
            "createdBy": actor_id,
            "createdAt": now,
            "updatedAt": now,
            "canonicalWriteAuthority": "agora_handoff_service",
            "persistenceMode": "owner_store",
        })
        self._handoffs.put(handoff_id, handoff)
        return handoff

    def get_handoff(self, handoff_id: str) -> Optional[DictRecord]:
        data = self._handoffs.get(handoff_id)
        return DictRecord(data) if data else None

    def list_handoffs(self) -> List[DictRecord]:
        rows = [DictRecord(r) for r in self._handoffs.list_all()]
        rows.sort(key=lambda h: h.get("createdAt", ""), reverse=True)
        return rows

    # -------------------------------------------------------------------------
    # Audit Events
    # -------------------------------------------------------------------------
    def record_audit_event(
        self,
        event_type: str,
        actor_id: str,
        target_id: str,
        payload: Optional[Dict[str, Any]] = None,
        event_id: Optional[str] = None,
        recorded_at: Optional[str] = None,
    ) -> DictRecord:
        now = recorded_at or _utc_now_rfc3339()
        ev_id = event_id or f"aud-agora-{uuid.uuid4().hex[:12]}"
        p = payload or {}
        ev = DictRecord({
            "id": ev_id,
            "eventId": ev_id,
            "eventType": event_type,
            "actorId": actor_id,
            "targetId": target_id,
            "details": dict(p),
            "recordedAt": now,
            "canonicalWriteAuthority": "agora_audit_service",
            "persistenceMode": "owner_store",
        })
        self._audit_events.put(ev_id, ev)
        return ev

    def get_audit_event(self, event_id: str) -> Optional[DictRecord]:
        data = self._audit_events.get(event_id)
        return DictRecord(data) if data else None

    def list_audit_events(self) -> List[DictRecord]:
        rows = [DictRecord(r) for r in self._audit_events.list_all()]
        rows.sort(key=lambda a: a.get("recordedAt", ""), reverse=True)
        return rows

    # -------------------------------------------------------------------------
    # Decision Journal (RETIRED: JOURNAL-CONSUMER-ISOLATION-CORRECTIVE-001)
    # -------------------------------------------------------------------------
    def create_journal_entry(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> DictRecord:
        raise RuntimeError(
            "Agora decision journal writer slice is retired; "
            "use canonical governance decision journal owner (services.governance.decision_journal)."
        )

    def get_journal_entry(self, entry_id: str) -> Optional[DictRecord]:
        raise RuntimeError(
            "Agora decision journal writer slice is retired; "
            "use canonical governance decision journal owner (services.governance.decision_journal)."
        )

    def list_journal_entries(self) -> List[DictRecord]:
        raise RuntimeError(
            "Agora decision journal writer slice is retired; "
            "use canonical governance decision journal owner (services.governance.decision_journal)."
        )

    def patch_journal_entry(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Optional[DictRecord]:
        raise RuntimeError(
            "Agora decision journal writer slice is retired; "
            "use canonical governance decision journal owner (services.governance.decision_journal)."
        )

    # -------------------------------------------------------------------------
    # Strategy Workshops, Proposals & Interactions
    # -------------------------------------------------------------------------
    def create_workshop(
        self,
        initial_message: str,
        created_by: str,
        workshop_id: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> DictRecord:
        ws_id = workshop_id or f"ws-{uuid.uuid4().hex[:12]}"
        now = created_at or _utc_now_rfc3339()
        ws = DictRecord({
            "id": ws_id,
            "workshop_id": ws_id,
            "status": "active",
            "version": 1,
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
            "messages": [
                {
                    "id": f"msg-{uuid.uuid4().hex[:8]}",
                    "content": initial_message,
                    "sender": created_by,
                    "created_at": now,
                }
            ],
        })
        self._workshops.put(ws_id, ws)
        return ws

    def get_workshop(self, workshop_id: str) -> Optional[DictRecord]:
        data = self._workshops.get(workshop_id)
        return DictRecord(data) if data else None

    def append_workshop_message(
        self,
        workshop_id: str,
        content: str,
        actor_id: str,
        message_id: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> Optional[DictRecord]:
        ws = self.get_workshop(workshop_id)
        if not ws:
            return None
        now = created_at or _utc_now_rfc3339()
        m_id = message_id or f"msg-{uuid.uuid4().hex[:8]}"
        msg = {
            "id": m_id,
            "content": content,
            "sender": actor_id,
            "created_at": now,
        }
        ws["messages"].append(msg)
        ws["version"] = int(ws.get("version", 1)) + 1
        ws["updated_at"] = now
        self._workshops.put(workshop_id, ws)
        return ws

    def create_proposal(
        self,
        payload: Dict[str, Any],
        created_by: str,
        proposal_id: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> DictRecord:
        p_id = proposal_id or f"prop-{uuid.uuid4().hex[:12]}"
        now = created_at or _utc_now_rfc3339()
        prop = DictRecord({
            "id": p_id,
            "proposal_id": p_id,
            "proposal_type": str(payload.get("proposal_type") or "strategy_patch"),
            "target_kind": str(payload.get("target_kind") or "strategy"),
            "target_id": str(payload.get("target_id") or ""),
            "target_version": str(payload.get("target_version") or "v1"),
            "status": str(payload.get("status") or "draft"),
            "revision": int(payload.get("revision") or 1),
            "current_value": dict(payload.get("current_value") or {}),
            "proposed_value": dict(payload.get("proposed_value") or {}),
            "rationale": str(payload.get("rationale") or ""),
            "confidence": payload.get("confidence"),
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
            "change_history": [],
        })
        self._proposals.put(p_id, prop)
        return prop

    def get_proposal(self, proposal_id: str) -> Optional[DictRecord]:
        data = self._proposals.get(proposal_id)
        return DictRecord(data) if data else None

    def modify_proposal(
        self,
        proposal_id: str,
        action: str,
        reason: str,
        proposed_value: Optional[Dict[str, Any]],
        actor_id: str,
        updated_at: Optional[str] = None,
    ) -> Optional[DictRecord]:
        prop = self.get_proposal(proposal_id)
        if not prop:
            return None
        now = updated_at or _utc_now_rfc3339()
        history_entry = {
            "action": action,
            "reason": reason,
            "actor_id": actor_id,
            "at": now,
            "previous_revision": prop.get("revision", 1),
        }
        prop["change_history"].append(history_entry)
        prop["revision"] = int(prop.get("revision", 1)) + 1
        prop["updated_at"] = now
        if action == "approve":
            prop["status"] = "approved"
        elif action == "reject":
            prop["status"] = "rejected"
        elif action == "modify_value" and proposed_value is not None:
            prop["proposed_value"] = dict(proposed_value)
        self._proposals.put(proposal_id, prop)
        return prop

    def create_interaction(
        self,
        payload: Dict[str, Any],
        created_by: str,
        interaction_id: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> DictRecord:
        act_id = interaction_id or f"act-{uuid.uuid4().hex[:12]}"
        now = created_at or _utc_now_rfc3339()
        act = DictRecord({
            "id": act_id,
            "interaction_id": act_id,
            "workshop_id": str(payload.get("workshop_id") or ""),
            "mode": str(payload.get("mode") or "propose_action"),
            "environment": str(payload.get("environment") or "paper"),
            "status": str(payload.get("status") or "active"),
            "topic": str(payload.get("topic") or ""),
            "participant_persona_ids": list(payload.get("participant_persona_ids") or []),
            "context_refs": list(payload.get("context_refs") or []),
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
        })
        self._interactions.put(act_id, act)
        return act

    def get_interaction(self, interaction_id: str) -> Optional[DictRecord]:
        data = self._interactions.get(interaction_id)
        return DictRecord(data) if data else None

    # -------------------------------------------------------------------------
    # Shadow-mode decision locks (immutable; insert-only, no update/delete path)
    # -------------------------------------------------------------------------
    @staticmethod
    def decision_lock_id(tenant_id: str, decision_event_id: str) -> str:
        digest = hashlib.sha256(f"{tenant_id}\x1f{decision_event_id}".encode("utf-8")).hexdigest()
        return f"hdl-{digest[:32]}"

    def create_decision_lock(self, lock: Dict[str, Any]) -> DictRecord:
        """Insert one lock; a second write for the same decision is rejected."""
        from .shadow_mode import DecisionLockExistsError

        inserted, existing = self._decision_locks.insert_if_absent(
            str(lock["human_decision_id"]), dict(lock)
        )
        if not inserted:
            raise DecisionLockExistsError(str(lock.get("decision_event_id")))
        return DictRecord(existing)

    def get_decision_lock(self, human_decision_id: str) -> Optional[DictRecord]:
        data = self._decision_locks.get(human_decision_id)
        return DictRecord(data) if data else None

    def record_decision_lock_paper_proxy_fill(
        self,
        human_decision_id: str,
        *,
        tenant_id: str,
        fill: Dict[str, Any],
        user_id: Optional[str] = None,
        decision_event_id: Optional[str] = None,
    ) -> Optional[DictRecord]:
        """Attach a paper-book proxy fill (`pantheon_paper_proxy`) to the lock's
        ``human_actual`` arm without ever marking ``actual_fill_claimed`` or
        ``execution_verified`` true.
        """
        from .shadow_mode import apply_paper_proxy_fill_to_decision_lock

        candidate_ids: List[str] = []
        clean_lock_id = str(human_decision_id or "").strip()
        if clean_lock_id:
            candidate_ids.append(clean_lock_id)
        clean_event_id = str(decision_event_id or "").strip()
        if clean_event_id and tenant_id:
            derived = self.decision_lock_id(str(tenant_id), clean_event_id)
            if derived not in candidate_ids:
                candidate_ids.append(derived)

        target_id: Optional[str] = None
        current: Optional[Dict[str, Any]] = None
        for cid in candidate_ids:
            found = self._decision_locks.get(cid)
            if found is not None:
                target_id = cid
                current = dict(found)
                break
        if current is None or target_id is None:
            return None
        if str(current.get("tenant_id") or "") != str(tenant_id):
            return None
        if user_id is not None and str(user_id).strip() and str(current.get("user_id") or "") != str(user_id).strip():
            return None

        updated = apply_paper_proxy_fill_to_decision_lock(current, fill)
        if updated.get("checksum") == current.get("checksum"):
            return DictRecord(current)
        if hasattr(self._decision_locks, "compare_and_set"):
            swapped, latest = self._decision_locks.compare_and_set(target_id, current, updated)
            if swapped:
                return DictRecord(updated)
            if latest is not None and str(latest.get("tenant_id") or "") == str(tenant_id):
                retried = apply_paper_proxy_fill_to_decision_lock(dict(latest), fill)
                self._decision_locks.put(target_id, retried)
                return DictRecord(retried)
            return None
        self._decision_locks.put(target_id, updated)
        return DictRecord(updated)

    def list_decision_locks(self) -> List[DictRecord]:
        return [DictRecord(row) for row in self._decision_locks.list_all()]

    def list_trader_decision_locks(self, tenant_id: str, user_id: str) -> List[DictRecord]:
        """Owner-scoped read: only this trader's locks (predicate in the owner query)."""
        rows = self._decision_locks.select_where(
            "payload ->> 'tenant_id' = %s AND payload ->> 'user_id' = %s",
            (str(tenant_id), str(user_id)),
        )
        return [DictRecord(row) for row in rows]

    # -------------------------------------------------------------------------
    # Trader habit profiles (AGORA-TRADER-HABIT-PROFILE-20261127)
    # -------------------------------------------------------------------------
    def get_habit_row(self, profile_key: str) -> Optional[DictRecord]:
        data = self._habit_profiles.get(profile_key)
        return DictRecord(data) if data else None

    def put_habit_profile(self, profile: Dict[str, Any], *, now: Optional[str] = None) -> tuple[bool, DictRecord]:
        """Store the latest profile of one trader and strategy; a profile whose inputs did not
        change is not written. The row keeps the latest profile plus
        ``HABIT_PROFILE_HISTORY_VERSIONS`` superseded versions; older ones are deleted here."""
        key = str(profile["profile_key"])
        now = now or _utc_now_rfc3339()
        for _attempt in range(8):
            row = self._habit_profiles.get(key)
            current = (row or {}).get("current")
            if current and current.get("input_digest") == profile.get("input_digest") and not (row or {}).get("stale_since"):
                return False, DictRecord(row)
            if current and current.get("input_digest") == profile.get("input_digest"):
                updated = {**row, "stale_since": None, "refreshed_at": now}
            else:
                version = int((current or {}).get("version") or 0) + 1
                history = ([current] if current else []) + list((row or {}).get("history") or [])
                updated = {
                    "profile_key": key,
                    "tenant_id": profile.get("tenant_id"),
                    "user_id": profile.get("user_id"),
                    "strategy_id": profile.get("strategy_id"),
                    "current": {**profile, "version": version},
                    "history": history[:HABIT_PROFILE_HISTORY_VERSIONS],
                    "stale_since": None,
                    "updated_at": now,
                }
            swapped, _cur = self._habit_profiles.compare_and_set(key, dict(row) if row else None, updated)
            if swapped:
                return True, DictRecord(updated)
        raise RuntimeError(f"habit profile {key} write lost 8 concurrent races")

    def mark_habit_profiles_stale(self, tenant_id: str, user_id: str, *, now: Optional[str] = None) -> int:
        """A decision lock was written: flag this trader's profiles for recompute on next read."""
        now = now or _utc_now_rfc3339()
        marked = 0
        for row in self._habit_profiles.select_where(
            "payload ->> 'tenant_id' = %s AND payload ->> 'user_id' = %s", (str(tenant_id), str(user_id))
        ):
            swapped, _cur = self._habit_profiles.compare_and_set(
                str(row["profile_key"]), dict(row), {**row, "stale_since": now}
            )
            marked += 1 if swapped else 0
        return marked

    def list_servant_user_messages(self, tenant_id: str, user_id: str) -> List[Dict[str, Any]]:
        """User messages of this trader's servant sessions (bounded), for widget-order facts."""
        out: List[Dict[str, Any]] = []
        for session in self._sessions.select_where(
            "payload ->> 'mode' = %s AND payload ->> 'userId' = %s", (SERVANT_SESSION_MODE, str(user_id))
        ):
            if str(session.get("tenantId")) != str(tenant_id):
                continue
            for message in session.get("messages") or []:
                if isinstance(message, dict) and message.get("role") == "user":
                    out.append({"session_id": session.get("id"), **message})
        out.sort(key=lambda m: (str(m.get("createdAt")), str(m.get("id"))))
        return out[-HABIT_WIDGET_EVENTS_MAX:]

    # -------------------------------------------------------------------------
    # Trader reports (AGORA-SERVANT-REVISIONS-AND-REPORT-20261127, SD §9.3)
    # -------------------------------------------------------------------------
    @staticmethod
    def _enforce_trader_report_byte_cap(record: Dict[str, Any], max_bytes: int = TRADER_REPORT_MAX_BYTES) -> Dict[str, Any]:
        """Keep each stored trader report within TRADER_REPORT_MAX_BYTES (~20 KB)."""
        candidate = json.loads(json.dumps(record))
        encoded = json.dumps(candidate, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) <= max_bytes:
            return candidate
        sections = candidate.get("sections")
        if isinstance(sections, dict):
            for sec_key, list_key, cap in (
                ("interventions", "items", 5),
                ("stale_data_sources", "items", 5),
                ("paper_book", "positions", 5),
                ("imitation", "differences", 3),
                ("habit_changes", "changed_features", 5),
            ):
                sec = sections.get(sec_key)
                if isinstance(sec, dict) and isinstance(sec.get(list_key), list) and len(sec[list_key]) > cap:
                    sec[list_key] = sec[list_key][:cap]
        if isinstance(candidate.get("facts"), list) and len(candidate["facts"]) > 24:
            candidate["facts"] = candidate["facts"][:24]
        narrative = candidate.get("narrative")
        if isinstance(narrative, str) and len(narrative) > 1500:
            candidate["narrative"] = narrative[:1500]
            candidate["narrative_truncated"] = True
        encoded = json.dumps(candidate, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > max_bytes and isinstance(candidate.get("facts"), list):
            while candidate["facts"] and len(
                json.dumps(candidate, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            ) > max_bytes:
                candidate["facts"].pop()
        return candidate

    def prune_trader_reports(
        self,
        tenant_id: str,
        user_id: str,
        *,
        strategy_id: Optional[str] = None,
        now: Optional[str] = None,
        weekly_cap: int = TRADER_REPORT_WEEKLY_HISTORY_CAP,
        on_demand_retention_days: int = TRADER_REPORT_ONDEMAND_RETENTION_DAYS,
    ) -> Dict[str, int]:
        """Enforce SD §9.3 retention on write:
        - Keep at most ``weekly_cap`` (26) weekly reports per trader and strategy; delete older ones.
        - Delete on-demand reports older than ``on_demand_retention_days`` (7) days.
        """
        ref_dt = _parse_rfc3339(now or _utc_now_rfc3339()) or datetime.now(timezone.utc)
        cutoff = ref_dt - timedelta(days=int(on_demand_retention_days))
        rows = self._trader_reports.select_where(
            "payload ->> 'tenant_id' = %s AND payload ->> 'user_id' = %s",
            (str(tenant_id), str(user_id)),
        )
        removed = {"expired_on_demand": 0, "excess_weekly": 0}
        weekly_by_strategy: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            cadence = str(row.get("cadence") or "")
            if cadence == "on_demand":
                created_dt = _parse_rfc3339(row.get("created_at") or row.get("updated_at"))
                if created_dt is not None and created_dt < cutoff:
                    if self._trader_reports.delete_if_matches(str(row["report_id"]), row):
                        removed["expired_on_demand"] += 1
            elif cadence == "weekly":
                strat_key = str(row.get("strategy_id") or "*")
                if strategy_id is None or strat_key == str(strategy_id or "*"):
                    weekly_by_strategy.setdefault(strat_key, []).append(row)

        for _strat, weekly_rows in weekly_by_strategy.items():
            weekly_rows.sort(
                key=lambda r: (
                    str(r.get("iso_week") or ""),
                    str(r.get("created_at") or ""),
                    str(r.get("report_id") or ""),
                ),
                reverse=True,
            )
            for excess in weekly_rows[max(1, int(weekly_cap)) :]:
                if self._trader_reports.delete_if_matches(str(excess["report_id"]), excess):
                    removed["excess_weekly"] += 1
        removed["reports"] = removed["expired_on_demand"] + removed["excess_weekly"]
        return removed

    def purge_expired_trader_reports(
        self,
        *,
        now: Optional[str] = None,
        on_demand_retention_days: int = TRADER_REPORT_ONDEMAND_RETENTION_DAYS,
        weekly_cap: int = TRADER_REPORT_WEEKLY_HISTORY_CAP,
    ) -> Dict[str, int]:
        """Periodic cleanup path called by agora-interaction-worker."""
        ref_dt = _parse_rfc3339(now or _utc_now_rfc3339()) or datetime.now(timezone.utc)
        cutoff = ref_dt - timedelta(days=int(on_demand_retention_days))
        removed = {"expired_on_demand": 0, "excess_weekly": 0}
        for row in self._trader_reports.select_where("payload ->> 'cadence' = %s", ("on_demand",)):
            created_dt = _parse_rfc3339(row.get("created_at") or row.get("updated_at"))
            if created_dt is not None and created_dt < cutoff:
                if self._trader_reports.delete_if_matches(str(row["report_id"]), row):
                    removed["expired_on_demand"] += 1
        weekly_groups: Dict[tuple[str, str, str], List[Dict[str, Any]]] = {}
        for row in self._trader_reports.select_where("payload ->> 'cadence' = %s", ("weekly",)):
            key = (
                str(row.get("tenant_id") or ""),
                str(row.get("user_id") or ""),
                str(row.get("strategy_id") or "*"),
            )
            weekly_groups.setdefault(key, []).append(row)
        for _key, weekly_rows in weekly_groups.items():
            weekly_rows.sort(
                key=lambda r: (
                    str(r.get("iso_week") or ""),
                    str(r.get("created_at") or ""),
                    str(r.get("report_id") or ""),
                ),
                reverse=True,
            )
            for excess in weekly_rows[max(1, int(weekly_cap)) :]:
                if self._trader_reports.delete_if_matches(str(excess["report_id"]), excess):
                    removed["excess_weekly"] += 1
        removed["reports"] = removed["expired_on_demand"] + removed["excess_weekly"]
        return removed

    def count_on_demand_trader_reports_for_day(
        self,
        tenant_id: str,
        user_id: str,
        report_day: str,
        *,
        now: Optional[str] = None,
    ) -> int:
        self.prune_trader_reports(tenant_id, user_id, now=now)
        rows = self._trader_reports.select_where(
            "payload ->> 'tenant_id' = %s AND payload ->> 'user_id' = %s AND payload ->> 'cadence' = %s AND payload ->> 'report_day' = %s",
            (str(tenant_id), str(user_id), "on_demand", str(report_day)),
        )
        return len(rows)

    def put_trader_report(
        self,
        report: Dict[str, Any],
        *,
        now: Optional[str] = None,
        enforce_daily_cap: bool = True,
    ) -> tuple[bool, DictRecord]:
        """Store or update one trader report while enforcing SD §9.3 caps and cleanup."""
        ts = str(now or report.get("updated_at") or report.get("created_at") or _utc_now_rfc3339())
        tenant_id = str(report.get("tenant_id") or "").strip()
        user_id = str(report.get("user_id") or "").strip()
        strategy_id = str(report.get("strategy_id") or "*").strip() or "*"
        cadence = str(report.get("cadence") or "on_demand").strip()
        report_id = str(report.get("report_id") or "").strip()
        if not tenant_id or not user_id or not report_id:
            raise ValueError("tenant_id, user_id, and report_id are required for trader report")
        report_day = str(report.get("report_day") or ts[:10]).strip()
        iso_week_val = str(report.get("iso_week") or "").strip()

        self.prune_trader_reports(tenant_id, user_id, strategy_id=strategy_id, now=ts)

        existing = self._trader_reports.get(report_id)
        if existing is not None:
            if str(existing.get("tenant_id")) != tenant_id or str(existing.get("user_id")) != user_id:
                raise ServantSessionConflictError(report_id)
            merged = self._enforce_trader_report_byte_cap({
                **existing,
                **report,
                "report_id": report_id,
                "tenant_id": tenant_id,
                "user_id": user_id,
                "strategy_id": strategy_id,
                "cadence": cadence,
                "report_day": report_day,
                "iso_week": iso_week_val or existing.get("iso_week"),
                "created_at": existing.get("created_at") or ts,
                "updated_at": ts,
            })
            self._trader_reports.put(report_id, merged)
            self.prune_trader_reports(tenant_id, user_id, strategy_id=strategy_id, now=ts)
            return False, DictRecord(merged)

        if cadence == "weekly" and iso_week_val:
            same_week = self._trader_reports.select_where(
                "payload ->> 'tenant_id' = %s AND payload ->> 'user_id' = %s AND payload ->> 'strategy_id' = %s AND payload ->> 'cadence' = %s AND payload ->> 'iso_week' = %s",
                (tenant_id, user_id, strategy_id, "weekly", iso_week_val),
            )
            if same_week:
                prior = same_week[0]
                prior_id = str(prior["report_id"])
                merged = self._enforce_trader_report_byte_cap({
                    **prior,
                    **report,
                    "report_id": prior_id,
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "strategy_id": strategy_id,
                    "cadence": "weekly",
                    "report_day": report_day,
                    "iso_week": iso_week_val,
                    "created_at": prior.get("created_at") or ts,
                    "updated_at": ts,
                })
                self._trader_reports.put(prior_id, merged)
                self.prune_trader_reports(tenant_id, user_id, strategy_id=strategy_id, now=ts)
                return False, DictRecord(merged)

        if cadence == "on_demand" and enforce_daily_cap:
            today_count = self.count_on_demand_trader_reports_for_day(
                tenant_id, user_id, report_day, now=ts
            )
            if today_count >= TRADER_REPORT_ONDEMAND_DAILY_CAP:
                raise TraderReportDailyCapError(
                    f"On-demand trader report daily cap ({TRADER_REPORT_ONDEMAND_DAILY_CAP}/day) reached for {report_day}"
                )

        prepared = self._enforce_trader_report_byte_cap({
            **report,
            "report_id": report_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "strategy_id": strategy_id,
            "cadence": cadence,
            "report_day": report_day,
            "iso_week": iso_week_val,
            "status": str(report.get("status") or "queued"),
            "created_at": str(report.get("created_at") or ts),
            "updated_at": ts,
        })
        self._trader_reports.put(report_id, prepared)
        self.prune_trader_reports(tenant_id, user_id, strategy_id=strategy_id, now=ts)
        return True, DictRecord(prepared)

    def get_trader_report(
        self,
        report_id: str,
        *,
        tenant_id: str,
        user_id: str,
    ) -> Optional[DictRecord]:
        data = self._trader_reports.get(str(report_id))
        if data is None:
            return None
        if str(data.get("tenant_id")) != str(tenant_id) or str(data.get("user_id")) != str(user_id):
            return None
        return DictRecord(data)

    def list_trader_reports(
        self,
        tenant_id: str,
        user_id: str,
        *,
        strategy_id: Optional[str] = None,
        cadence: Optional[str] = None,
        limit: int = TRADER_REPORT_WEEKLY_HISTORY_CAP,
        now: Optional[str] = None,
    ) -> List[DictRecord]:
        self.prune_trader_reports(tenant_id, user_id, now=now)
        rows = self._trader_reports.select_where(
            "payload ->> 'tenant_id' = %s AND payload ->> 'user_id' = %s",
            (str(tenant_id), str(user_id)),
        )
        out: List[DictRecord] = []
        clean_strat = str(strategy_id or "").strip()
        clean_cadence = str(cadence or "").strip()
        for row in rows:
            if clean_strat and str(row.get("strategy_id") or "") != clean_strat:
                continue
            if clean_cadence and str(row.get("cadence") or "") != clean_cadence:
                continue
            out.append(DictRecord(row))
        out.sort(
            key=lambda r: (str(r.get("created_at") or ""), str(r.get("report_id") or "")),
            reverse=True,
        )
        return out[: max(1, int(limit))]

    def claim_trader_report(
        self,
        *,
        lease_owner: str,
        lease_seconds: float = 600,
        now: Optional[str] = None,
    ) -> Optional[DictRecord]:
        """Claim the oldest queued trader report job for agora-interaction-worker generation."""
        now = now or _utc_now_rfc3339()
        for status in ("queued", "running"):
            rows = self._trader_reports.select_where(
                "payload ->> 'status' = %s",
                (status,),
                tail_sql="ORDER BY payload ->> 'created_at' ASC LIMIT 25",
            )
            for row in sorted(rows, key=lambda r: (str(r.get("created_at")), str(r.get("report_id")))):
                if status == "running" and str(row.get("lease_expires_at") or "") > now:
                    continue
                claimed = {
                    **row,
                    "status": "running",
                    "attempts": int(row.get("attempts") or 0) + 1,
                    "lease_owner": lease_owner,
                    "lease_expires_at": _rfc3339_after(now, lease_seconds),
                    "updated_at": now,
                }
                swapped, _current = self._trader_reports.compare_and_set(str(row["report_id"]), row, claimed)
                if swapped:
                    return DictRecord(claimed)
        return None


def build_agora_store(
    *,
    dsn: Optional[str] = None,
    schema: str = "agora",
    bootstrap: bool = True,
) -> AgoraStore:
    """Factory creating an AgoraStore bound to Postgres storage."""
    selected_dsn = dsn or os.getenv("AGORA_STORE_DSN") or os.getenv("DATABASE_URL")
    if not selected_dsn:
        raise ValueError(
            "DATABASE_URL or AGORA_STORE_DSN is required for Postgres Agora write owner"
        )
    return AgoraStore(
        dsn=selected_dsn,
        schema=schema,
        bootstrap=bootstrap,
    )


__all__ = [
    "AgoraStore",
    "DictRecord",
    "SERVANT_HISTORY_MESSAGES",
    "SERVANT_MESSAGE_CAP",
    "SERVANT_SESSION_MODE",
    "SERVANT_SESSION_RETENTION_DAYS",
    "ServantSessionConflictError",
    "ServantSessionMessageCapError",
    "TRADER_REPORTS_TABLE",
    "TRADER_REPORT_MAX_BYTES",
    "TRADER_REPORT_ONDEMAND_DAILY_CAP",
    "TRADER_REPORT_ONDEMAND_RETENTION_DAYS",
    "TRADER_REPORT_WEEKLY_HISTORY_CAP",
    "TraderReportDailyCapError",
    "build_agora_store",
]
