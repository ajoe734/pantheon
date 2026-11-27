"""Servant habit profile: facts, private interpretations, read and correct
(AGORA-TRADER-HABIT-PROFILE-20261127, SD_AGORA_SERVANT_CORE_2026-11-27 §6).

Two layers, with different owners and different audiences:

* **Facts** — the deterministic habit profile (``services.agora.habit_profile``). Owned by the
  Agora owner, derived from the trader's own decisions, shared within the tenant, every feature
  carries its sample size. No language model computes a feature.
* **Interpretations** — the servant's *reading* of those facts. They are written only to the
  servant persona's private memory (``persona_private`` scope, memory type ``preference``) and
  never tagged ``scope:committee``. Another persona and a meeting cannot read them; they cross
  only through meeting minutes (``carry_interpretation_in_minutes``).

The trader reads both through ``GET /bff/agora/servant/habits`` and corrects an interpretation
with ``PATCH``. A correction is stored as ``training_example`` evidence (the existing dataset
pipeline, SD §7) and the interpretation is superseded in memory, never deleted. The servant turn
carries the profile and the live interpretations, so the next answer reflects the correction.

Nothing here trades, calls a persona or a meeting, or computes a number with a model.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from fastapi import APIRouter, Body, Cookie, Header, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from services.agora.habit_profile import (
    ALL_STRATEGIES,
    FEATURE_KEYS,
    FEATURE_LABELS,
    HABIT_MIN_SAMPLE,
    feature_by_key,
    profile_key,
)

logger = logging.getLogger("agora.servant.habits")

INTERPRETATION_TAG = "agora_habit_interpretation"
INTERPRETATION_TTL_DAYS = 180  # derived readings are re-derived from facts after expiry
CORRECTION_TTL_DAYS = 365  # a trader's own words outlive the derived ones
MAX_ACTIVE_INTERPRETATIONS = 24  # cap on live private entries per servant persona
CORRECTION_STATEMENT_MAX_CHARS = 300
FACT_TEXT_MAX_CHARS = 160
SERVANT_HABITS_CAPABILITY = "agora.servant.habits.v1"
_MEMORY_WRITER_ROLES = ("persona_session", "memory-writer")
_CORRECTION_ACTIONS = ("reject", "replace", "add")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _after(base: str, days: int) -> str:
    start = datetime.fromisoformat(base.replace("Z", "+00:00"))
    return (start + timedelta(days=days)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _digest(*parts: str, size: int = 20) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:size]


# ---------------------------------------------------------------------------- interpretations

_FAMILY_NAMES = {"chips": "籌碼", "price": "價格", "news": "新聞公告", "fundamental": "基本面"}


def _value(profile: Mapping[str, Any], key: str) -> Optional[Mapping[str, Any]]:
    feature = feature_by_key(profile, key)
    if feature is None or not feature.get("shown") or not isinstance(feature.get("value"), Mapping):
        return None
    return feature["value"]


def derive_interpretations(profile: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """The servant's deterministic reading of the *shown* habit facts.

    Statements carry no digits: the numbers stay in the cited facts (11/16 rule), so a
    statement can never disagree with the profile it reads.
    """
    out: List[Dict[str, Any]] = []

    def add(feature: str, rule: str, statement: str) -> None:
        out.append({
            "interpretation_id": f"hint-{_digest(str(profile.get('profile_key')), feature, rule, size=16)}",
            "feature_key": feature,
            "rule": rule,
            "statement": statement,
            "fact_refs": [feature],
            "origin": "servant",
        })

    hold = _value(profile, "holding_period")
    if hold:
        if hold["median"] <= 5:
            add("holding_period", "short_hold", "您偏向短線持有")
        elif hold["median"] >= 20:
            add("holding_period", "long_hold", "您偏向波段持有")
    timing = _value(profile, "entry_timing")
    if timing:
        if timing["median"] <= 30:
            add("entry_timing", "fast_entry", "您通常在訊號出現後很快進場")
        elif timing["median"] >= 1440:
            add("entry_timing", "slow_entry", "您通常等訊號出現隔天以後才進場")
    rules = _value(profile, "rule_discipline")
    if rules:
        if rules["follow_rate"] >= 0.8:
            add("rule_discipline", "follows_rule", "您大多照規則的建議執行")
        elif rules["follow_rate"] <= 0.5:
            add("rule_discipline", "deviates_rule", "您常偏離規則的建議")
    size = _value(profile, "position_size")
    if size and size["p25"] > 0 and size["p75"] / size["p25"] <= 1.5:
        add("position_size", "steady_size", "您的進場部位大小相當固定")
    rejected = _value(profile, "rejected_candidates")
    if rejected:
        if rejected.get("low_liquidity_share", 0) >= 0.6:
            add("rejected_candidates", "rejects_illiquid", "您常剔除低流動性的候選")
        if rejected.get("top_sector") and rejected.get("top_sector_share", 0) >= 0.5:
            add("rejected_candidates", "rejects_sector", f"您常剔除{rejected['top_sector']}類股的候選")
    order = _value(profile, "data_order")
    if order and order.get("first_family_share", 0) >= 0.6:
        family = str(order["first_family"])
        if family == "chips":
            add("data_order", "chips_first", "您偏好先看籌碼再看價格")
        elif family == "price":
            add("data_order", "price_first", "您偏好先看價格再看籌碼")
        else:
            add("data_order", f"{family}_first", f"您偏好先看{_FAMILY_NAMES.get(family, family)}")
    inter = _value(profile, "intervention")
    if inter:
        if inter["intervention_rate"] >= 0.3:
            add("intervention", "intervenes_often", "您常介入調整規則的建議")
        if inter.get("size_down_share", 0) >= 0.6:
            add("intervention", "intervenes_smaller", "您介入時多半縮小部位")
        elif inter.get("size_up_share", 0) >= 0.6:
            add("intervention", "intervenes_larger", "您介入時多半加大部位")
    return out


# ---------------------------------------------------------------------------- private memory


class InProcessHabitMemory:
    """Servant private memory over a ``PersonaMemoryStore`` (tests and single-process use)."""

    def __init__(self, store: Any = None) -> None:
        from services.memory.persona_memory_store import PersonaMemoryStore

        self.store = store if store is not None else PersonaMemoryStore()

    def list_entries(self, *, tenant_id: str, persona_id: str) -> List[Dict[str, Any]]:
        entries = self.store.list(persona_id=persona_id, memory_type="preference", active_only=True)
        return [
            e.to_dict() for e in entries
            if INTERPRETATION_TAG in (e.content.get("tags") or []) and str(e.tenant_id or "") == str(tenant_id)
        ]

    def write(self, entry: Dict[str, Any], *, supersedes: Optional[str] = None) -> Dict[str, Any]:
        from services.memory.persona_memory_store import PersonaMemoryEntry

        saved = self.store.create(PersonaMemoryEntry.from_dict(entry))
        if supersedes:
            self.store.supersede(supersedes, saved.memory_id)
        return saved.to_dict()


class HttpHabitMemory:
    """Servant private memory through the memory owner's persona routes.

    The memory owner has no supersede route, so a replacement entry lists what it supersedes in
    ``content.structured_payload.supersedes``; readers hide those entries. Nothing is deleted.
    """

    URL_ENVS = ("PANTHEON_MEMORY_API_URL", "PANTHEON_MEMORY_SERVICE_URL", "MEMORY_URL")
    SECRET_ENVS = ("PANTHEON_RUNTIME_JWT_SECRET", "PANTHEON_BFF_JWT_SECRET")
    TOKEN_ENVS = ("PANTHEON_AGORA_SERVANT_MEMORY_TOKEN",)

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        jwt_secret: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        urlopen: Optional[Callable[..., Any]] = None,
    ) -> None:
        env_url = next((os.getenv(n, "").strip() for n in self.URL_ENVS if os.getenv(n, "").strip()), "")
        self.base_url = (base_url if base_url is not None else env_url).rstrip("/")
        self._token = token if token is not None else next(
            (os.getenv(n, "").strip() for n in self.TOKEN_ENVS if os.getenv(n, "").strip()), ""
        )
        self._secret = jwt_secret if jwt_secret is not None else next(
            (os.getenv(n, "").strip() for n in self.SECRET_ENVS if os.getenv(n, "").strip()), ""
        )
        self.timeout_seconds = float(
            timeout_seconds if timeout_seconds is not None
            else os.getenv("PANTHEON_AGORA_SERVANT_MEMORY_TIMEOUT_SECONDS", "5")
        )
        self._urlopen = urlopen or urllib.request.urlopen

    @property
    def configured(self) -> bool:
        return bool(self.base_url and (self._token or self._secret))

    def _bearer(self, tenant_id: str, persona_id: str) -> str:
        if self._token:
            return self._token
        from services.runtime_auth_inbound import encode_jwt_hs256

        now = int(time.time())
        claims: Dict[str, Any] = {
            "sub": f"agora-servant-habits:{persona_id}",
            "roles": list(_MEMORY_WRITER_ROLES),
            "tenant_id": tenant_id,
            "iat": now,
            "exp": now + 120,
        }
        for claim, names in (
            ("iss", ("PANTHEON_RUNTIME_JWT_ISSUER", "PANTHEON_BFF_JWT_ISSUER")),
            ("aud", ("PANTHEON_RUNTIME_JWT_AUDIENCE", "PANTHEON_BFF_JWT_AUDIENCE")),
        ):
            value = next((os.getenv(n, "").strip() for n in names if os.getenv(n, "").strip()), "")
            if value:
                claims[claim] = value
        return encode_jwt_hs256(claims, secret=self._secret)

    def _call(self, method: str, path: str, *, tenant_id: str, persona_id: str, payload: Any = None) -> Dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8") if payload is not None else None,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._bearer(tenant_id, persona_id)}",
                "X-Tenant-Id": tenant_id,
            },
            method=method,
        )
        with self._urlopen(request, timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8") or "{}")

    def list_entries(self, *, tenant_id: str, persona_id: str) -> List[Dict[str, Any]]:
        query = urllib.parse.urlencode({
            "actor_id": f"agora-servant-habits:{persona_id}",
            "actor_roles": "persona_session",
            "session_id": f"agora-servant-habits:{persona_id}",
            "persona_id": persona_id,
            "session_persona_id": persona_id,
            "scope": "persona",
            "memory_type": "preference",
            "tags": INTERPRETATION_TAG,
            "limit": 100,
        })
        result = self._call("GET", f"/api/memory/retrieve?{query}", tenant_id=tenant_id, persona_id=persona_id)
        entries = []
        for hit in result.get("hits") or []:
            entry = hit.get("entry") if isinstance(hit, Mapping) else None
            if (
                isinstance(entry, dict)
                and hit.get("type") == "persona"
                and entry.get("persona_id") == persona_id
                and str(entry.get("tenant_id") or "") == str(tenant_id)
                and INTERPRETATION_TAG in ((entry.get("content") or {}).get("tags") or [])
            ):
                entries.append(entry)
        return entries

    def write(self, entry: Dict[str, Any], *, supersedes: Optional[str] = None) -> Dict[str, Any]:
        self._call(
            "POST", "/api/memory/persona-entries",
            tenant_id=str(entry["tenant_id"]), persona_id=str(entry["persona_id"]), payload=entry,
        )
        return entry


def build_default_habit_memory() -> Optional[Any]:
    """Memory owner HTTP adapter when configured; None means interpretations are unavailable."""
    memory = HttpHabitMemory()
    return memory if memory.configured else None


def carry_interpretation_in_minutes(
    memory: Any,
    interpretation: Mapping[str, Any],
    *,
    tenant_id: str,
    persona_id: str,
    meeting_id: str,
    minutes_ref: str,
    written_at: Optional[str] = None,
) -> Dict[str, Any]:
    """The only path an interpretation leaves the servant: the trader's meeting minutes.

    A committee-visible copy (``persona_and_committee``, ``scope:committee``) is written for the
    meeting the trader convened; the private entry itself stays private.
    """
    now = written_at or _utc_now()
    entry = {
        "memory_id": f"hint-min-{_digest(tenant_id, persona_id, meeting_id, str(interpretation.get('interpretation_id')))}",
        "persona_id": persona_id,
        "tenant_id": tenant_id,
        "memory_type": "consultation_outcome",
        "content": {
            "summary": str(interpretation.get("statement") or ""),
            "tags": ["habit_interpretation_in_minutes"],
            "structured_payload": {"meeting_id": meeting_id, "minutes_ref": minutes_ref,
                                   "interpretation_id": interpretation.get("interpretation_id")},
        },
        "source_event_type": "consultation_closed",
        "source_event_id": meeting_id,
        "written_at": now,
        "write_authority": "consultation-svc",
        "relevance_scope": "persona_and_committee",
    }
    return memory.write(entry)


# ---------------------------------------------------------------------------- service


@dataclass
class HabitTurnContext:
    """What one servant turn carries about the trader's habits (cited as h1..hN)."""

    facts: List[Dict[str, Any]] = field(default_factory=list)
    ref: Dict[str, Any] = field(default_factory=dict)

    @property
    def fact_ids(self) -> List[str]:
        return [f["id"] for f in self.facts]


