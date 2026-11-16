"""Persona standing questions (Hindsight mental models) behind the memory service (H13).

* Only the memory service calls the Hindsight mental-model API; the persona service
  declares a persona's questions through an internal owner route and agents can neither
  create nor edit them.
* ``declare`` is an idempotent set by key: missing questions are created, changed ones
  updated, and keys no longer declared are retired.  Models live in the persona's own
  bank, so another persona's or tenant's models are unreachable by construction.
* Answers are derived data that exist only in Hindsight.  ``read_answers`` returns an
  answer only when every cited memory maps to an active, unexpired canonical entry of
  the same tenant and persona; otherwise the answer is withheld with a recorded reason.
* Refresh follows consolidation (``refresh_after_consolidation``) with a per-model
  minimum interval from configuration; nothing here is scheduled and nothing is written
  to the canonical stores.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence

from . import hindsight_index as hs

MODEL_PREFIX = "pq-"
REGIME_QUESTION_KEY = "market_regime"
MIN_REFRESH_ENV = "PANTHEON_MEMORY_MENTAL_MODEL_MIN_REFRESH_SECONDS"
DEFAULT_MIN_REFRESH_SECONDS = 86400  # coordinator proposal: one day
MAX_QUESTIONS = 32
MAX_QUESTION_CHARS = 600
MAX_ANSWER_TOKENS = 2048
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9:_.\-]{0,95}$")
_PERSONA_MEMORY_TYPES = {"episodic", "semantic", "preference", "strategy_lesson",
                         "risk_observation", "consultation_outcome"}
REGIME_CLASSES = frozenset({
    "risk_on", "risk_off", "trending_up", "trending_down", "mean_reverting",
    "high_volatility", "low_volatility", "regime_transition", "mixed",
})


class StandingQuestionError(ValueError):
    """Invalid declaration; ``reason`` is stable."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def min_refresh_seconds() -> int:
    raw = os.getenv(MIN_REFRESH_ENV, "").strip()
    try:
        value = int(raw) if raw else DEFAULT_MIN_REFRESH_SECONDS
    except ValueError:
        return DEFAULT_MIN_REFRESH_SECONDS
    return max(0, value)


def model_id(key: str) -> str:
    return MODEL_PREFIX + hs._slug(key)


def _active_regime_class(record: Any, pool_families: set) -> Optional[str]:
    if not isinstance(record, Mapping):
        return None
    if record.get("state") not in (None, "active"):
        return None
    if not str(record.get("regime_id") or "").strip():
        return None
    sid = str(record.get("strategy_id") or "").strip().lower()
    if sid and sid not in pool_families:
        return None
    regime_class = str(record.get("regime_class") or "").strip().lower()
    return regime_class if regime_class in REGIME_CLASSES else None


def active_regime_classes(families: Any, regime: Any) -> List[str]:
    """Sorted active regime classes for strategies in ``families``; empty when none exists."""
    if not isinstance(families, (list, tuple, set)):
        return []
    pool_families = {str(f).strip().lower() for f in families if str(f or "").strip()}
    if not pool_families or regime is None:
        return []
    classes: set = set()
    if isinstance(regime, Mapping):
        if "status" in regime and regime.get("status") != "observed":
            return []
        if "regime_state_ref" in regime and not str(regime.get("regime_state_ref") or "").startswith("regime-state://"):
            return []
        records = regime.get("records")
        if isinstance(records, Mapping):
            for sid, rec in records.items():
                if str(sid or "").strip().lower() in pool_families:
                    cls = _active_regime_class(rec, pool_families)
                    if cls:
                        classes.add(cls)
        else:
            cls = _active_regime_class(regime, pool_families)
            if cls:
                classes.add(cls)
    elif isinstance(regime, (list, tuple, set)):
        for rec in regime:
            cls = _active_regime_class(rec, pool_families)
            if cls:
                classes.add(cls)
    return sorted(classes)


def regime_standing_question(families: Any, regime: Any) -> Optional[Dict[str, Any]]:
    """Market-regime standing question when an active regime record exists for a pool strategy, else ``None``."""
    classes = active_regime_classes(families, regime)
    if not classes:
        return None
    label = ", ".join(classes)
    return {
        "key": REGIME_QUESTION_KEY,
        "anchors": [f"regime:{cls}" for cls in classes],
        "text": (f"What have my own retained interpretations and lessons taught me about operating in the "
                 f"{label} market regime, and how should that regime change my proposals or risk posture?"),
    }


