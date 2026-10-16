"""Fail-closed Persona trade reflection pipeline.

The worker consumes projection facts and emits review artifacts only.  It has no
broker, policy, capital-allocation, or persona-memory mutation dependency.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from jsonschema import Draft7Validator

REFLECTION_SCHEMA = json.loads(Path(__file__).with_name("persona_trade_reflection.schema.json").read_text())
_REFLECTION_VALIDATOR = Draft7Validator(REFLECTION_SCHEMA)
_LESSON_SCHEMA = REFLECTION_SCHEMA["properties"]["lesson_candidates"]["items"]
SERVER_LESSON_FIELDS = ("lesson_candidate_id", "supporting_episode_ids", "expiry")
PROVIDER_ARRAYS = ("mistakes", "what_worked", "unknowns", "followups")


class ReflectionProvider(Protocol):
    name: str
    model: str

    def reflect(self, *, facts: Mapping[str, Any], trigger: str) -> Mapping[str, Any]: ...


class ReflectionError(RuntimeError):
    """Base error for rejected or failed reflection work."""


class FactsUnavailable(ReflectionError):
    """Raised when no canonical facts are available; providers are not called."""


@dataclass(frozen=True)
class ReflectionRequest:
    request_id: str
    persona_id: str
    trade_episode_ids: tuple[str, ...]
    trigger: str
    facts: Mapping[str, Any]
    missing_refs: tuple[str, ...] = ()


def reflection_schema_errors(row: Mapping[str, Any]) -> list[str]:
    """Canonical Draft7 errors for one reflection row; empty when valid."""
    return [e.message for e in _REFLECTION_VALIDATOR.iter_errors(dict(row))]


def provider_extraction_schema() -> dict[str, Any]:
    """Provider-owned slice of the canonical reflection schema; server fields stay server-owned."""
    props = REFLECTION_SCHEMA["properties"]
    lesson = {
        "type": "object",
        "properties": {k: v for k, v in _LESSON_SCHEMA["properties"].items() if k not in SERVER_LESSON_FIELDS},
        "required": [k for k in _LESSON_SCHEMA["required"] if k not in SERVER_LESSON_FIELDS],
        "additionalProperties": False,
    }
    properties = {
        key: props[key] for key in ("expected_vs_actual", "attribution", "counterfactuals", *PROVIDER_ARRAYS)
    }
    properties["lesson_candidates"] = {"type": "array", "items": lesson}
    return {
        "type": "object",
        "properties": properties,
        "required": ["expected_vs_actual", "attribution", "counterfactuals", "lesson_candidates"],
    }


def episode_missing_refs(row: Mapping[str, Any]) -> tuple[str, ...]:
    """Canonical gaps a telemetry episode declares, top-level or under ``coverage``."""
    coverage = row.get("coverage")
    refs = [*(row.get("missing_refs") or ()), *((coverage.get("missing_refs") or ()) if isinstance(coverage, Mapping) else ())]
    return tuple(dict.fromkeys(str(ref) for ref in refs))


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def facts_snapshot(facts: Mapping[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """Return a content-addressed, detached snapshot of canonical input facts."""
    detached = deepcopy(dict(facts))
    digest = hashlib.sha256(_canonical(detached)).hexdigest()
    return f"facts://sha256/{digest}", f"sha256:{digest}", detached


class TradeReflectionPipeline:
    VALID_TRIGGERS = {"fill_review", "episode_closed", "scheduled_pattern", "manual_retry"}

    def __init__(
        self,
        provider: ReflectionProvider,
        *,
        prompt_template: str = "persona-trade-reflection-v1",
        prompt_version: str = "1.0.0",
        max_attempts: int = 3,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self.provider = provider
        self.prompt_template = prompt_template
        self.prompt_version = prompt_version
        self.max_attempts = max_attempts
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.attempts: dict[str, int] = {}
        self.completed: dict[str, dict[str, Any]] = {}
        self.dead_letters: list[dict[str, Any]] = []

    def process(self, request: ReflectionRequest) -> dict[str, Any]:
        if request.request_id in self.completed:
            return deepcopy(self.completed[request.request_id])
        self._validate(request)
        snapshot_ref, snapshot_hash, snapshot = facts_snapshot(request.facts)
        attempt = self.attempts.get(request.request_id, 0) + 1
        self.attempts[request.request_id] = attempt

        try:
            generated = dict(self.provider.reflect(facts=deepcopy(snapshot), trigger=request.trigger))
            artifact = self._artifact(request, generated, snapshot_ref, snapshot_hash)
        except Exception as exc:
            if attempt >= self.max_attempts:
                self.dead_letters.append(
                    {
                        "request_id": request.request_id,
                        "attempts": attempt,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "facts_snapshot_ref": snapshot_ref,
                        "facts_snapshot_hash": snapshot_hash,
                        "failed_at": self.now().isoformat(),
                    }
                )
            raise ReflectionError(f"reflection attempt {attempt} failed") from exc

        self.completed[request.request_id] = artifact
        return deepcopy(artifact)

    def _validate(self, request: ReflectionRequest) -> None:
        if request.trigger not in self.VALID_TRIGGERS:
            raise ValueError(f"unsupported trigger: {request.trigger}")
        if not request.facts or not request.trade_episode_ids:
            raise FactsUnavailable("canonical episode facts are required")
        if request.trigger == "scheduled_pattern" and len(set(request.trade_episode_ids)) < 2:
            raise FactsUnavailable("pattern review requires facts from multiple episodes")

    def _artifact(
        self,
        request: ReflectionRequest,
        generated: Mapping[str, Any],
        snapshot_ref: str,
        snapshot_hash: str,
    ) -> dict[str, Any]:
        coverage = "partial" if request.missing_refs else "complete"
        unknowns = list(generated.get("unknowns", []))
        unknowns.extend(f"missing canonical ref: {ref}" for ref in request.missing_refs)
        expiry = (self.now() + timedelta(days=30)).isoformat()
        lessons = [
            {
                **deepcopy(item),
                "lesson_candidate_id": str(uuid.uuid4()),
                "supporting_episode_ids": list(request.trade_episode_ids),
                "expiry": expiry,
            }
            if isinstance(item, Mapping)
            else item
            for item in generated.get("lesson_candidates") or ()
        ]
        comparison = generated.get("expected_vs_actual")
        if isinstance(comparison, Mapping):
            comparison = {**dict.fromkeys(REFLECTION_SCHEMA["properties"]["expected_vs_actual"]["required"]), **comparison}

        artifact = {
            "reflection_id": str(uuid.uuid4()),
            "trade_episode_id": request.trade_episode_ids[0],
            "persona_id": request.persona_id,
            "reflection_version": 1,
            "trigger": request.trigger,
            "facts_snapshot_ref": snapshot_ref,
            "facts_snapshot_hash": snapshot_hash,
            "expected_vs_actual": comparison,
            "counterfactuals": deepcopy(generated.get("counterfactuals")),
            "attribution": generated.get("attribution"),
            "mistakes": list(generated.get("mistakes", [])),
            "what_worked": list(generated.get("what_worked", [])),
            "unknowns": unknowns,
            "followups": list(generated.get("followups", [])),
            "lesson_candidates": lessons,
            "model": self.provider.model,
            "provider": self.provider.name,
            "prompt_template": self.prompt_template,
            "version": self.prompt_version,
            "generated_at": self.now().isoformat(),
            "evidence_coverage": coverage,
            "review_state": "proposed",
        }
        errors = reflection_schema_errors(artifact)
        if errors:
            raise ReflectionError(f"provider output violates canonical reflection schema: {errors[0]}")
        return artifact


def due_pattern_requests(
    episodes: Sequence[Mapping[str, Any]], *, minimum_sample: int = 3
) -> list[tuple[str, tuple[str, ...]]]:
    """Group closed episodes for pattern review without inferring missing keys."""
    groups: dict[str, list[str]] = {}
    for episode in episodes:
        if episode.get("status") not in {"closed", "reflected", "force_closed"}:
            continue
        persona_id, episode_id = episode.get("persona_id"), episode.get("trade_episode_id")
        if persona_id and episode_id:
            groups.setdefault(str(persona_id), []).append(str(episode_id))
    return [
        (persona_id, tuple(ids))
        for persona_id, ids in sorted(groups.items())
        if len(set(ids)) >= minimum_sample
    ]