class HabitUnavailableError(Exception):
    """The private memory owner is not configured or failed; interpretations are not guessed."""


class HabitCorrectionError(Exception):
    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


class HabitService:
    """Composes the Agora habit facts with the servant's private interpretations."""

    def __init__(
        self,
        *,
        owner: Any,
        memory: Optional[Any] = None,
        journal_reader: Any = None,
        evidence_owner: Optional[Any] = None,
        clock: Callable[[], str] = _utc_now,
    ) -> None:
        self.owner = owner
        self.memory = memory
        self.journal_reader = journal_reader
        self.evidence_owner = evidence_owner
        self.clock = clock

    # -- facts ------------------------------------------------------------------

    def _journal_entries(self, tenant_id: str, user_id: str) -> List[Dict[str, Any]]:
        reader = getattr(self.journal_reader, "list_decision_journal_entries", None)
        if reader is None:
            return []
        try:
            try:
                entries = reader(tenant_id=tenant_id, user_id=user_id)
            except TypeError:
                entries = reader()
        except Exception as exc:
            logger.warning("habit journal read failed: %s", type(exc).__name__)
            return []
        return [
            dict(e) for e in entries or []
            if isinstance(e, Mapping)
            and str(e.get("user_id") or e.get("userId") or "") == str(user_id)
            and str(e.get("tenant_id") or e.get("tenantId") or tenant_id) == str(tenant_id)
        ]

    def refresh_profile(self, *, tenant_id: str, user_id: str, strategy_id: Optional[str] = None) -> Dict[str, Any]:
        return dict(self.owner.refresh_habit_profile(
            tenant_id=tenant_id, user_id=user_id, strategy_id=strategy_id,
            journal_entries=self._journal_entries(tenant_id, user_id),
            actor_roles=("trader",), computed_at=self.clock(),
        ))

    # -- private interpretations -------------------------------------------------

    @staticmethod
    def _hidden_ids(entries: Sequence[Mapping[str, Any]]) -> set:
        hidden = set()
        for entry in entries:
            habit = (entry.get("content") or {}).get("structured_payload") or {}
            hidden.update(str(x) for x in habit.get("supersedes") or [])
            if entry.get("superseded_by") or entry.get("archived_at"):
                hidden.add(str(entry.get("memory_id")))
        return hidden

    def _active_entries(self, tenant_id: str, persona_id: str) -> List[Dict[str, Any]]:
        if self.memory is None:
            raise HabitUnavailableError("servant_memory_unconfigured")
        try:
            entries = list(self.memory.list_entries(tenant_id=tenant_id, persona_id=persona_id))
        except HabitUnavailableError:
            raise
        except Exception as exc:
            raise HabitUnavailableError(f"servant_memory_unavailable:{type(exc).__name__}") from exc
        hidden = self._hidden_ids(entries)
        active = [e for e in entries if str(e.get("memory_id")) not in hidden]
        active.sort(key=lambda e: (str(e.get("written_at")), str(e.get("memory_id"))))
        return active

    def _entry(
        self, *, tenant_id: str, persona_id: str, memory_id: str, statement: str, interpretation: Mapping[str, Any],
        profile: Mapping[str, Any], origin: str, supersedes: Sequence[str], now: str, ttl_days: int, status: str = "active",
        correction: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        habit: Dict[str, Any] = {
            "interpretation_id": interpretation["interpretation_id"],
            "feature_key": interpretation["feature_key"],
            "rule": interpretation.get("rule"),
            "fact_refs": list(interpretation.get("fact_refs") or []),
            "profile_key": profile.get("profile_key"),
            "profile_version": profile.get("version"),
            "origin": origin,
            "status": status,
            "supersedes": list(supersedes),
        }
        if correction:
            habit["correction"] = dict(correction)
        return {
            "memory_id": memory_id,
            "persona_id": persona_id,
            "tenant_id": tenant_id,
            "memory_type": "preference",
            "content": {
                "summary": statement,
                "tags": [INTERPRETATION_TAG, f"habit:{interpretation['feature_key']}"],
                "structured_payload": habit,
            },
            "source_event_type": "operator_feedback",
            "source_event_id": f"habit:{profile.get('profile_key')}:v{profile.get('version')}",
            "written_at": now,
            "expires_at": _after(now, ttl_days),
            "write_authority": "persona-memory-svc",
            "relevance_scope": "persona_private",
        }

    def sync_interpretations(self, *, tenant_id: str, persona_id: str, profile: Mapping[str, Any]) -> List[Dict[str, Any]]:
        """Write the servant's current reading of the facts to its own private memory.

        A reading already written for the same rule is not written again; a changed reading of
        a feature supersedes the old one. A feature the trader corrected is left to the trader
        until the trader corrects it again.
        """
        every = self._active_entries(tenant_id, persona_id)
        now = self.clock()
        own_key = profile.get("profile_key")
        active = [e for e in every if ((e["content"].get("structured_payload") or {}).get("profile_key")) == own_key]
        corrected = {
            (e["content"].get("structured_payload") or {}).get("feature_key")
            for e in active if (e["content"].get("structured_payload") or {}).get("origin") == "trader_correction"
        }
        derived = derive_interpretations(profile)
        by_feature: Dict[str, List[Dict[str, Any]]] = {}
        for entry in active:
            habit = entry["content"].get("structured_payload") or {}
            if habit.get("origin") == "servant":
                by_feature.setdefault(str(habit.get("feature_key")), []).append(entry)
        live_ids = {str(e["content"]["structured_payload"]["interpretation_id"]) for e in active if "structured_payload" in e["content"]}
        for item in derived:
            if item["feature_key"] in corrected or item["interpretation_id"] in live_ids:
                continue
            old = by_feature.get(item["feature_key"]) or []
            entry = self._entry(
                tenant_id=tenant_id, persona_id=persona_id,
                memory_id=f"hint-{_digest(tenant_id, persona_id, item['interpretation_id'], str(profile.get('version')))}",
                statement=item["statement"], interpretation=item, profile=profile, origin="servant",
                supersedes=[str(e["memory_id"]) for e in old], now=now, ttl_days=INTERPRETATION_TTL_DAYS,
            )
            self._write(entry, supersedes=str(old[-1]["memory_id"]) if old else None)
            for extra in old[:-1]:
                self._supersede_extra(extra, entry["memory_id"])
            active = [e for e in active if e not in old] + [entry]
            live_ids.add(item["interpretation_id"])
        # A derived reading whose rule no longer holds is hidden from the trader and the turn
        # once the facts stop supporting it (it stays in memory until its TTL; never deleted).
        current_ids = {i["interpretation_id"] for i in derived}
        return [
            e for e in active
            if (e["content"].get("structured_payload") or {}).get("origin") == "trader_correction"
            or (e["content"].get("structured_payload") or {}).get("interpretation_id") in current_ids
        ][-MAX_ACTIVE_INTERPRETATIONS:]

    def _write(self, entry: Dict[str, Any], *, supersedes: Optional[str] = None) -> None:
        try:
            self.memory.write(entry, supersedes=supersedes)
        except Exception as exc:
            raise HabitUnavailableError(f"servant_memory_write_failed:{type(exc).__name__}") from exc

    def _supersede_extra(self, old: Mapping[str, Any], new_id: str) -> None:
        store = getattr(self.memory, "store", None)
        if store is not None:
            try:
                store.supersede(str(old["memory_id"]), new_id)
            except Exception:  # content.structured_payload.supersedes already hides it
                pass

    @staticmethod
    def interpretation_view(entry: Mapping[str, Any]) -> Dict[str, Any]:
        habit = (entry.get("content") or {}).get("structured_payload") or {}
        return {
            "interpretation_id": habit.get("interpretation_id"),
            "memory_id": entry.get("memory_id"),
            "feature_key": habit.get("feature_key"),
            "feature_label": FEATURE_LABELS.get(str(habit.get("feature_key")), str(habit.get("feature_key"))),
            "statement": (entry.get("content") or {}).get("summary"),
            "origin": habit.get("origin"),
            "status": habit.get("status", "active"),
            "fact_refs": list(habit.get("fact_refs") or []),
            "profile_version": habit.get("profile_version"),
            "written_at": entry.get("written_at"),
        }

    # -- read ----------------------------------------------------------------------

    def read(self, *, tenant_id: str, user_id: str, persona_id: str, strategy_id: Optional[str] = None) -> Dict[str, Any]:
        profile = self.refresh_profile(tenant_id=tenant_id, user_id=user_id, strategy_id=strategy_id)
        interpretations: List[Dict[str, Any]] = []
        memory_status = {"status": "ok", "reason": None}
        try:
            interpretations = [
                self.interpretation_view(e)
                for e in self.sync_interpretations(tenant_id=tenant_id, persona_id=persona_id, profile=profile)
            ]
        except HabitUnavailableError as exc:
            memory_status = {"status": "unavailable", "reason": str(exc)}
        return {"profile": profile, "interpretations": interpretations, "interpretations_status": memory_status}

    # -- correct -------------------------------------------------------------------

    def correct(
        self, *, tenant_id: str, user_id: str, operator_id: str, roles: Sequence[str], persona_id: str,
        strategy_id: Optional[str], action: str, interpretation_id: Optional[str], feature_key: Optional[str],
        statement: Optional[str], reason: Optional[str], idempotency_key: str,
    ) -> Dict[str, Any]:
        if action not in _CORRECTION_ACTIONS:
            raise HabitCorrectionError(422, "unsupported_action")
        if self.evidence_owner is None:
            raise HabitUnavailableError("agora_evidence_owner_unconfigured")
        state = self.read(tenant_id=tenant_id, user_id=user_id, persona_id=persona_id, strategy_id=strategy_id)
        if state["interpretations_status"]["status"] != "ok":
            raise HabitUnavailableError(state["interpretations_status"]["reason"])
        profile = state["profile"]
        active = [
            e for e in self._active_entries(tenant_id, persona_id)
            if (e["content"].get("structured_payload") or {}).get("profile_key") == profile.get("profile_key")
        ]
        target = next(
            (e for e in active if (e["content"].get("structured_payload") or {}).get("interpretation_id") == interpretation_id), None
        ) if interpretation_id else None
        if action in ("reject", "replace"):
            if target is None:
                raise HabitCorrectionError(404, "interpretation_not_found")  # also another trader's id
            feature_key = str(target["content"]["structured_payload"]["feature_key"])
        elif feature_key not in FEATURE_KEYS:
            raise HabitCorrectionError(422, "feature_key_required")
        old_statement = str(target["content"]["summary"]) if target else None
        clean = (statement or "").strip()[:CORRECTION_STATEMENT_MAX_CHARS]
        if action in ("replace", "add") and not clean:
            raise HabitCorrectionError(422, "statement_required")
        new_statement = clean if action != "reject" else f"交易員不同意此解讀：{old_statement}"
        now = self.clock()
        digest = _digest(tenant_id, user_id, idempotency_key)
        correction_id = f"hint-corr-{_digest(str(profile['profile_key']), str(feature_key), action, clean, str(old_statement), size=16)}"
        corrected = {"interpretation_id": correction_id, "feature_key": feature_key, "rule": f"trader_{action}",
                     "fact_refs": [feature_key]}
        evidence = self._store_training_example(
            tenant_id=tenant_id, user_id=user_id, operator_id=operator_id, roles=roles, persona_id=persona_id,
            profile=profile, action=action, feature_key=str(feature_key), old=target, old_statement=old_statement,
            new_statement=new_statement, reason=reason, idempotency_key=idempotency_key, digest=digest, now=now,
        )
        entry = self._entry(
            tenant_id=tenant_id, persona_id=persona_id, memory_id=f"hint-corr-{digest}", statement=new_statement,
            interpretation=corrected, profile=profile, origin="trader_correction",
            supersedes=[str(target["memory_id"])] if target else [], now=now, ttl_days=CORRECTION_TTL_DAYS,
            status="rejected" if action == "reject" else "active",
            correction={"action": action, "reason": (reason or "")[:300], "evidence_id": evidence["evidence_id"],
                        "replaces_interpretation_id": interpretation_id},
        )
        self._write(entry, supersedes=str(target["memory_id"]) if target else None)
        return {
            "correction": {"action": action, "interpretation": self.interpretation_view(entry),
                           "superseded_interpretation_id": interpretation_id, "training_example": evidence},
            **self.read(tenant_id=tenant_id, user_id=user_id, persona_id=persona_id, strategy_id=strategy_id),
        }

    def _store_training_example(
        self, *, tenant_id: str, user_id: str, operator_id: str, roles: Sequence[str], persona_id: str,
        profile: Mapping[str, Any], action: str, feature_key: str, old: Optional[Mapping[str, Any]],
        old_statement: Optional[str], new_statement: str, reason: Optional[str], idempotency_key: str,
        digest: str, now: str,
    ) -> Dict[str, Any]:
        """A correction is human teaching: it enters the dataset pipeline as ``training_example``."""
        feature = feature_by_key(profile, feature_key) or {}
        body = SimpleNamespace(
            evidence_id=f"habit-correction-{digest}",
            persona_id=persona_id,
            subject_ref=f"habit_profile:{profile['profile_key']}:v{profile.get('version')}:{feature_key}",
            content={
                "topic": "habit_interpretation_correction",
                "input": {
                    "feature_key": feature_key,
                    "profile_key": profile["profile_key"],
                    "profile_version": profile.get("version"),
                    "sample_size": feature.get("sample_size"),
                    "servant_interpretation": old_statement,
                },
                "expected_output": {"action": action, "statement": new_statement, "reason": (reason or "")[:300]},
            },
            source_refs=[f"habit_profile:{profile['profile_key']}"],
            captured_at=datetime.fromisoformat(now.replace("Z", "+00:00")),
            consent_granted=True,
            retention_days=365,
            learning_eligible=True,
        )
        scope = SimpleNamespace(tenant_id=tenant_id, user_id=user_id, operator_id=operator_id, roles=list(roles))
        entry, _created = self.evidence_owner.submit(
            "training_example", body, scope=scope, idempotency_key=idempotency_key, admitted_at=now,
        )
        return {"evidence_id": entry["evidence_id"], "interaction_kind": entry.get("interaction_kind"),
                "status": entry.get("status")}

    # -- turn ------------------------------------------------------------------------

    def turn_context(self, *, tenant_id: str, user_id: str, persona_id: str, strategy_id: Optional[str] = None) -> HabitTurnContext:
        """Habit facts (h1..hN) and live interpretations carried by one servant turn."""
        state = self.read(tenant_id=tenant_id, user_id=user_id, persona_id=persona_id, strategy_id=strategy_id)
        profile = state["profile"]
        facts: List[Dict[str, Any]] = []
        for feature in profile.get("features") or []:
            if not feature.get("shown") or not feature.get("headline"):
                continue
            head = feature["headline"]
            facts.append({
                "id": f"h{len(facts) + 1}",
                "tool": "habit_profile",
                "label": f"我的習慣：{feature['label']}（樣本 {feature['sample_size']} 筆）",
                "value": head["value"],
                "unit": head["unit"],
                "sample_size": feature["sample_size"],
                "feature_key": feature["key"],
                "source_id": f"{profile['profile_key']}:v{profile.get('version')}",
                "owner": "agora.habit_profiles",
                "as_of": profile.get("computed_at"),
                "freshness": "fresh",
                "habit_kind": "fact",
            })
        for item in state["interpretations"]:
            facts.append({
                "id": f"h{len(facts) + 1}",
                "tool": "habit_profile",
                "label": f"僕人對您習慣的解讀（{item['feature_label']}）",
                "value": str(item["statement"] or "")[:FACT_TEXT_MAX_CHARS],
                "unit": "text",
                "feature_key": item["feature_key"],
                "source_id": item["memory_id"],
                "owner": "agora.servant_private_memory",
                "as_of": item["written_at"],
                "freshness": "fresh",
                "habit_kind": "interpretation",
                "origin": item["origin"],
            })
        ref = {
            "profile_key": profile.get("profile_key"),
            "profile_version": profile.get("version"),
            "input_digest": profile.get("input_digest"),
            "decision_count": profile.get("decision_count"),
            "interpretations_status": state["interpretations_status"]["status"],
        }
        return HabitTurnContext(facts=facts, ref=ref)


def build_default_habit_service(owner: Any, *, journal_reader: Any = None, dataset_store: Any = None) -> Optional[HabitService]:
    """Production wiring for the turn worker; None when the Agora owner lacks habit support."""
    if not hasattr(owner, "refresh_habit_profile"):
        return None
    evidence_owner = None
    if dataset_store is not None:
        from services.agora.service import AgoraEvidenceWriteService
        from services.agora.store import AgoraEvidenceStore

        evidence_owner = AgoraEvidenceWriteService(AgoraEvidenceStore(dataset_store))
    return HabitService(
        owner=owner, memory=build_default_habit_memory(), journal_reader=journal_reader, evidence_owner=evidence_owner,
    )


# ---------------------------------------------------------------------------- routes


class HabitCorrectionRequest(BaseModel):
    model_config = {"extra": "forbid"}
    action: str = Field(description="reject | replace | add")
    interpretation_id: Optional[str] = Field(default=None, max_length=80)
    feature_key: Optional[str] = Field(default=None, max_length=40)
    statement: Optional[str] = Field(default=None, max_length=CORRECTION_STATEMENT_MAX_CHARS)
    reason: Optional[str] = Field(default=None, max_length=300)
    strategy_id: Optional[str] = Field(default=None, max_length=120)

    @field_validator("action")
    @classmethod
    def known_action(cls, value: str) -> str:
        if value not in _CORRECTION_ACTIONS:
            raise ValueError("action must be reject, replace or add")
        return value


def create_servant_habits_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    require_write_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
    get_read_store: Callable[[], Any] = lambda: None,
    habit_service: Optional[Any] = None,
) -> APIRouter:
    """``GET`` and ``PATCH /bff/agora/servant/habits``: owner-scoped, advice only."""
    from services.control_plane.bff.models import ErrorCode

    from ..identity.scope import AgoraScopeResolutionError, resolve_agora_user_scope
    from .router import (
        _extract_caller_identity,
        _raise_scope_error,
        _require_header,
        _servant_persona_id_for_scope,
    )

    router = APIRouter(tags=["agora-servant-habits"])
    service_cache: Dict[str, Any] = {}

    def _service() -> HabitService:
        resolved = habit_service() if callable(habit_service) else habit_service
        if resolved is not None:
            return resolved
        cached = service_cache.get("service")
        if cached is None:
            from .router import _lazy_default_servant_session_owner

            owner = _lazy_default_servant_session_owner()
            if owner is not None:
                from services.control_plane.bff.agora.dataset_extraction.router import _default_store

                cached = build_default_habit_service(owner, journal_reader=get_read_store(), dataset_store=_default_store())
            if cached is None:
                raise bff_error(
                    503, ErrorCode.DEPENDENCY_UNAVAILABLE, "Agora owner is unavailable for habit profiles",
                    "habit_owner_unavailable", precondition_failed="agora_owner",
                )
            service_cache["service"] = cached
        return cached

    def _caller(authorization: Optional[str], pantheon_session: Optional[str], tenant: Optional[str], *, write: bool) -> tuple[Any, Any, str]:
        identity = _extract_caller_identity(extract_identity, authorization, pantheon_session)
        (require_write_role if write else require_read_role)(identity)
        try:
            scope = resolve_agora_user_scope(identity, utc_now=utc_now, requested_tenant_id=tenant)
        except AgoraScopeResolutionError as exc:
            _raise_scope_error(exc, bff_error)
        return identity, scope, _servant_persona_id_for_scope(get_read_store(), scope)

    def _not_found(what: str) -> HTTPException:
        return bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, f"{what} not found", f"{what}_not_found", precondition_failed="habit_owner_scope")

    def _unavailable(reason: str) -> HTTPException:
        return bff_error(
            503, ErrorCode.DEPENDENCY_UNAVAILABLE, "Servant private memory is unavailable", reason,
            precondition_failed="servant_memory_owner",
        )

    def _envelope(data: Dict[str, Any], scope: Any) -> Dict[str, Any]:
        return {
            "status": "ok",
            "data": data,
            "meta": {
                "snapshot_at": utc_now(),
                "capability": SERVANT_HABITS_CAPABILITY,
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
                "read_only": False,
                "advice_only": True,
                "execution_authority": "none",
                "environment_ceiling": "paper",
                "min_sample": HABIT_MIN_SAMPLE,
                "no_order_route_proof": "agora_habit_profile_advice_only",
            },
        }

    @router.get("/bff/agora/servant/habits")
    def get_servant_habits(
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
        strategy_id: Optional[str] = Query(default=None, max_length=120),
        trader_id: Optional[str] = Query(default=None, max_length=200),
        profile_key_param: Optional[str] = Query(default=None, alias="profile_key", max_length=80),
    ) -> Dict[str, Any]:
        _identity, scope, persona_id = _caller(authorization, pantheon_session, x_tenant_id or x_pantheon_tenant, write=False)
        if trader_id not in (None, "", scope.user_id):
            raise _not_found("habit_profile")  # another trader's profile reads as absent
        if profile_key_param and profile_key_param != profile_key(scope.tenant_id, scope.user_id, strategy_id):
            raise _not_found("habit_profile")
        data = _service().read(
            tenant_id=scope.tenant_id, user_id=scope.user_id, persona_id=persona_id, strategy_id=strategy_id
        )
        data["servant_persona_id"] = persona_id
        return _envelope(data, scope)

    @router.patch("/bff/agora/servant/habits")
    def patch_servant_habits(
        body: HabitCorrectionRequest = Body(...),
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    ) -> Dict[str, Any]:
        _identity, scope, persona_id = _caller(authorization, pantheon_session, x_tenant_id or x_pantheon_tenant, write=True)
        key = _require_header(idempotency_key, "Idempotency-Key", bff_error)
        try:
            data = _service().correct(
                tenant_id=scope.tenant_id, user_id=scope.user_id, operator_id=scope.operator_id,
                roles=list(scope.roles) or ["trader"], persona_id=persona_id, strategy_id=body.strategy_id,
                action=body.action, interpretation_id=body.interpretation_id, feature_key=body.feature_key,
                statement=body.statement, reason=body.reason, idempotency_key=key,
            )
        except HabitCorrectionError as exc:
            if exc.status == 404:
                raise _not_found("interpretation") from exc
            raise bff_error(
                exc.status, ErrorCode.VALIDATION_FAILED, "Habit correction rejected", exc.reason,
                precondition_failed="habit_correction",
            ) from exc
        except HabitUnavailableError as exc:
            raise _unavailable(str(exc)) from exc
        data["servant_persona_id"] = persona_id
        return _envelope(data, scope)

    return router


__all__ = [
    "HabitCorrectionError",
    "HabitService",
    "HabitTurnContext",
    "HabitUnavailableError",
    "HttpHabitMemory",
    "InProcessHabitMemory",
    "build_default_habit_memory",
    "build_default_habit_service",
    "carry_interpretation_in_minutes",
    "create_servant_habits_router",
    "derive_interpretations",
]
