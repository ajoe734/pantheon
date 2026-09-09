"""Deterministic negative-memory matching for strategy seed safety.

The v1 matcher is intentionally local and keyword-based.  It compares a
SeedCandidate-shaped payload against known negative records such as rejected
seeds, retired strategies, failed experiments, and postmortems.  It does not
call embedding services or mutate registry state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence
import re


class NegativeMemoryWarningLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    BLOCKING = "blocking"


class NegativeMemoryKind(str, Enum):
    RETIRED_STRATEGY = "retired_strategy"
    REJECTED_CANDIDATE = "rejected_candidate"
    FAILED_EXPERIMENT = "failed_experiment"
    POSTMORTEM = "postmortem"
    NEGATIVE_MEMORY = "negative_memory"


_NO_MATCH_REASON = "No retired, rejected, failed, or postmortem memory crossed deterministic match thresholds."
_BLOCKING_STATUSES = frozenset({"rejected", "retired", "failed", "blocked", "postmortem"})
_WARNING_THRESHOLD = 0.35
_BLOCKING_THRESHOLD = 0.7
_EXACT_REF_THRESHOLD = 0.95
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "by",
        "can",
        "for",
        "from",
        "in",
        "into",
        "is",
        "it",
        "of",
        "on",
        "or",
        "over",
        "per",
        "the",
        "to",
        "using",
        "with",
        "without",
        "strategy",
        "strategies",
        "seed",
        "candidate",
        "experiment",
        "run",
        "result",
        "results",
        "evidence",
        "source",
        "data",
        "dataset",
        "research",
    }
)
_ALIASES = {
    "equities": "equity",
    "stocks": "equity",
    "stock": "equity",
    "taiwan": "twse",
    "tw": "twse",
    "tpex": "twse",
    "five": "5",
    "5day": "5_day",
    "5-day": "5_day",
    "forward": "forward_return",
    "returns": "return",
    "return": "return",
    "lightgbm": "lightgbm",
    "lgbm": "lightgbm",
    "look-ahead": "lookahead",
    "look_ahead": "lookahead",
    "post-mortem": "postmortem",
}
_TEXT_KEYS = (
    "hypothesis",
    "title",
    "summary",
    "description",
    "reason",
    "rejection_reason",
    "failure_reason",
    "failure_mode",
    "root_cause",
    "lesson",
    "lessons",
    "action_items",
    "contributing_factors",
)
_STRATEGY_KEYS = (
    "strategy_family",
    "strategy_families",
    "feature_hints",
    "features",
    "signals",
    "factors",
    "keywords",
    "backend_hint",
    "backend",
    "framework",
)
_MARKET_KEYS = (
    "asset_class",
    "asset_classes",
    "assets",
    "market_scope",
    "markets",
    "market",
    "venues",
    "symbols",
)
_DATA_KEYS = (
    "required_data",
    "data_dependencies",
    "dataset_refs",
    "label_hints",
    "labels",
    "target",
    "targets",
    "holding_period",
    "horizon",
)
_RISK_KEYS = (
    "risk_notes",
    "risks",
    "risk_constraints",
    "risk_profile",
    "risk_level",
    "drawdown",
    "incident_tags",
)
_REF_KEYS = (
    "seed_id",
    "strategy_id",
    "artifact_id",
    "experiment_id",
    "experiment_run_id",
    "run_id",
    "postmortem_id",
    "source_ids",
    "source_refs",
    "evidence_item_ids",
    "citation_refs",
    "trace_refs",
)


@dataclass(frozen=True)
class NegativeMemoryMatch:
    """Best deterministic match result for a seed candidate."""

    warning_level: NegativeMemoryWarningLevel | str
    similarity: float
    reason: str
    matched_memory_id: str | None = None
    matched_memory_kind: NegativeMemoryKind | str | None = None
    matched_terms: Sequence[str] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        try:
            level = (
                self.warning_level
                if isinstance(self.warning_level, NegativeMemoryWarningLevel)
                else NegativeMemoryWarningLevel(str(self.warning_level))
            )
        except ValueError as exc:
            allowed = ", ".join(level.value for level in NegativeMemoryWarningLevel)
            raise NegativeMemoryError(f"warning_level must be one of: {allowed}") from exc
        object.__setattr__(self, "warning_level", level)
        similarity = max(0.0, min(1.0, float(self.similarity)))
        object.__setattr__(self, "similarity", round(similarity, 4))
        object.__setattr__(self, "reason", _require_text(self.reason, "reason"))
        object.__setattr__(self, "matched_terms", tuple(_unique_strings(self.matched_terms)))
        if self.matched_memory_kind is not None:
            object.__setattr__(self, "matched_memory_kind", str(self.matched_memory_kind))
        if self.matched_memory_id is not None:
            object.__setattr__(self, "matched_memory_id", str(self.matched_memory_id))

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "warning_level": self.warning_level.value,
            "similarity": self.similarity,
            "reason": self.reason,
        }
        if self.matched_memory_id:
            payload["matched_memory_id"] = self.matched_memory_id
        if self.matched_memory_kind:
            payload["matched_memory_kind"] = str(self.matched_memory_kind)
        if self.matched_terms:
            payload["matched_terms"] = list(self.matched_terms)
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "NegativeMemoryMatch":
        if not data:
            return no_negative_memory_match()
        return cls(
            warning_level=str(data.get("warning_level") or NegativeMemoryWarningLevel.INFO.value),
            similarity=float(data.get("similarity") or 0.0),
            reason=str(data.get("reason") or _NO_MATCH_REASON),
            matched_memory_id=data.get("matched_memory_id"),
            matched_memory_kind=data.get("matched_memory_kind"),
            matched_terms=tuple(str(item) for item in data.get("matched_terms") or ()),
        )


class NegativeMemoryError(ValueError):
    """Raised when negative-memory match payloads are invalid."""


def no_negative_memory_match() -> NegativeMemoryMatch:
    return NegativeMemoryMatch(
        warning_level=NegativeMemoryWarningLevel.INFO,
        similarity=0.0,
        reason=_NO_MATCH_REASON,
    )


def is_blocking_negative_memory_match(match: Mapping[str, Any] | NegativeMemoryMatch | None) -> bool:
    if isinstance(match, NegativeMemoryMatch):
        return match.warning_level == NegativeMemoryWarningLevel.BLOCKING
    if not match:
        return False
    return str(match.get("warning_level") or "").lower() == NegativeMemoryWarningLevel.BLOCKING.value


def _cosine_similarity(vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
    if len(vec_a) != len(vec_b) or not vec_a:
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = sum(a * a for a in vec_a) ** 0.5
    norm_b = sum(b * b for b in vec_b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return max(-1.0, min(1.0, dot / (norm_a * norm_b)))


def match_negative_memory(
    seed_candidate: Mapping[str, Any],
    negative_memory_records: Sequence[Mapping[str, Any] | Any] = (),
    *,
    embedding_engine: Any | None = None,
    backend: Any | None = None,
) -> NegativeMemoryMatch:
    """Return the strongest deterministic negative-memory match.

    ``seed_candidate`` accepts the same shape as ``StrategySpecSeed.to_dict()``
    or an interaction-derived SeedCandidate dict.  ``negative_memory_records``
    accepts loose dicts from seed stores, strategy registries, ExperimentRun
    outputs, or postmortem stores.
    """

    records_to_evaluate = list(negative_memory_records)
    if backend is not None:
        if embedding_engine is None and hasattr(backend, "embedding_engine"):
            embedding_engine = backend.embedding_engine
        cand_text = " ".join(str(v) for v in seed_candidate.values() if isinstance(v, str))
        if cand_text.strip():
            try:
                from services.search.filters import SearchAccessContext
                ctx = SearchAccessContext(
                    environment="paper",
                    access_scopes=["operator", "research", "public"],
                )
                backend_hits = backend.search(
                    query=cand_text[:500],
                    context=ctx,
                    top_k=20,
                    mode="hybrid",
                    record_kind="negative_memory",
                )
                for hit in backend_hits:
                    rec_dict = {
                        "negative_memory_id": hit.id,
                        "title": hit.title,
                        "summary": hit.search_text,
                        "metadata": hit.metadata,
                        "record_kind": hit.record_kind,
                        **(hit.metadata or {}),
                    }
                    records_to_evaluate.append(rec_dict)
            except Exception:
                pass

    candidate = _profile(seed_candidate)
    cand_text = " ".join(str(v) for v in seed_candidate.values() if isinstance(v, str))
    cand_vec = None
    if embedding_engine is not None and cand_text.strip():
        try:
            cand_vec = embedding_engine.embed_query(cand_text)
        except Exception:
            cand_vec = None

    best: _ScoredRecord | None = None
    for raw_record in records_to_evaluate:
        record = _to_mapping(raw_record)
        if not record:
            continue
        sem_sim = None
        if cand_vec is not None and embedding_engine is not None:
            rec_text = " ".join(str(v) for v in record.values() if isinstance(v, str))
            if rec_text.strip():
                try:
                    mem_vec = embedding_engine.embed_query(rec_text)
                    sem_sim = _cosine_similarity(cand_vec, mem_vec)
                except Exception:
                    sem_sim = None

        scored = _score(candidate, _profile(record, is_memory=True), record, sem_sim=sem_sim)
        if scored is None:
            continue
        if best is None or scored.similarity > best.similarity:
            best = scored

    if best is None or best.similarity < _WARNING_THRESHOLD:
        return no_negative_memory_match()

    level = NegativeMemoryWarningLevel.WARNING
    if best.similarity >= _BLOCKING_THRESHOLD or best.exact_ref_match:
        level = NegativeMemoryWarningLevel.BLOCKING
    reason = (
        f"Seed candidate resembles {best.kind} {best.memory_id}: "
        f"similarity={best.similarity:.2f}; matched terms={', '.join(best.matched_terms)}."
    )
    if level == NegativeMemoryWarningLevel.BLOCKING:
        reason = f"Blocking negative-memory match. {reason}"
    return NegativeMemoryMatch(
        warning_level=level,
        similarity=best.similarity,
        reason=reason,
        matched_memory_id=best.memory_id,
        matched_memory_kind=best.kind,
        matched_terms=best.matched_terms,
    )


def negative_memory_record_from_seed(seed: Mapping[str, Any] | Any) -> dict[str, Any] | None:
    """Project a stored seed into a negative-memory record when its status qualifies."""

    raw = _to_mapping(seed)
    if not raw:
        return None
    status = _status(raw)
    metadata = _mapping(raw.get("metadata"))
    if status not in _BLOCKING_STATUSES and not _truthy(metadata.get("negative_memory")):
        return None
    projected = dict(raw)
    projected.setdefault("negative_memory_kind", _kind_for_record(raw))
    projected.setdefault("negative_memory_id", _memory_id(raw))
    return projected


@dataclass(frozen=True)
class _Profile:
    refs: frozenset[str]
    text_terms: frozenset[str]
    strategy_terms: frozenset[str]
    market_terms: frozenset[str]
    data_terms: frozenset[str]
    risk_terms: frozenset[str]
    all_terms: frozenset[str]


@dataclass(frozen=True)
class _ScoredRecord:
    similarity: float
    matched_terms: tuple[str, ...]
    memory_id: str
    kind: str
    exact_ref_match: bool


def _score(
    candidate: _Profile,
    memory: _Profile,
    raw_record: Mapping[str, Any],
    sem_sim: float | None = None,
) -> _ScoredRecord | None:
    matched_terms = tuple(sorted(candidate.all_terms.intersection(memory.all_terms)))
    exact_ref_match = bool(candidate.refs and candidate.refs.intersection(memory.refs))
    if exact_ref_match:
        similarity = 1.0
    else:
        weighted_score = 0.0
        total_weight = 0.0
        for weight, candidate_terms, memory_terms in (
            (0.25, candidate.text_terms, memory.text_terms),
            (0.3, candidate.strategy_terms, memory.strategy_terms),
            (0.2, candidate.market_terms, memory.market_terms),
            (0.15, candidate.data_terms, memory.data_terms),
            (0.1, candidate.risk_terms, memory.risk_terms),
        ):
            if not candidate_terms or not memory_terms:
                continue
            total_weight += weight
            weighted_score += weight * _jaccard(candidate_terms, memory_terms)
        if total_weight == 0:
            similarity = _jaccard(candidate.all_terms, memory.all_terms)
        else:
            similarity = weighted_score / total_weight
        if len(matched_terms) >= 3:
            similarity += min(0.15, len(matched_terms) * 0.015)
        if sem_sim is not None and sem_sim > 0.6:
            similarity = max(similarity, float(sem_sim))
        similarity = max(0.0, min(1.0, similarity))

    if similarity < _WARNING_THRESHOLD and not exact_ref_match:
        return None
    if not exact_ref_match and len(matched_terms) < 2 and (sem_sim is None or sem_sim < 0.7):
        return None
    return _ScoredRecord(
        similarity=round(similarity, 4),
        matched_terms=matched_terms[:16],
        memory_id=_memory_id(raw_record),
        kind=_kind_for_record(raw_record),
        exact_ref_match=exact_ref_match,
    )


def _profile(value: Mapping[str, Any], *, is_memory: bool = False) -> _Profile:
    flattened = _flatten_record(value)
    metadata = _mapping(value.get("metadata"))
    if metadata:
        flattened.update({f"metadata.{key}": nested for key, nested in metadata.items()})
    if is_memory:
        for key in ("metrics", "evaluation_summary", "postmortem", "incident", "lineage"):
            nested = _mapping(value.get(key))
            if nested:
                flattened.update({f"{key}.{nested_key}": nested_value for nested_key, nested_value in nested.items()})

    text_terms = _terms_for_keys(flattened, _TEXT_KEYS)
    strategy_terms = _terms_for_keys(flattened, _STRATEGY_KEYS)
    market_terms = _terms_for_keys(flattened, _MARKET_KEYS)
    data_terms = _terms_for_keys(flattened, _DATA_KEYS)
    risk_terms = _terms_for_keys(flattened, _RISK_KEYS)
    refs = frozenset(_normalize_ref(value) for value in _values_for_keys(flattened, _REF_KEYS) if _normalize_ref(value))
    all_terms = frozenset().union(text_terms, strategy_terms, market_terms, data_terms, risk_terms)
    if not all_terms:
        all_terms = _tokenize(" ".join(str(item) for item in _flatten_strings(value)))
    return _Profile(
        refs=refs,
        text_terms=text_terms,
        strategy_terms=strategy_terms,
        market_terms=market_terms,
        data_terms=data_terms,
        risk_terms=risk_terms,
        all_terms=all_terms,
    )


def _kind_for_record(record: Mapping[str, Any]) -> str:
    explicit = str(record.get("negative_memory_kind") or record.get("kind") or "").strip().lower()
    if explicit in {kind.value for kind in NegativeMemoryKind}:
        return explicit
    status = _status(record)
    if "postmortem_id" in record or "postmortem" in explicit or str(record.get("source_surface") or "") == "postmortem":
        return NegativeMemoryKind.POSTMORTEM.value
    if status == "rejected":
        return NegativeMemoryKind.REJECTED_CANDIDATE.value
    if status == "retired":
        return NegativeMemoryKind.RETIRED_STRATEGY.value
    if status == "failed":
        return NegativeMemoryKind.FAILED_EXPERIMENT.value
    return NegativeMemoryKind.NEGATIVE_MEMORY.value


def _memory_id(record: Mapping[str, Any]) -> str:
    for key in (
        "negative_memory_id",
        "seed_id",
        "strategy_id",
        "artifact_id",
        "experiment_run_id",
        "run_id",
        "postmortem_id",
        "id",
    ):
        value = str(record.get(key) or "").strip()
        if value:
            return value
    return "negative-memory-record"


def _status(record: Mapping[str, Any]) -> str:
    for key in ("status", "lifecycle_state", "artifact_state", "result_status"):
        value = str(record.get(key) or "").strip().lower()
        if value:
            return value
    metadata = _mapping(record.get("metadata"))
    for key in ("status", "lifecycle_state", "artifact_state", "result_status"):
        value = str(metadata.get(key) or "").strip().lower()
        if value:
            return value
    return ""


def _flatten_record(value: Mapping[str, Any]) -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for key, nested in value.items():
        if isinstance(nested, Mapping):
            for nested_key, nested_value in nested.items():
                flattened[f"{key}.{nested_key}"] = nested_value
        else:
            flattened[str(key)] = nested
    return flattened


def _terms_for_keys(flattened: Mapping[str, Any], keys: Sequence[str]) -> frozenset[str]:
    values = _values_for_keys(flattened, keys)
    return _tokenize(" ".join(str(value) for value in values))


def _values_for_keys(flattened: Mapping[str, Any], keys: Sequence[str]) -> tuple[Any, ...]:
    values: list[Any] = []
    suffixes = tuple(f".{key}" for key in keys)
    for key, value in flattened.items():
        if key in keys or key.endswith(suffixes):
            values.extend(_flatten_strings(value))
    return tuple(values)


def _flatten_strings(value: Any) -> tuple[str, ...]:
    if value in (None, "", [], ()):
        return ()
    if isinstance(value, Mapping):
        values: list[str] = []
        for nested in value.values():
            values.extend(_flatten_strings(nested))
        return tuple(values)
    if isinstance(value, Sequence) and not isinstance(value, str):
        values = []
        for nested in value:
            values.extend(_flatten_strings(nested))
        return tuple(values)
    return (str(value).strip(),) if str(value).strip() else ()


def _tokenize(text: str) -> frozenset[str]:
    raw_tokens = re.findall(r"[a-zA-Z0-9][a-zA-Z0-9_\-+.]*", text.lower())
    tokens: list[str] = []
    for raw in raw_tokens:
        token = raw.strip("_-.+")
        token = _ALIASES.get(token, token)
        if "_" in token:
            tokens.extend(part for part in token.split("_") if part)
        if len(token) < 2 or token in _STOPWORDS:
            continue
        tokens.append(token)
    return frozenset(_unique_strings(tokens))


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left.intersection(right)) / len(left.union(right))


def _normalize_ref(value: Any) -> str:
    return str(value or "").strip().lower()


def _unique_strings(values: Sequence[Any] | Any | None) -> tuple[str, ...]:
    if values in (None, "", [], ()):
        return ()
    if isinstance(values, (str, int, float)):
        values = (values,)
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            ordered.append(text)
    return tuple(ordered)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _to_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "to_dict"):
        payload = value.to_dict()
        if isinstance(payload, Mapping):
            return payload
    return {}


def _require_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise NegativeMemoryError(f"{field_name} is required")
    return text


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


__all__ = [
    "NegativeMemoryError",
    "NegativeMemoryKind",
    "NegativeMemoryMatch",
    "NegativeMemoryWarningLevel",
    "is_blocking_negative_memory_match",
    "match_negative_memory",
    "negative_memory_record_from_seed",
    "no_negative_memory_match",
]