def standing_questions(families: Any, regime: Any = None) -> List[Dict[str, Any]]:
    """Deterministic standing-question set from a persona's pool families and active regime records (H13)."""
    questions: List[Dict[str, Any]] = [
        {"key": f"family:{fam}", "anchors": [f"family:{fam}"],
         "text": (f"What have my own retained interpretations and lessons taught me about running the "
                  f"{fam} strategy family, and what has gone wrong with it?")}
        for fam in sorted({str(f).strip().lower() for f in (families or ()) if str(f or "").strip()})
    ]
    questions.append({
        "key": "risk_lessons", "memory_types": ["risk_observation"],
        "text": "Which risk lessons have I recorded, and what should they change in how I size or stop positions?",
    })
    regime_question = regime_standing_question(families, regime)
    if regime_question is not None:
        questions.append(regime_question)
    return questions


def normalize_questions(raw: Any) -> List[Dict[str, Any]]:
    """Validate a declaration. ``[]`` is valid (retire everything)."""
    if not isinstance(raw, (list, tuple)) or len(raw) > MAX_QUESTIONS:
        raise StandingQuestionError("invalid_question_set")
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for item in raw:
        if not isinstance(item, Mapping):
            raise StandingQuestionError("invalid_question")
        key = str(item.get("key") or "").strip()
        text = str(item.get("text") or "").strip()
        if not _KEY_RE.match(key) or not text or len(text) > MAX_QUESTION_CHARS:
            raise StandingQuestionError("invalid_question")
        if key in seen:
            raise StandingQuestionError("duplicate_question_key")
        seen.add(key)
        tags: List[str] = []
        regime_anchors: List[str] = []
        for anchor in item.get("anchors") or []:
            normalized = hs.parse_subject_anchor(str(anchor))
            if normalized is None:
                raise StandingQuestionError("invalid_anchor")
            if normalized.startswith("regime:"):
                if normalized.split(":", 1)[1] not in REGIME_CLASSES:
                    raise StandingQuestionError("invalid_regime_anchor")
                regime_anchors.append(normalized)
            tags.append(f"anchor:{normalized}")
        for memory_type in item.get("memory_types") or []:
            if str(memory_type) not in _PERSONA_MEMORY_TYPES:
                raise StandingQuestionError("invalid_memory_type")
            tags.append(f"type:{memory_type}")
        if key == REGIME_QUESTION_KEY and (not regime_anchors or len(regime_anchors) != len(tags)):
            raise StandingQuestionError("regime_anchor_required")
        out.append({"key": key, "text": text, "tags": sorted(set(tags))})
    return out


def _model_body(question: Mapping[str, Any], interval: int) -> Dict[str, Any]:
    trigger: Dict[str, Any] = {
        "refresh_after_consolidation": True,
        "min_refresh_interval_seconds": interval,
        "exclude_mental_models": True,  # answers rest on canonical-derived memories only
    }
    if question["tags"]:
        trigger["tags_match"] = "any_strict"
    return {"name": question["key"], "source_query": question["text"], "tags": list(question["tags"]),
            "max_tokens": MAX_ANSWER_TOKENS, "trigger": trigger}


def _differs(existing: Mapping[str, Any], body: Mapping[str, Any]) -> bool:
    trigger = existing.get("trigger") or {}
    wanted = body["trigger"]
    return (existing.get("name") != body["name"] or existing.get("source_query") != body["source_query"]
            or sorted(existing.get("tags") or []) != sorted(body["tags"])
            or any(trigger.get(k) != v for k, v in wanted.items()))


