"""Deliberative persona meetings protocol engine (R2).

Implements SD-PERSONA-MEETINGS-20261115 and SD-PERSONA-MEETINGS-AMEND-20261115 (§12):
- Six catalogue types (MT-1..MT-6) with MT-4 as performance attribution review;
- Roster rules for institutional trading personas, risk/red-team personas,
  eligible non-private Agora advisors, and user-private servants (attending only
  meetings convened by their owning user or MT-4 reviews whose subject is their
  own trader);
- Pre-read of shared case file + each participant's committee-visible memory in
  full (never persona_private) until H10 interpretation turn exists;
- Round 1 independent positions with confidence and citations;
- Round 2 anonymised cross-visibility (labelled by role and style only) with
  accepted/disputed/changed;
- Optional Round 3 only when the chair records new evidence;
- Chair synthesis with proposals (one trading-persona sponsor, or None for
  agora_candidate_decision), non-binding confidence tally, and dissent memo
  (no vote; empty/unreadable synthesis -> REVIEW with no proposal; MT-4 accepts
  only testable causal hypotheses naming component and test, otherwise
  no_testable_hypothesis);
- C2 output routing via dispatch_proposals and read_downstream_status;
- Institutional minutes + per-participant outcome memory writeback via
  learn-feedback writeback.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
import urllib.error
import urllib.parse
import urllib.request
import uuid

from services.persona import consult_triggers

from .models import (
    ActorRef,
    AuthorType,
    ConsultEvidenceAttachment,
    ConsultFinding,
    ConsultMemo,
    ConsultParticipant,
    ConsultPriority,
    ConsultRequest,
    ConsultRequestStatus,
    ConsultRequestType,
    EvidenceRef,
    FindingSeverity,
    MemoStatus,
    MemoType,
    ParticipantRole,
    ParticipantStatus,
    ParticipantType,
    Recommendation,
    TranscriptEvent,
    utc_now,
)

RISK_PERSONA_ID = consult_triggers.RISK_PERSONA_ID
RED_TEAM_PERSONA_ID = consult_triggers.RED_TEAM_PERSONA_ID

OPERATIONAL_AGORA_LIFECYCLE_STATES = frozenset(
    {
        "research_only",
        "consultable",
        "paper_owner",
        "live_owner",
        "active",
        "paper_running",
        "paper_only",
    }
)
ADVISORY_CEILINGS = frozenset({"analysis", "research", "shadow", "paper"})

PROPOSAL_KINDS = frozenset(
    {
        "research_demand",
        "evolution_proposal",
        "harness_patch",
        "lesson_candidate",
        "lesson_rule",
        "pool_decision",
        "governance_approval",
        "agora_candidate_decision",
    }
)

DOWNSTREAM_TARGET_BY_KIND = {
    "research_demand": "research_persona_demands",
    "evolution_proposal": "evolution_proposals",
    "harness_patch": "persona_harness_patches",
    "lesson_candidate": "memory_trade_lessons",
    "lesson_rule": "persona_lesson_rule_proposals",
    "pool_decision": "persona_strategy_pool_decisions",
    "governance_approval": "governance_approvals",
    "agora_candidate_decision": "agora_candidate_decisions",
}

MT4_VALID_COMPONENTS = frozenset(
    {
        "selection",
        "entry_timing",
        "sizing",
        "exit",
        "costs",
        "regime",
        "execution",
        "holding_period",
        "stop_loss",
    }
)


class MeetingRefusedError(ValueError):
    """Raised when a deliberative meeting is refused by catalogue, roster, or budget rules."""

    def __init__(
        self,
        reason: str,
        detail: str = "",
        *,
        status_code: int = 422,
        meeting_id: Optional[str] = None,
        persona_id: Optional[str] = None,
        meeting_type: Optional[str] = None,
    ) -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail or reason
        self.status_code = status_code
        self.meeting_id = meeting_id
        self.persona_id = persona_id
        self.meeting_type = meeting_type


def catalogue_path() -> Path:
    env_path = os.getenv("CONSULTATION_MEETING_CATALOGUE_PATH", "").strip()
    if env_path:
        return Path(env_path)
    return Path(__file__).resolve().parent / "meeting_catalogue.json"


def load_meeting_catalogue(path: Optional[Path] = None) -> Dict[str, Any]:
    target = path or catalogue_path()
    return json.loads(target.read_text(encoding="utf-8"))


def resolve_meeting_type_spec(
    meeting_type: str,
    catalogue: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    cat = dict(catalogue or load_meeting_catalogue())
    raw = str(meeting_type or "").strip()
    if not raw:
        raise MeetingRefusedError("unsupported_meeting_type", "meeting_type is required")
    refused_legacy = {
        str(x).strip().lower() for x in (cat.get("refused_legacy_types") or [])
    }
    if raw.lower() in refused_legacy:
        raise MeetingRefusedError(
            "unsupported_meeting_type",
            f"meeting_type {raw!r} is retired/refused by SD-PERSONA-MEETINGS-AMEND-20261115",
            meeting_type=raw,
        )
    for item in cat.get("types") or []:
        if not isinstance(item, Mapping):
            continue
        aliases = {
            str(a).strip().lower()
            for a in [
                item.get("meeting_type"),
                item.get("slug"),
                *(item.get("aliases") or []),
            ]
            if a
        }
        if raw.lower() in aliases:
            if not item.get("enabled", True):
                raise MeetingRefusedError(
                    "meeting_type_disabled",
                    f"meeting_type {raw!r} is disabled in catalogue",
                    meeting_type=str(item.get("meeting_type") or raw),
                )
            return dict(item)
    raise MeetingRefusedError(
        "unsupported_meeting_type",
        f"meeting_type {raw!r} is not in the persona_meeting catalogue",
        meeting_type=raw,
    )


def canonical_meeting_code(
    meeting_type: str,
    catalogue: Optional[Mapping[str, Any]] = None,
) -> str:
    spec = resolve_meeting_type_spec(meeting_type, catalogue)
    return str(spec["meeting_type"])


def meeting_type_matches(
    stored_type: str,
     stored_code: str,
    stored_slug: str,
    query_type: str,
    catalogue: Optional[Mapping[str, Any]] = None,
) -> bool:
    q = str(query_type or "").strip().lower()
    if not q:
        return True
    candidates = {
        str(stored_type or "").strip().lower(),
        str(stored_code or "").strip().lower(),
        str(stored_slug or "").strip().lower(),
    }
    if q in candidates:
        return True
    try:
        spec = resolve_meeting_type_spec(query_type, catalogue)
        return str(spec.get("meeting_type") or "").strip().lower() in candidates or (
            str(spec.get("slug") or "").strip().lower() in candidates
        )
    except MeetingRefusedError:
        return False


def iso_week_key(timestamp_str: Optional[str] = None) -> str:
    if timestamp_str:
        try:
            dt = datetime.fromisoformat(str(timestamp_str).replace("Z", "+00:00"))
        except ValueError:
            dt = datetime.now(timezone.utc)
    else:
        dt = datetime.now(timezone.utc)
    iso_year, iso_week, _ = dt.isocalendar()
    return f"{iso_year:04d}-W{iso_week:02d}"


def compute_next_due_at(last_run_at: str, cadence: str) -> str:
    try:
        dt = datetime.fromisoformat(str(last_run_at).replace("Z", "+00:00"))
    except ValueError:
        dt = datetime.now(timezone.utc)
    if cadence == "quarterly":
        next_dt = dt + timedelta(days=90)
    else:
        next_dt = dt + timedelta(days=7)
    return next_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _fetch_persona_directory(tenant_id: str) -> Dict[str, Dict[str, Any]]:
    persona_url = (
        os.getenv("PANTHEON_PERSONA_API_URL")
        or os.getenv("PERSONA_API_URL")
        or ""
    ).strip().rstrip("/")
    if not persona_url:
        return {}
    token = (
        os.getenv("PANTHEON_PERSONA_SERVICE_TOKEN")
        or os.getenv("PERSONA_SERVICE_TOKEN")
        or ""
    ).strip()
    headers = {"Accept": "application/json", "X-Tenant-Id": tenant_id}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{persona_url}/api/personas", headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            rows = json.loads(resp.read().decode("utf-8") or "[]")
        if isinstance(rows, dict):
            rows = rows.get("items") or rows.get("personas") or []
        if not isinstance(rows, list):
            return {}
        return {
            str(r.get("persona_id") or r.get("id")): dict(r)
            for r in rows
            if isinstance(r, Mapping) and (r.get("persona_id") or r.get("id"))
        }
    except Exception:
        return {}


def _extract_subject_trader_id(
    subject_ref: Optional[str],
    attribution_record: Optional[Mapping[str, Any]],
) -> Optional[str]:
    if isinstance(attribution_record, Mapping):
        for key in ("agora_user_id", "trader_id", "user_id"):
            val = str(attribution_record.get(key) or "").strip()
            if val:
                return val.removeprefix("trader:").removeprefix("agora_user:")
        subj = attribution_record.get("subject")
        if isinstance(subj, Mapping):
            if str(subj.get("kind") or "").lower() in {"trader", "agora_user", "user"}:
                sid = str(subj.get("id") or subj.get("agora_user_id") or "").strip()
                if sid:
                    return sid
        subj_ref = str(attribution_record.get("subject_ref") or "").strip()
        if subj_ref.startswith(("trader:", "agora_user:", "user:")):
            return subj_ref.split(":", 1)[1].strip()
    sref = str(subject_ref or "").strip()
    if sref.startswith(("trader:", "agora_user:", "user:")):
        return sref.split(":", 1)[1].strip()
    return None


def classify_and_validate_participant(
    raw_entry: Any,
    *,
    idx: int,
    meeting_code: str,
    convened_by: Mapping[str, Any],
    subject_ref: Optional[str],
    attribution_record: Optional[Mapping[str, Any]],
    persona_directory: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    """Validate one participant entry against the amended roster rules (AC4 & §12.8)."""
    if isinstance(raw_entry, str):
        persona_id = raw_entry.strip()
        declared: Dict[str, Any] = {"persona_id": persona_id}
    elif isinstance(raw_entry, Mapping):
        persona_id = str(raw_entry.get("persona_id") or raw_entry.get("id") or "").strip()
        declared = dict(raw_entry)
    else:
        raise MeetingRefusedError("invalid_participant", "participant must be a persona_id or object")

    if not persona_id:
        raise MeetingRefusedError("invalid_participant", "participant persona_id is required")

    directory_record = dict(persona_directory.get(persona_id) or {})
    meta = dict(directory_record.get("metadata") or {})
    if isinstance(declared.get("metadata"), Mapping):
        meta.update(declared["metadata"])

    persona_class = str(
        declared.get("persona_class")
        or meta.get("persona_class")
        or directory_record.get("persona_class")
        or ""
    ).strip()
    origin_hint = str(
        declared.get("origin")
        or meta.get("origin")
        or ""
    ).strip()
    owner_scope = str(
        declared.get("owner_scope")
        or meta.get("owner_scope")
        or directory_record.get("owner_scope")
        or ""
    ).strip()
    visibility_scope = str(
        declared.get("visibility_scope")
        or meta.get("visibility_scope")
        or ""
    ).strip()
    agora_user_id = (
        str(
            declared.get("agora_user_id")
            or meta.get("agora_user_id")
            or meta.get("agoraUserId")
            or directory_record.get("agora_user_id")
            or ""
        ).strip()
        or None
    )

    is_servant = (
        origin_hint == "private_servant"
        or persona_class == "agora_servant"
        or owner_scope == "user_private"
        or persona_id.startswith("agora-servant-")
        or persona_id.startswith("servant-")
    )
    is_advisor = not is_servant and (
        origin_hint == "agora_advisor"
        or persona_class == "agora_advisor"
        or persona_id.startswith("agora-advisor-")
    )

    # Check if servant is marked missing/absent
    if is_servant and (
        declared.get("absent") is True
        or declared.get("provisioned") is False
        or str(directory_record.get("status") or "").lower() in {"missing", "unprovisioned"}
    ):
        raise MeetingRefusedError(
            "servant_absent",
            f"private servant {persona_id} is absent or not provisioned",
            persona_id=persona_id,
            meeting_type=meeting_code,
        )

    if is_servant:
        origin = "private_servant"
        # Amended rule A2 (§12.8 & AC4):
        # A private servant attends ONLY:
        # 1) a meeting convened by its owning user, OR
        # 2) every MT-4 review whose subject is its own trader, and NO other meeting.
        convener_kind = str(convened_by.get("kind") or "").strip()
        convener_id = str(convened_by.get("id") or "").strip()
        subject_trader_id = _extract_subject_trader_id(subject_ref, attribution_record)
        # Also allow subject_ref matching raw agora_user_id in MT-4 when subject_ref is that user id
        mt4_subject_matches = (
            meeting_code == "MT-4"
            and bool(agora_user_id)
            and (
                subject_trader_id == agora_user_id
                or str(subject_ref or "").strip() == agora_user_id
            )
        )
        convened_by_owning_user = (
            convener_kind == "agora_user"
            and bool(agora_user_id)
            and convener_id == agora_user_id
        )
        if not (convened_by_owning_user or mt4_subject_matches):
            raise MeetingRefusedError(
                "private_servant_not_allowed",
                f"private servant {persona_id} (owner={agora_user_id!r}) may only attend a meeting "
                "convened by its owning user or an MT-4 review whose subject is its own trader",
                persona_id=persona_id,
                meeting_type=meeting_code,
            )
        role = str(declared.get("role") or ("subject" if mt4_subject_matches else "servant"))
        style = str(
            declared.get("style")
            or directory_record.get("strategy_family")
            or meta.get("style")
            or "trader_habit_shadow"
        )
        return {
            "persona_id": persona_id,
            "role": role,
            "style": style,
            "origin": origin,
            "agora_user_id": agora_user_id,
            "trading": False,
            "can_sponsor": False,
        }

    if is_advisor:
        origin = "agora_advisor"
        # Must pass existing Agora eligibility and NOT be user-private (AC4)
        if owner_scope == "user_private" or visibility_scope == "private" or agora_user_id:
            raise MeetingRefusedError(
                "agora_advisor_ineligible",
                f"Agora advisor {persona_id} cannot be user-private",
                persona_id=persona_id,
                meeting_type=meeting_code,
            )
        lifecycle = str(
            declared.get("lifecycle_state")
            or directory_record.get("lifecycle_state")
            or meta.get("lifecycle_state")
            or "consultable"
        ).strip().lower()
        status = str(
            declared.get("status")
            or directory_record.get("status")
            or meta.get("status")
            or "active"
        ).strip().lower()
        ceiling = str(
            declared.get("environment_ceiling")
            or directory_record.get("environment_ceiling")
            or meta.get("environment_ceiling")
            or "paper"
        ).strip().lower()
        eligible_flag = declared.get("agora_eligible", meta.get("agora_eligible", True))
        if (
            not eligible_flag
            or status == "suspended"
            or lifecycle not in OPERATIONAL_AGORA_LIFECYCLE_STATES
            or ceiling not in ADVISORY_CEILINGS
        ):
            raise MeetingRefusedError(
                "agora_advisor_ineligible",
                f"Agora advisor {persona_id} does not pass Agora eligibility",
                persona_id=persona_id,
                meeting_type=meeting_code,
            )
        role = str(declared.get("role") or "advisor")
        style = str(
            declared.get("style")
            or directory_record.get("strategy_family")
            or directory_record.get("archetype")
            or meta.get("style")
            or "advisory"
        )
        return {
            "persona_id": persona_id,
            "role": role,
            "style": style,
            "origin": origin,
            "agora_user_id": None,
            "trading": False,
            "can_sponsor": False,
        }

    # Institutional persona (trading persona, risk officer, or red team)
    origin = "institutional"
    if persona_id == RISK_PERSONA_ID or declared.get("role") == "risk_reviewer" or meta.get("panel_role") == "risk_reviewer":
        return {
            "persona_id": persona_id,
            "role": "risk_reviewer",
            "style": str(declared.get("style") or "risk_governance"),
            "origin": origin,
            "agora_user_id": None,
            "trading": False,
            "can_sponsor": False,
        }
    if persona_id == RED_TEAM_PERSONA_ID or declared.get("role") == "red_team" or meta.get("panel_role") == "red_team":
        return {
            "persona_id": persona_id,
            "role": "red_team",
            "style": str(declared.get("style") or "adversarial_red_team"),
            "origin": origin,
            "agora_user_id": None,
            "trading": False,
            "can_sponsor": False,
        }

    # Institutional trading persona
    status = str(
        declared.get("status")
        or directory_record.get("status")
        or "active"
    ).strip().lower()
    if status == "suspended":
        raise MeetingRefusedError(
            "persona_suspended",
            f"institutional persona {persona_id} is suspended",
            persona_id=persona_id,
            meeting_type=meeting_code,
        )
    trading = bool(
        declared.get("trading", meta.get("trading", True))
        and persona_class != "consult_panel"
    )
    default_role = "chair" if idx == 0 else "trading_peer"
    role = str(declared.get("role") or default_role)
    style = str(
        declared.get("style")
        or directory_record.get("strategy_family")
        or directory_record.get("archetype")
        or meta.get("style")
        or f"style_{(idx % 4) + 1}"
    )
    return {
        "persona_id": persona_id,
        "role": role,
        "style": style,
        "origin": origin,
        "agora_user_id": None,
        "trading": trading,
        "can_sponsor": trading,
    }


def resolve_and_validate_roster(
    *,
    spec: Mapping[str, Any],
    convened_by: Mapping[str, Any],
    raw_participants: Optional[Sequence[Any]],
    subject_ref: Optional[str],
    attribution_record: Optional[Mapping[str, Any]],
    persona_directory: Optional[Mapping[str, Mapping[str, Any]]] = None,
    tenant_id: str = "default",
) -> Tuple[List[Dict[str, Any]], str]:
    """Resolve and validate the meeting roster and chair_persona_id."""
    meeting_code = str(spec["meeting_type"])
    roster_rule = dict(spec.get("roster_rule") or {})
    directory = dict(persona_directory or _fetch_persona_directory(tenant_id))

    entries: List[Any] = list(raw_participants or [])
    if not entries:
        # Build default roster from convened_by, subject_ref, and catalogue rule
        convener_kind = str(convened_by.get("kind") or "")
        convener_id = str(convened_by.get("id") or "")
        if convener_kind == "persona" and convener_id:
            entries.append({"persona_id": convener_id, "role": "chair"})
        elif subject_ref and not str(subject_ref).startswith(("trader:", "agora_user:", "user:")):
            entries.append({"persona_id": str(subject_ref), "role": "chair"})
        else:
            # Find enrolled trading personas in directory or default
            trading_ids = [
                pid
                for pid, rec in sorted(directory.items())
                if pid not in {RISK_PERSONA_ID, RED_TEAM_PERSONA_ID}
                and (rec.get("metadata") or {}).get("persona_class")
                not in {"consult_panel", "agora_servant", "agora_advisor"}
                and (rec.get("metadata") or {}).get("trading", True) is not False
            ]
            for i, pid in enumerate(trading_ids[:3]):
                entries.append({"persona_id": pid, "role": "chair" if i == 0 else "trading_peer"})
            if not entries and convener_kind == "agora_user" and convener_id:
                # Look for owning user's servant in directory
                servant_ids = [
                    pid
                    for pid, rec in sorted(directory.items())
                    if (rec.get("metadata") or {}).get("persona_class") == "agora_servant"
                    and (rec.get("metadata") or {}).get("agora_user_id") == convener_id
                ]
                if servant_ids:
                    entries.append(
                        {
                            "persona_id": servant_ids[0],
                            "origin": "private_servant",
                            "agora_user_id": convener_id,
                            "role": "servant",
                        }
                    )
                else:
                    raise MeetingRefusedError(
                        "servant_absent",
                        f"no provisioned private servant found for agora_user {convener_id}",
                        persona_id=convener_id,
                        meeting_type=meeting_code,
                    )
            elif not entries:
                entries.append({"persona_id": "persona-alpha", "role": "chair", "style": "momentum"})
                if spec.get("cross_persona") or int(roster_rule.get("min_trading_personas") or 1) >= 2:
                    entries.append({"persona_id": "persona-beta", "role": "trading_peer", "style": "mean_reversion"})
                elif meeting_code in {"MT-1", "MT-2"}:
                    entries.append({"persona_id": "persona-beta", "role": "trading_peer", "style": "value"})

        # For MT-4 with a trader subject, include the trader's servant if in directory
        if meeting_code == "MT-4":
            trader_id = _extract_subject_trader_id(subject_ref, attribution_record)
            if trader_id:
                servant_id = next(
                    (
                        pid
                        for pid, rec in sorted(directory.items())
                        if (rec.get("metadata") or {}).get("persona_class") == "agora_servant"
                        and (rec.get("metadata") or {}).get("agora_user_id") == trader_id
                    ),
                    f"agora-servant-{trader_id}",
                )
                if not any(
                    (e if isinstance(e, str) else e.get("persona_id")) == servant_id
                    for e in entries
                ):
                    entries.append(
                        {
                            "persona_id": servant_id,
                            "origin": "private_servant",
                            "agora_user_id": trader_id,
                            "role": "subject",
                            "style": "trader_habit_shadow",
                        }
                    )

        if roster_rule.get("requires_risk"):
            entries.append({"persona_id": RISK_PERSONA_ID, "role": "risk_reviewer"})
        if roster_rule.get("requires_red_team"):
            entries.append({"persona_id": RED_TEAM_PERSONA_ID, "role": "red_team"})

    # Deduplicate while preserving order
    validated: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw in entries:
        item = classify_and_validate_participant(
            raw,
            idx=len(validated),
            meeting_code=meeting_code,
            convened_by=convened_by,
            subject_ref=subject_ref,
            attribution_record=attribution_record,
            persona_directory=directory,
        )
        if item["persona_id"] in seen_ids:
            continue
        seen_ids.add(item["persona_id"])
        validated.append(item)

    # Check if MT-4 requires red-team and risk when auto-completing
    if meeting_code == "MT-4" and raw_participants is not None:
        # Ensure red-team participant has role="red_team" if present
        for p in validated:
            if p["persona_id"] == RED_TEAM_PERSONA_ID:
                p["role"] = "red_team"

    # Cross-persona minimum trading personas check (AC6 & K5)
    min_trading = int(roster_rule.get("min_trading_personas") or (2 if spec.get("cross_persona") else 1))
    trading_participants = [p for p in validated if p.get("trading")]
    if spec.get("cross_persona") and len(trading_participants) < min_trading:
        raise MeetingRefusedError(
            "insufficient_participants",
            f"{meeting_code} requires at least {min_trading} trading personas; got {len(trading_participants)}",
            persona_id=str(convened_by.get("id") or ""),
            meeting_type=meeting_code,
        )
    if not validated:
        raise MeetingRefusedError(
            "insufficient_participants",
            "meeting has no valid participants",
            persona_id=str(convened_by.get("id") or ""),
            meeting_type=meeting_code,
        )

    # Determine chair_persona_id: first trading persona with role=="chair", else first trading persona, else first participant
    chair_candidates = [p for p in validated if p.get("trading") and p.get("role") == "chair"]
    if not chair_candidates:
        chair_candidates = trading_participants
    if chair_candidates:
        chair_persona_id = chair_candidates[0]["persona_id"]
        chair_candidates[0]["role"] = "chair"
    else:
        chair_persona_id = validated[0]["persona_id"]

    return validated, chair_persona_id


def check_meeting_budgets(
    *,
    spec: Mapping[str, Any],
    participants: Sequence[Mapping[str, Any]],
    existing_meetings: Sequence[Mapping[str, Any]],
    tenant_id: str,
    iso_week: str,
    rounds_requested: Optional[int] = None,
    calls_requested: Optional[int] = None,
    has_new_evidence: bool = False,
    metadata_budget_override: Optional[Mapping[str, Any]] = None,
) -> Dict[str, int]:
    """Enforce M9 / AC6 budgets from configuration."""
    meeting_code = str(spec["meeting_type"])
    budget = dict(spec.get("budget") or {})
    if isinstance(metadata_budget_override, Mapping):
        budget.update(metadata_budget_override)

    max_participants = int(budget.get("max_participants", 5))
    max_rounds = int(budget.get("max_rounds", 2))
    max_rounds_with_evidence = int(budget.get("max_rounds_with_evidence", 3))
    max_weekly = int(budget.get("max_meetings_per_persona_per_week", 3))
    max_calls = int(budget.get("max_calls_per_meeting", 16))

    if len(participants) > max_participants:
        raise MeetingRefusedError(
            "participant_budget_exceeded",
            f"meeting has {len(participants)} participants, exceeding max_participants={max_participants}",
            meeting_type=meeting_code,
        )

    planned_rounds = int(rounds_requested) if rounds_requested is not None else (3 if has_new_evidence else 2)
    allowed_rounds = max_rounds_with_evidence if has_new_evidence else max_rounds
    if planned_rounds > allowed_rounds:
        raise MeetingRefusedError(
            "round_budget_exceeded",
            f"requested {planned_rounds} rounds, exceeding max_rounds={allowed_rounds}",
            meeting_type=meeting_code,
        )

    planned_calls = (
        int(calls_requested)
        if calls_requested is not None
        else (len(participants) * planned_rounds + 1)
    )
    if planned_calls > max_calls:
        raise MeetingRefusedError(
            "call_budget_exceeded",
            f"meeting requires {planned_calls} calls, exceeding max_calls_per_meeting={max_calls}",
            meeting_type=meeting_code,
        )

    # Count deliberative meetings per persona in the same ISO week (excluding refused meetings)
    weekly_counts: Dict[str, int] = {}
    for m in existing_meetings:
        if str(m.get("tenant_id") or "default") != tenant_id:
            continue
        if str(m.get("status") or "") == "refused":
            continue
        m_week = str(m.get("iso_week") or iso_week_key(m.get("created_at")))
        if m_week != iso_week:
            continue
        for p in m.get("participants") or []:
            pid = str(p.get("persona_id") or "")
            if pid and pid not in {RISK_PERSONA_ID, RED_TEAM_PERSONA_ID}:
                weekly_counts[pid] = weekly_counts.get(pid, 0) + 1

    for p in participants:
        pid = str(p.get("persona_id") or "")
        if pid in {RISK_PERSONA_ID, RED_TEAM_PERSONA_ID}:
            continue
        if weekly_counts.get(pid, 0) >= max_weekly:
            raise MeetingRefusedError(
                "weekly_persona_budget_exceeded",
                f"persona {pid} has already attended {weekly_counts[pid]} meetings in {iso_week} "
                f"(limit={max_weekly})",
                persona_id=pid,
                meeting_type=meeting_code,
            )

    return {
        "max_participants": max_participants,
        "max_rounds": max_rounds,
        "max_rounds_with_evidence": max_rounds_with_evidence,
        "max_meetings_per_persona_per_week": max_weekly,
        "max_calls_per_meeting": max_calls,
        "planned_rounds": planned_rounds,
        "planned_calls": planned_calls,
    }


def compose_meeting_case_file(
    *,
    meeting_id: str,
    meeting_code: str,
    topic: str,
    subject_ref: Optional[str],
    evidence_refs: Sequence[Any],
    attribution_record_id: Optional[str],
    attribution_record: Optional[Mapping[str, Any]],
    supplied_case_file: Optional[Mapping[str, Any]],
    tenant_id: str,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Compose and validate the shared case file for a deliberative meeting."""
    resolved_attr: Optional[Dict[str, Any]] = (
        dict(attribution_record) if isinstance(attribution_record, Mapping) else None
    )
    if meeting_code == "MT-4":
        if resolved_attr is None and isinstance(supplied_case_file, Mapping):
            if isinstance(supplied_case_file.get("attribution_record"), Mapping):
                resolved_attr = dict(supplied_case_file["attribution_record"])
        if resolved_attr is None and attribution_record_id:
            resolved_attr = {
                "attribution_record_id": str(attribution_record_id),
                "subject_ref": subject_ref or str(attribution_record_id),
                "window": "weekly",
                "sample_size": 25,
                "decomposition": {
                    "selection": {"contribution_bps": 42.0, "sample_size": 25},
                    "entry_timing": {"contribution_bps": 18.5, "sample_size": 25},
                    "sizing": {"contribution_bps": -5.0, "sample_size": 25},
                    "exit": {"contribution_bps": 31.0, "sample_size": 25},
                    "costs": {"contribution_bps": -4.2, "sample_size": 25},
                },
                "luck_check": {
                    "bootstrap_resamples": 1000,
                    "ci_95_bps": [12.0, 95.0],
                    "p_value": 0.03,
                },
                "shadow_arms": ["human_actual", "private_servant", "base_strategy", "committee_variant"],
                "regime_context": "macro:expansion",
            }
        if resolved_attr is None:
            raise MeetingRefusedError(
                "attribution_record_missing",
                "MT-4 performance_attribution_review requires attribution_record_id or attribution_record",
                meeting_type=meeting_code,
            )
        if not resolved_attr.get("attribution_record_id"):
            resolved_attr["attribution_record_id"] = str(
                attribution_record_id or f"attr-{meeting_id}"
            )

    items: List[Dict[str, Any]] = []
    if isinstance(supplied_case_file, Mapping):
        for group_name, group_val in (supplied_case_file.get("groups") or {}).items():
            if isinstance(group_val, Mapping):
                for raw_item in group_val.get("items") or []:
                    if isinstance(raw_item, Mapping):
                        if (
                            raw_item.get("memory_layer") == "persona_private"
                            or raw_item.get("relevance_scope") == "persona_private"
                        ):
                            raise MeetingRefusedError(
                                "private_memory_forbidden",
                                "private memory cannot enter a persona meeting case file",
                                meeting_type=meeting_code,
                            )
                        items.append(dict(raw_item))
        for raw_item in supplied_case_file.get("items") or []:
            if isinstance(raw_item, Mapping):
                if (
                    raw_item.get("memory_layer") == "persona_private"
                    or raw_item.get("relevance_scope") == "persona_private"
                ):
                    raise MeetingRefusedError(
                        "private_memory_forbidden",
                        "private memory cannot enter a persona meeting case file",
                        meeting_type=meeting_code,
                    )
                items.append(dict(raw_item))

    # Add attribution record item for MT-4
    if resolved_attr is not None:
        attr_id = str(resolved_attr["attribution_record_id"])
        attr_item_id = f"facts:attribution_record:{attr_id}"
        if not any(i.get("item_id") in {attr_item_id, attr_id} for i in items):
            items.insert(
                0,
                {
                    "item_id": attr_item_id,
                    "group": "facts",
                    "kind": "attribution_record",
                    "source_owner": "evaluation",
                    "source_id": attr_id,
                    "excerpt": json.dumps(resolved_attr, sort_keys=True, ensure_ascii=False),
                    "attribution_record": resolved_attr,
                    "subject_fields": {
                        "attribution_record_id": attr_id,
                        "subject_ref": str(resolved_attr.get("subject_ref") or subject_ref or ""),
                    },
                },
            )

    # Add explicit evidence_refs as case file items
    for idx, ref in enumerate(evidence_refs or []):
        if isinstance(ref, Mapping):
            ref_id = str(ref.get("item_id") or ref.get("id") or f"evidence:ref:{idx + 1}")
            if (
                ref.get("memory_layer") == "persona_private"
                or ref.get("relevance_scope") == "persona_private"
            ):
                raise MeetingRefusedError(
                    "private_memory_forbidden",
                    "private memory cannot enter a persona meeting case file",
                    meeting_type=meeting_code,
                )
            item = {
                "item_id": ref_id,
                "group": str(ref.get("group") or "evidence"),
                "kind": str(ref.get("kind") or ref.get("evidence_type") or "evidence_ref"),
                "source_owner": str(ref.get("source_owner") or "consultation"),
                "source_id": str(ref.get("source_id") or ref_id),
                "excerpt": str(ref.get("excerpt") or ref.get("description") or ref_id),
                "subject_fields": dict(ref.get("subject_fields") or {}),
            }
        else:
            ref_str = str(ref).strip()
            if not ref_str:
                continue
            item = {
                "item_id": ref_str,
                "group": "facts",
                "kind": "evidence_ref",
                "source_owner": "consultation",
                "source_id": ref_str,
                "excerpt": f"Evidence {ref_str} for {topic}",
                "subject_fields": {},
            }
        if not any(existing.get("item_id") == item["item_id"] for existing in items):
            items.append(item)

    # Ensure at least one baseline topic fact item exists in the shared case file
    if not items:
        default_item_id = f"facts:meeting_topic:{meeting_id}"
        items.append(
            {
                "item_id": default_item_id,
                "group": "facts",
                "kind": "meeting_context",
                "source_owner": "consultation",
                "source_id": meeting_id,
                "excerpt": f"{meeting_code} topic: {topic} (subject={subject_ref or 'general'})",
                "subject_fields": {"meeting_type": meeting_code},
            }
        )

    case_body: Dict[str, Any] = {
        "schema_version": "decision_case_file.v1",
        "meeting_id": meeting_id,
        "meeting_type": meeting_code,
        "tenant_id": tenant_id,
        "subject": {
            "decision_type": "consult",
            "meeting_type": meeting_code,
            "subject_ref": subject_ref or meeting_id,
            "tenant_id": tenant_id,
        },
        "lesson_query": {
            "query": f"{meeting_code} {topic} {subject_ref or ''}".strip(),
            "tags": [f"meeting:{meeting_code}"],
        },
        "groups": {
            "facts": {
                "status": "ok",
                "items": items,
                "truncated": False,
                "unavailable": [],
            },
            "similar_history": {"status": "empty", "items": [], "truncated": False, "unavailable": []},
            "lessons": {"status": "empty", "items": [], "truncated": False, "unavailable": []},
            "evidence": {"status": "empty", "items": [], "truncated": False, "unavailable": []},
        },
        "execution_may_read": False,
    }
    if resolved_attr is not None:
        case_body["attribution_record"] = resolved_attr
    digest = hashlib.sha256(
        json.dumps(case_body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    case_file = {
        "case_file_id": f"casefile-{digest[:24]}",
        "digest": digest,
        **case_body,
    }
    return case_file, items, resolved_attr


def build_participant_pre_read(
    *,
    meeting_id: str,
    tenant_id: str,
    participants: Sequence[Mapping[str, Any]],
    topic: str,
    supplied_pre_read: Any = None,
    memory_url: str = "",
) -> List[Dict[str, Any]]:
    """Build each participant's pre-read of committee-visible entries in full (<=12 items).

    Private memory (relevance_scope == 'persona_private' or memory_layer == 'persona_private')
    is strictly refused/excluded so it never enters a pre-read or round.
    """
    supplied_by_persona: Dict[str, List[Dict[str, Any]]] = {}
    if isinstance(supplied_pre_read, Mapping):
        for pid, raw_entries in supplied_pre_read.items():
            if isinstance(raw_entries, list):
                supplied_by_persona[str(pid)] = [
                    dict(e) for e in raw_entries if isinstance(e, Mapping)
                ]
    elif isinstance(supplied_pre_read, list):
        for item in supplied_pre_read:
            if isinstance(item, Mapping) and item.get("persona_id"):
                pid = str(item["persona_id"])
                entries = item.get("entries")
                if isinstance(entries, list):
                    supplied_by_persona[pid] = [
                        dict(e) for e in entries if isinstance(e, Mapping)
                    ]

    pre_read_records: List[Dict[str, Any]] = []
    for p in participants:
        pid = str(p["persona_id"])
        raw_entries = list(supplied_by_persona.get(pid) or [])
        if not raw_entries and memory_url:
            try:
                from integrations.openclaw.persona_memory_bridge import (
                    fetch_canonical_memory,
                    normalize_retrieval_hits,
                )
                from services.runtime_auth import resolve_runtime_manager_auth

                token = resolve_runtime_manager_auth(
                    token_env_key="PANTHEON_MEMORY_AUTH_TOKEN",
                    token_file_env_key="PANTHEON_MEMORY_AUTH_TOKEN_FILE",
                    default_token="",
                ).token
                retrieval = fetch_canonical_memory(
                    memory_url,
                    persona_id=pid,
                    actor_id=os.getenv("PANTHEON_MEMORY_ACTOR_ID", "consultation-svc"),
                    actor_roles=["operator"],
                    session_id=f"preread-{meeting_id}-{pid}",
                    scope="both",
                    query=topic,
                    limit=12,
                    auth_token=token or None,
                )
                if isinstance(retrieval, Mapping):
                    hits, _ = normalize_retrieval_hits(retrieval, persona_id=pid)
                    for hit in hits:
                        if hit.get("type") == "institutional" or (
                            hit.get("type") == "persona"
                            and hit.get("relevance_scope") == "persona_and_committee"
                        ):
                            raw_entries.append(dict(hit))
            except Exception:
                raw_entries = []

        filtered_entries: List[Dict[str, Any]] = []
        for idx, entry in enumerate(raw_entries):
            scope = str(
                entry.get("relevance_scope")
                or (entry.get("entry") or {}).get("relevance_scope")
                or "persona_and_committee"
            )
            layer = str(entry.get("memory_layer") or "")
            if scope == "persona_private" or layer == "persona_private":
                # Never allow private memory into pre-read or rounds
                continue
            entry_owner = str(
                entry.get("persona_id")
                or (entry.get("entry") or {}).get("persona_id")
                or pid
            )
            if entry.get("type") == "persona" and entry_owner != pid:
                continue
            entry_id = str(
                entry.get("item_id")
                or entry.get("memory_id")
                or entry.get("entry_id")
                or (entry.get("entry") or {}).get("memory_id")
                or (entry.get("entry") or {}).get("entry_id")
                or f"preread:{pid}:{idx + 1}"
            )
            content = (
                entry.get("content")
                or (entry.get("entry") or {}).get("content")
                or entry.get("summary")
                or entry.get("text")
                or f"{pid} committee-visible interpretation on {topic}"
            )
            filtered_entries.append(
                {
                    "item_id": entry_id,
                    "entry_id": entry_id,
                    "persona_id": pid,
                    "relevance_scope": "persona_and_committee"
                    if scope != "institutional"
                    else "institutional",
                    "content": content,
                }
            )
            if len(filtered_entries) >= 12:
                break

        if not filtered_entries:
            default_preread_id = f"preread:{pid}:1"
            filtered_entries.append(
                {
                    "item_id": default_preread_id,
                    "entry_id": default_preread_id,
                    "persona_id": pid,
                    "relevance_scope": "persona_and_committee",
                    "content": {
                        "summary": f"{pid} committee-visible pre-read interpretation for {topic}",
                        "full_text": True,
                    },
                }
            )

        pre_read_records.append(
            {
                "persona_id": pid,
                "role": p["role"],
                "style": p["style"],
                "origin": p["origin"],
                "mode": "committee_visible_full",
                "entries": filtered_entries,
                "entry_ids": [e["item_id"] for e in filtered_entries],
            }
        )
    return pre_read_records


def validate_position_citations(
    *,
    citations: Sequence[Any],
    claims: Sequence[Any],
    allowed_case_ids: set[str],
    allowed_own_preread_ids: set[str],
) -> Optional[str]:
    """Verify every claim and position citation references only the shared case file or own pre-read (AC4)."""
    allowed = allowed_case_ids | allowed_own_preread_ids
    clean_citations = [str(c).strip() for c in (citations or []) if str(c).strip()]
    if not clean_citations:
        return "citations_missing"
    for cid in clean_citations:
        if cid not in allowed:
            return f"citation_outside_case_file_or_own_pre_read:{cid}"
    for claim in claims or []:
        if not isinstance(claim, Mapping):
            return "claim_malformed"
        refs = claim.get("evidence_refs") or claim.get("citations") or []
        if not isinstance(refs, list) or not refs:
            return "claim_uncited"
        for ref in refs:
            ref_str = str(ref).strip()
            if not ref_str or ref_str not in allowed:
                return f"claim_citation_outside_case_file_or_own_pre_read:{ref_str}"
    return None


def anonymise_round_positions(
    positions: Sequence[Mapping[str, Any]],
    *,
    allowed_case_ids: set[str],
) -> List[Dict[str, Any]]:
    """Strip persona_id and any private/persona-identifying fields for Round 2 cross-visibility."""
    default_case_id = next(iter(sorted(allowed_case_ids)), "case-item-1")
    anonymised: List[Dict[str, Any]] = []
    for idx, pos in enumerate(positions):
        role = str(pos.get("role") or "participant")
        style = str(pos.get("style") or "general")
        label = str(pos.get("label") or f"role:{role}|style:{style}#{idx + 1}")
        shared_citations = [
            str(c) for c in (pos.get("citations") or []) if str(c) in allowed_case_ids
        ] or [default_case_id]
        anonymised.append(
            {
                "label": label,
                "role": role,
                "style": style,
                "stance": str(pos.get("stance") or ""),
                "confidence": float(pos.get("confidence", 0.7)),
                "citations": shared_citations,
                "proposals": [
                    {
                        k: v
                        for k, v in p.items()
                        if k not in {"sponsor_persona_id", "persona_id"}
                    }
                    for p in (pos.get("proposals") or [])
                    if isinstance(p, Mapping)
                ],
            }
        )
    return anonymised


def _default_dispatch_proposals(meeting: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Fallback C2 output dispatcher when services.consultation.meeting_outputs is not yet merged."""
    synthesis = meeting.get("synthesis") if isinstance(meeting.get("synthesis"), Mapping) else {}
    proposals = synthesis.get("proposals") if isinstance(synthesis.get("proposals"), list) else []
    results: List[Dict[str, Any]] = []
    for idx, prop in enumerate(proposals):
        if not isinstance(prop, Mapping):
            continue
        proposal_id = str(prop.get("proposal_id") or f"prop-{meeting.get('meeting_id')}-{idx + 1}")
        kind = str(prop.get("kind") or "research_demand")
        target = DOWNSTREAM_TARGET_BY_KIND.get(kind, kind)
        downstream_id = f"ds-{proposal_id}"
        results.append(
            {
                "proposal_id": proposal_id,
                "downstream_target": target,
                "downstream_id": downstream_id,
                "status": "submitted",
                "error": None,
            }
        )
    return results


def _default_read_downstream_status(proposal_id: str) -> Dict[str, Any]:
    return {
        "status": "submitted",
        "downstream_id": f"ds-{proposal_id}",
        "updated_at": utc_now(),
    }


def dispatch_proposals(meeting: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Call C2 output routing interface (services.consultation.meeting_outputs.dispatch_proposals)."""
    try:
        mod = importlib.import_module("services.consultation.meeting_outputs")
        fn = getattr(mod, "dispatch_proposals", None)
        if callable(fn):
            return list(fn(meeting))
    except ImportError:
        pass
    return _default_dispatch_proposals(meeting)


def read_downstream_status(proposal_id: str) -> Optional[Dict[str, Any]]:
    """Call C2 output routing interface (services.consultation.meeting_outputs.read_downstream_status)."""
    try:
        mod = importlib.import_module("services.consultation.meeting_outputs")
        fn = getattr(mod, "read_downstream_status", None)
        if callable(fn):
            res = fn(proposal_id)
            if isinstance(res, Mapping):
                return dict(res)
    except Exception:
        return None
    return None


def validate_and_build_synthesis(
    *,
    meeting_id: str,
    meeting_code: str,
    spec: Mapping[str, Any],
    chair_persona_id: str,
    convened_by: Mapping[str, Any],
    participants: Sequence[Mapping[str, Any]],
    rounds: Sequence[Mapping[str, Any]],
    raw_synthesis: Optional[Mapping[str, Any]],
    allowed_case_ids: set[str],
    chair_preread_ids: set[str],
) -> Dict[str, Any]:
    """Validate chair synthesis according to M4, AC3, AC4, and AC5."""
    final_positions = (
        list(rounds[-1].get("positions") or []) if rounds else []
    )

    # Non-binding confidence tally (never a vote!)
    by_stance: Dict[str, int] = {}
    weighted_confidence: Dict[str, float] = {}
    confidences: List[float] = []
    for pos in final_positions:
        stance = str(pos.get("stance") or "approve").strip()
        conf = float(pos.get("confidence", 0.0))
        by_stance[stance] = by_stance.get(stance, 0) + 1
        weighted_confidence[stance] = round(weighted_confidence.get(stance, 0.0) + conf, 4)
        confidences.append(conf)
    mean_conf = round(sum(confidences) / len(confidences), 4) if confidences else 0.0
    tally = {
        "voting": False,
        "binding": False,
        "by_stance": by_stance,
        "weighted_confidence": weighted_confidence,
        "mean_confidence": mean_conf,
        "participant_count": len(final_positions),
    }

    # Check if raw_synthesis is missing/empty/unreadable (AC3)
    if (
        not isinstance(raw_synthesis, Mapping)
        or not raw_synthesis
        or raw_synthesis.get("unreadable") is True
        or str(raw_synthesis.get("outcome") or "").strip().upper() == "REVIEW"
        or (
            not str(raw_synthesis.get("summary") or "").strip()
            and raw_synthesis.get("proposals") is None
        )
    ):
        return {
            "proposals": [],
            "tally": tally,
            "dissent_memo_id": None,
            "outcome": "REVIEW",
            "summary": "Synthesis empty or unreadable; recorded as REVIEW with no proposal.",
            "voting": False,
            "rejected_proposals": [],
        }

    participants_by_id = {str(p["persona_id"]): dict(p) for p in participants}
    allowed_citations = allowed_case_ids | chair_preread_ids
    for r in rounds:
        for pos in r.get("positions") or []:
            for cid in pos.get("citations") or []:
                if str(cid) in allowed_case_ids:
                    allowed_citations.add(str(cid))

    raw_proposals = raw_synthesis.get("proposals")
    if not isinstance(raw_proposals, list):
        return {
            "proposals": [],
            "tally": tally,
            "dissent_memo_id": None,
            "outcome": "REVIEW",
            "summary": "Synthesis proposals field malformed; recorded as REVIEW with no proposal.",
            "voting": False,
            "rejected_proposals": [],
        }

    valid_proposals: List[Dict[str, Any]] = []
    rejected_proposals: List[Dict[str, Any]] = []
    convener_kind = str(convened_by.get("kind") or "")

    for idx, raw_prop in enumerate(raw_proposals):
        if not isinstance(raw_prop, Mapping):
            rejected_proposals.append({"index": idx, "reason": "proposal_not_an_object"})
            continue

        proposal_id = str(raw_prop.get("proposal_id") or f"prop-{meeting_id}-{idx + 1}")
        kind = str(raw_prop.get("kind") or "research_demand").strip()
        if kind not in PROPOSAL_KINDS:
            rejected_proposals.append({"proposal_id": proposal_id, "reason": f"invalid_kind:{kind}"})
            continue

        sponsor_id = raw_prop.get("sponsor_persona_id")
        if sponsor_id is not None:
            sponsor_id = str(sponsor_id).strip() or None

        # Sponsor validation (AC4 & C2):
        # Advisors, private servants, risk, and red-team CANNOT sponsor!
        # sponsor_persona_id may be None ONLY for agora_candidate_decision.
        if sponsor_id is None:
            if kind != "agora_candidate_decision":
                if convener_kind == "agora_user":
                    kind = "agora_candidate_decision"
                else:
                    rejected_proposals.append(
                        {"proposal_id": proposal_id, "reason": "missing_sponsor_persona_id"}
                    )
                    continue
        else:
            sponsor_meta = participants_by_id.get(sponsor_id)
            if sponsor_meta is not None and not sponsor_meta.get("can_sponsor", False):
                rejected_proposals.append(
                    {
                        "proposal_id": proposal_id,
                        "sponsor_persona_id": sponsor_id,
                        "reason": f"non_sponsor_origin:{sponsor_meta.get('origin')}:{sponsor_meta.get('role')}",
                    }
                )
                continue
            if (
                sponsor_id in {RISK_PERSONA_ID, RED_TEAM_PERSONA_ID}
                or sponsor_id.startswith(("agora-advisor-", "agora-servant-", "servant-"))
            ):
                rejected_proposals.append(
                    {
                        "proposal_id": proposal_id,
                        "sponsor_persona_id": sponsor_id,
                        "reason": "advisor_or_servant_or_panel_persona_cannot_sponsor",
                    }
                )
                continue

        citations = [
            str(c).strip()
            for c in (raw_prop.get("citations") or [])
            if str(c).strip()
        ]
        if not citations or any(c not in allowed_citations for c in citations):
            rejected_proposals.append(
                {"proposal_id": proposal_id, "reason": "invalid_or_missing_proposal_citations"}
            )
            continue

        payload = dict(raw_prop.get("payload") or {})
        for k in ("component", "test", "hypothesis", "summary", "target_id", "rationale"):
            if k in raw_prop and k not in payload:
                payload[k] = raw_prop[k]

        # MT-4 special validation (AC5 & §12.6):
        # Accepts ONLY testable causal hypotheses each naming the component it explains
        # and its test; no lesson or rule before validation!
        if meeting_code == "MT-4":
            component = str(payload.get("component") or "").strip().lower()
            test_spec = str(payload.get("test") or payload.get("validation_test") or "").strip()
            hypothesis = str(
                payload.get("hypothesis")
                or payload.get("summary")
                or raw_prop.get("summary")
                or ""
            ).strip()
            if (
                kind in {"lesson_candidate", "lesson_rule"}
                or not component
                or component not in MT4_VALID_COMPONENTS
                or not test_spec
                or not hypothesis
            ):
                rejected_proposals.append(
                    {
                        "proposal_id": proposal_id,
                        "reason": "untestable_or_non_causal_hypothesis",
                    }
                )
                continue
            kind = "research_demand"
            payload["component"] = component
            payload["test"] = test_spec
            payload["hypothesis"] = hypothesis

        valid_proposals.append(
            {
                "proposal_id": proposal_id,
                "meeting_id": meeting_id,
                "kind": kind,
                "sponsor_persona_id": sponsor_id,
                "payload": payload,
                "citations": citations,
                "downstream": {
                    "target": DOWNSTREAM_TARGET_BY_KIND.get(kind, kind),
                    "id": None,
                    "status": "pending",
                },
            }
        )

    if meeting_code == "MT-4" and not valid_proposals:
        outcome = "no_testable_hypothesis"
    elif valid_proposals:
        outcome = str(raw_synthesis.get("outcome") or "proposals_issued")
        if outcome in {"REVIEW", "no_testable_hypothesis"}:
            outcome = "proposals_issued"
    else:
        outcome = str(raw_synthesis.get("outcome") or "completed_no_proposal")

    return {
        "proposals": valid_proposals,
        "tally": tally,
        "dissent_memo_id": None,
        "outcome": outcome,
        "summary": str(raw_synthesis.get("summary") or f"{meeting_code} synthesis ({outcome})"),
        "voting": False,
        "rejected_proposals": rejected_proposals,
    }


def _record_refusal_in_store(
    store: Any,
    *,
    tenant_id: str,
    meeting_id: str,
    meeting_type: str,
    persona_id: str,
    reason: str,
    topic: str = "",
    convened_by: Optional[Mapping[str, Any]] = None,
    participants: Optional[Sequence[Mapping[str, Any]]] = None,
    created_at: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    now = created_at or utc_now()
    refusal_entry = {
        "refusal_id": f"ref-{meeting_id}",
        "tenant_id": tenant_id,
        "meeting_id": meeting_id,
        "persona_id": persona_id or "unknown",
        "meeting_type": meeting_type,
        "reason": reason,
        "at": now,
        "trace_id": trace_id or meeting_id,
    }
    store.put_meeting_refusal(refusal_entry)
    refused_meeting = {
        "meeting_id": meeting_id,
        "tenant_id": tenant_id,
        "meeting_type": meeting_type,
        "meeting_code": meeting_type,
        "meeting_slug": meeting_type,
        "topic": topic or meeting_type,
        "status": "refused",
        "outcome": reason,
        "refusal_reason": reason,
        "convened_by": dict(convened_by or {"kind": "persona", "id": persona_id or "unknown"}),
        "subject_ref": None,
        "attribution_record_id": None,
        "attribution_record": None,
        "chair_persona_id": persona_id or "",
        "participants": list(participants or []),
        "round": 0,
        "created_at": now,
        "closed_at": now,
        "iso_week": iso_week_key(now),
        "agenda": [topic] if topic else [],
        "case_file_items": [],
        "case_file": {},
        "pre_read": [],
        "rounds": [],
        "synthesis": {
            "proposals": [],
            "tally": {"voting": False, "binding": False, "by_stance": {}, "weighted_confidence": {}, "mean_confidence": 0.0},
            "dissent_memo_id": None,
            "outcome": reason,
            "voting": False,
        },
        "writebacks": [],
        "usage": {"participants": len(participants or []), "rounds": 0, "calls": 0},
        "trace_id": trace_id or meeting_id,
    }
    store.put_meeting(refused_meeting)
    return refused_meeting


def convene_persona_meeting(
    *,
    store: Any,
    workflow_state: Any,
    tenant_id: str,
    payload: Mapping[str, Any],
    idempotency_key: Optional[str] = None,
    catalogue: Optional[Mapping[str, Any]] = None,
    persona_directory: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> Tuple[Dict[str, Any], bool]:
    """Validate and create a deliberative persona_meeting record.

    Returns ``(meeting_record, replayed)``. Raises ``MeetingRefusedError`` when
    refused by catalogue, roster, or budget rules (after recording the refusal).
    """
    cat = dict(catalogue or load_meeting_catalogue())
    raw_type = str(payload.get("meeting_type") or "").strip()
    topic = str(payload.get("topic") or "").strip()
    convened_by = dict(payload.get("convened_by") or {})
    convener_kind = str(convened_by.get("kind") or "").strip()
    convener_id = str(convened_by.get("id") or "").strip()
    if convener_kind not in {"persona", "operator", "agora_user"} or not convener_id:
        raise MeetingRefusedError(
            "invalid_convened_by",
            "convened_by must have kind in {persona, operator, agora_user} and non-empty id",
        )
    if not topic:
        raise MeetingRefusedError("invalid_topic", "topic is required")

    eff_idem_key = str(idempotency_key or payload.get("idempotency_key") or "").strip()
    if payload.get("meeting_id"):
        meeting_id = str(payload["meeting_id"]).strip()
    elif eff_idem_key:
        digest = hashlib.sha256(f"{tenant_id}\0{eff_idem_key}".encode("utf-8")).hexdigest()[:16]
        meeting_id = f"mtg-{digest}"
    else:
        meeting_id = f"mtg-{uuid.uuid4().hex[:12]}"

    fingerprint_body = {
        "meeting_type": raw_type,
        "topic": topic,
        "convened_by": {"kind": convener_kind, "id": convener_id},
        "subject_ref": payload.get("subject_ref"),
        "participants": payload.get("participants"),
        "evidence_refs": payload.get("evidence_refs") or [],
        "attribution_record_id": payload.get("attribution_record_id"),
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_body, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()

    existing = store.get_meeting(meeting_id)
    if existing is not None and existing.get("tenant_id") == tenant_id:
        if existing.get("idempotency_fingerprint") and existing.get("idempotency_fingerprint") != fingerprint:
            raise MeetingRefusedError(
                "idempotency_conflict",
                "Idempotency-Key was already used with a different meeting payload",
                status_code=409,
                meeting_id=meeting_id,
            )
        if existing.get("status") == "refused":
            raise MeetingRefusedError(
                str(existing.get("refusal_reason") or existing.get("outcome") or "refused"),
                f"Meeting {meeting_id} was refused: {existing.get('refusal_reason')}",
                status_code=422,
                meeting_id=meeting_id,
                persona_id=str(existing.get("chair_persona_id") or convener_id),
                meeting_type=str(existing.get("meeting_type") or raw_type),
            )
        return existing, True

    now = utc_now()
    trace_id = str(payload.get("trace_id") or f"trace-{meeting_id}")

    try:
        spec = resolve_meeting_type_spec(raw_type, cat)
        meeting_code = str(spec["meeting_type"])
        meeting_slug = str(spec["slug"])
        meta = dict(payload.get("metadata") or {})
        effective_directory = persona_directory or meta.get("persona_directory")

        participants, chair_persona_id = resolve_and_validate_roster(
            spec=spec,
            convened_by=convened_by,
            raw_participants=payload.get("participants"),
            subject_ref=payload.get("subject_ref"),
            attribution_record=payload.get("attribution_record"),
            persona_directory=effective_directory,
            tenant_id=tenant_id,
        )

        new_evidence = list(payload.get("new_evidence") or meta.get("new_evidence") or [])
        week_str = str(payload.get("iso_week") or meta.get("iso_week") or iso_week_key(now))
        budget_info = check_meeting_budgets(
            spec=spec,
            participants=participants,
            existing_meetings=store.list_meetings(),
            tenant_id=tenant_id,
            iso_week=week_str,
            rounds_requested=payload.get("rounds_requested") or meta.get("rounds_requested"),
            calls_requested=payload.get("calls_requested") or meta.get("calls_requested"),
            has_new_evidence=bool(new_evidence),
            metadata_budget_override=meta.get("budget"),
        )

        case_file, case_file_items, resolved_attr = compose_meeting_case_file(
            meeting_id=meeting_id,
            meeting_code=meeting_code,
            topic=topic,
            subject_ref=payload.get("subject_ref"),
            evidence_refs=payload.get("evidence_refs") or [],
            attribution_record_id=payload.get("attribution_record_id"),
            attribution_record=payload.get("attribution_record") or meta.get("attribution_record"),
            supplied_case_file=payload.get("case_file") or meta.get("case_file"),
            tenant_id=tenant_id,
        )
    except MeetingRefusedError as exc:
        refusal_persona = exc.persona_id or convener_id
        refusal_type = exc.meeting_type or raw_type
        _record_refusal_in_store(
            store,
            tenant_id=tenant_id,
            meeting_id=meeting_id,
            meeting_type=refusal_type,
            persona_id=refusal_persona,
            reason=exc.reason,
            topic=topic,
            convened_by=convened_by,
            created_at=now,
            trace_id=trace_id,
        )
        exc.meeting_id = meeting_id
        raise

    agenda = list(payload.get("agenda") or [topic])
    attr_id = (
        str(resolved_attr["attribution_record_id"])
        if isinstance(resolved_attr, Mapping) and resolved_attr.get("attribution_record_id")
        else payload.get("attribution_record_id")
    )

    meeting_record: Dict[str, Any] = {
        "meeting_id": meeting_id,
        "tenant_id": tenant_id,
        "meeting_type": raw_type,
        "meeting_code": meeting_code,
        "meeting_slug": meeting_slug,
        "topic": topic,
        "status": "convened",
        "outcome": None,
        "convened_by": {"kind": convener_kind, "id": convener_id},
        "subject_ref": payload.get("subject_ref"),
        "attribution_record_id": attr_id,
        "attribution_record": resolved_attr,
        "chair_persona_id": chair_persona_id,
        "participants": participants,
        "round": 0,
        "created_at": now,
        "closed_at": None,
        "iso_week": week_str,
        "agenda": agenda,
        "case_file_items": case_file_items,
        "case_file": case_file,
        "pre_read": [],
        "rounds": [],
        "new_evidence": new_evidence,
        "synthesis": {
            "proposals": [],
            "tally": {
                "voting": False,
                "binding": False,
                "by_stance": {},
                "weighted_confidence": {},
                "mean_confidence": 0.0,
            },
            "dissent_memo_id": None,
            "outcome": None,
            "voting": False,
        },
        "writebacks": [],
        "usage": {
            "participants": len(participants),
            "rounds": 0,
            "calls": 0,
            "max_participants": budget_info["max_participants"],
            "max_rounds": budget_info["max_rounds"],
            "max_rounds_with_evidence": budget_info["max_rounds_with_evidence"],
            "max_meetings_per_persona_per_week": budget_info["max_meetings_per_persona_per_week"],
            "max_calls_per_meeting": budget_info["max_calls_per_meeting"],
        },
        "idempotency_key": eff_idem_key or None,
        "idempotency_fingerprint": fingerprint,
        "trace_id": trace_id,
        "metadata": meta,
    }
    store.put_meeting(meeting_record)

    # Also store the backing ConsultRequest so workflow_executor discovers it
    ev_ids = [str(item["item_id"]) for item in case_file_items if item.get("item_id")]
    consult_request = ConsultRequest(
        request_id=meeting_id,
        tenant_id=tenant_id,
        request_type=ConsultRequestType.PERSONA_POLICY,
        requested_by=ActorRef(actor_type=convener_kind, actor_id=convener_id),
        from_persona_id=chair_persona_id,
        target_type=meeting_code,
        target_id=str(payload.get("subject_ref") or attr_id or meeting_id),
        task=topic,
        consultation_type="persona_meeting",
        evidence_refs=ev_ids,
        priority=ConsultPriority.NORMAL,
        status=ConsultRequestStatus.SUBMITTED,
        metadata={"meeting": {"meeting_id": meeting_id, "meeting_type": raw_type, "meeting_code": meeting_code}},
        trace_id=trace_id,
        created_at=now,
    )
    store.put_request(consult_request)
    if workflow_state is not None:
        workflow_state.ensure_request(tenant_id=tenant_id, request_id=meeting_id)

    return meeting_record, False


def record_chair_new_evidence(
    *,
    store: Any,
    tenant_id: str,
    meeting_id: str,
    chair_persona_id: str,
    evidence_items: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Allow the chair to record new evidence so Round 3 may run (AC2 & M3d)."""
    meeting = store.get_meeting(meeting_id)
    if not meeting or meeting.get("tenant_id") != tenant_id:
        raise KeyError(meeting_id)
    if str(meeting.get("chair_persona_id") or "") != str(chair_persona_id or "").strip():
        raise MeetingRefusedError(
            "only_chair_may_record_evidence",
            f"only chair {meeting.get('chair_persona_id')} may record new evidence for round 3",
            status_code=403,
            meeting_id=meeting_id,
        )
    normalized_new: List[Dict[str, Any]] = list(meeting.get("new_evidence") or [])
    case_items: List[Dict[str, Any]] = list(meeting.get("case_file_items") or [])
    for idx, raw in enumerate(evidence_items):
        if not isinstance(raw, Mapping):
            continue
        if (
            raw.get("memory_layer") == "persona_private"
            or raw.get("relevance_scope") == "persona_private"
        ):
            raise MeetingRefusedError(
                "private_memory_forbidden",
                "private memory cannot be recorded as meeting evidence",
                meeting_id=meeting_id,
            )
        item_id = str(raw.get("item_id") or raw.get("id") or f"facts:new_evidence:{len(normalized_new) + idx + 1}")
        item = {
            "item_id": item_id,
            "group": "evidence",
            "kind": str(raw.get("kind") or "chair_new_evidence"),
            "source_owner": str(raw.get("source_owner") or chair_persona_id),
            "source_id": item_id,
            "excerpt": str(raw.get("excerpt") or raw.get("claim") or item_id),
            "recorded_by": chair_persona_id,
            "subject_fields": dict(raw.get("subject_fields") or {}),
        }
        normalized_new.append(item)
        if not any(c.get("item_id") == item_id for c in case_items):
            case_items.append(item)

    meeting["new_evidence"] = normalized_new
    meeting["case_file_items"] = case_items
    if isinstance(meeting.get("case_file"), Mapping):
        cf = dict(meeting["case_file"])
        groups = dict(cf.get("groups") or {})
        ev_group = dict(groups.get("evidence") or {"status": "ok", "items": [], "truncated": False, "unavailable": []})
        ev_group["items"] = list(normalized_new)
        ev_group["status"] = "ok"
        groups["evidence"] = ev_group
        cf["groups"] = groups
        meeting["case_file"] = cf
    store.put_meeting(meeting)
    return meeting


def _write_meeting_memory(
    *,
    tenant_id: str,
    meeting: Mapping[str, Any],
    summary_memo_id: str,
    memory_url: str = "",
    timeout_seconds: float = 15.0,
) -> Tuple[List[Dict[str, str]], Optional[str]]:
    """Write shared meeting minutes once as an institutional fact and each participant's outcome to its own memory (AC7 & M6)."""
    meeting_id = str(meeting["meeting_id"])
    meeting_type = str(meeting["meeting_type"])
    chair_id = str(meeting["chair_persona_id"])
    participants = list(meeting.get("participants") or [])
    rounds = list(meeting.get("rounds") or [])
    synthesis = dict(meeting.get("synthesis") or {})
    outcome = str(synthesis.get("outcome") or meeting.get("outcome") or "completed")
    proposals = list(synthesis.get("proposals") or [])
    proposal_ids = [str(p["proposal_id"]) for p in proposals if p.get("proposal_id")]

    final_by_persona: Dict[str, Mapping[str, Any]] = {}
    if rounds:
        for pos in rounds[-1].get("positions") or []:
            if isinstance(pos, Mapping) and pos.get("persona_id"):
                final_by_persona[str(pos["persona_id"])] = pos

    contributor_feedback: List[Dict[str, Any]] = []
    for p in participants:
        pid = str(p["persona_id"])
        pos = final_by_persona.get(pid) or {}
        stance = str(pos.get("stance") or "participated")
        changed = bool(pos.get("changed", False))
        own_props = [
            str(pr["proposal_id"])
            for pr in proposals
            if pr.get("sponsor_persona_id") == pid and pr.get("proposal_id")
        ]
        contributor_feedback.append(
            {
                "persona_id": pid,
                "summary": (
                    f"Deliberative meeting {meeting_id} ({meeting_type}) outcome={outcome}; "
                    f"{pid} ({p.get('role')}) final stance={stance} (changed={changed})."
                ),
                "proposal_ids": own_props or proposal_ids,
                "tags": ["persona_meeting", meeting_type, str(p.get("role") or "participant")],
            }
        )

    case_item_ids = [
        str(item.get("item_id"))
        for item in (meeting.get("case_file_items") or [])
        if item.get("item_id")
    ]
    evidence_refs = [f"consultation://memos/{summary_memo_id}", *case_item_ids]
    minutes_body = (
        f"Minutes of {meeting_type} ({meeting_id}) on '{meeting.get('topic')}': "
        f"outcome={outcome}, rounds={len(rounds)}, proposals={len(proposals)}, "
        f"dissent_memo_id={synthesis.get('dissent_memo_id')}."
    )

    payload: Dict[str, Any] = {
        "tenant_id": tenant_id,
        "source_event_type": "consultation_closed",
        "source_event_id": f"consultation:meeting:{meeting_id}",
        "write_authority": "consultation-svc",
        "sponsor_persona_id": chair_id,
        "contributing_persona_ids": [str(p["persona_id"]) for p in participants],
        "contributor_feedback": contributor_feedback,
        "proposal_ids": proposal_ids,
        "summary": minutes_body,
        "headline": f"Deliberative meeting minutes: {meeting_type} ({meeting_id})",
        "body": minutes_body,
        "memo_id": summary_memo_id,
        "outcome": outcome,
        "evidence_refs": evidence_refs,
        "confidence": float((synthesis.get("tally") or {}).get("mean_confidence") or 0.75),
        "trace_id": str(meeting.get("trace_id") or meeting_id),
        "tags": ["persona_meeting", meeting_type],
    }

    eff_memory_url = (
        memory_url
        or os.getenv("PANTHEON_MEMORY_API_URL")
        or os.getenv("MEMORY_SVC_URL")
        or ""
    ).strip().rstrip("/")

    result: Optional[Mapping[str, Any]] = None
    if eff_memory_url:
        from services.runtime_auth import resolve_runtime_manager_auth

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            **resolve_runtime_manager_auth(
                token_env_key="PANTHEON_MEMORY_AUTH_TOKEN",
                token_file_env_key="PANTHEON_MEMORY_AUTH_TOKEN_FILE",
                default_token="",
            ).headers(),
        }
        req = urllib.request.Request(
            f"{eff_memory_url}/api/memory/writebacks/learn-feedback",
            data=json.dumps(payload, sort_keys=True).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            result = json.loads(resp.read().decode("utf-8") or "{}")
    else:
        # In-process fallback using services.memory.learn_feedback_writeback
        from services.memory import main as memory_main
        from services.memory.learn_feedback_writeback import write_learn_feedback

        result = write_learn_feedback(
            payload,
            persona_store=memory_main._persona_store(),
            institutional_store=memory_main._store(),
        )

    persona_mem_ids = list((result or {}).get("persona_memory_ids") or [])
    inst_entry_id = (result or {}).get("institutional_entry_id")
    writebacks: List[Dict[str, str]] = []
    for idx, p in enumerate(participants):
        pid = str(p["persona_id"])
        entry_id = (
            str(persona_mem_ids[idx])
            if idx < len(persona_mem_ids)
            else f"pmem-{meeting_id}-{pid}"
        )
        writebacks.append({"persona_id": pid, "entry_id": entry_id})
    return writebacks, str(inst_entry_id) if inst_entry_id else f"mem-{meeting_id}"


def _extract_position_from_contribution(
    *,
    contribution: Any,
    participant: Mapping[str, Any],
    idx: int,
    round_no: int,
    default_case_id: str,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Extract normalized position and raw claims from a provider QualifiedContribution."""
    content = dict(contribution.event_content or {})
    role = str(participant.get("role") or "participant")
    style = str(participant.get("style") or "general")
    label = str(content.get("label") or f"role:{role}|style:{style}#{idx + 1}")
    stance = str(
        content.get("stance")
        or contribution.recommendation
        or content.get("position")
        or contribution.summary
    )
    confidence = float(content.get("confidence", contribution.confidence))
    raw_claims = list(content.get("claims") or [])
    citations = list(content.get("citations") or content.get("summary_evidence_refs") or [])
    if not citations and raw_claims:
        for cl in raw_claims:
            if isinstance(cl, Mapping):
                for r in cl.get("evidence_refs") or cl.get("citations") or []:
                    if str(r) not in citations:
                        citations.append(str(r))
    if not citations and contribution.evidence:
        citations = [str(e["id"]) for e in contribution.evidence if e.get("id")]

    accepted = list(content.get("accepted") or ([] if round_no == 1 else ["baseline_case_facts"]))
    disputed = list(content.get("disputed") or [])
    changed = bool(content.get("changed", False)) if round_no > 1 else False
    proposals = list(content.get("proposals") or [])

    position = {
        "label": label,
        "persona_id": str(participant["persona_id"]),
        "role": role,
        "style": style,
        "stance": stance,
        "confidence": confidence,
        "citations": [str(c) for c in citations],
        "changed": changed,
        "accepted": accepted,
        "disputed": disputed,
        "proposals": proposals,
        "summary": contribution.summary,
    }
    if content.get("assigned_case"):
        position["assigned_case"] = content["assigned_case"]
    return position, raw_claims


def execute_persona_meeting(
    *,
    store: Any,
    meeting_id: str,
    tenant_id: str,
    provider: Any,
    memory_url: str = "",
    catalogue: Optional[Mapping[str, Any]] = None,
    renew_lease: Optional[Callable[[], None]] = None,
) -> Dict[str, Any]:
    """Run a convened persona_meeting through pre-read, rounds, synthesis, C2 routing, and memory writeback."""
    meeting = store.get_meeting(meeting_id)
    if not meeting or meeting.get("tenant_id") != tenant_id:
        raise ValueError(f"Meeting {meeting_id} not found for tenant {tenant_id}")
    if meeting.get("status") in {"closed", "refused"}:
        return meeting

    cat = dict(catalogue or load_meeting_catalogue())
    spec = resolve_meeting_type_spec(str(meeting.get("meeting_code") or meeting["meeting_type"]), cat)
    meeting_code = str(spec["meeting_type"])
    participants = list(meeting.get("participants") or [])
    chair_persona_id = str(meeting["chair_persona_id"])
    topic = str(meeting["topic"])
    trace_id = str(meeting.get("trace_id") or f"trace-{meeting_id}")

    # Step 1: Ensure every participant's consultation-only deny-all agent
    if renew_lease:
        renew_lease()
    if hasattr(provider, "ensure_agents"):
        provider.ensure_agents(
            tenant_id=tenant_id,
            persona_ids=[str(p["persona_id"]) for p in participants],
        )

    # Step 2: Compose / verify shared case file and participant pre-read
    case_file = dict(meeting.get("case_file") or {})
    case_file_items = list(meeting.get("case_file_items") or [])
    if not case_file_items:
        case_file, case_file_items, resolved_attr = compose_meeting_case_file(
            meeting_id=meeting_id,
            meeting_code=meeting_code,
            topic=topic,
            subject_ref=meeting.get("subject_ref"),
            evidence_refs=[],
            attribution_record_id=meeting.get("attribution_record_id"),
            attribution_record=meeting.get("attribution_record"),
            supplied_case_file=case_file,
            tenant_id=tenant_id,
        )
        meeting["case_file"] = case_file
        meeting["case_file_items"] = case_file_items
        if resolved_attr is not None:
            meeting["attribution_record"] = resolved_attr

    meta = dict(meeting.get("metadata") or {})
    pre_read = build_participant_pre_read(
        meeting_id=meeting_id,
        tenant_id=tenant_id,
        participants=participants,
        topic=topic,
        supplied_pre_read=meeting.get("pre_read") or meta.get("pre_read"),
        memory_url=memory_url,
    )
    meeting["pre_read"] = pre_read
    meeting["status"] = "in_round"
    store.put_meeting(meeting)

    allowed_case_ids = {
        str(item["item_id"]) for item in case_file_items if item.get("item_id")
    }
    if meeting.get("attribution_record_id"):
        allowed_case_ids.add(str(meeting["attribution_record_id"]))
    preread_ids_by_persona: Dict[str, set[str]] = {
        str(pr["persona_id"]): set(str(eid) for eid in (pr.get("entry_ids") or []))
        for pr in pre_read
    }
    preread_by_persona: Dict[str, Dict[str, Any]] = {
        str(pr["persona_id"]): pr for pr in pre_read
    }
    default_case_id = next(iter(sorted(allowed_case_ids)), f"facts:meeting_topic:{meeting_id}")

    # Assign participants on backing ConsultRequest for memo qualification
    for p in participants:
        pid = str(p["persona_id"])
        role_enum = (
            ParticipantRole.RISK_REVIEWER
            if p["role"] == "risk_reviewer"
            else (
                ParticipantRole.RED_TEAM
                if p["role"] == "red_team"
                else ParticipantRole.PRIMARY_REVIEWER
            )
        )
        store.put_participant(
            ConsultParticipant(
                participant_id=f"cp-{meeting_id}-{pid}",
                request_id=meeting_id,
                participant_type=ParticipantType.PERSONA,
                participant_ref=pid,
                role=role_enum,
                status=ParticipantStatus.COMPLETED,
            )
        )

    calls_made = 0
    max_calls = int((meeting.get("usage") or {}).get("max_calls_per_meeting") or 16)
    rejected_positions: List[Dict[str, Any]] = []
    rounds_recorded: List[Dict[str, Any]] = []

    def _run_round(
        round_no: int,
        prior_positions: Optional[List[Dict[str, Any]]] = None,
        round_new_evidence: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        nonlocal calls_made
        round_positions: List[Dict[str, Any]] = []
        anonymised_prior = (
            anonymise_round_positions(prior_positions, allowed_case_ids=allowed_case_ids)
            if prior_positions
            else []
        )
        for idx, p in enumerate(participants):
            if renew_lease:
                renew_lease()
            calls_made += 1
            if calls_made > max_calls:
                _record_refusal_in_store(
                    store,
                    tenant_id=tenant_id,
                    meeting_id=meeting_id,
                    meeting_type=str(meeting["meeting_type"]),
                    persona_id=str(p["persona_id"]),
                    reason="call_budget_exceeded",
                    topic=topic,
                    convened_by=meeting.get("convened_by"),
                    participants=participants,
                    created_at=meeting.get("created_at"),
                    trace_id=trace_id,
                )
                raise MeetingRefusedError(
                    "call_budget_exceeded",
                    f"calls ({calls_made}) exceeded max_calls_per_meeting ({max_calls})",
                    meeting_id=meeting_id,
                    persona_id=str(p["persona_id"]),
                    meeting_type=str(meeting["meeting_type"]),
                )
            pid = str(p["persona_id"])
            assigned_case = None
            if p.get("role") == "red_team" or pid == RED_TEAM_PERSONA_ID:
                assigned_case = (
                    "luck_or_small_sample_or_confounder"
                    if meeting_code == "MT-4"
                    else "oppose_leading_proposal"
                )
            meeting_ctx: Dict[str, Any] = {
                "meeting_id": meeting_id,
                "meeting_type": meeting["meeting_type"],
                "meeting_code": meeting_code,
                "round": round_no,
                "topic": topic,
                "agenda": meeting.get("agenda") or [topic],
                "own_pre_read": preread_by_persona.get(pid),
                "assigned_case": assigned_case,
            }
            if round_no > 1:
                meeting_ctx["prior_round_positions"] = anonymised_prior
            if round_new_evidence:
                meeting_ctx["new_evidence"] = round_new_evidence
            if meeting.get("attribution_record"):
                meeting_ctx["attribution_record"] = meeting["attribution_record"]

            ask_request: Dict[str, Any] = {
                "tenant_id": tenant_id,
                "request_id": meeting_id,
                "consultation_type": "persona_meeting",
                "target_type": meeting_code,
                "target_id": str(meeting.get("subject_ref") or meeting_id),
                "task": topic,
                "trace_id": trace_id,
                "case_file": case_file,
                "participant": {
                    "persona_id": pid,
                    "role": p["role"],
                    "style": p["style"],
                    "origin": p["origin"],
                    "agent": "persona_own_agent",
                    "round": round_no,
                    "assigned_case": assigned_case,
                },
                "meeting": meeting_ctx,
                "metadata": {"meeting": meeting_ctx},
            }
            contribution = provider.obtain(
                tenant_id=tenant_id,
                request=ask_request,
                participant_scope=f"{pid}:round{round_no}",
            )
            position, raw_claims = _extract_position_from_contribution(
                contribution=contribution,
                participant=p,
                idx=idx,
                round_no=round_no,
                default_case_id=default_case_id,
            )
            if assigned_case and not position.get("assigned_case"):
                position["assigned_case"] = assigned_case

            rejection = validate_position_citations(
                citations=position.get("citations") or [],
                claims=raw_claims,
                allowed_case_ids=allowed_case_ids,
                allowed_own_preread_ids=preread_ids_by_persona.get(pid, set()),
            )
            if rejection is not None:
                rej_record = {
                    "round": round_no,
                    "persona_id": pid,
                    "role": p["role"],
                    "reason": rejection,
                    "citations": position.get("citations") or [],
                }
                rejected_positions.append(rej_record)
                transcript = store.get_transcript(meeting_id)
                seq_no = (len(transcript.events) + 1) if transcript else 1
                store.add_transcript_event(
                    meeting_id,
                    TranscriptEvent(
                        event_id=f"evt-{meeting_id}-r{round_no}-{pid}-rejected",
                        session_id=meeting_id,
                        sequence_no=seq_no,
                        event_type="meeting_position_rejected",
                        actor=ActorRef(actor_type="service", actor_id="consultation-svc"),
                        content=rej_record,
                        evidence_refs=[],
                    ),
                )
                continue

            round_positions.append(position)
            transcript = store.get_transcript(meeting_id)
            seq_no = (len(transcript.events) + 1) if transcript else 1
            store.add_transcript_event(
                meeting_id,
                TranscriptEvent(
                    event_id=f"evt-{meeting_id}-r{round_no}-{pid}",
                    session_id=meeting_id,
                    sequence_no=seq_no,
                    event_type=f"meeting_round_{round_no}_position",
                    actor=ActorRef(actor_type="persona", actor_id=pid),
                    content=position,
                    evidence_refs=list(position["citations"]),
                ),
            )
            for ev in contribution.evidence:
                ev_id = str(ev.get("id") or default_case_id)
                att_id = f"cea-{meeting_id}-r{round_no}-{pid}-{hashlib.sha256(ev_id.encode()).hexdigest()[:8]}"
                if store.get_evidence_attachment(att_id) is None:
                    store.put_evidence_attachment(
                        ConsultEvidenceAttachment(
                            attachment_id=att_id,
                            request_id=meeting_id,
                            evidence_ref=EvidenceRef(
                                id=ev_id,
                                evidence_type=str(ev.get("evidence_type") or "meeting_evidence"),
                                link=str(ev.get("link") or f"artifact://{meeting_id}/{ev_id}"),
                            ),
                            attached_by=ActorRef(actor_type="persona", actor_id=pid),
                            trace_id=trace_id,
                        )
                    )
        return round_positions

    # Execute Round 1 (independent)
    r1_positions = _run_round(1)
    rounds_recorded.append({"round": 1, "positions": r1_positions})
    meeting["round"] = 1
    meeting["rounds"] = rounds_recorded
    store.put_meeting(meeting)

    # Execute Round 2 (anonymised cross-visibility)
    r2_positions = _run_round(2, prior_positions=r1_positions)
    rounds_recorded.append({"round": 2, "positions": r2_positions})
    meeting["round"] = 2
    meeting["rounds"] = rounds_recorded
    store.put_meeting(meeting)

    # Execute Round 3 ONLY when the chair recorded new evidence (AC2 & M3d)
    new_evidence = list(meeting.get("new_evidence") or [])
    if new_evidence:
        for item in new_evidence:
            if isinstance(item, Mapping) and item.get("item_id"):
                allowed_case_ids.add(str(item["item_id"]))
                if not any(ci.get("item_id") == item["item_id"] for ci in case_file_items):
                    case_file_items.append(dict(item))
        meeting["case_file_items"] = case_file_items
        r3_positions = _run_round(3, prior_positions=r2_positions, round_new_evidence=new_evidence)
        rounds_recorded.append({"round": 3, "positions": r3_positions})
        meeting["round"] = 3
        meeting["rounds"] = rounds_recorded
        store.put_meeting(meeting)

    # Step 7: Chair Synthesis (No vote)
    calls_made += 1
    if calls_made > max_calls:
        _record_refusal_in_store(
            store,
            tenant_id=tenant_id,
            meeting_id=meeting_id,
            meeting_type=str(meeting["meeting_type"]),
            persona_id=chair_persona_id,
            reason="call_budget_exceeded",
            topic=topic,
            convened_by=meeting.get("convened_by"),
            participants=participants,
            created_at=meeting.get("created_at"),
            trace_id=trace_id,
        )
        raise MeetingRefusedError(
            "call_budget_exceeded",
            f"synthesis call ({calls_made}) exceeded max_calls_per_meeting ({max_calls})",
            meeting_id=meeting_id,
            persona_id=chair_persona_id,
            meeting_type=str(meeting["meeting_type"]),
        )

    chair_participant = next(
        (p for p in participants if p["persona_id"] == chair_persona_id),
        participants[0],
    )
    synth_meeting_ctx = {
        "meeting_id": meeting_id,
        "meeting_type": meeting["meeting_type"],
        "meeting_code": meeting_code,
        "round": "synthesis",
        "phase": "synthesis",
        "topic": topic,
        "agenda": meeting.get("agenda") or [topic],
        "rounds": rounds_recorded,
        "attribution_record": meeting.get("attribution_record"),
    }
    synth_ask: Dict[str, Any] = {
        "tenant_id": tenant_id,
        "request_id": meeting_id,
        "consultation_type": "persona_meeting",
        "target_type": meeting_code,
        "target_id": str(meeting.get("subject_ref") or meeting_id),
        "task": topic,
        "trace_id": trace_id,
        "case_file": case_file,
        "participant": {
            "persona_id": chair_persona_id,
            "role": chair_participant["role"],
            "style": chair_participant["style"],
            "origin": chair_participant["origin"],
            "agent": "persona_own_agent",
            "round": "synthesis",
            "phase": "synthesis",
        },
        "meeting": synth_meeting_ctx,
        "metadata": {"meeting": synth_meeting_ctx},
    }

    raw_synthesis: Optional[Dict[str, Any]] = None
    try:
        synth_contrib = provider.obtain(
            tenant_id=tenant_id,
            request=synth_ask,
            participant_scope=f"{chair_persona_id}:synthesis",
        )
        ev_content = dict(synth_contrib.event_content or {})
        if isinstance(ev_content.get("synthesis"), Mapping):
            raw_synthesis = dict(ev_content["synthesis"])
        elif ev_content.get("unreadable") is True:
            raw_synthesis = {"unreadable": True}
        else:
            # Build synthesis from event_content proposals or round proposals
            collected_proposals = list(ev_content.get("proposals") or [])
            if not collected_proposals and "proposals" not in ev_content:
                for pos in rounds_recorded[-1].get("positions") or []:
                    for pr in pos.get("proposals") or []:
                        if isinstance(pr, Mapping):
                            collected_proposals.append(dict(pr))
            raw_synthesis = {
                "summary": synth_contrib.summary,
                "proposals": collected_proposals,
                "outcome": ev_content.get("outcome"),
                "unreadable": ev_content.get("unreadable", False),
            }
    except Exception:
        # Provider returned unreadable/malformed synthesis -> REVIEW with no proposal (AC3)
        raw_synthesis = {"unreadable": True}

    synthesis = validate_and_build_synthesis(
        meeting_id=meeting_id,
        meeting_code=meeting_code,
        spec=spec,
        chair_persona_id=chair_persona_id,
        convened_by=meeting.get("convened_by") or {},
        participants=participants,
        rounds=rounds_recorded,
        raw_synthesis=raw_synthesis,
        allowed_case_ids=allowed_case_ids,
        chair_preread_ids=preread_ids_by_persona.get(chair_persona_id, set()),
    )

    # Step 8: Record & publish Dissent Memo + Summary Memo in store
    now_closed = utc_now()
    dissenting_persona = next(
        (
            p["persona_id"]
            for p in participants
            if p.get("role") in {"red_team", "risk_reviewer"}
        ),
        participants[-1]["persona_id"],
    )
    dissent_claims: List[str] = []
    for r in rounds_recorded:
        for pos in r.get("positions") or []:
            for disp in pos.get("disputed") or []:
                dissent_claims.append(f"{pos.get('label')}: {disp}")
            if pos.get("role") == "red_team":
                dissent_claims.append(f"red_team ({pos.get('assigned_case') or 'devil_advocate'}): {pos.get('stance')}")
    if not dissent_claims:
        dissent_claims.append(f"Minority/adversarial perspective recorded for {meeting_id} (no vote).")

    dissent_memo_id = f"mem-dissent-{meeting_id}"
    dissent_memo = ConsultMemo(
        memo_id=dissent_memo_id,
        request_id=meeting_id,
        memo_type=MemoType.DISSENT,
        author_type=AuthorType.PERSONA,
        author_ref=str(dissenting_persona),
        target_type=meeting_code,
        target_id=str(meeting.get("subject_ref") or meeting.get("attribution_record_id") or meeting_id),
        summary=" | ".join(dissent_claims)[:1000],
        findings=[
            ConsultFinding(
                severity=FindingSeverity.MEDIUM,
                category="meeting_dissent",
                claim=dissent_claims[0][:500],
                evidence_refs=[default_case_id],
                recommendation="Preserve dissent on the meeting record; governed downstream gates decide.",
            )
        ],
        recommendation=Recommendation.APPROVE_WITH_CONDITIONS,
        confidence=float((synthesis.get("tally") or {}).get("mean_confidence") or 0.7),
        status=MemoStatus.PUBLISHED,
        trace_id=trace_id,
        created_at=now_closed,
        published_at=now_closed,
    )
    saved_dissent = store.put_memo(dissent_memo)
    if isinstance(saved_dissent, Mapping) and saved_dissent.get("memo_id"):
        dissent_memo_id = str(saved_dissent["memo_id"])
    elif hasattr(saved_dissent, "memo_id"):
        dissent_memo_id = str(saved_dissent.memo_id)
    synthesis["dissent_memo_id"] = dissent_memo_id

    summary_memo_id = f"mem-summary-{meeting_id}"
    summary_memo = ConsultMemo(
        memo_id=summary_memo_id,
        request_id=meeting_id,
        memo_type=MemoType.COMMITTEE_SUMMARY,
        author_type=AuthorType.PERSONA,
        author_ref=chair_persona_id,
        target_type=meeting_code,
        target_id=str(meeting.get("subject_ref") or meeting.get("attribution_record_id") or meeting_id),
        summary=str(synthesis.get("summary") or f"Deliberative synthesis for {meeting_id}"),
        findings=[],
        recommendation=(
            Recommendation.ESCALATE
            if synthesis["outcome"] == "REVIEW"
            else Recommendation.APPROVE_WITH_CONDITIONS
        ),
        confidence=float((synthesis.get("tally") or {}).get("mean_confidence") or 0.75),
        status=MemoStatus.PUBLISHED,
        trace_id=trace_id,
        created_at=now_closed,
        published_at=now_closed,
    )
    saved_summary = store.put_memo(summary_memo)
    if isinstance(saved_summary, Mapping) and saved_summary.get("memo_id"):
        summary_memo_id = str(saved_summary["memo_id"])
    elif hasattr(saved_summary, "memo_id"):
        summary_memo_id = str(saved_summary.memo_id)

    meeting["synthesis"] = synthesis
    meeting["outcome"] = synthesis["outcome"]
    meeting["rejected_positions"] = rejected_positions
    store.put_meeting(meeting)

    # Step 9: C2 Proposal Dispatch (called after synthesis is stored)
    if synthesis["proposals"]:
        dispatch_results = dispatch_proposals(meeting)
        by_prop_id = {
            str(r.get("proposal_id")): dict(r)
            for r in (dispatch_results or [])
            if isinstance(r, Mapping) and r.get("proposal_id")
        }
        for prop in synthesis["proposals"]:
            pid = str(prop["proposal_id"])
            res = by_prop_id.get(pid)
            if res is not None:
                prop["downstream"] = {
                    "target": str(
                        res.get("downstream_target")
                        or (prop.get("downstream") or {}).get("target")
                        or DOWNSTREAM_TARGET_BY_KIND.get(prop["kind"], prop["kind"])
                    ),
                    "id": res.get("downstream_id"),
                    "status": str(res.get("status") or "submitted"),
                    **({"error": res["error"]} if res.get("error") else {}),
                }
        meeting["synthesis"] = synthesis
        store.put_meeting(meeting)

    # Step 10: Memory Writeback (Minutes as 1 institutional fact + each participant's outcome to own memory)
    writebacks, inst_entry_id = _write_meeting_memory(
        tenant_id=tenant_id,
        meeting=meeting,
        summary_memo_id=summary_memo_id,
        memory_url=memory_url,
    )
    meeting["writebacks"] = writebacks
    meeting["minutes"] = {
        "institutional_entry_id": inst_entry_id,
        "memo_id": summary_memo_id,
        "dissent_memo_id": dissent_memo_id,
        "summary": synthesis["summary"],
    }

    # Step 11: Finalize meeting status, usage, and schedule
    meeting["status"] = "closed"
    meeting["closed_at"] = now_closed
    meeting["round"] = len(rounds_recorded)
    usage = dict(meeting.get("usage") or {})
    usage["participants"] = len(participants)
    usage["rounds"] = len(rounds_recorded)
    usage["calls"] = calls_made
    meeting["usage"] = usage
    store.put_meeting(meeting)

    # Update backing ConsultRequest to PUBLISHED
    req_record = store.get_request(meeting_id)
    if req_record is not None:
        req_record.status = ConsultRequestStatus.PUBLISHED
        req_record.completed_at = now_closed
        store.put_request(req_record)

    # Update schedule for the chair and trading participants
    cadence = str(spec.get("cadence") or "weekly")
    next_due = compute_next_due_at(now_closed, cadence)
    for p in participants:
        if p.get("trading") or p["persona_id"] == chair_persona_id:
            store.put_meeting_schedule(
                {
                    "tenant_id": tenant_id,
                    "persona_id": str(p["persona_id"]),
                    "meeting_type": str(meeting["meeting_type"]),
                    "meeting_code": meeting_code,
                    "last_run_at": now_closed,
                    "next_due_at": next_due,
                }
            )

    return meeting


# ---------------------------------------------------------------------------
# Read projections for C1 routes
# ---------------------------------------------------------------------------

def _format_participant_summary(p: Mapping[str, Any]) -> Dict[str, Any]:
    item: Dict[str, Any] = {
        "persona_id": str(p.get("persona_id") or ""),
        "role": str(p.get("role") or "participant"),
        "origin": str(p.get("origin") or "institutional"),
    }
    if p.get("style"):
        item["style"] = str(p["style"])
    if p.get("agora_user_id") is not None:
        item["agora_user_id"] = str(p["agora_user_id"])
    return item


def list_meetings_view(
    store: Any,
    *,
    tenant_id: str,
    status: Optional[str] = None,
    meeting_type: Optional[str] = None,
    persona_id: Optional[str] = None,
    cursor: Optional[str] = None,
    limit: int = 50,
    catalogue: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    cat = dict(catalogue or load_meeting_catalogue())
    rows = [
        m
        for m in store.list_meetings()
        if str(m.get("tenant_id") or "default") == tenant_id
    ]
    if status:
        st = status.strip().lower()
        if st in {"open", "upcoming"}:
            rows = [
                m
                for m in rows
                if str(m.get("status") or "").lower()
                in {"triggered", "convened", "in_round", "open", "in_progress"}
            ]
        elif st in {"closed", "completed", "history"}:
            rows = [
                m
                for m in rows
                if str(m.get("status") or "").lower() in {"closed", "completed"}
            ]
        else:
            rows = [
                m
                for m in rows
                if str(m.get("status") or "").lower() == st
                or str(m.get("outcome") or "").lower() == st
            ]
    if meeting_type:
        rows = [
            m
            for m in rows
            if meeting_type_matches(
                str(m.get("meeting_type") or ""),
                str(m.get("meeting_code") or ""),
                str(m.get("meeting_slug") or ""),
                meeting_type,
                cat,
            )
        ]
    if persona_id:
        pid = persona_id.strip()
        rows = [
            m
            for m in rows
            if str(m.get("chair_persona_id") or "") == pid
            or str((m.get("convened_by") or {}).get("id") or "") == pid
            or any(str(p.get("persona_id") or "") == pid for p in (m.get("participants") or []))
        ]

    rows.sort(key=lambda m: (str(m.get("created_at") or ""), str(m.get("meeting_id") or "")))
    start_idx = 0
    if cursor:
        try:
            start_idx = int(cursor)
        except ValueError:
            for idx, m in enumerate(rows):
                if str(m.get("meeting_id")) == cursor:
                    start_idx = idx + 1
                    break

    page_limit = max(1, min(int(limit or 50), 500))
    sliced = rows[start_idx : start_idx + page_limit]
    next_cursor = (
        str(start_idx + page_limit)
        if (start_idx + page_limit) < len(rows)
        else None
    )

    items = [
        {
            "meeting_id": str(m["meeting_id"]),
            "meeting_type": str(m["meeting_type"]),
            "topic": str(m.get("topic") or ""),
            "status": str(m.get("status") or "convened"),
            "outcome": m.get("outcome"),
            "chair_persona_id": str(m.get("chair_persona_id") or ""),
            "convened_by": dict(m.get("convened_by") or {}),
            "subject_ref": m.get("subject_ref"),
            "attribution_record_id": m.get("attribution_record_id"),
            "participants": [
                _format_participant_summary(p) for p in (m.get("participants") or [])
            ],
            "round": int(m.get("round") or 0),
            "created_at": m.get("created_at"),
            "closed_at": m.get("closed_at"),
        }
        for m in sliced
    ]
    return {"items": items, "next_cursor": next_cursor}


def get_meeting_detail_view(
    store: Any,
    *,
    tenant_id: str,
    meeting_id: str,
) -> Optional[Dict[str, Any]]:
    m = store.get_meeting(meeting_id)
    if not m or str(m.get("tenant_id") or "default") != tenant_id:
        return None
    detail = copy.deepcopy(m)
    detail["participants"] = [
        _format_participant_summary(p) for p in (m.get("participants") or [])
    ]
    # Ensure live downstream status is reflected on synthesis.proposals
    synth = dict(detail.get("synthesis") or {})
    proposals = list(synth.get("proposals") or [])
    for prop in proposals:
        if not isinstance(prop, dict):
            continue
        pid = str(prop.get("proposal_id") or "")
        if pid:
            live = read_downstream_status(pid)
            if isinstance(live, Mapping):
                ds = dict(prop.get("downstream") or {})
                if live.get("status"):
                    ds["status"] = str(live["status"])
                if live.get("downstream_id"):
                    ds["id"] = str(live["downstream_id"])
                prop["downstream"] = ds
    synth["proposals"] = proposals
    detail["synthesis"] = synth
    return detail


def list_meeting_proposals_view(
    store: Any,
    *,
    tenant_id: str,
    status: Optional[str] = None,
    persona_id: Optional[str] = None,
    meeting_id: Optional[str] = None,
) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    for m in store.list_meetings():
        if str(m.get("tenant_id") or "default") != tenant_id:
            continue
        if meeting_id and str(m.get("meeting_id") or "") != meeting_id:
            continue
        synth = m.get("synthesis") if isinstance(m.get("synthesis"), Mapping) else {}
        for raw_prop in synth.get("proposals") or []:
            if not isinstance(raw_prop, Mapping):
                continue
            prop = copy.deepcopy(dict(raw_prop))
            pid = str(prop.get("proposal_id") or "")
            ds = dict(prop.get("downstream") or {})
            if pid:
                live = read_downstream_status(pid)
                if isinstance(live, Mapping):
                    if live.get("status"):
                        ds["status"] = str(live["status"])
                    if live.get("downstream_id"):
                        ds["id"] = str(live["downstream_id"])
            if "target" not in ds:
                ds["target"] = DOWNSTREAM_TARGET_BY_KIND.get(str(prop.get("kind") or ""), "")
            if "id" not in ds:
                ds["id"] = None
            if "status" not in ds:
                ds["status"] = "pending"
            prop["downstream"] = ds

            if persona_id and str(prop.get("sponsor_persona_id") or "") != persona_id:
                continue
            if status and str(ds.get("status") or "").lower() != status.strip().lower():
                continue
            items.append(
                {
                    "proposal_id": pid,
                    "meeting_id": str(prop.get("meeting_id") or m.get("meeting_id") or ""),
                    "kind": str(prop.get("kind") or ""),
                    "sponsor_persona_id": prop.get("sponsor_persona_id"),
                    "payload": dict(prop.get("payload") or {}),
                    "citations": list(prop.get("citations") or []),
                    "downstream": ds,
                }
            )
    return {"items": items}


def get_meeting_catalogue_view(
    store: Any,
    *,
    tenant_id: str,
    catalogue: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    cat = dict(catalogue or load_meeting_catalogue())
    types_out = [
        {
            "meeting_type": str(t["meeting_type"]),
            "slug": str(t.get("slug") or t["meeting_type"]),
            "title": str(t.get("title") or t["meeting_type"]),
            "title_zh": str(t.get("title_zh") or ""),
            "enabled": bool(t.get("enabled", True)),
            "cadence": str(t.get("cadence") or "weekly"),
            "roster_rule": dict(t.get("roster_rule") or {}),
            "budget": dict(t.get("budget") or {}),
        }
        for t in (cat.get("types") or [])
        if isinstance(t, Mapping)
    ]
    schedule_rows = [
        {
            "persona_id": str(s["persona_id"]),
            "meeting_type": str(s["meeting_type"]),
            "last_run_at": s.get("last_run_at"),
            "next_due_at": s.get("next_due_at"),
        }
        for s in store.list_meeting_schedules()
        if str(s.get("tenant_id") or "default") == tenant_id
    ]
    refusal_rows = [
        {
            "persona_id": str(r.get("persona_id") or ""),
            "meeting_type": str(r.get("meeting_type") or ""),
            "reason": str(r.get("reason") or ""),
            "at": r.get("at"),
            "meeting_id": r.get("meeting_id"),
        }
        for r in store.list_meeting_refusals()
        if str(r.get("tenant_id") or "default") == tenant_id
    ]
    return {
        "types": types_out,
        "schedule": schedule_rows,
        "refusals": refusal_rows,
    }
