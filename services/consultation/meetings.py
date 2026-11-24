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
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import json
import os
import re
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
    ConsultAuditEvent,
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


_DIRECTORY_FILE_ENV = "CONSULTATION_PERSONA_DIRECTORY_READ_TOKEN_FILE"


def _persona_directory_unavailable(detail: str) -> MeetingRefusedError:
    return MeetingRefusedError(
        "persona_directory_unavailable",
        f"persona directory could not be read: {detail}",
        status_code=503,
    )


def _fetch_persona_directory(tenant_id: str) -> Dict[str, Dict[str, Any]]:
    """Read the persona owner's directory with the issued directory credential.

    The credential is delivered as a file (viewer role, GET only). A directory
    that cannot be read is refused by name; it is never an empty directory.
    Tokens are never put into the error text.
    """
    persona_url = (
        os.getenv("PANTHEON_PERSONA_API_URL")
        or os.getenv("PERSONA_API_URL")
        or ""
    ).strip().rstrip("/")
    if not persona_url:
        return {}  # no persona service wired; callers that need a directory refuse (see roster)
    try:
        from services.runtime_auth import resolve_runtime_manager_auth

        if os.getenv(_DIRECTORY_FILE_ENV, "").strip():
            token = resolve_runtime_manager_auth(
                token_env_key="CONSULTATION_PERSONA_DIRECTORY_READ_TOKEN",
                token_file_env_key=_DIRECTORY_FILE_ENV,
                default_token="",
            ).token
        else:
            token = (
                os.getenv("PANTHEON_PERSONA_SERVICE_TOKEN")
                or os.getenv("PERSONA_SERVICE_TOKEN")
                or ""
            ).strip()
    except OSError as exc:
        raise _persona_directory_unavailable(f"credential file unreadable ({type(exc).__name__})") from exc
    headers = {"Accept": "application/json", "X-Tenant-Id": tenant_id}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{persona_url}/api/personas", headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            rows = json.loads(resp.read().decode("utf-8") or "[]")
    except urllib.error.HTTPError as exc:
        raise _persona_directory_unavailable(f"http_{exc.code}") from exc
    except Exception as exc:
        raise _persona_directory_unavailable(type(exc).__name__) from exc
    if isinstance(rows, dict):
        rows = rows.get("items") or rows.get("personas") or []
    if not isinstance(rows, list):
        raise _persona_directory_unavailable("unexpected response shape")
    return {
        str(r.get("persona_id") or r.get("id")): dict(r)
        for r in rows
        if isinstance(r, Mapping) and (r.get("persona_id") or r.get("id"))
    }


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


_ROSTER_PERSONA_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


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
            subject_persona_id = str(subject_ref).strip()
            if subject_persona_id.startswith("persona:"):
                subject_persona_id = subject_persona_id[len("persona:"):].strip()
            if not _ROSTER_PERSONA_ID_PATTERN.fullmatch(subject_persona_id):
                raise MeetingRefusedError(
                    "invalid_subject_persona",
                    f"subject_ref {subject_ref!r} does not resolve to an admissible persona id",
                    persona_id=subject_persona_id,
                    meeting_type=meeting_code,
                )
            if not directory:
                raise _persona_directory_unavailable("persona directory is empty or not configured")
            if subject_persona_id not in directory:
                raise MeetingRefusedError(
                    "subject_persona_not_found",
                    f"subject_ref persona {subject_persona_id} is not in the persona directory",
                    persona_id=subject_persona_id,
                    meeting_type=meeting_code,
                )
            entries.append({"persona_id": subject_persona_id, "role": "chair"})
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


def _is_legacy_unresolved_placeholder(item: Mapping[str, Any], topic: str = "") -> bool:
    """Return True only for genuinely unresolved legacy string/placeholder evidence_refs."""
    if not isinstance(item, Mapping):
        return False
    if item.get("kind") != "evidence_ref" or item.get("source_owner") != "consultation":
        return False
    ref_str = str(item.get("source_id") or item.get("item_id") or "").strip()
    if not ref_str:
        return False
    excerpt = str(item.get("excerpt") or "").strip()
    item_id_str = str(item.get("item_id") or "").strip()
    if not excerpt or excerpt == ref_str or excerpt == item_id_str:
        return True
    if topic and excerpt == f"Evidence {ref_str} for {topic}":
        return True
    if excerpt.startswith(f"Evidence {ref_str} for "):
        return True
    return False