def declare(client: Any, tenant_id: Optional[str], persona_id: str, raw_questions: Any,
            *, interval: Optional[int] = None) -> Dict[str, Any]:
    """Make the persona's standing-question set exactly ``raw_questions`` (idempotent)."""
    questions = normalize_questions(raw_questions)
    interval = min_refresh_seconds() if interval is None else interval
    bank = hs.persona_bank(tenant_id, persona_id)
    hs.ensure_layers(client, bank)
    existing = {m.get("id"): m for m in client.list_mental_models(bank, detail="content")
                if str(m.get("id") or "").startswith(MODEL_PREFIX)}
    report: Dict[str, List[str]] = {"created": [], "updated": [], "unchanged": [], "retired": []}
    wanted: set = set()
    for question in questions:
        mid = model_id(question["key"])
        wanted.add(mid)
        body = _model_body(question, interval)
        current = existing.get(mid)
        if current is None:
            client.create_mental_model(bank, {"id": mid, **body})
            report["created"].append(question["key"])
        elif _differs(current, body):
            client.update_mental_model(bank, mid, body)
            report["updated"].append(question["key"])
        else:
            report["unchanged"].append(question["key"])
    for mid, current in sorted(existing.items()):
        if mid not in wanted:
            client.delete_mental_model(bank, mid)
            report["retired"].append(str(current.get("name") or mid))
    return {"persona_id": persona_id, "bank": bank, "min_refresh_interval_seconds": interval, **report}


def _verify(citations: Sequence[str], store: Any, tenant_id: Optional[str], persona_id: str) -> Optional[str]:
    """First reason a cited canonical entry cannot back an answer, else ``None``."""
    for cid in citations:
        entry = store.get(cid)
        if entry is None:
            return "canonical_source_missing"
        if (entry.tenant_id or "default") != (tenant_id or "default") or entry.persona_id != persona_id:
            return "canonical_source_out_of_scope"
        if entry.is_expired():
            return "canonical_source_expired"
        if not entry.is_active:
            return "canonical_source_inactive"
    return None


_NON_CANONICAL_SOURCES = ("mental_models", "mental-models", "directives")


def _source_documents(client: Any, bank: str, based_on: Mapping[str, Any]) -> "tuple[List[str], Optional[str]]":
    """Canonical ids behind a refreshed answer, resolved through the cited memory units.

    A refresh reports ``based_on`` keyed by fact type with unit ids only; each unit is read back from
    the index and mapped through its ``document_id`` (one level of ``source_memory_ids`` for
    observations).  Any unit that cannot be mapped fails the whole answer closed.
    """
    if any(based_on.get(k) for k in _NON_CANONICAL_SOURCES):
        return [], "non_canonical_source"
    unit_ids = [str(f.get("id")) for kind, facts in based_on.items() if kind not in _NON_CANONICAL_SOURCES
                for f in (facts or []) if isinstance(f, dict) and f.get("id")]
    if not unit_ids:
        return [], "citation_unmapped"
    documents: Dict[str, None] = {}
    for unit_id in unit_ids:
        unit = client.memory_unit(bank, unit_id) or {}
        mapped = [unit["document_id"]] if unit.get("document_id") else []
        for source_id in (unit.get("source_memory_ids") or unit.get("source_fact_ids") or []) if not mapped else []:
            source = client.memory_unit(bank, str(source_id)) or {}
            if source.get("document_id"):
                mapped.append(source["document_id"])
            else:
                return [], "citation_unmapped"
        if not mapped:
            return [], "citation_unmapped"
        documents.update(dict.fromkeys(mapped))
    return list(documents), None


def read_answers(client: Any, tenant_id: Optional[str], persona_id: str, persona_store: Any) -> List[Dict[str, Any]]:
    """Current answers of one persona with canonical citations; fails closed per answer."""
    bank = hs.persona_bank(tenant_id, persona_id)
    answers: List[Dict[str, Any]] = []
    for model in client.list_mental_models(bank, detail="full"):
        if not str(model.get("id") or "").startswith(MODEL_PREFIX):
            continue
        reflected = model.get("reflect_response") or {}
        answer: Dict[str, Any] = {"key": model.get("name"), "question": model.get("source_query"),
                                  "refreshed_at": model.get("last_refreshed_at"), "stale": bool(model.get("is_stale")),
                                  "status": "answered", "text": model.get("content") or "", "citations": []}
        reason: Optional[str] = None
        if reflected.get("reflect_skipped"):
            reason = str(reflected["reflect_skipped"])
        elif not reflected or not model.get("last_refreshed_at") or not answer["text"].strip():
            reason = "not_refreshed"
        else:
            citations, reason = _source_documents(client, bank, reflected.get("based_on") or {})
            if reason is None:
                reason = _verify(citations, persona_store, tenant_id, persona_id)
                answer["citations"] = citations
        if reason:
            answer.update(status="unavailable", reason=reason, text="", citations=[])
        answers.append(answer)
    return sorted(answers, key=lambda a: str(a["key"]))
