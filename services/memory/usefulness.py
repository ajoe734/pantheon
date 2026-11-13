"""Owner-issued, replay-safe usefulness marks on canonical memory entries.

Reads remain audit counts. Only measured decision outcomes affect ranking.
The +/-5 bound, two neutral prior observations and three-harm review threshold
are coordinator starting values, not agent-selectable parameters.
"""
from __future__ import annotations

import copy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

USEFULNESS_BOUND = 5.0
HURT_REVIEW_THRESHOLD = 3


class UsefulnessMark(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    decision_id: str = Field(min_length=1)
    case_file_id: str = Field(min_length=1)
    cited_item_ids: list[str] = Field(min_length=1, max_length=100)
    outcome: Literal["helped", "hurt", "no_effect", "confounded"]
    reason: str = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)
    window_end: str = Field(min_length=1)


class LessonMark(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    entry_id: str = Field(min_length=1)
    layer: Literal["persona", "institutional"]
    mark: UsefulnessMark


class UsefulnessWriteback(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_event_type: Literal["case_file_usefulness"]
    source_event_id: str = Field(min_length=1)
    write_authority: Literal["persona-svc"]
    tenant_id: str = Field(min_length=1)
    sponsor_persona_id: str = Field(min_length=1)
    marks: list[LessonMark] = Field(min_length=1, max_length=100)


def usefulness_score(entry) -> float:
    marks = entry.usefulness_marks.values()
    counts = {outcome: sum(m["outcome"] == outcome for m in marks)
              for outcome in ("helped", "hurt", "no_effect")}
    return USEFULNESS_BOUND * (counts["helped"] - counts["hurt"]) / (2 + sum(counts.values()))


def marked_payload(entry, mark: dict) -> dict:
    mark = UsefulnessMark.model_validate(mark).model_dump()
    payload = copy.deepcopy(entry.to_dict())
    key = mark["decision_id"]
    prior = payload["usefulness_marks"].get(key)
    if prior is not None and prior != mark:
        raise ValueError("usefulness_decision_replay_conflict")
    payload["usefulness_marks"][key] = mark
    harmful = [m for m in payload["usefulness_marks"].values() if m["outcome"] == "hurt"]
    if len(harmful) >= HURT_REVIEW_THRESHOLD and not payload.get("memory_review"):
        # A durable review queue on the original lesson, not a fabricated trade
        # reflection/candidate. Existing retrieval exposes this pending review.
        payload["memory_review"] = {"status": "pending", "reason": "three_hurt_marks",
                                  "decision_ids": sorted(m["decision_id"] for m in harmful)}
    return payload


def apply_usefulness(payload, *, persona_store, institutional_store) -> dict:
    request = UsefulnessWriteback.model_validate(payload)
    targets = []
    for row in request.marks:
        store = persona_store if row.layer == "persona" else institutional_store
        entry = store.get(row.entry_id)
        if entry is None or entry.tenant_id != request.tenant_id:
            raise ValueError("usefulness_entry_unavailable")
        if row.layer == "persona" and entry.persona_id != request.sponsor_persona_id:
            raise ValueError("usefulness_persona_scope_denied")
        marked_payload(entry, row.mark.model_dump())  # validate whole batch before writes
        targets.append((store, row))
    results = []
    for store, row in targets:
        updated = store.mark_usefulness(row.entry_id, row.mark.model_dump())
        results.append({"entry_id": row.entry_id, "layer": row.layer,
                        "decision_id": row.mark.decision_id,
                        "outcome": row.mark.outcome, "memory_review": updated.memory_review})
    return {"source_event_id": request.source_event_id, "source_event_type": request.source_event_type,
            "sponsor_persona_id": request.sponsor_persona_id, "marks": results}