def _normalize_meeting_case_item(
    raw_item: Mapping[str, Any],
    *,
    budgets: Any,
    default_group: str = "facts",
) -> Dict[str, Any]:
    from services.knowledge import case_file as decision_case

    item = dict(raw_item)
    group_name = str(item.get("group") or default_group).strip()
    if group_name not in decision_case.GROUPS:
        group_name = default_group
    item_id = str(item.get("item_id") or item.get("id") or item.get("source_id") or "facts:item").strip()
    source_id = str(item.get("source_id") or item_id).strip()
    item["item_id"] = item_id
    item["group"] = group_name
    item["kind"] = str(item.get("kind") or item.get("evidence_type") or "evidence_ref").strip()
    item["source_owner"] = str(item.get("source_owner") or "consultation").strip()
    item["source_id"] = source_id
    item["excerpt"] = decision_case._clip(item.get("excerpt", ""), budgets.max_excerpt_chars)
    item["retrieval_score"] = item.get("retrieval_score")
    item["retrieval_mode"] = str(item.get("retrieval_mode") or "supplied")
    item["subject_fields"] = decision_case._bound_subject_fields(item.get("subject_fields"), budgets)
    return item


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
    chair_persona_id: Optional[str] = None,
    convened_by: Optional[Mapping[str, Any]] = None,
    owners: Optional[Any] = None,
    budgets: Optional[Any] = None,
    prior_actions: Sequence[Mapping[str, Any]] = (),
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Compose and validate the shared case file for a deliberative meeting."""
    from dataclasses import replace
    from services.knowledge import case_file as decision_case
    from .workflow_executor import memory_read_identity

    eff_budgets = budgets if isinstance(budgets, decision_case.Budgets) else decision_case.Budgets()
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

    supplied_items: List[Dict[str, Any]] = []
    explicit_items: List[Dict[str, Any]] = []
    string_refs: List[str] = []

    def _collect_supplied_item(raw_item: Mapping[str, Any], default_group: str = "facts") -> None:
        if (
            raw_item.get("memory_layer") == "persona_private"
            or raw_item.get("relevance_scope") == "persona_private"
        ):
            raise MeetingRefusedError(
                "private_memory_forbidden",
                "private memory cannot enter a persona meeting case file",
                meeting_type=meeting_code,
            )
        if _is_legacy_unresolved_placeholder(raw_item, topic):
            legacy_ref = str(raw_item.get("source_id") or raw_item.get("item_id") or "").strip()
            if legacy_ref:
                string_refs.append(legacy_ref)
            return
        if (
            raw_item.get("kind") == "meeting_context"
            and raw_item.get("source_owner") == "consultation"
            and str(raw_item.get("item_id") or "") == f"facts:meeting_topic:{meeting_id}"
        ):
            return
        supplied_items.append(
            _normalize_meeting_case_item(raw_item, budgets=eff_budgets, default_group=default_group)
        )

    if isinstance(supplied_case_file, Mapping):
        for group_name, group_val in (supplied_case_file.get("groups") or {}).items():
            if isinstance(group_val, Mapping):
                target_grp = str(group_name) if str(group_name) in decision_case.GROUPS else "facts"
                for raw_item in group_val.get("items") or []:
                    if isinstance(raw_item, Mapping):
                        _collect_supplied_item(raw_item, default_group=target_grp)
        for raw_item in supplied_case_file.get("items") or []:
            if isinstance(raw_item, Mapping):
                _collect_supplied_item(raw_item, default_group="facts")

    for idx, ref in enumerate(evidence_refs or []):
        if isinstance(ref, Mapping):
            ref_id = str(ref.get("item_id") or ref.get("id") or ref.get("source_id") or f"evidence:ref:{idx + 1}").strip()
            if (
                ref.get("memory_layer") == "persona_private"
                or ref.get("relevance_scope") == "persona_private"
            ):
                raise MeetingRefusedError(
                    "private_memory_forbidden",
                    "private memory cannot enter a persona meeting case file",
                    meeting_type=meeting_code,
                )
            raw_excerpt = ref.get("excerpt") if ref.get("excerpt") is not None else ref.get("description")
            if (
                raw_excerpt is not None
                and str(raw_excerpt).strip()
                and str(raw_excerpt).strip() != ref_id
                and not _is_legacy_unresolved_placeholder(ref, topic)
            ):
                explicit_items.append(
                    _normalize_meeting_case_item(
                        {
                            **dict(ref),
                            "item_id": ref_id,
                            "group": str(ref.get("group") or "facts"),
                            "kind": str(ref.get("kind") or ref.get("evidence_type") or "evidence_ref"),
                            "source_owner": str(ref.get("source_owner") or "consultation"),
                            "source_id": str(ref.get("source_id") or ref_id),
                            "excerpt": str(raw_excerpt).strip(),
                            "retrieval_score": ref.get("retrieval_score"),
                            "retrieval_mode": str(ref.get("retrieval_mode") or "supplied"),
                            "subject_fields": dict(ref.get("subject_fields") or {}),
                        },
                        budgets=eff_budgets,
                        default_group="facts",
                    )
                )
            else:
                target_ref = str(ref.get("source_id") or ref_id).strip()
                if target_ref:
                    string_refs.append(target_ref)
        else:
            ref_str = str(ref).strip()
            if ref_str:
                string_refs.append(ref_str)

    sref = str(subject_ref or "").strip()
    if sref.startswith("persona:"):
        subject_persona_id = sref[len("persona:"):].strip()
    elif sref and not sref.startswith(("trader:", "agora_user:", "user:", "iso-week:", "attr-")):
        subject_persona_id = sref
    elif chair_persona_id and str(chair_persona_id).strip():
        subject_persona_id = str(chair_persona_id).strip()
    elif (
        isinstance(convened_by, Mapping)
        and str(convened_by.get("kind") or "").strip() == "persona"
        and str(convened_by.get("id") or "").strip()
    ):
        subject_persona_id = str(convened_by["id"]).strip()
    else:
        subject_persona_id = "general"

    eff_owners = owners if isinstance(owners, decision_case.Owners) else decision_case.Owners.from_env()
    mem_token, read_actor_id, read_actor_roles = memory_read_identity()
    if eff_owners.memory is not None and not eff_owners.memory.token and mem_token:
        eff_owners = replace(
            eff_owners,
            memory=decision_case.OwnerEndpoint(eff_owners.memory.base_url, mem_token),
        )
    caller = decision_case.Caller(
        actor_id=read_actor_id or "consultation-svc",
        actor_roles=tuple(read_actor_roles) or ("consultation_session",),
        session_id=f"meeting-{meeting_id}",
        # Shared meeting case file: never a private persona caller (AC3)
        persona_id="",
    )
    unique_string_refs = list(dict.fromkeys(string_refs))
    refs_truncated = len(unique_string_refs) > eff_budgets.max_items_per_group
    bounded_string_refs = unique_string_refs[: eff_budgets.max_items_per_group]

    shared_case = decision_case.compose_case_file(
        decision_case.Subject(
            decision_type="consult",
            persona_id=subject_persona_id or "general",
            tenant_id=tenant_id or "default",
            strategy_family=meeting_code or "consult",
            member_artifact_refs=tuple(bounded_string_refs),
        ),
        caller,
        eff_owners,
        budgets=eff_budgets,
    )

    for grp in (shared_case.get("groups") or {}).values():
        if isinstance(grp, Mapping):
            for it in grp.get("items") or []:
                if isinstance(it, Mapping) and (
                    it.get("memory_layer") == "persona_private"
                    or it.get("relevance_scope") == "persona_private"
                ):
                    raise MeetingRefusedError(
                        "private_memory_forbidden",
                        "private memory cannot enter a persona meeting case file",
                        meeting_type=meeting_code,
                    )

    groups = {k: copy.deepcopy(v) for k, v in (shared_case.get("groups") or {}).items()}
    facts_group = dict(groups.get("facts") or {"status": "empty", "items": [], "truncated": False, "unavailable": []})
    facts_items: List[Dict[str, Any]] = list(facts_group.get("items") or [])
    facts_unavail: List[Dict[str, Any]] = list(facts_group.get("unavailable") or [])
    unavail_by_owner = {
        str(u.get("owner") or ""): str(u.get("reason") or "unavailable")
        for u in facts_unavail
        if isinstance(u, Mapping)
    }
    resolved_registry_refs = {
        str(i.get("source_id") or "")
        for i in facts_items
        if isinstance(i, Mapping) and i.get("kind") == "registry_entry"
    }

    # Add attribution record item for MT-4 at index 0
    if resolved_attr is not None:
        attr_id = str(resolved_attr["attribution_record_id"])
        attr_item_id = f"facts:attribution_record:{attr_id}"
        if not any(i.get("item_id") in {attr_item_id, attr_id} for i in facts_items):
            facts_items.insert(
                0,
                _normalize_meeting_case_item(
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
                    budgets=eff_budgets,
                    default_group="facts",
                ),
            )

    for item in [*supplied_items, *explicit_items]:
        target_group_name = str(item.get("group") or "facts")
        if target_group_name in decision_case.GROUPS and target_group_name != "facts":
            target_grp = dict(groups.get(target_group_name) or {"status": "empty", "items": [], "truncated": False, "unavailable": []})
            target_items = list(target_grp.get("items") or [])
            if not any(existing.get("item_id") == item.get("item_id") for existing in target_items):
                target_items.append(item)
                target_grp["items"] = target_items
                groups[target_group_name] = target_grp
        else:
            if not any(existing.get("item_id") == item.get("item_id") for existing in facts_items):
                facts_items.append(item)

    # Unresolvable refs stay in the case file with a named unavailable reason instead of an excerpt equal to the ref id (AC2)
    for ref_str in bounded_string_refs:
        if ref_str not in resolved_registry_refs:
            reason = unavail_by_owner.get(f"registry:{ref_str}") or "registry_entry_unavailable"
            unavail_item = _normalize_meeting_case_item(
                {
                    "item_id": f"facts:registry_entry:{ref_str}",
                    "group": "facts",
                    "kind": "registry_entry",
                    "source_owner": "registry",
                    "source_id": ref_str,
                    "status": "unavailable",
                    "unavailable_reason": reason,
                    "excerpt": json.dumps(
                        {
                            "ref": ref_str,
                            "status": "unavailable",
                            "unavailable_reason": f"registry:{ref_str}:{reason}",
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ),
                    "retrieval_score": None,
                    "retrieval_mode": "owner_read",
                    "subject_fields": {"unavailable_reason": reason},
                },
                budgets=eff_budgets,
                default_group="facts",
            )
            if not any(existing.get("item_id") == unavail_item["item_id"] for existing in facts_items):
                facts_items.append(unavail_item)

    # Ensure at least one baseline topic fact item exists in the shared case file
    if not facts_items:
        default_item_id = f"facts:meeting_topic:{meeting_id}"
        facts_items.append(
            _normalize_meeting_case_item(
                {
                    "item_id": default_item_id,
                    "group": "facts",
                    "kind": "meeting_context",
                    "source_owner": "consultation",
                    "source_id": meeting_id,
                    "excerpt": f"{meeting_code} topic: {topic} (subject={subject_ref or 'general'})",
                    "subject_fields": {"meeting_type": meeting_code},
                },
                budgets=eff_budgets,
                default_group="facts",
            )
        )

    facts_group["items"] = facts_items
    facts_group["unavailable"] = facts_unavail
    if refs_truncated:
        facts_group["truncated"] = True
    groups["facts"] = facts_group

    lessons_group = dict(groups.get("lessons") or {"status": "empty", "items": [], "truncated": False, "unavailable": []})
    lessons_unavail = [
        u
        for u in (lessons_group.get("unavailable") or [])
        if not (isinstance(u, Mapping) and u.get("owner") == "memory:persona_private" and u.get("expected"))
    ]
    lessons_group["unavailable"] = lessons_unavail
    groups["lessons"] = lessons_group

    # Enforce per-group item and excerpt/subject_fields budgets across all groups (including appended items)
    for g_name in decision_case.GROUPS:
        grp = dict(groups.get(g_name) or {"status": "empty", "items": [], "truncated": False, "unavailable": []})
        norm_items = [
            _normalize_meeting_case_item(it, budgets=eff_budgets, default_group=g_name)
            for it in (grp.get("items") or [])
            if isinstance(it, Mapping)
        ]
        if len(norm_items) > eff_budgets.max_items_per_group:
            norm_items = norm_items[: eff_budgets.max_items_per_group]
            grp["truncated"] = True
        grp["items"] = norm_items
        groups[g_name] = grp

    # Enforce total character budget across all groups (excerpts + structured subject_fields)
    def _total_char_size() -> int:
        return sum(
            decision_case._item_char_size(it)
            for g_name in decision_case.GROUPS
            for it in (groups[g_name].get("items") or [])
        )

    while _total_char_size() > eff_budgets.max_total_chars:
        non_empty = [groups[g_name] for g_name in decision_case.GROUPS if groups[g_name].get("items")]
        if not non_empty:
            break
        biggest = max(non_empty, key=lambda g: len(g["items"]))
        biggest["items"].pop()
        biggest["truncated"] = True

    for g_name in decision_case.GROUPS:
        grp = groups[g_name]
        g_items = grp.get("items") or []
        g_unavail = grp.get("unavailable") or []
        if g_unavail and not g_items:
            grp["status"] = "unavailable"
            grp["reason"] = "; ".join(f"{u['owner']}:{u['reason']}" for u in g_unavail if isinstance(u, Mapping))
        elif g_unavail:
            grp["status"] = "partial"
            grp.pop("reason", None)
        else:
            grp["status"] = "ok" if g_items else "empty"
            grp.pop("reason", None)

    all_items: List[Dict[str, Any]] = []
    for g_name in decision_case.GROUPS:
        for it in (groups.get(g_name) or {}).get("items") or []:
            if isinstance(it, Mapping) and not any(x.get("item_id") == it.get("item_id") for x in all_items):
                all_items.append(dict(it))

    subject_body = dict(shared_case.get("subject") or {})
    subject_body.update(
        {
            "decision_type": "consult",
            "meeting_type": meeting_code,
            "subject_ref": subject_ref or meeting_id,
            "tenant_id": tenant_id,
        }
    )
    lesson_q = dict(shared_case.get("lesson_query") or {})
    base_q = str(lesson_q.get("query") or "").strip()
    base_tags = list(lesson_q.get("tags") or [])
    if f"meeting:{meeting_code}" not in base_tags:
        base_tags.append(f"meeting:{meeting_code}")

    case_body: Dict[str, Any] = {
        "schema_version": "decision_case_file.v1",
        "composed_at": shared_case.get("composed_at"),
        "meeting_id": meeting_id,
        "meeting_type": meeting_code,
        "tenant_id": tenant_id,
        "subject": subject_body,
        "caller": shared_case.get("caller") or {"actor_id": caller.actor_id, "is_subject_persona": False},
        "lesson_query": {
            "query": f"{meeting_code} {topic} {subject_ref or ''} {base_q}".strip(),
            "tags": base_tags,
        },
        "budgets": shared_case.get("budgets"),
        "groups": groups,
        "execution_may_read": False,
    }
    if resolved_attr is not None:
        case_body["attribution_record"] = resolved_attr
    digest = hashlib.sha256(
        json.dumps(case_body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()
    case_file = {
        "case_file_id": f"casefile-{digest[:24]}",
        "digest": digest,
        **case_body,
    }
    case_file, action_items = _attach_meeting_action_case(case_file, prior_actions)
    return case_file, [*all_items, *action_items], resolved_attr


def _attach_meeting_action_case(case_file: Mapping[str, Any], actions: Sequence[Mapping[str, Any]]) -> Tuple[Dict, List[Dict]]:
    """Bounded shared citations; complete owner snapshots stay in meeting context."""
    result = copy.deepcopy(dict(case_file))
    items = [{"item_id": f"meeting_action:{a['action_id']}", "source_id": a["action_id"],
              "source_owner": "research", "source_kind": "meeting_action", "memory_layer": "institutional",
              "relevance_scope": "committee", "excerpt": json.dumps(
                  {k: a.get(k) for k in ("state", "owner", "deliverable", "acceptance_criterion", "due_at", "next_review")},
                  ensure_ascii=False)[:400], "subject_fields": {"ticket_id": a["action_id"]}}
             for a in actions[:24]]
    if items:
        result.setdefault("groups", {})["meeting_actions"] = {"status": "ok", "items": items,
                                                               "truncated": False, "unavailable": []}
        body = {k: v for k, v in result.items() if k not in {"case_file_id", "digest"}}
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()).hexdigest()
        result.update(case_file_id=f"casefile-{digest[:24]}", digest=digest)
    if len(json.dumps(result).encode()) > 262144:
        raise MeetingRefusedError("case_file_byte_budget_exceeded", "Meeting case file exceeds 256 KiB")
    return result, items


def _check_meeting_prompt_budget(request: Mapping[str, Any]) -> None:
    if len(json.dumps(request).encode()) > 2097152:
        raise MeetingRefusedError("prompt_byte_budget_exceeded", "Meeting provider request exceeds 2 MiB")


def build_participant_pre_read(
    *,
    meeting_id: str,
    tenant_id: str,
    participants: Sequence[Mapping[str, Any]],
    topic: str,
    supplied_pre_read: Any = None,
    memory_url: str = "",
    refusals: Optional[Dict[str, str]] = None,
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

    def _build_one(p: Mapping[str, Any]) -> Tuple[Dict[str, Any], str]:
        pid = str(p["persona_id"])
        raw_entries = list(supplied_by_persona.get(pid) or [])
        memory_read_refusal = ""
        if not raw_entries and memory_url:
            try:
                from integrations.openclaw.persona_memory_bridge import (
                    fetch_canonical_memory,
                    normalize_retrieval_hits,
                )
                from .workflow_executor import memory_read_identity

                token, read_actor_id, read_actor_roles = memory_read_identity()
                retrieval = fetch_canonical_memory(
                    memory_url,
                    persona_id=pid,
                    actor_id=read_actor_id,
                    actor_roles=list(read_actor_roles),
                    session_id=f"preread-{meeting_id}-{pid}",
                    scope="both",
                    query=topic,
                    limit=12,
                    auth_token=token or None,
                    timeout_seconds=float(os.getenv("CONSULTATION_MEMORY_READ_TIMEOUT_SECONDS", "15.0")),
                )
                if isinstance(retrieval, Mapping):
                    hits, _ = normalize_retrieval_hits(retrieval, persona_id=pid)
                    for hit in hits:
                        if hit.get("type") == "institutional" or (
                            hit.get("type") == "persona"
                            and hit.get("relevance_scope") == "persona_and_committee"
                        ):
                            raw_entries.append(dict(hit))
            except Exception as exc:
                # A refused or failed canonical read never blocks the round; it is
                # named in the contribution. Never include credentials in the text.
                raw_entries = []
                code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
                memory_read_refusal = (
                    f"memory_read_refused:{type(exc).__name__}"
                    + (f":http_{code}" if code else "")
                )

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
                or entry.get("source_id")
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

        record = {
            "persona_id": pid,
            "role": p["role"],
            "style": p["style"],
            "origin": p["origin"],
            "mode": "committee_visible_full",
            "entries": filtered_entries,
            "entry_ids": [e["item_id"] for e in filtered_entries],
        }
        return record, memory_read_refusal

    # Pre-reads run concurrently so N participants spend one memory deadline,
    # not N; order, refusals and private-memory filtering are unchanged.
    participant_list = list(participants)
    if len(participant_list) > 1 and memory_url:
        with ThreadPoolExecutor(max_workers=min(len(participant_list), 5),
                                thread_name_prefix="preread") as pool:
            results = list(pool.map(_build_one, participant_list))
    else:
        results = [_build_one(p) for p in participant_list]
    pre_read_records: List[Dict[str, Any]] = []
    for record, memory_read_refusal in results:
        if memory_read_refusal and refusals is not None:
            # Kept out of the pre-read record: that record is part of the provider
            # request, so a retry must not change its content (idempotency key).
            refusals[record["persona_id"]] = memory_read_refusal
        pre_read_records.append(record)
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


def _iter_exception_chain(exc: BaseException) -> List[BaseException]:
    chain: List[BaseException] = []
    seen: set[int] = set()
    cur: Optional[BaseException] = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        chain.append(cur)
        reason_obj = getattr(cur, "reason", None)
        if isinstance(reason_obj, BaseException) and id(reason_obj) not in seen:
            seen.add(id(reason_obj))
            chain.append(reason_obj)
        cur = cur.__cause__ if cur.__cause__ is not None else cur.__context__
    return chain


def _classify_synthesis_unreadable_reason(
    raw_synthesis: Any = None,
    exc: Optional[BaseException] = None,
) -> str:
    """Classify why a chair synthesis is empty or unreadable (AC5):
    model_output_missing | parse_failure (raw_length=<N|unknown>) | timeout.
    Never invent raw_length from diagnostic error message length.
    """
    if exc is not None:
        chain = _iter_exception_chain(exc)
        for item in chain:
            msg = str(item)
            low = msg.lower()
            tname = type(item).__name__.lower()
            if (
                isinstance(item, TimeoutError)
                or "timeout" in tname
                or "timeout" in low
                or "timed out" in low
            ):
                return "timeout"
        for item in chain:
            if isinstance(item, json.JSONDecodeError):
                return f"parse_failure (raw_length={len(item.doc or '')})"
            raw_len_attr = getattr(item, "raw_length", None)
            if raw_len_attr is not None:
                return f"parse_failure (raw_length={int(raw_len_attr)})"
            raw_txt_attr = (
                getattr(item, "raw_output", None)
                if getattr(item, "raw_output", None) is not None
                else getattr(item, "raw_text", None)
            )
            if raw_txt_attr is not None:
                return f"parse_failure (raw_length={len(str(raw_txt_attr))})"
            m = re.search(r"raw_length[=:]\s*(\d+)", str(item))
            if m:
                return f"parse_failure (raw_length={int(m.group(1))})"
        for item in chain:
            low = str(item).lower()
            if any(
                tok in low
                for tok in (
                    "did not complete",
                    "model_output_missing",
                    "output missing",
                    "is missing",
                    "missing summary",
                    "requires non-empty",
                    "empty",
                )
            ):
                return "model_output_missing"
        return "parse_failure (raw_length=unknown)"

    if isinstance(raw_synthesis, Mapping):
        explicit = str(
            raw_synthesis.get("unreadable_reason")
            or raw_synthesis.get("failure_reason")
            or raw_synthesis.get("cause")
            or ""
        ).strip()
        if explicit:
            if "timeout" in explicit.lower() or "timed out" in explicit.lower():
                return "timeout"
            if "parse_failure" in explicit or "model_output_missing" in explicit:
                return explicit
            if raw_synthesis.get("raw_length") is not None and "raw_length" not in explicit:
                return f"parse_failure (raw_length={int(raw_synthesis['raw_length'])})"
            return explicit
        if raw_synthesis.get("timeout") is True:
            return "timeout"
        if raw_synthesis.get("unreadable") is True:
            raw_txt = raw_synthesis.get("raw_output") if raw_synthesis.get("raw_output") is not None else raw_synthesis.get("raw_text")
            if raw_synthesis.get("raw_length") is not None:
                return f"parse_failure (raw_length={int(raw_synthesis['raw_length'])})"
            if raw_txt is not None:
                return f"parse_failure (raw_length={len(str(raw_txt))})"
            return "parse_failure (raw_length=unknown)"
        return "model_output_missing"

    if raw_synthesis is None:
        return "model_output_missing"
    return f"parse_failure (raw_length={len(str(raw_synthesis))})"


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

    # Check if raw_synthesis is missing/empty/unreadable (AC3 & AC5)
    if (
        not isinstance(raw_synthesis, Mapping)
        or not raw_synthesis
        or raw_synthesis.get("unreadable") is True
        or str(raw_synthesis.get("outcome") or "").strip().upper() == "REVIEW"
        or (
            not str(raw_synthesis.get("summary") or "").strip()
            and not raw_synthesis.get("proposals")
            and isinstance(raw_synthesis.get("proposals"), (list, type(None)))
        )
    ):
        is_declared_review = (
            isinstance(raw_synthesis, Mapping)
            and raw_synthesis.get("unreadable") is not True
            and str(raw_synthesis.get("outcome") or "").strip().upper() == "REVIEW"
            and bool(str(raw_synthesis.get("summary") or "").strip())
        )
        if is_declared_review:
            return {
                "proposals": [],
                "tally": tally,
                "dissent_memo_id": None,
                "outcome": "REVIEW",
                "summary": str(raw_synthesis.get("summary")).strip(),
                "voting": False,
                "rejected_proposals": [],
            }
        cause = _classify_synthesis_unreadable_reason(raw_synthesis)
        return {
            "proposals": [],
            "tally": tally,
            "dissent_memo_id": None,
            "outcome": "REVIEW",
            "unreadable_reason": cause,
            "summary": f"Synthesis empty or unreadable ({cause}); recorded as REVIEW with no proposal.",
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
        cause = _classify_synthesis_unreadable_reason(
            {
                "unreadable": True,
                "raw_length": raw_synthesis.get("raw_length"),
                "raw_output": raw_synthesis.get("raw_output")
                if raw_synthesis.get("raw_output") is not None
                else raw_synthesis.get("raw_text"),
            }
        )
        return {
            "proposals": [],
            "tally": tally,
            "dissent_memo_id": None,
            "outcome": "REVIEW",
            "unreadable_reason": cause,
            "summary": f"Synthesis proposals field malformed ({cause}); recorded as REVIEW with no proposal.",
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
            can_sponsor_default = (
                sponsor_meta.get("origin", "institutional") == "institutional"
                and sponsor_meta.get("role") not in {"risk_reviewer", "red_team", "advisor", "servant"}
            ) if sponsor_meta is not None else True
            if sponsor_meta is not None and not sponsor_meta.get("can_sponsor", can_sponsor_default):
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


def _meeting_series(meeting: Mapping[str, Any]) -> Optional[Dict[str, str]]:
    """Join only a verified stable subject, never a meeting-type global default."""
    code = str(meeting.get("meeting_code") or "")
    ref = str(meeting.get("subject_ref") or "").strip()
    if len(ref) > 256:
        return None
    chair = str(meeting.get("chair_persona_id") or "")
    convener = meeting.get("convened_by") or {}
    if code == "MT-1":
        # MT-1 is the owning Persona's pool; dates/weekly trigger IDs are not series identities.
        if ref in {chair, f"persona:{chair}", f"pool:{chair}"} or (
                convener.get("kind") == "persona" and convener.get("id") == chair
                and (not ref or ref.startswith((f"{chair}:", f"{chair}|", "week:", "meeting:")))):
            return {"meeting_type": code, "subject_ref": f"pool:{chair}"}
    if ref and not ref.startswith(("meeting:", "mtg-", "week:", "trigger:")) and not re.search(r"\d{4}-W\d{2}", ref):
        return {"meeting_type": code, "subject_ref": ref}
    # Existing recurrence/committee identity may be propagated by the trusted trigger.
    # Do not trust arbitrary metadata to join committees here.
    return None


def _participant_action_capability(tenant_id: str, persona_id: str,
                                   required_data: List[str] | None = None) -> Dict[str, Any]:
    """Read the Persona owner/broker decision; role labels grant no capability."""
    base = (os.getenv("PANTHEON_PERSONA_API_URL") or os.getenv("PERSONA_API_URL") or "").rstrip("/")
    result: Dict[str, Any] = {"persona_id": persona_id, "status": "owner_unavailable",
                              "can_receive_persona_action": False, "can_mutate_trading_state": False,
                              "consultation_can_mutate_trading_state": False}
    if not base:
        return {**result, "reason": "persona_owner_unconfigured"}
    try:
        from services.runtime_auth import resolve_runtime_manager_auth
        token = resolve_runtime_manager_auth(
            token_env_key="CONSULTATION_PERSONA_DIRECTORY_READ_TOKEN",
            token_file_env_key=_DIRECTORY_FILE_ENV, default_token="").token
        if not token:
            token = os.getenv("PANTHEON_PERSONA_SERVICE_TOKEN") or os.getenv("PERSONA_SERVICE_TOKEN") or ""
        headers = {"X-Tenant-Id": tenant_id, "Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        query = urllib.parse.urlencode({"run_purpose": "daily_cycle", "required_data": required_data or []}, doseq=True)
        path = f"/api/personas/{urllib.parse.quote(persona_id, safe='')}/action-capability?{query}"
        req = urllib.request.Request(base + path, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = json.loads(resp.read(16385).decode())
        if not isinstance(raw, Mapping) or raw.get("persona_id") != persona_id:
            return {**result, "reason": "persona_owner_invalid_receipt"}
        # Never pass access credentials, private data, or an unbounded owner object to the model.
        for key in ("status", "reason", "can_receive_persona_action", "can_be_assigned", "executable",
                    "allowed_deliverables", "allowed_execution_targets", "denial_reasons", "run_purpose"):
            value = raw.get(key)
            if isinstance(value, (bool, str, list)) and len(json.dumps(value).encode()) <= 1024:
                result[key] = value
        return result
    except urllib.error.HTTPError as exc:
        return {**result, "status": "denied" if exc.code in {401, 403} else "owner_unavailable",
                "reason": f"persona_owner_http_{exc.code}"}
    except Exception as exc:
        return {**result, "reason": f"persona_owner_{type(exc).__name__}"}


def _validate_adopted_actions(raw: Mapping[str, Any], meeting: Mapping[str, Any],
                             allowed_citations: set[str], *, recheck_capabilities: bool = False
                             ) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    """Only an explicit chair adoption produces work; rounds remain advisory."""
    actions, reviews, rejected = [], [], []
    if str(raw.get("outcome") or "").upper() == "REVIEW" or raw.get("unreadable"):
        return actions, reviews, rejected
    source_ids: set[str] = set()
    capabilities = {c["persona_id"]: c for c in meeting.get("participant_action_capabilities") or []}
    prior = {a["action_id"]: a for a in meeting.get("prior_actions") or []}
    raw_actions = raw.get("actions") or []
    if not isinstance(raw_actions, list) or len(raw_actions) > 8:
        return [], [], [{"reason": "actions_must_be_bounded_list"}]
    for idx, item in enumerate(raw_actions):
        reason = None
        if not isinstance(item, Mapping):
            rejected.append({"index": idx, "reason": "action_not_object"})
            continue
        fields = ("source_action_id", "assignee", "assignee_kind", "deliverable", "acceptance_criterion")
        action = {k: item.get(k) for k in fields}
        if any(not isinstance(v, str) or not v.strip() or len(v) > (2000 if k in {"deliverable", "acceptance_criterion"} else 256)
               for k, v in action.items()):
            rejected.append({"index": idx, "reason": "missing_or_unbounded_assignment_fields"})
            continue
        # Research canonicalizes these owner fields before issuing a receipt.
        # Normalize before identity/capability checks and decision freezing so
        # every dispatch attempt carries exactly the same canonical origin.
        action = {k: v.strip() for k, v in action.items()}
        if "required_data" in item:
            datasets = item["required_data"]
            if (not isinstance(datasets, list) or len(datasets) > 8
                    or any(not isinstance(d, str) or not d.strip() or len(d) > 256 for d in datasets)):
                rejected.append({"index": idx, "reason": "invalid_required_data"})
                continue
            datasets = [d.strip() for d in datasets]
            if len(set(datasets)) != len(datasets) or len(json.dumps(datasets).encode()) > 4096:
                rejected.append({"index": idx, "reason": "invalid_required_data"})
                continue
            action["required_data"] = datasets
        refs = item.get("source_refs")
        if isinstance(refs, list):
            refs = [r.strip() if isinstance(r, str) else r for r in refs]
        if (not isinstance(refs, list) or not 1 <= len(refs) <= 8
                or any(not isinstance(r, str) or len(r) > 256 or r not in allowed_citations for r in refs)
                or len(json.dumps(refs).encode()) > 4096):
            reason = "invalid_action_source_refs"
        if not meeting.get("meeting_series"):
            reason = "stable_series_unavailable"
        if action.get("source_action_id") in source_ids:
            reason = "duplicate_source_action_id"
        if action.get("assignee_kind") == "persona":
            # A generic note capability cannot authorize a data-dependent deliverable.
            # Check each action separately: the same Persona may receive an evidence-only
            # note and be denied a licensed-feed analysis in this very synthesis.
            datasets = action.get("required_data") or []
            cap = capabilities.get(action["assignee"], {})
            if cap and (recheck_capabilities or datasets):
                cap = (_participant_action_capability(str(meeting["tenant_id"]), action["assignee"], datasets)
                       if datasets else _participant_action_capability(str(meeting["tenant_id"]), action["assignee"]))
            if (cap.get("can_receive_persona_action") is not True or cap.get("status") != "allowed"
                    or "research_note" not in (cap.get("allowed_deliverables") or [])):
                reason = "persona_owner_did_not_allow_assignment"
        elif action.get("assignee_kind") != "human":
            reason = "invalid_assignee_kind"
        if action.get("assignee_kind") == "human":
            # RW-01 already permits the tenant's authenticated operators to act
            # on this inbox. Neither model text nor caller-supplied convened_by
            # proves that an arbitrary username is a reachable human principal.
            if action["assignee"] != "operator":
                reason = "human_assignee_unresolvable_use_operator_inbox"
            requirement = item.get("human_requirement")
            if not isinstance(requirement, str) or not requirement.strip() or len(requirement) > 2000:
                reason = "human_requirement_required_or_unbounded"
            else:
                action["human_requirement"] = requirement.strip()
        due, review = item.get("due_at"), item.get("next_review")
        if due:
            try:
                parsed = datetime.fromisoformat(str(due).replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    raise ValueError()
                action["due_at"] = parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            except (ValueError, TypeError):
                reason = "invalid_due_at"
        elif isinstance(review, str) and review.strip() and len(review) <= 256:
            action["next_review"] = review.strip()
        else:
            reason = "due_or_explicit_next_review_required"
        if reason:
            failure = {"index": idx, "reason": reason}
            if reason == "persona_owner_did_not_allow_assignment":
                failure.update(required_data=action.get("required_data", []),
                               capability_status=cap.get("status"), capability_reason=cap.get("reason"))
            rejected.append(failure)
            continue
        source_ids.add(action["source_action_id"])
        actions.append({**action, "source_refs": list(refs), "state": "dispatch_pending"})
    seen_reviews = set()
    raw_reviews = raw.get("action_reviews") or []
    if not isinstance(raw_reviews, list) or len(raw_reviews) > 8:
        rejected.append({"reason": "action_reviews_must_be_bounded_list"})
        return actions, [], rejected
    for item in raw_reviews:
        if not isinstance(item, Mapping):
            continue
        tid = item.get("action_id")
        if not isinstance(tid, str):
            rejected.append({"reason": "invalid_review_action_id"})
            continue
        snapshot = prior.get(tid, {})
        refs = item.get("evidence_refs")
        # The model must compare the actual prior criterion and cite the submitted evidence it saw.
        expected = snapshot.get("evidence_refs") or []
        reason = item.get("reason")
        if (tid in seen_reviews or snapshot.get("state") != "submitted" or snapshot.get("evidence_omitted")
                or snapshot.get("evidence_available") is not True
                or item.get("decision") not in {"accept", "return"}
                or not isinstance(reason, str) or not reason.strip() or len(reason) > 2000
                or item.get("acceptance_criterion") != snapshot.get("acceptance_criterion")
                or not expected or refs != expected):
            rejected.append({"action_id": tid, "reason": "invalid_review_criterion_or_evidence"})
            continue
        seen_reviews.add(tid)
        reviews.append({"action_id": tid, "decision": item["decision"], "reason": reason,
                        "acceptance_criterion": item["acceptance_criterion"], "evidence_refs": refs})
    return actions, reviews, rejected


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
            chair_persona_id=chair_persona_id,
            convened_by=convened_by,
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
    meeting_record["meeting_series"] = _meeting_series(meeting_record)
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


def _resolve_meeting_review_cause(meeting: Mapping[str, Any]) -> Optional[str]:
    """Return the stored specific synthesis review cause when present, or 'unknown' on legacy REVIEW rows."""
    synth = meeting.get("synthesis") if isinstance(meeting.get("synthesis"), Mapping) else {}
    outcome = str(
        meeting.get("outcome")
        or synth.get("outcome")
        or ""
    ).strip().upper()
    status = str(meeting.get("status") or "").strip().upper()
    is_review = outcome == "REVIEW" or status == "REVIEW"
    for candidate in (
        synth.get("unreadable_reason"),
        meeting.get("synthesis_unreadable_reason"),
        meeting.get("unreadable_reason"),
        (meeting.get("operator_review") or {}).get("cause")
        if isinstance(meeting.get("operator_review"), Mapping)
        else None,
    ):
        val = str(candidate or "").strip()
        if val and val.lower() not in {"none", "null", "unknown"}:
            return val
    if is_review:
        return "unknown"
    return None


_MEETING_PRIVATE_SCOPES = frozenset({
    "persona_private",
    "private",
    "private_user",
    "user_private",
})
_MEETING_PRIVATE_KIND_VALUES = frozenset({
    "private_memory",
    "persona_private",
    "persona_private_memory",
    "memory:persona_private",
})


def _is_private_meeting_item(item: Mapping[str, Any]) -> bool:
    for key in ("relevance_scope", "memory_layer", "scope", "visibility_scope", "memory_scope"):
        val = str(item.get(key) or "").strip().lower()
        if val in _MEETING_PRIVATE_SCOPES:
            return True
    if item.get("is_private") is True or item.get("private") is True:
        return True
    for key in ("kind", "memory_type", "type", "owner"):
        val = str(item.get(key) or "").strip().lower()
        if val in _MEETING_PRIVATE_KIND_VALUES:
            return True
    return False


def _is_agora_or_trader_meeting_record(item: Mapping[str, Any]) -> bool:
    if str(item.get("source_owner") or "").strip().lower() == "agora":
        return True
    if str(item.get("origin") or "").strip().lower() in {"agora", "agora_advisor", "private_servant"}:
        return True
    if str(item.get("provenance") or "").strip().lower() == "agora":
        return True
    if str(item.get("kind") or "").strip().lower().startswith("agora"):
        return True
    if str(item.get("subject_kind") or "").strip().lower() in {"trader", "agora_user"}:
        return True
    if any(
        k in item
        for k in (
            "agora_user_id",
            "agoraUserId",
            "trader_id",
            "traderId",
            "account_id",
            "accountId",
            "broker_ids",
            "brokerIds",
            "raw_text",
        )
    ):
        return True
    return False


def _extract_meeting_trader_id(
    item: Mapping[str, Any],
    default_trader_id: Optional[str] = None,
) -> Optional[str]:
    for key in ("agora_user_id", "agoraUserId", "trader_id", "traderId", "user_id", "userId"):
        val = str(item.get(key) or "").strip()
        if val:
            return val
    if str(item.get("subject_kind") or "").strip().lower() in {"trader", "agora_user"}:
        val = str(item.get("subject_id") or "").strip()
        if val:
            return val
    subject_ref = item.get("subject_ref")
    if isinstance(subject_ref, Mapping):
        if str(subject_ref.get("kind") or "").strip().lower() in {"trader", "agora_user"}:
            val = str(
                subject_ref.get("id")
                or subject_ref.get("agora_user_id")
                or subject_ref.get("trader_id")
                or ""
            ).strip()
            if val:
                return val
    elif isinstance(subject_ref, str) and subject_ref.startswith(("trader:", "agora_user:", "user:")):
        val = subject_ref.split(":", 1)[1].strip()
        if val:
            return val
    convened_by = item.get("convened_by")
    if isinstance(convened_by, Mapping):
        if str(convened_by.get("kind") or "").strip().lower() in {"trader", "agora_user"}:
            val = str(
                convened_by.get("id")
                or convened_by.get("agora_user_id")
                or convened_by.get("trader_id")
                or ""
            ).strip()
            if val:
                return val
    return default_trader_id


def _can_view_meeting_trader(
    trader_id: Optional[str],
    *,
    is_operator: bool,
    caller_user_id: Optional[str],
) -> bool:
    if is_operator:
        return True
    if trader_id and caller_user_id and str(trader_id).strip() == str(caller_user_id).strip():
        return True
    return False


def _extract_item_ref_ids(item: Mapping[str, Any]) -> List[str]:
    out: List[str] = []
    for k in ("item_id", "id", "source_id", "ref_id", "entry_id", "record_id"):
        v = str(item.get(k) or "").strip()
        if v and v not in out:
            out.append(v)
    return out


def _collect_meeting_evidence_refs(
    meeting: Mapping[str, Any],
    *,
    is_operator: bool = False,
    caller_user_id: Optional[str] = None,
) -> List[str]:
    """Collect deduplicated non-private, caller-visible evidence/citation refs for a meeting."""
    meeting_trader_id = _extract_meeting_trader_id(meeting)

    def _is_withheld(item: Mapping[str, Any]) -> bool:
        if _is_private_meeting_item(item):
            return True
        if _is_agora_or_trader_meeting_record(item):
            rec_trader = _extract_meeting_trader_id(item, default_trader_id=meeting_trader_id)
            if not _can_view_meeting_trader(
                rec_trader,
                is_operator=is_operator,
                caller_user_id=caller_user_id,
            ):
                return True
        return False

    visible_item_ids: set[str] = set()
    withheld_ids: set[str] = set()
    visible_case_refs: List[str] = []
    visible_citation_refs: List[str] = []
    withheld_citation_refs: set[str] = set()

    for item in meeting.get("case_file_items") or []:
        if not isinstance(item, Mapping):
            continue
        ids = _extract_item_ref_ids(item)
        if _is_withheld(item):
            withheld_ids.update(ids)
        else:
            visible_item_ids.update(ids)
            primary = str(item.get("item_id") or item.get("id") or item.get("source_id") or "").strip()
            if primary:
                visible_case_refs.append(primary)

    raw_case_file = meeting.get("case_file")
    if isinstance(raw_case_file, Mapping):
        cf_lists: List[Any] = [raw_case_file.get("items")]
        groups = raw_case_file.get("groups")
        if isinstance(groups, Mapping):
            for grp in groups.values():
                if isinstance(grp, Mapping):
                    cf_lists.append(grp.get("items"))
        for cf_items in cf_lists:
            if not isinstance(cf_items, list):
                continue
            for item in cf_items:
                if not isinstance(item, Mapping):
                    continue
                ids = _extract_item_ref_ids(item)
                if _is_withheld(item):
                    withheld_ids.update(ids)
                else:
                    visible_item_ids.update(ids)

    raw_pre_read = meeting.get("pre_read")
    pre_read_entries: List[Mapping[str, Any]] = []
    if isinstance(raw_pre_read, list):
        pre_read_entries.extend(x for x in raw_pre_read if isinstance(x, Mapping))
    elif isinstance(raw_pre_read, Mapping):
        if _is_withheld(raw_pre_read):
            withheld_ids.update(_extract_item_ref_ids(raw_pre_read))
        for list_key in ("interpretations", "items"):
            sub = raw_pre_read.get(list_key)
            if isinstance(sub, list):
                pre_read_entries.extend(x for x in sub if isinstance(x, Mapping))
    for entry in pre_read_entries:
        ids = _extract_item_ref_ids(entry)
        if _is_withheld(entry):
            withheld_ids.update(ids)
            for cit in entry.get("citations") or []:
                if isinstance(cit, Mapping):
                    withheld_ids.update(_extract_item_ref_ids(cit))
                else:
                    s = str(cit or "").strip()
                    if s:
                        withheld_citation_refs.add(s)
        else:
            visible_item_ids.update(ids)

    attr_rec = meeting.get("attribution_record")
    if isinstance(attr_rec, Mapping) and _is_withheld(attr_rec):
        withheld_ids.update(_extract_item_ref_ids(attr_rec))
        if meeting.get("attribution_record_id"):
            withheld_ids.add(str(meeting["attribution_record_id"]).strip())

    for rec in meeting.get("agora_records") or []:
        if isinstance(rec, Mapping) and _is_withheld(rec):
            withheld_ids.update(_extract_item_ref_ids(rec))

    for rnd in meeting.get("rounds") or []:
        if not isinstance(rnd, Mapping):
            continue
        rnd_withheld = _is_withheld(rnd)
        for pos in rnd.get("positions") or []:
            if not isinstance(pos, Mapping):
                continue
            if rnd_withheld or _is_withheld(pos):
                withheld_ids.update(_extract_item_ref_ids(pos))
                for cit in pos.get("citations") or []:
                    if isinstance(cit, Mapping):
                        withheld_ids.update(_extract_item_ref_ids(cit))
                    else:
                        s = str(cit or "").strip()
                        if s:
                            withheld_citation_refs.add(s)
                continue
            for cit in pos.get("citations") or []:
                if isinstance(cit, Mapping):
                    if _is_withheld(cit):
                        withheld_ids.update(_extract_item_ref_ids(cit))
                        continue
                    s = str(cit.get("item_id") or cit.get("id") or cit.get("source_id") or "").strip()
                else:
                    s = str(cit or "").strip()
                if s:
                    visible_citation_refs.append(s)

    for cit_ref in withheld_citation_refs:
        if cit_ref not in visible_item_ids and cit_ref not in visible_citation_refs:
            withheld_ids.add(cit_ref)

    refs: List[str] = []
    seen: set[str] = set()

    def _add(val: Any) -> None:
        if isinstance(val, Mapping):
            if _is_withheld(val):
                withheld_ids.update(_extract_item_ref_ids(val))
                return
            s = str(val.get("item_id") or val.get("id") or val.get("source_id") or val.get("ref_id") or "").strip()
        else:
            s = str(val or "").strip()
        if s and s not in withheld_ids and s not in seen:
            seen.add(s)
            refs.append(s)

    synth = meeting.get("synthesis") if isinstance(meeting.get("synthesis"), Mapping) else {}
    minutes = meeting.get("minutes") if isinstance(meeting.get("minutes"), Mapping) else {}
    memo_refs = [
        str(x).strip()
        for x in (
            synth.get("dissent_memo_id"),
            minutes.get("memo_id"),
            minutes.get("dissent_memo_id"),
        )
        if x and str(x).strip()
    ]
    memo_ref_set = set(memo_refs)

    for r in meeting.get("evidence_refs") or []:
        if isinstance(r, str) and r.strip() in memo_ref_set:
            continue
        _add(r)
    for r in visible_case_refs:
        _add(r)
    for r in visible_citation_refs:
        _add(r)
    for r in memo_refs:
        _add(r)
    return refs


def _normalize_operator_review(
    raw_review: Any,
    *,
    default_cause: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if not isinstance(raw_review, Mapping):
        return None
    status = str(raw_review.get("status") or "").strip().lower()
    if status != "reviewed":
        return None
    note = str(raw_review.get("note") or raw_review.get("rationale") or "").strip()
    reviewed_by = str(raw_review.get("reviewed_by") or "").strip()
    reviewed_at = str(raw_review.get("reviewed_at") or "").strip()
    stored_cause = str(
        raw_review.get("cause")
        or raw_review.get("unreadable_reason")
        or ""
    ).strip()
    if stored_cause and stored_cause.lower() not in {"none", "null", "unknown"}:
        cause = stored_cause
    else:
        cause = str(default_cause or stored_cause or "unknown").strip() or "unknown"
    out: Dict[str, Any] = {
        "status": "reviewed",
        "disposition": str(raw_review.get("disposition") or "closed_no_proposal"),
        "note": note,
        "reviewed_by": reviewed_by,
        "reviewed_at": reviewed_at,
        "cause": cause,
        "unreadable_reason": cause,
    }
    if raw_review.get("idempotency_key"):
        out["idempotency_key"] = str(raw_review["idempotency_key"])
    return out


def _ensure_meeting_review_audit(
    store: Any,
    *,
    meeting: Mapping[str, Any],
    operator_id: str,
    service_actor_id: Optional[str] = None,
) -> None:
    if not (hasattr(store, "list_audit_for_request") and hasattr(store, "append_audit")):
        return
    meeting_id = str(meeting["meeting_id"])
    for ev in store.list_audit_for_request(meeting_id):
        if getattr(ev, "action", None) == "meeting_review_recorded":
            return
    op_review = meeting.get("operator_review") if isinstance(meeting.get("operator_review"), Mapping) else {}
    effective_operator = str(op_review.get("reviewed_by") or operator_id).strip() or operator_id
    review_ts = str(op_review.get("reviewed_at") or "").strip() or utc_now()
    digest_src = json.dumps(
        {
            "meeting_id": meeting_id,
            "reviewed_by": effective_operator,
            "reviewed_at": review_ts,
            "note": str(op_review.get("note") or ""),
        },
        sort_keys=True,
        ensure_ascii=False,
    ).encode("utf-8")
    payload_hash = hashlib.sha256(digest_src).hexdigest()
    audit_id = f"aud-mrev-{hashlib.sha256(f'{meeting_id}:{effective_operator}'.encode('utf-8')).hexdigest()[:12]}"
    service_actor_ref = (
        {"actor_type": "service", "actor_id": service_actor_id}
        if service_actor_id
        else None
    )
    event = ConsultAuditEvent(
        audit_id=audit_id,
        request_id=meeting_id,
        actor_ref={"actor_type": "operator", "actor_id": effective_operator},
        service_actor_ref=service_actor_ref,
        action="meeting_review_recorded",
        before_state="REVIEW:pending",
        after_state="REVIEW:reviewed",
        payload_hash=payload_hash,
        trace_id=str(meeting.get("trace_id") or meeting_id),
        timestamp=review_ts,
    )
    store.append_audit(event)


def record_meeting_operator_review(
    *,
    store: Any,
    tenant_id: str,
    meeting_id: str,
    reviewed_by: str,
    note: str,
    reviewed_at: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    service_actor_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], bool]:
    """Persist an authenticated operator's review disposition on a closed REVIEW meeting.

    Preserves the original REVIEW synthesis, transcript/rounds, and zero proposals.
    Repeated identical submissions return ``(meeting, True)`` without creating duplicate
    writes; partial write failures across lifecycle, outbox, and audit are recoverably
    idempotent on retry while preserving the first actor and timestamp. Conflicting
    submissions by another operator or with a different note raise 409
    ``MeetingRefusedError("meeting_already_reviewed")``.
    """
    from contextlib import nullcontext
    from .store import MEETING_OPERATOR_REVIEW_NOTE_MAX_CHARS

    lock = getattr(store, "_lock", None)
    ctx = lock if lock is not None else nullcontext()
    with ctx:
        meeting = store.get_meeting(meeting_id)
        if not meeting or str(meeting.get("tenant_id") or "default") != tenant_id:
            raise KeyError(meeting_id)

        operator_id = str(reviewed_by or "").strip()
        if not operator_id:
            raise MeetingRefusedError(
                "operator_required",
                "reviewed_by operator identity is required",
                status_code=403,
                meeting_id=meeting_id,
            )

        clean_note = str(note or "").strip()
        if not clean_note:
            raise MeetingRefusedError(
                "review_note_required",
                "A non-empty operator review note is required",
                status_code=422,
                meeting_id=meeting_id,
            )
        if len(clean_note) > MEETING_OPERATOR_REVIEW_NOTE_MAX_CHARS:
            raise MeetingRefusedError(
                "review_note_too_long",
                f"Operator review note exceeds {MEETING_OPERATOR_REVIEW_NOTE_MAX_CHARS} characters",
                status_code=422,
                meeting_id=meeting_id,
            )

        status = str(meeting.get("status") or "").strip().lower()
        synth = meeting.get("synthesis") if isinstance(meeting.get("synthesis"), Mapping) else {}
        outcome = str(meeting.get("outcome") or synth.get("outcome") or "").strip().upper()
        if status not in {"closed", "completed", "review"} or outcome != "REVIEW":
            raise MeetingRefusedError(
                "meeting_not_in_review",
                f"Meeting {meeting_id} (status={meeting.get('status')!r}, outcome={meeting.get('outcome')!r}) "
                "is not a closed REVIEW meeting",
                status_code=409,
                meeting_id=meeting_id,
            )
        if len(synth.get("proposals") or []) > 0:
            raise MeetingRefusedError(
                "meeting_has_proposals",
                f"Meeting {meeting_id} issued proposals and cannot be closed as a zero-proposal synthesis review",
                status_code=409,
                meeting_id=meeting_id,
            )

        cause = _resolve_meeting_review_cause(meeting) or "unknown"
        pending_map = getattr(store, "_pending_meeting_reviews", None)
        if not isinstance(pending_map, dict):
            pending_map = {}

        existing_review = _normalize_operator_review(meeting.get("operator_review"), default_cause=cause)
        if existing_review is not None:
            if existing_review["reviewed_by"] != operator_id or existing_review["note"] != clean_note:
                raise MeetingRefusedError(
                    "meeting_already_reviewed",
                    f"Meeting {meeting_id} has already been reviewed by {existing_review['reviewed_by']}",
                    status_code=409,
                    meeting_id=meeting_id,
                )
            meeting["operator_review"] = existing_review
            store.put_meeting(meeting)
            _ensure_meeting_review_audit(
                store,
                meeting=meeting,
                operator_id=operator_id,
                service_actor_id=service_actor_id,
            )
            pending_map.pop(meeting_id, None)
            return meeting, True

        staged_review = _normalize_operator_review(pending_map.get(meeting_id), default_cause=cause)
        if staged_review is not None:
            if staged_review["reviewed_by"] != operator_id or staged_review["note"] != clean_note:
                raise MeetingRefusedError(
                    "meeting_already_reviewed",
                    f"Meeting {meeting_id} has already been reviewed by {staged_review['reviewed_by']}",
                    status_code=409,
                    meeting_id=meeting_id,
                )
            review_record = dict(staged_review)
        else:
            eff_reviewed_at = str(reviewed_at or "").strip() or utc_now()
            clean_idem = str(idempotency_key or "").strip() or None
            review_record = {
                "status": "reviewed",
                "disposition": "closed_no_proposal",
                "note": clean_note,
                "reviewed_by": operator_id,
                "reviewed_at": eff_reviewed_at,
                "cause": cause,
                "unreadable_reason": cause,
            }
            if clean_idem:
                review_record["idempotency_key"] = clean_idem
            pending_map[meeting_id] = copy.deepcopy(review_record)

        meeting["operator_review"] = review_record
        try:
            store.put_meeting(meeting)
        except ValueError as exc:
            pending_map.pop(meeting_id, None)
            if "immutable" in str(exc).lower():
                raise MeetingRefusedError(
                    "meeting_already_reviewed",
                    str(exc),
                    status_code=409,
                    meeting_id=meeting_id,
                ) from exc
            raise MeetingRefusedError(
                "invalid_operator_review",
                str(exc),
                status_code=422,
                meeting_id=meeting_id,
            ) from exc

        _ensure_meeting_review_audit(
            store,
            meeting=meeting,
            operator_id=operator_id,
            service_actor_id=service_actor_id,
        )
        pending_map.pop(meeting_id, None)
        return meeting, False


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

        write_file_env = (
            "PANTHEON_MEMORY_AUTH_TOKEN_FILE"
            if os.getenv("PANTHEON_MEMORY_AUTH_TOKEN_FILE", "").strip()
            else "CONSULTATION_MEMORY_WRITE_TOKEN_FILE"
        )
        write_token_env = (
            "PANTHEON_MEMORY_AUTH_TOKEN"
            if os.getenv("PANTHEON_MEMORY_AUTH_TOKEN", "").strip()
            or not os.getenv("CONSULTATION_MEMORY_WRITE_TOKEN", "").strip()
            else "CONSULTATION_MEMORY_WRITE_TOKEN"
        )
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            **resolve_runtime_manager_auth(
                token_env_key=write_token_env,
                token_file_env_key=write_file_env,
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


def _restore_action_review_state(store: Any, meeting: Dict[str, Any]) -> None:
    """Read this chair/series checkpoint; HTTP execution receives it in detail."""
    if not meeting.get("meeting_series") or not hasattr(store, "get_meeting_schedule"):
        return
    previous = store.get_meeting_schedule(
        str(meeting["tenant_id"]), str(meeting["chair_persona_id"]), str(meeting["meeting_type"]),
        meeting_series=meeting["meeting_series"])
    if previous and previous.get("meeting_series") == meeting.get("meeting_series"):
        meeting["previous_action_review_at"] = previous.get("action_review_watermark")
        meeting["action_review_scan"] = previous.get("action_review_scan") or {}


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
    if meeting.get("status") == "closed":
        # Explicit execution retry resumes owner dispatch, never a new chair decision.
        if (meeting.get("synthesis") or {}).get("actions") or (meeting.get("synthesis") or {}).get("action_reviews"):
            from .meeting_outputs import dispatch_meeting_actions, review_meeting_actions
            meeting["actions"] = dispatch_meeting_actions(meeting)
            meeting["action_review_dispatches"] = review_meeting_actions(meeting)
            store.put_meeting(meeting)
        return meeting
    if meeting.get("status") == "refused":
        return meeting

    cat = dict(catalogue or load_meeting_catalogue())
    spec = resolve_meeting_type_spec(str(meeting.get("meeting_code") or meeting["meeting_type"]), cat)
    meeting_code = str(spec["meeting_type"])
    participants = list(meeting.get("participants") or [])
    chair_persona_id = str(meeting["chair_persona_id"])
    topic = str(meeting["topic"])
    trace_id = str(meeting.get("trace_id") or f"trace-{meeting_id}")

    if meeting.get("action_decision_frozen"):
        return _close_persona_meeting(store=store, meeting=meeting, spec=spec, memory_url=memory_url)

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
    legacy_unresolved_refs = [
        str(i.get("source_id") or i.get("item_id") or "").strip()
        for i in case_file_items
        if _is_legacy_unresolved_placeholder(i, topic)
    ]
    if not case_file_items or legacy_unresolved_refs:
        preserved_case_file: Dict[str, Any] = copy.deepcopy(case_file) if isinstance(case_file, Mapping) else {}
        if case_file_items:
            existing_items = list(preserved_case_file.get("items") or [])
            for it in case_file_items:
                if isinstance(it, Mapping) and not _is_legacy_unresolved_placeholder(it, topic):
                    if not any(
                        isinstance(x, Mapping) and x.get("item_id") == it.get("item_id")
                        for x in existing_items
                    ):
                        existing_items.append(dict(it))
            preserved_case_file["items"] = existing_items
        case_file, case_file_items, resolved_attr = compose_meeting_case_file(
            meeting_id=meeting_id,
            meeting_code=meeting_code,
            topic=topic,
            subject_ref=meeting.get("subject_ref"),
            evidence_refs=legacy_unresolved_refs,
            attribution_record_id=meeting.get("attribution_record_id"),
            attribution_record=meeting.get("attribution_record"),
            supplied_case_file=preserved_case_file or None,
            tenant_id=tenant_id,
            chair_persona_id=chair_persona_id,
            convened_by=meeting.get("convened_by"),
        )
        meeting["case_file"] = case_file
        meeting["case_file_items"] = case_file_items
        if resolved_attr is not None:
            meeting["attribution_record"] = resolved_attr

    from .meeting_outputs import read_meeting_actions
    # Detail projections include an empty prior_actions list before execution.
    # Only the persisted source receipt proves an owner read actually happened.
    if "prior_actions_source" not in meeting:
        _restore_action_review_state(store, meeting)
        action_read = read_meeting_actions(meeting)
        meeting["prior_actions"] = action_read["items"]
        meeting["prior_action_evidence"] = action_read.get("evidence") or []
        meeting["prior_actions_source"] = {k: v for k, v in action_read.items() if k not in {"items", "evidence"}}
        if action_read["items"] or action_read["status"] != "ok":
            meeting["agenda"] = ["Review previous work against its acceptance criterion (including pending owner pages)",
                                  *(meeting.get("agenda") or [topic])]
    meeting["participant_action_capabilities"] = [
        _participant_action_capability(tenant_id, str(p["persona_id"])) for p in participants
    ]
    case_file, action_case_items = _attach_meeting_action_case(case_file, meeting.get("prior_actions") or [])
    case_file_items = [i for i in case_file_items if i.get("source_kind") != "meeting_action"] + action_case_items
    meeting.update(case_file=case_file, case_file_items=case_file_items)
    meta = dict(meeting.get("metadata") or {})
    memory_refusals: Dict[str, str] = {}
    pre_read = build_participant_pre_read(
        refusals=memory_refusals,
        meeting_id=meeting_id,
        tenant_id=tenant_id,
        participants=participants,
        topic=topic,
        supplied_pre_read=meeting.get("pre_read") or meta.get("pre_read"),
        memory_url=memory_url,
    )
    meeting["pre_read"] = pre_read
    if memory_refusals:
        # Named per persona, never blocking; kept off the provider request so a
        # retry's content (idempotency key) is unchanged.
        meeting["memory_read_refusals"] = dict(memory_refusals)
    meeting["status"] = "in_round"
    store.put_meeting(meeting)

    allowed_case_ids = {
        str(cid)
        for item in case_file_items
        for cid in (item.get("item_id"), item.get("source_id"))
        if cid
    }
    if meeting.get("attribution_record_id"):
        allowed_case_ids.add(str(meeting["attribution_record_id"]))
    allowed_case_ids.update(f"meeting_action:{a['action_id']}" for a in meeting.get("prior_actions") or [])
    preread_ids_by_persona: Dict[str, set[str]] = {
        str(pr["persona_id"]): set(str(eid) for eid in (pr.get("entry_ids") or []))
        for pr in pre_read
    }
    preread_by_persona: Dict[str, Dict[str, Any]] = {
        str(pr["persona_id"]): pr for pr in pre_read
    }
    default_case_id = (
        str(case_file_items[0]["item_id"])
        if case_file_items and case_file_items[0].get("item_id")
        else next(iter(sorted(allowed_case_ids)), f"facts:meeting_topic:{meeting_id}")
    )

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
                "prior_actions": meeting.get("prior_actions") or [],
                "prior_action_evidence": meeting.get("prior_action_evidence") or [],
                "prior_actions_source": meeting.get("prior_actions_source"),
                "own_action_capability": next((c for c in meeting["participant_action_capabilities"]
                                                if c["persona_id"] == pid), None),
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
            _check_meeting_prompt_budget(ask_request)
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
        "prior_actions": meeting.get("prior_actions") or [],
        "prior_action_evidence": meeting.get("prior_action_evidence") or [],
        "prior_actions_source": meeting.get("prior_actions_source"),
        "participant_action_capabilities": meeting.get("participant_action_capabilities") or [],
        "own_pre_read": preread_by_persona.get(chair_persona_id),
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
    _check_meeting_prompt_budget(synth_ask)
    try:
        synth_contrib = provider.obtain(
            tenant_id=tenant_id,
            request=synth_ask,
            participant_scope=f"{chair_persona_id}:synthesis",
        )
        ev_content = dict(synth_contrib.event_content or {})
        if isinstance(ev_content.get("synthesis"), Mapping):
            raw_synthesis = dict(ev_content["synthesis"])
        elif ev_content.get("synthesis") is not None:
            raw_txt = str(ev_content.get("synthesis"))
            raw_synthesis = {
                "unreadable": True,
                "unreadable_reason": f"parse_failure (raw_length={len(raw_txt)})",
                "raw_length": len(raw_txt),
            }
        elif ev_content.get("unreadable") is True:
            raw_txt = ev_content.get("raw_output") if ev_content.get("raw_output") is not None else ev_content.get("raw_text")
            raw_len: Optional[int] = (
                int(ev_content["raw_length"])
                if ev_content.get("raw_length") is not None
                else (len(str(raw_txt)) if raw_txt is not None else None)
            )
            default_cause = (
                f"parse_failure (raw_length={raw_len})"
                if raw_len is not None
                else "parse_failure (raw_length=unknown)"
            )
            raw_synthesis = {
                "unreadable": True,
                "unreadable_reason": ev_content.get("unreadable_reason") or default_cause,
                **({"raw_length": raw_len} if raw_len is not None else {}),
            }
        else:
            # Only the chair's explicit output adopts work. Round suggestions remain advisory.
            collected_proposals = list(ev_content.get("proposals") or [])
            raw_synthesis = {
                "summary": synth_contrib.summary,
                "proposals": collected_proposals,
                "actions": ev_content.get("actions") or [],
                "action_reviews": ev_content.get("action_reviews") or [],
                "no_action_reason": ev_content.get("no_action_reason"),
                "outcome": ev_content.get("outcome"),
                "unreadable": ev_content.get("unreadable", False),
            }
    except Exception as exc:
        # Provider returned unreadable/malformed/timed-out synthesis -> REVIEW with named cause (AC3 & AC5)
        cause = _classify_synthesis_unreadable_reason(exc=exc)
        raw_synthesis = {"unreadable": True, "unreadable_reason": cause}

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
    actions, reviews, rejected_actions = _validate_adopted_actions(
        raw_synthesis or {}, meeting, allowed_case_ids | preread_ids_by_persona.get(chair_persona_id, set()),
        recheck_capabilities=True)
    # A malformed/REVIEW synthesis cannot become a new source of work.
    if synthesis["outcome"] == "REVIEW":
        actions, reviews = [], []
    synthesis.update(actions=actions, action_reviews=reviews, rejected_actions=rejected_actions,
                     no_action_reason=None if actions else str((raw_synthesis or {}).get("no_action_reason") or synthesis["summary"]))
    if actions and not synthesis["proposals"]:
        synthesis["outcome"] = "actions_adopted"
    if synthesis.get("unreadable_reason"):
        meeting["synthesis_unreadable_reason"] = synthesis["unreadable_reason"]
    else:
        meeting.pop("synthesis_unreadable_reason", None)

    # Freeze the actual chair decision before any owner call or writeback can fail.
    meeting.update(synthesis=synthesis, rejected_positions=rejected_positions,
                   action_decision_frozen=True)
    meeting["usage"]["calls"] = calls_made
    store.put_meeting(meeting)
    return _close_persona_meeting(store=store, meeting=meeting, spec=spec, memory_url=memory_url)


def _close_persona_meeting(*, store: Any, meeting: Dict[str, Any], spec: Mapping[str, Any],
                           memory_url: str) -> Dict[str, Any]:
    """Resume a frozen close without asking the model to adopt a different decision."""
    meeting_id, tenant_id = str(meeting["meeting_id"]), str(meeting["tenant_id"])
    meeting_code, topic = str(meeting["meeting_code"]), str(meeting["topic"])
    chair_persona_id, trace_id = str(meeting["chair_persona_id"]), str(meeting["trace_id"])
    participants, rounds_recorded = meeting["participants"], meeting["rounds"]
    synthesis = meeting["synthesis"]
    rejected_positions = meeting.get("rejected_positions") or []
    calls_made = int((meeting.get("usage") or {}).get("calls") or 0)
    default_case_id = next((str(i["item_id"]) for i in meeting.get("case_file_items") or [] if i.get("item_id")),
                           f"facts:meeting_topic:{meeting_id}")

    # Step 8: Record & publish Dissent Memo + Summary Memo in store
    now_closed = utc_now()
    # The first published dissent anchors both immutable memo timestamps across
    # partial-close retries, even when a retry runs in another second/process.
    prior_dissent = store.get_memo(f"mem-dissent-{meeting_id}") if hasattr(store, "get_memo") else None
    memo_timestamp = prior_dissent.created_at if prior_dissent and prior_dissent.status == MemoStatus.PUBLISHED else now_closed
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
        created_at=memo_timestamp,
        published_at=memo_timestamp,
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
        created_at=memo_timestamp,
        published_at=memo_timestamp,
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

    from .meeting_outputs import dispatch_meeting_actions, review_meeting_actions, MeetingOutputRoutingError
    meeting["actions"] = dispatch_meeting_actions(meeting)
    meeting["action_review_dispatches"] = review_meeting_actions(meeting)
    store.put_meeting(meeting)
    if (any(a.get("dispatch_error") for a in meeting["actions"])
            or any(r.get("status") != "recorded" for r in meeting["action_review_dispatches"])):
        # Use the existing workflow's bounded retry/lease machinery. Publishing a
        # closed request here would suppress its next attempt permanently.
        raise MeetingOutputRoutingError("meeting_action_owner_pending; frozen decision retained for retry", status_code=503)

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
    source = meeting.get("prior_actions_source") or {}
    pages = source.get("pages") or {}
    evidence_read = all(e.get("status") == "ok" for e in meeting.get("prior_action_evidence") or [])
    scan = source.get("scan") or {}
    finished = (source.get("status") == "ok" and set(pages) == {"outstanding", "submitted", "reviewed"}
                and all(not p.get("has_more") and p.get("status") == "ok" for p in pages.values()))
    evidence_failed = bool(scan.get("evidence_failed")) or not evidence_read
    # Keyset traversal is not a snapshot: updates behind a cursor must be read
    # again. Advance only to the first page's start, never the last meeting's
    # creation time. Inclusive owner filters retain same-timestamp updates.
    watermark = scan.get("started_at") if finished and not evidence_failed else None
    watermark = watermark or meeting.get("previous_action_review_at")
    continuation = {}
    if scan.get("started_at") and not finished:
        tokens = dict(scan.get("page_tokens") or {})
        completed = set(scan.get("completed_lanes") or [])
        for lane, page in pages.items():
            if page.get("status") == "ok":
                tokens[lane] = page.get("next_page_token")
                if not page.get("has_more"):
                    completed.add(lane)
        continuation = {"started_at": scan["started_at"], "page_tokens": tokens,
                        "completed_lanes": sorted(completed), "evidence_failed": evidence_failed}
    # Unavailable evidence was shown explicitly and cannot be accepted. It must
    # not pin a cursor: finish the traversal, then retry from the old watermark.
    # Sticky failure state prevents a later healthy page from hiding obligations.
    for p in participants:
        if p.get("trading") or p["persona_id"] == chair_persona_id:
            persona_id = str(p["persona_id"])
            # Attendance updates cadence, but only the chair owns this series'
            # review state. The store merges peer cadence at the write boundary,
            # including for the HTTP proxy; never send a stale peer cursor back.
            schedule: Dict[str, Any] = {}
            if persona_id == chair_persona_id:
                schedule.update(
                    meeting_series=meeting.get("meeting_series"),
                    action_review_watermark=watermark,
                    action_review_scan=continuation,
                )
            store.put_meeting_schedule(
                {
                    **schedule,
                    "tenant_id": tenant_id,
                    "persona_id": persona_id,
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
    if "trading" in p:
        item["trading"] = bool(p["trading"])
    if "can_sponsor" in p:
        item["can_sponsor"] = bool(p["can_sponsor"])
    return item


_DEFAULT_MAX_ATTEMPTS = 3
_ACTIVE_MEETING_STATUSES = {"triggered", "convened", "in_round", "open", "in_progress"}


def _max_attempts() -> int:
    try:
        return max(1, int(os.environ.get("CONSULTATION_WORKFLOW_MAX_BLOCKED_ATTEMPTS", _DEFAULT_MAX_ATTEMPTS)))
    except (TypeError, ValueError):
        return _DEFAULT_MAX_ATTEMPTS


def _iso_from_epoch(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _execution_state_from_row(row: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    return {
        "status": str(row.get("status") or ""),
        "attempt_count": int(row.get("attempt_count") or row.get("blocked_count") or 0),
        "max_attempts": _max_attempts(),
        "last_error": row.get("last_error"),
        "updated_at": _iso_from_epoch(row.get("updated_at")),
    }


def _effective_status(meeting: Mapping[str, Any], execution_state: Optional[Mapping[str, Any]]) -> str:
    stored = str(meeting.get("status") or "convened")
    if (
        execution_state
        and execution_state.get("status") == "dead_letter"
        and stored.lower() in _ACTIVE_MEETING_STATUSES
        and int(meeting.get("round") or 0) == 0
    ):
        return "execution_failed"
    return stored


def _execution_states_by_request(
    workflow_state: Any, tenant_id: str
) -> Dict[str, Dict[str, Any]]:
    if workflow_state is None:
        return {}
    # One tenant-scoped read per request; matched in memory (no N+1).
    return {
        str(r.get("request_id")): r
        for r in workflow_state.list_items(tenant_id=tenant_id)
    }


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
    workflow_state: Any = None,
    include_failed: bool = False,
) -> Dict[str, Any]:
    cat = dict(catalogue or load_meeting_catalogue())
    wf_rows = _execution_states_by_request(workflow_state, tenant_id)
    exec_states = {
        mid: _execution_state_from_row(r) for mid, r in wf_rows.items()
    }
    dead_letter_count = 0
    rows = [
        m
        for m in store.list_meetings()
        if str(m.get("tenant_id") or "default") == tenant_id
    ]
    if status:
        st = status.strip().lower()
        if st in {"open", "upcoming"}:
            kept = []
            for m in rows:
                if str(m.get("status") or "").lower() not in _ACTIVE_MEETING_STATUSES:
                    continue
                if (
                    _effective_status(m, exec_states.get(str(m.get("meeting_id")))) == "execution_failed"
                    and not include_failed
                ):
                    dead_letter_count += 1
                    continue
                kept.append(m)
            rows = kept
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

    items = []
    for m in sliced:
        cause = _resolve_meeting_review_cause(m)
        op_review = _normalize_operator_review(m.get("operator_review"), default_cause=cause)
        items.append(
            {
                "execution_state": exec_states.get(str(m["meeting_id"])),
                "effective_status": _effective_status(m, exec_states.get(str(m["meeting_id"]))),
                "meeting_id": str(m["meeting_id"]),
                "meeting_type": str(m["meeting_type"]),
                "topic": str(m.get("topic") or ""),
                "status": str(m.get("status") or "convened"),
                "outcome": m.get("outcome"),
                "synthesis_unreadable_reason": cause,
                "unreadable_reason": cause,
                "operator_review": op_review,
                "evidence_refs": _collect_meeting_evidence_refs(m),
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
        )
    return {
        "items": items,
        "next_cursor": next_cursor,
        "summary": {"dead_letter_count": dead_letter_count},
    }


def get_meeting_detail_view(
    store: Any,
    *,
    tenant_id: str,
    meeting_id: str,
    workflow_state: Any = None,
) -> Optional[Dict[str, Any]]:
    m = store.get_meeting(meeting_id)
    if not m or str(m.get("tenant_id") or "default") != tenant_id:
        return None
    detail = copy.deepcopy(m)
    if "prior_actions_source" not in m and m.get("status") not in {"closed", "refused"}:
        # The normal HTTP executor reads this existing detail route, so supply
        # authoritative continuation without another endpoint or archive scan.
        _restore_action_review_state(store, detail)
    exec_state = None
    if workflow_state is not None:
        exec_state = _execution_state_from_row(
            workflow_state.get(tenant_id=tenant_id, request_id=str(m.get("meeting_id") or meeting_id))
        )
    detail["execution_state"] = exec_state
    detail["effective_status"] = _effective_status(m, exec_state)
    detail["participants"] = [
        _format_participant_summary(p) for p in (m.get("participants") or [])
    ]
    cause = _resolve_meeting_review_cause(m)
    detail["synthesis_unreadable_reason"] = cause
    detail["unreadable_reason"] = cause
    detail["operator_review"] = _normalize_operator_review(m.get("operator_review"), default_cause=cause)
    detail["evidence_refs"] = _collect_meeting_evidence_refs(m)
    # Ensure live downstream status is reflected on synthesis.proposals
    synth = dict(detail.get("synthesis") or {})
    if cause is not None:
        synth["unreadable_reason"] = cause
        synth["cause"] = cause
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
    current_actions = list(synth.get("actions") or [])
    prior_actions = list(detail.get("prior_actions") or [])
    for action in current_actions + prior_actions:
        if isinstance(action, dict) and action.get("ticket_id"):
            from .meeting_outputs import read_action_status
            action["live_owner_status"] = read_action_status(tenant_id, str(action["ticket_id"]))
    # Frozen snapshots remain intact; live owner progress is explicitly separate.
    detail["actions"] = current_actions
    detail["prior_actions"] = prior_actions
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
