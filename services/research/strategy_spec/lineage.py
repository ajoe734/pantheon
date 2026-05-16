"""StrategySpec evidence and code-reference lineage helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from services.knowledge.evidence.models import EvidenceItem
from services.research.strategy_spec.models import CodeRef, EvidenceRef, StrategySpecValidationError
from services.source_ingestion.connectors.base import SourceRecord, SourceType
from services.source_ingestion.strategy_seed_builder import StrategySpecSeed


class StrategySpecLineageError(ValueError):
    """Raised when evidence or code refs cannot be linked to a StrategySpec."""


@dataclass(frozen=True)
class StrategySpecLineageRefs:
    """Evidence/code refs ready to attach to a StrategySpec payload."""

    evidence_bundle_id: str
    source_ids: Sequence[str]
    evidence_refs: Sequence[EvidenceRef]
    code_refs: Sequence[CodeRef] = field(default_factory=tuple)
    citation_refs: Sequence[str] = field(default_factory=tuple)
    trace_refs: Sequence[str] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_bundle_id", _require_text(self.evidence_bundle_id, "evidence_bundle_id"))
        object.__setattr__(self, "source_ids", _unique_strings(self.source_ids))
        if not self.source_ids:
            raise StrategySpecLineageError("source_ids is required")
        evidence_refs = tuple(
            ref if isinstance(ref, EvidenceRef) else EvidenceRef.from_dict(_as_mapping(ref, "evidence_refs[]"))
            for ref in _as_sequence(self.evidence_refs, "evidence_refs")
        )
        if not evidence_refs:
            raise StrategySpecLineageError("evidence_refs is required")
        object.__setattr__(self, "evidence_refs", evidence_refs)
        object.__setattr__(
            self,
            "code_refs",
            tuple(
                ref if isinstance(ref, CodeRef) else CodeRef.from_dict(_as_mapping(ref, "code_refs[]"))
                for ref in _as_sequence(self.code_refs, "code_refs")
            ),
        )
        object.__setattr__(self, "citation_refs", _unique_strings(self.citation_refs))
        object.__setattr__(self, "trace_refs", _unique_strings(self.trace_refs))

        evidence_keys = {(ref.ref_type, ref.ref_id) for ref in self.evidence_refs}
        if len(evidence_keys) != len(self.evidence_refs):
            raise StrategySpecLineageError("evidence_refs must not contain duplicate ref_type/ref_id pairs")
        code_keys = {
            (ref.repo_ref, ref.path, ref.commit, ref.symbol, ref.line_start, ref.line_end)
            for ref in self.code_refs
        }
        if len(code_keys) != len(self.code_refs):
            raise StrategySpecLineageError("code_refs must not contain duplicate repo/path/commit/symbol/line pairs")

    def to_strategy_spec_patch(self) -> dict[str, Any]:
        """Return the optional StrategySpec fields represented by this lineage."""

        payload: dict[str, Any] = {
            "evidence_refs": [ref.to_dict() for ref in self.evidence_refs],
        }
        if self.code_refs:
            payload["code_refs"] = [ref.to_dict() for ref in self.code_refs]
        return payload

    def to_lineage_edge(
        self,
        *,
        strategy_spec_id: str,
        actor_ref: str,
        created_at: datetime | str | None = None,
        trace_id: str | None = None,
        edge_id: str | None = None,
    ) -> dict[str, Any]:
        """Build a normalized StrategySpec evidence/code lineage edge."""

        target_id = _require_text(strategy_spec_id, "strategy_spec_id")
        actor = _require_text(actor_ref, "actor_ref")
        timestamp = created_at or _utc_now()
        effective_trace_id = trace_id or (self.trace_refs[0] if self.trace_refs else "")
        return {
            "edge_id": edge_id or f"lineage-{_short_hash([self.evidence_bundle_id, target_id, actor])}",
            "edge_type": "strategy_spec_evidence_code_linked",
            "from_type": "evidence_bundle",
            "from_id": self.evidence_bundle_id,
            "to_type": "strategy_spec",
            "to_id": target_id,
            "created_at": _iso(timestamp),
            "actor_ref": actor,
            "trace_id": effective_trace_id,
            "source_ids": list(self.source_ids),
            "evidence_refs": [ref.to_dict() for ref in self.evidence_refs],
            "code_refs": [ref.to_dict() for ref in self.code_refs],
            "citation_refs": list(self.citation_refs),
        }


def build_strategy_spec_lineage_refs(
    seed: StrategySpecSeed | Mapping[str, Any],
    *,
    source_records: Sequence[SourceRecord | Mapping[str, Any]] = (),
    evidence_items: Sequence[EvidenceItem | Mapping[str, Any]] = (),
) -> StrategySpecLineageRefs:
    """Build StrategySpec evidence/code refs from a StrategySpecSeed lineage surface."""

    seed_obj = seed if isinstance(seed, StrategySpecSeed) else StrategySpecSeed.from_dict(seed)
    records = tuple(_coerce_source_record(record) for record in source_records)
    items = tuple(_coerce_evidence_item(item) for item in evidence_items)
    _validate_seed_inputs(seed_obj, records, items)

    items_by_id = {item.evidence_item_id: item for item in items}
    evidence_refs: list[EvidenceRef] = [
        EvidenceRef(
            ref_type="evidence_bundle",
            ref_id=seed_obj.evidence_bundle_id,
            association="source_evidence",
        )
    ]
    for item_id in seed_obj.evidence_item_ids:
        item = items_by_id.get(item_id)
        evidence_refs.append(
            EvidenceRef(
                ref_type="evidence_item",
                ref_id=item_id,
                source_id=item.source_id if item else None,
                evidence_item_id=item_id,
                citation_ref=item.citation_label if item else _citation_for_item(seed_obj, item_id),
                association="supporting_evidence",
                summary=_first_sentence(item.body) if item and item.body else None,
            )
        )

    trace_refs = _unique_strings(
        (
            *seed_obj.trace_refs,
            *_flatten_strings(seed_obj.lineage.get("trace_refs")),
            *[record.trace_id for record in records],
            *[trace_ref for item in items for trace_ref in item.trace_refs],
        )
    )
    code_refs = _collect_code_refs(seed_obj, records, items)

    return StrategySpecLineageRefs(
        evidence_bundle_id=seed_obj.evidence_bundle_id,
        source_ids=seed_obj.source_ids,
        evidence_refs=evidence_refs,
        code_refs=code_refs,
        citation_refs=seed_obj.citation_refs,
        trace_refs=trace_refs,
    )


def attach_lineage_refs_to_strategy_spec_payload(
    strategy_spec: Mapping[str, Any],
    lineage_refs: StrategySpecLineageRefs | Mapping[str, Any],
) -> dict[str, Any]:
    """Return a StrategySpec payload copy with evidence_refs/code_refs attached."""

    refs = lineage_refs if isinstance(lineage_refs, StrategySpecLineageRefs) else StrategySpecLineageRefs(**lineage_refs)
    payload = dict(strategy_spec)
    payload.update(refs.to_strategy_spec_patch())
    provenance = dict(payload.get("provenance") or {})
    existing_source_refs = _unique_strings(provenance.get("source_refs"))
    provenance["source_refs"] = list(_unique_strings((*existing_source_refs, *refs.source_ids)))
    payload["provenance"] = provenance
    return payload


def _validate_seed_inputs(
    seed: StrategySpecSeed,
    records: Sequence[SourceRecord],
    items: Sequence[EvidenceItem],
) -> None:
    source_ids = set(seed.source_ids)
    item_ids = set(seed.evidence_item_ids)
    outside_records = [record.source_id for record in records if record.source_id not in source_ids]
    if outside_records:
        raise StrategySpecLineageError(f"Source records outside StrategySpecSeed lineage: {outside_records}")
    outside_items = [
        item.evidence_item_id
        for item in items
        if item.evidence_item_id not in item_ids or item.source_id not in source_ids
    ]
    if outside_items:
        raise StrategySpecLineageError(f"Evidence items outside StrategySpecSeed lineage: {outside_items}")


def _collect_code_refs(
    seed: StrategySpecSeed,
    records: Sequence[SourceRecord],
    items: Sequence[EvidenceItem],
) -> tuple[CodeRef, ...]:
    refs: list[CodeRef] = []
    for payload in _code_ref_payloads(seed.metadata):
        refs.append(_coerce_code_ref(payload, default_source_id=seed.source_id))
    for record in records:
        for payload in _code_ref_payloads(record.metadata):
            refs.append(
                _coerce_code_ref(
                    payload,
                    default_source_id=record.source_id,
                    default_repo_ref=record.content_ref,
                )
            )
        if record.source_type == SourceType.REPO and not _code_ref_payloads(record.metadata):
            fallback = _repo_record_code_ref(record)
            if fallback is not None:
                refs.append(fallback)
    for item in items:
        for payload in _code_ref_payloads(item.metadata):
            refs.append(_coerce_code_ref(payload, default_source_id=item.source_id))
    return _unique_code_refs(refs)


def _code_ref_payloads(metadata: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    payload = metadata.get("code_refs") or metadata.get("code_ref")
    if payload in (None, "", [], ()):
        return ()
    if isinstance(payload, Mapping):
        return (payload,)
    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
        return tuple(item for item in payload if isinstance(item, Mapping))
    return ()


def _coerce_code_ref(
    payload: Mapping[str, Any],
    *,
    default_source_id: str | None = None,
    default_repo_ref: str | None = None,
) -> CodeRef:
    try:
        return CodeRef(
            repo_ref=payload.get("repo_ref") or payload.get("repository_ref") or payload.get("repository_url") or default_repo_ref,
            path=payload.get("path") or payload.get("file_path") or payload.get("code_path"),
            source_id=payload.get("source_id") or default_source_id,
            commit=payload.get("commit") or payload.get("commit_sha"),
            symbol=payload.get("symbol") or payload.get("function") or payload.get("class_name"),
            line_start=payload.get("line_start") or payload.get("start_line"),
            line_end=payload.get("line_end") or payload.get("end_line"),
            url=payload.get("url") or payload.get("permalink"),
            association=payload.get("association") or payload.get("link_type"),
        )
    except StrategySpecValidationError as exc:
        raise StrategySpecLineageError(str(exc)) from exc


def _repo_record_code_ref(record: SourceRecord) -> CodeRef | None:
    path = (
        record.metadata.get("path")
        or record.metadata.get("file_path")
        or record.metadata.get("code_path")
        or record.metadata.get("entrypoint")
    )
    if not path:
        return None
    return CodeRef(
        repo_ref=record.metadata.get("repo_ref") or record.metadata.get("repository_url") or record.content_ref,
        path=str(path),
        source_id=record.source_id,
        commit=record.metadata.get("commit") or record.metadata.get("commit_sha"),
        symbol=record.metadata.get("symbol") or record.metadata.get("function") or record.metadata.get("class_name"),
        line_start=record.metadata.get("line_start") or record.metadata.get("start_line"),
        line_end=record.metadata.get("line_end") or record.metadata.get("end_line"),
        url=record.metadata.get("url") or record.metadata.get("permalink"),
        association=record.metadata.get("association") or "strategy_code_reference",
    )


def _citation_for_item(seed: StrategySpecSeed, item_id: str) -> str | None:
    if len(seed.evidence_item_ids) == len(seed.citation_refs):
        for index, candidate in enumerate(seed.evidence_item_ids):
            if candidate == item_id:
                return seed.citation_refs[index]
    return None


def _coerce_source_record(value: SourceRecord | Mapping[str, Any]) -> SourceRecord:
    if isinstance(value, SourceRecord):
        return value
    return SourceRecord.from_dict(value)


def _coerce_evidence_item(value: EvidenceItem | Mapping[str, Any]) -> EvidenceItem:
    if isinstance(value, EvidenceItem):
        return value
    return EvidenceItem.from_dict(value)


def _as_mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise StrategySpecLineageError(f"{field_name} must be an object")
    return value


def _as_sequence(value: Sequence[Any] | Any | None, field_name: str) -> tuple[Any, ...]:
    if value is None:
        return tuple()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise StrategySpecLineageError(f"{field_name} must be an array")
    return tuple(value)


def _unique_strings(values: Sequence[Any] | Any | None) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in _flatten_strings(values):
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return tuple(ordered)


def _unique_code_refs(refs: Sequence[CodeRef]) -> tuple[CodeRef, ...]:
    seen: set[tuple[Any, ...]] = set()
    ordered: list[CodeRef] = []
    for ref in refs:
        key = (ref.repo_ref, ref.path, ref.commit, ref.symbol, ref.line_start, ref.line_end)
        if key not in seen:
            seen.add(key)
            ordered.append(ref)
    return tuple(ordered)


def _flatten_strings(value: Any) -> tuple[str, ...]:
    if value in (None, "", [], ()):
        return ()
    if isinstance(value, Mapping):
        values: list[str] = []
        for item in value.values():
            values.extend(_flatten_strings(item))
        return tuple(values)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        values = []
        for item in value:
            values.extend(_flatten_strings(item))
        return tuple(values)
    normalized = str(value).strip()
    return (normalized,) if normalized else ()


def _require_text(value: Any, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise StrategySpecLineageError(f"{field_name} is required")
    return normalized


def _first_sentence(text: str) -> str:
    compact = " ".join(str(text or "").split())
    if not compact:
        return ""
    for delimiter in (". ", "! ", "? "):
        if delimiter in compact:
            return compact.split(delimiter, maxsplit=1)[0] + delimiter.strip()
    return compact


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(value: datetime | str) -> str:
    if isinstance(value, str):
        return value
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _short_hash(values: Sequence[str]) -> str:
    payload = json.dumps(list(values), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
