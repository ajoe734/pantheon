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
MIN_REFRESH_ENV = "PANTHEON_MEMORY_MENTAL_MODEL_MIN_REFRESH_SECONDS"
DEFAULT_MIN_REFRESH_SECONDS = 86400  # coordinator proposal: one day
MAX_QUESTIONS = 32
MAX_QUESTION_CHARS = 600
MAX_ANSWER_TOKENS = 2048
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9:_.\-]{0,95}$")
_PERSONA_MEMORY_TYPES = {"episodic", "semantic", "preference", "strategy_lesson",
                         "risk_observation", "consultation_outcome"}


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
        for anchor in item.get("anchors") or []:
            normalized = hs.parse_subject_anchor(str(anchor))
            if normalized is None:
                raise StandingQuestionError("invalid_anchor")
            tags.append(f"anchor:{normalized}")
        for memory_type in item.get("memory_types") or []:
            if str(memory_type) not in _PERSONA_MEMORY_TYPES:
                raise StandingQuestionError("invalid_memory_type")
            tags.append(f"type:{memory_type}")
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


def read_answers(client: Any, tenant_id: Optional[str], persona_id: str, persona_store: Any) -> List[Dict[str, Any]]:
    """Current answers of one persona with canonical citations; fails closed per answer."""
    bank = hs.persona_bank(tenant_id, persona_id)
    answers: List[Dict[str, Any]] = []
    for model in client.list_mental_models(bank, detail="full"):
        if not str(model.get("id") or "").startswith(MODEL_PREFIX):
            continue
        based_on = ((model.get("reflect_response") or {}).get("based_on") or {})
        memories = [m for m in (based_on.get("memories") or []) if isinstance(m, dict)]
        citations = hs.reflect_citations({"based_on": {"memories": memories}})
        answer: Dict[str, Any] = {"key": model.get("name"), "question": model.get("source_query"),
                                  "refreshed_at": model.get("last_refreshed_at"), "stale": bool(model.get("is_stale")),
                                  "status": "answered", "text": model.get("content") or "", "citations": citations}
        reason: Optional[str] = None
        if not model.get("last_refreshed_at") or not answer["text"].strip():
            reason = "not_refreshed"
        elif based_on.get("mental_models") or based_on.get("directives"):
            reason = "non_canonical_source"
        elif not memories or not citations or any(not m.get("document_id") for m in memories):
            reason = "citation_unmapped"
        else:
            reason = _verify(citations, persona_store, tenant_id, persona_id)
        if reason:
            answer.update(status="unavailable", reason=reason, text="", citations=[])
        answers.append(answer)
    return sorted(answers, key=lambda a: str(a["key"]))
