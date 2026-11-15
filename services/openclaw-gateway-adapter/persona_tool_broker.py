"""Brokered persona tools for OpenClaw persona agents (design H9 / H10).

The ``pantheon-persona-tools`` gateway plugin registers three agent tools
(``persona_memory_recall``, ``persona_mental_model_read``, ``persona_proposal_submit``)
and forwards every call, with the host-provided agent id, run id and session key, to
this broker over the internal network. The broker is the only authority:

* it resolves the agent id to a persona through the persona agent registry
  (``agents.list``) and accepts only an agent whose tool profile is exactly the
  governed persona profile - a non-persona agent never reaches memory;
* it checks the per-persona tool profile deny-first;
* every run must be registered with a purpose (``daily_cycle`` or
  ``panel_interpretation``) before dispatch; a call from an unregistered run is refused;
* a panel-interpretation run sees only that persona's ``scope:committee`` entries and
  the tenant facts bank and has neither the standing-question tool nor the proposal tool;
* recall reaches memory-svc only for the persona's own bank or the tenant facts bank;
  the standing-question read returns only that persona's answers;
* a proposal is schema-validated and recorded, never applied;
* per-run call limits are enforced and every call is recorded (persona, run id,
  parameter digest, returned canonical ids, outcome) in a per-run record the controller reads.

There is no reflect tool: the persona reasons itself. No agent holds a memory or
persona-service credential, a Hindsight path, or a write path: this module holds the
single memory credential, and the plugin holds only the broker secret.

The module is transport-free: ``PersonaToolBroker.handle`` takes the request pieces,
an ``agent_lookup`` and a ``memory_get`` callable, so tests drive it without a gateway.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from integrations.openclaw.persona_agent_sync import (
    PERSONA_AGENT_TOOL_PROFILE,
    PERSONA_BROKERED_TOOLS,
    PERSONA_MEMORY_RECALL_TOOL,
    PERSONA_MENTAL_MODEL_READ_TOOL,
    PERSONA_PROPOSAL_TOOL,
    assert_persona_agent_tool_profile,
)
from integrations.openclaw.persona_memory_bridge import normalize_retrieval_hits
from integrations.openclaw.persona_ooda_runtime import CycleProposal

LOGGER = logging.getLogger("pantheon.persona_tool_broker")

PURPOSE_DAILY_CYCLE = "daily_cycle"
PURPOSE_PANEL_INTERPRETATION = "panel_interpretation"
PURPOSES = (PURPOSE_DAILY_CYCLE, PURPOSE_PANEL_INTERPRETATION)
# A panel-interpretation run is a pre-meeting read of what may be shared in the meeting.
PANEL_ALLOWED_TOOLS = frozenset({PERSONA_MEMORY_RECALL_TOOL})

# Tool name -> default calls per run. Numeric values are coordinator proposals.
DEFAULT_RUN_LIMITS = {
    PERSONA_MEMORY_RECALL_TOOL: 12,
    PERSONA_MENTAL_MODEL_READ_TOOL: 4,
    PERSONA_PROPOSAL_TOOL: 20,
}
DEFAULT_RECALL_LIMIT = 5
MAX_RECALL_LIMIT = 10
MAX_QUERY_CHARS = 1000

_PANEL_ACTOR_ROLES = ("consultation_session",)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class BrokerConfig:
    enabled: bool
    token: str
    memory_api_url: str = ""
    memory_auth_token: str = ""
    memory_actor_id: str = "persona-tool-broker"
    memory_actor_roles: tuple = ("operator",)
    run_ttl_seconds: int = 3600
    registry_ttl_seconds: float = 10.0
    state_path: str = ""
    limits: Mapping[str, int] = None  # type: ignore[assignment]

    @classmethod
    def from_env(cls) -> "BrokerConfig":
        limits = {
            tool: max(0, _env_int(f"PERSONA_TOOL_BROKER_MAX_{tool.upper()}_PER_RUN", default))
            for tool, default in DEFAULT_RUN_LIMITS.items()
        }
        roles = tuple(
            part.strip()
            for part in (os.getenv("PERSONA_TOOL_BROKER_MEMORY_ACTOR_ROLES") or "operator").split(",")
            if part.strip()
        ) or ("operator",)
        return cls(
            enabled=os.getenv("PERSONA_TOOL_BROKER_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"},
            token=os.getenv("PERSONA_TOOL_BROKER_TOKEN", ""),
            memory_api_url=(os.getenv("PANTHEON_MEMORY_API_URL") or os.getenv("PANTHEON_MEMORY_SERVICE_URL") or "").strip(),
            memory_auth_token=os.getenv("PANTHEON_MEMORY_AUTH_TOKEN", ""),
            memory_actor_id=os.getenv("PERSONA_TOOL_BROKER_MEMORY_ACTOR_ID", "persona-tool-broker"),
            memory_actor_roles=roles,
            run_ttl_seconds=max(60, _env_int("PERSONA_TOOL_BROKER_RUN_TTL_SECONDS", 3600)),
            registry_ttl_seconds=float(max(0, _env_int("PERSONA_TOOL_BROKER_REGISTRY_TTL_SECONDS", 10))),
            state_path=os.getenv("PERSONA_TOOL_BROKER_STATE_PATH", ""),
            limits=limits,
        )

    def limit_for(self, tool: str) -> int:
        limits = self.limits or DEFAULT_RUN_LIMITS
        return int(limits.get(tool, DEFAULT_RUN_LIMITS.get(tool, 0)))


@dataclass
class BrokerResponse:
    status_code: int
    body: Dict[str, Any]


class MemoryCallError(Exception):
    """memory-svc refused or was unreachable; the reason is recorded, never guessed."""

    def __init__(self, status_code: int, reason: str) -> None:
        super().__init__(reason)
        self.status_code = status_code
        self.reason = reason


class BrokerRefusal(Exception):
    def __init__(self, status_code: int, reason: str, message: str = "") -> None:
        super().__init__(reason)
        self.status_code = status_code
        self.reason = reason
        self.message = message or reason


# agent_lookup(agent_id) -> the agent's entry from the OpenClaw agents registry, or None.
AgentLookup = Callable[[str], Optional[Mapping[str, Any]]]
# memory_get(path, params, *, actor_roles) -> JSON body; raises MemoryCallError.
MemoryGet = Callable[..., Dict[str, Any]]


class _ProposalInput(CycleProposal):
    """The proposal-submit schema: the daily-cycle proposal plus its case-file citation."""

    case_file_item_ids: List[str] = Field(default_factory=list, max_length=100)
    none_applicable_reason: Optional[str] = Field(default=None, max_length=2000)


class _RecallInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    query: str = Field(min_length=1, max_length=MAX_QUERY_CHARS, pattern=r"\S")
    bank: str = Field(default="own", pattern=r"^(own|facts)$")
    limit: int = Field(default=DEFAULT_RECALL_LIMIT, ge=1, le=MAX_RECALL_LIMIT)
    tags: List[str] = Field(default_factory=list, max_length=10)


class _NoInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def _digest(params: Any) -> str:
    return hashlib.sha256(
        json.dumps(params, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class _Store:
    """Run registrations and per-run call records; sqlite-backed when a path is configured."""

    def __init__(self, path: str) -> None:
        self._lock = threading.RLock()
        if path:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._db = sqlite3.connect(path or ":memory:", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock, self._db:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_ref TEXT PRIMARY KEY, persona_id TEXT NOT NULL, purpose TEXT NOT NULL,
                    session_user TEXT NOT NULL, registered_at TEXT NOT NULL, expires_at REAL NOT NULL,
                    bound_run_id TEXT);
                CREATE TABLE IF NOT EXISTS calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_ref TEXT NOT NULL, run_id TEXT NOT NULL,
                    tool TEXT NOT NULL, persona_id TEXT NOT NULL, param_digest TEXT NOT NULL,
                    canonical_ids TEXT NOT NULL, outcome TEXT NOT NULL, reason TEXT NOT NULL,
                    proposal TEXT, at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS calls_run ON calls(run_ref);
                """
            )

    def register(self, run_ref: str, persona_id: str, purpose: str, session_user: str, expires_at: float, now: float) -> None:
        with self._lock, self._db:
            live = self._db.execute(
                "SELECT 1 FROM runs WHERE session_user=? AND expires_at>?", (session_user, now)
            ).fetchone()
            if live:
                raise BrokerRefusal(409, "session_user_already_registered")
            self._db.execute(
                "INSERT INTO runs(run_ref, persona_id, purpose, session_user, registered_at, expires_at) VALUES(?,?,?,?,?,?)",
                (run_ref, persona_id, purpose, session_user, _utc_now(), expires_at),
            )

    def find_for_session(self, persona_id: str, session_key: str, now: float) -> Optional[Dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM runs WHERE persona_id=? AND expires_at>?", (persona_id, now)
            ).fetchall()
        matches = [
            dict(r) for r in rows
            if session_key == r["session_user"] or session_key.endswith(":" + r["session_user"])
        ]
        return matches[0] if len(matches) == 1 else None

    def bind_run_id(self, run_ref: str, run_id: str) -> bool:
        with self._lock, self._db:
            row = self._db.execute("SELECT bound_run_id FROM runs WHERE run_ref=?", (run_ref,)).fetchone()
            if row is None:
                return False
            if row["bound_run_id"] is None:
                self._db.execute("UPDATE runs SET bound_run_id=? WHERE run_ref=?", (run_id, run_ref))
                return True
            return row["bound_run_id"] == run_id

    def count(self, run_ref: str, tool: str) -> int:
        with self._lock:
            return int(self._db.execute(
                "SELECT COUNT(*) FROM calls WHERE run_ref=? AND tool=? AND reason!='run_call_limit_exceeded'",
                (run_ref, tool),
            ).fetchone()[0])

    def record(self, run_ref: str, run_id: str, tool: str, persona_id: str, digest: str,
               canonical_ids: List[str], outcome: str, reason: str, proposal: Optional[Dict[str, Any]]) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO calls(run_ref, run_id, tool, persona_id, param_digest, canonical_ids, outcome, reason, proposal, at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (run_ref, run_id, tool, persona_id, digest, json.dumps(canonical_ids), outcome, reason,
                 json.dumps(proposal, ensure_ascii=False, sort_keys=True) if proposal is not None else None, _utc_now()),
            )

    def run_record(self, run_ref: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            run = self._db.execute("SELECT * FROM runs WHERE run_ref=?", (run_ref,)).fetchone()
            if run is None:
                return None
            calls = self._db.execute("SELECT * FROM calls WHERE run_ref=? ORDER BY id", (run_ref,)).fetchall()
        return {
            "run_ref": run["run_ref"],
            "persona_id": run["persona_id"],
            "purpose": run["purpose"],
            "registered_at": run["registered_at"],
            "run_id": run["bound_run_id"],
            "calls": [
                {
                    "seq": index + 1,
                    "tool": c["tool"],
                    "param_digest": c["param_digest"],
                    "canonical_ids": json.loads(c["canonical_ids"]),
                    "outcome": c["outcome"],
                    "reason": c["reason"],
                    "at": c["at"],
                }
                for index, c in enumerate(calls)
            ],
            # Proposals are taken only from this record, never from model text.
            "proposals": [json.loads(c["proposal"]) for c in calls if c["proposal"] and c["outcome"] == "recorded"],
        }


class PersonaToolBroker:
    def __init__(
        self,
        config: BrokerConfig,
        *,
        agent_lookup: AgentLookup,
        memory_get: MemoryGet,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self._agent_lookup = agent_lookup
        self._memory_get = memory_get
        self._clock = clock
        self._store = _Store(config.state_path)
        self._registry_cache: Dict[str, Any] = {}
        self._registry_lock = threading.Lock()
        self.telemetry: List[Dict[str, Any]] = []

    # -- telemetry: persona, run id, parameter digest, canonical ids, outcome; never memory text --
    def _telemetry(self, **event: Any) -> None:
        event["event"] = "persona_tool_call"
        self.telemetry.append(event)
        del self.telemetry[:-1000]
        LOGGER.info(json.dumps(event, sort_keys=True, default=str))

    def _authenticated(self, authorization: Optional[str]) -> bool:
        scheme, _, presented = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not presented.strip() or not self.config.token:
            return False
        return hmac.compare_digest(presented.strip().encode(), self.config.token.encode())

    # -- persona agent registry --
    def resolve_agent(self, agent_id: str) -> Dict[str, Any]:
        """The registered persona agent and its tool profile, or a refusal.

        A persona agent is one whose registry entry carries exactly the governed persona
        tool profile; the persona id is the agent id (`openclaw agents add <persona_id>`).
        """
        now = self._clock()
        with self._registry_lock:
            cached = self._registry_cache.get(agent_id)
            if cached and now - cached[0] <= self.config.registry_ttl_seconds:
                entry = cached[1]
            else:
                try:
                    entry = self._agent_lookup(agent_id)
                except Exception as exc:  # noqa: BLE001
                    raise BrokerRefusal(503, "agent_registry_unavailable", str(exc)[:200]) from exc
                self._registry_cache[agent_id] = (now, entry)
        if not isinstance(entry, Mapping) or str(entry.get("id") or "") != agent_id:
            raise BrokerRefusal(403, "unregistered_agent")
        profile = entry.get("tools") if isinstance(entry.get("tools"), Mapping) else {}
        try:
            assert_persona_agent_tool_profile(profile)
        except ValueError as exc:
            raise BrokerRefusal(403, "not_a_persona_agent", str(exc)) from exc
        return {"persona_id": agent_id, "profile": dict(profile)}

    @staticmethod
    def _profile_allows(profile: Mapping[str, Any], tool: str) -> bool:
        deny = {str(t) for t in profile.get("deny") or []}
        if tool in deny or "*" in deny:
            return False  # deny wins
        return tool in {str(t) for t in profile.get("allow") or []}

    # -- run registration (called by the adapter before dispatch) --
    def register_run(self, persona_id: str, purpose: str, session_user: str) -> Dict[str, Any]:
        if purpose not in PURPOSES:
            raise BrokerRefusal(422, "invalid_purpose", f"purpose must be one of {', '.join(PURPOSES)}")
        persona_id = str(persona_id or "").strip()
        session_user = str(session_user or "").strip()
        if not persona_id or not session_user:
            raise BrokerRefusal(422, "persona_and_session_required")
        self._registry_cache.pop(persona_id, None)  # registration always re-reads the registry
        self.resolve_agent(persona_id)
        run_ref = "ptr-" + uuid.uuid4().hex
        now = self._clock()
        self._store.register(run_ref, persona_id, purpose, session_user, now + self.config.run_ttl_seconds, now)
        return {"run_ref": run_ref, "persona_id": persona_id, "purpose": purpose}

    def run_record(self, run_ref: str) -> Optional[Dict[str, Any]]:
        return self._store.run_record(run_ref)

    # -- internal HTTP surface: authorize (no side effect) and call --
    def handle(self, *, authorization: Optional[str], body: Any, authorize_only: bool = False) -> BrokerResponse:
        if not self.config.enabled or not self.config.token:
            return BrokerResponse(404, {"error": "persona_tool_broker_disabled"})
        if not self._authenticated(authorization):
            self._telemetry(outcome="denied", reason="unauthorized")
            return BrokerResponse(401, {"error": "unauthorized"})
        if not isinstance(body, dict):
            return BrokerResponse(422, {"error": "invalid_body", "reason": "body_not_object"})
        tool = body.get("tool")
        agent_id = body.get("agent_id")
        run_id = body.get("run_id")
        session_key = body.get("session_key")
        params = body.get("params", {})
        if not all(isinstance(v, str) and v.strip() for v in (tool, agent_id, run_id, session_key)) or not isinstance(params, dict):
            self._telemetry(outcome="rejected", reason="identity_fields_required")
            return BrokerResponse(422, {"error": "invalid_body", "reason": "identity_fields_required"})
        tool, agent_id, run_id, session_key = tool.strip(), agent_id.strip(), run_id.strip(), session_key.strip()
        digest = _digest(params)
        persona_id = ""
        run: Optional[Dict[str, Any]] = None
        try:
            agent = self.resolve_agent(agent_id)
            persona_id = agent["persona_id"]
            if tool not in PERSONA_BROKERED_TOOLS or not self._profile_allows(agent["profile"], tool):
                raise BrokerRefusal(403, "tool_outside_profile")
            run = self._store.find_for_session(persona_id, session_key, self._clock())
            if run is None:
                raise BrokerRefusal(403, "run_not_registered")
            if not self._store.bind_run_id(run["run_ref"], run_id):
                raise BrokerRefusal(403, "run_id_mismatch")
            if run["purpose"] == PURPOSE_PANEL_INTERPRETATION and tool not in PANEL_ALLOWED_TOOLS:
                raise BrokerRefusal(403, "tool_not_allowed_for_purpose")
            if authorize_only:
                return BrokerResponse(200, {"allowed": True, "persona_id": persona_id, "purpose": run["purpose"],
                                            "allowed_tools": [t for t in agent["profile"]["allow"]
                                                              if t in PERSONA_BROKERED_TOOLS
                                                              and (run["purpose"] == PURPOSE_DAILY_CYCLE or t in PANEL_ALLOWED_TOOLS)]})
            if self._store.count(run["run_ref"], tool) >= self.config.limit_for(tool):
                raise BrokerRefusal(429, "run_call_limit_exceeded")
            result, canonical_ids, proposal = self._execute(tool, params, persona_id, run)
        except BrokerRefusal as refusal:
            self._finish(run, run_id, tool, persona_id, digest, [], "refused", refusal.reason, None)
            return BrokerResponse(refusal.status_code, {"error": "refused", "reason": refusal.reason,
                                                        "message": refusal.message})
        except MemoryCallError as exc:
            self._finish(run, run_id, tool, persona_id, digest, [], "unavailable", exc.reason, None)
            return BrokerResponse(503 if exc.status_code >= 500 or exc.status_code == 0 else exc.status_code,
                                  {"error": "unavailable", "reason": exc.reason})
        self._finish(run, run_id, tool, persona_id, digest, canonical_ids,
                     "recorded" if tool == PERSONA_PROPOSAL_TOOL else "ok", "", proposal)
        return BrokerResponse(200, {"status": "ok", "tool": tool, "persona_id": persona_id,
                                    "run_ref": run["run_ref"], "result": result})

    def _finish(self, run: Optional[Dict[str, Any]], run_id: str, tool: str, persona_id: str, digest: str,
                canonical_ids: List[str], outcome: str, reason: str, proposal: Optional[Dict[str, Any]]) -> None:
        self._telemetry(persona_id=persona_id, run_id=run_id, tool=tool, param_digest=digest,
                        canonical_ids=canonical_ids, outcome=outcome, reason=reason,
                        run_ref=(run or {}).get("run_ref"))
        if run is not None:  # a call outside any registered run has no per-run record, only telemetry
            self._store.record(run["run_ref"], run_id, tool, persona_id, digest, canonical_ids, outcome, reason, proposal)

    # -- tools --
    def _execute(self, tool: str, params: Dict[str, Any], persona_id: str, run: Dict[str, Any]):
        if tool == PERSONA_MEMORY_RECALL_TOOL:
            return self._recall(params, persona_id, run)
        if tool == PERSONA_MENTAL_MODEL_READ_TOOL:
            return self._mental_model(params, persona_id, run)
        return self._proposal(params, persona_id, run)

    def _actor(self, run: Dict[str, Any]) -> Dict[str, Any]:
        roles = _PANEL_ACTOR_ROLES if run["purpose"] == PURPOSE_PANEL_INTERPRETATION else self.config.memory_actor_roles
        return {"actor_id": self.config.memory_actor_id, "actor_roles": list(roles), "session_id": run["run_ref"]}

    def _recall(self, params: Dict[str, Any], persona_id: str, run: Dict[str, Any]):
        try:
            args = _RecallInput.model_validate(params)
        except ValidationError as exc:
            raise BrokerRefusal(422, "invalid_parameters", _validation_message(exc)) from exc
        query = {**self._actor(run), "query": args.query, "limit": args.limit}
        if args.tags:
            query["tags"] = list(args.tags)
        if args.bank == "own":
            query.update(scope="persona", persona_id=persona_id)
        else:  # the tenant facts bank: a separate scoped call, never mixed with the own bank
            query.update(scope="institutional")
        payload = self._memory_get("/api/memory/retrieve", query, actor_roles=query["actor_roles"])
        hits, rejected = normalize_retrieval_hits(payload, persona_id=persona_id, max_hits=args.limit)
        want = "persona" if args.bank == "own" else "institutional"
        stray = [h for h in hits if h["type"] != want]
        if stray:  # the route returned a bank that was not asked for: never pass it on
            rejected.extend({"type": h["type"], "source_id": h["source_id"], "reason": "bank_mismatch"} for h in stray)
            hits = [h for h in hits if h["type"] == want]
        if run["purpose"] == PURPOSE_PANEL_INTERPRETATION and args.bank == "own":
            leaked = [h for h in hits if h.get("relevance_scope") != "persona_and_committee"]
            if leaked:  # private entries never reach a panel run, whatever memory-svc returned
                rejected.extend({"type": "persona", "source_id": h["source_id"], "reason": "not_committee_scope"} for h in leaked)
                hits = [h for h in hits if h.get("relevance_scope") == "persona_and_committee"]
        canonical_ids = [h["canonical_ref"] for h in hits]
        if rejected:  # ids of filtered entries stay in telemetry; the agent only learns that some were withheld
            self._telemetry(persona_id=persona_id, run_ref=run["run_ref"], tool=PERSONA_MEMORY_RECALL_TOOL,
                            outcome="filtered", reason=",".join(sorted({r["reason"] for r in rejected})),
                            withheld=[r["source_id"] for r in rejected])
        return ({"bank": args.bank, "count": len(hits), "hits": hits, "withheld": len(rejected),
                 "retrieval_mode": payload.get("retrieval_mode"), "fallback_reason": payload.get("fallback_reason")},
                canonical_ids, None)

    def _mental_model(self, params: Dict[str, Any], persona_id: str, run: Dict[str, Any]):
        try:
            _NoInput.model_validate(params)
        except ValidationError as exc:
            raise BrokerRefusal(422, "invalid_parameters", _validation_message(exc)) from exc
        payload = self._memory_get(
            "/api/memory/persona-standing-questions",
            {**self._actor(run), "persona_id": persona_id},
            actor_roles=self._actor(run)["actor_roles"],
        )
        if str(payload.get("persona_id") or "") != persona_id:
            raise MemoryCallError(502, "persona_mismatch")  # fail closed: never return another persona's answers
        answers = [a for a in payload.get("answers") or [] if isinstance(a, dict)]
        canonical_ids = sorted({str(c) for a in answers for c in (a.get("citations") or [])})
        return ({"derived": True, "count": len(answers), "answers": answers}, canonical_ids, None)

    def _proposal(self, params: Dict[str, Any], persona_id: str, run: Dict[str, Any]):
        try:
            proposal = _ProposalInput.model_validate(params)
        except ValidationError as exc:
            raise BrokerRefusal(422, "invalid_parameters", _validation_message(exc)) from exc
        cited = bool(proposal.case_file_item_ids)
        stated = bool((proposal.none_applicable_reason or "").strip())
        if cited == stated:
            raise BrokerRefusal(422, "invalid_parameters",
                                "cite case_file_item_ids or state none_applicable_reason, not both or neither")
        record = proposal.model_dump(mode="json")
        # Recorded only: nothing is applied here; the controller reads the per-run record
        # and the owning services' gates decide.
        return ({"recorded": True, "applied": False, "proposal_index": self._store.count(run["run_ref"], PERSONA_PROPOSAL_TOOL)},
                [], record)


def _validation_message(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())[:400]


def http_memory_get(config: BrokerConfig, *, timeout_seconds: float = 8.0) -> MemoryGet:
    """The single memory-svc reader: GET with this module's own credential."""
    import urllib.error
    import urllib.parse
    import urllib.request

    def get(path: str, params: Mapping[str, Any], *, actor_roles: Any = None) -> Dict[str, Any]:
        if not config.memory_api_url:
            raise MemoryCallError(503, "memory_api_unconfigured")
        url = f"{config.memory_api_url.rstrip('/')}{path}?{urllib.parse.urlencode(dict(params), doseq=True)}"
        headers = {"Accept": "application/json"}
        if config.memory_auth_token:
            headers["Authorization"] = f"Bearer {config.memory_auth_token}"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers, method="GET"), timeout=timeout_seconds) as resp:
                return json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            reason = f"memory_http_{exc.code}"
            try:
                detail = json.loads(exc.read().decode("utf-8") or "{}").get("detail")
                if isinstance(detail, dict):
                    reason = str(detail.get("reason") or detail.get("error") or reason)[:120]
            except (ValueError, AttributeError):
                pass
            raise MemoryCallError(exc.code, reason) from exc
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise MemoryCallError(503, "memory_unreachable") from exc

    return get


# Re-exported so the adapter and tests share one profile definition.
__all__ = [
    "BrokerConfig", "BrokerResponse", "BrokerRefusal", "MemoryCallError", "PersonaToolBroker",
    "PURPOSES", "PURPOSE_DAILY_CYCLE", "PURPOSE_PANEL_INTERPRETATION", "PERSONA_AGENT_TOOL_PROFILE",
    "http_memory_get",
]
