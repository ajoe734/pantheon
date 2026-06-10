"""Production StrategySpec distillation from internal research notes.

The public surface is intentionally small: ``distill(source_record_id)`` reads a
governed internal-note SourceRecord, extracts structured strategy intent from
markdown, and returns a schema-valid StrategySpec payload. Registry writes remain
owned by the registry service.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from services.knowledge.evidence.models import EvidenceBundle, EvidenceItem
from services.research.strategy_spec.conversion import (
    StrategySpecConversionError,
    StrategySpecConversionResult,
    StrategySpecConversionService,
)
from services.research.strategy_spec.models import validate_strategy_spec_payload
from services.source_ingestion.connectors.base import SourceRecord


class ValidationError(ValueError):
    """Raised when source markdown cannot be distilled into a StrategySpec."""


REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_DIRS_ENV = "STRATEGY_SPEC_DISTILLATION_SOURCE_DIRS"
DEFAULT_SOURCE_DIRS = (
    REPO_ROOT / "docs" / "research" / "notes",
    REPO_ROOT / "tests" / "e2e" / "fixtures",
)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_KEY_VALUE_RE = re.compile(r"^\s*(?:[-*]\s*)?([A-Za-z][A-Za-z0-9 _./-]*?)\s*:\s*(.*?)\s*$")
_STRUCTURED_SECTION_NAMES = {
    "code_references",
    "code_refs",
    "data",
    "data_requirements",
    "evaluation",
    "evaluation_plan",
    "feature_hints",
    "features",
    "frequency",
    "hypothesis",
    "label_hints",
    "labels",
    "market",
    "market_scope",
    "metrics",
    "required_data",
    "risk_caps",
    "risk_limits",
    "risk_notes",
    "risks",
    "signals",
    "strategy",
    "strategy_seed",
    "targets",
    "thesis",
    "universe",
}


@dataclass(frozen=True)
class ParsedResearchNote:
    source_record_id: str
    title: str
    content_ref: str
    markdown: str
    hypothesis: str
    symbols: Sequence[str]
    venues: Sequence[str]
    asset_classes: Sequence[str]
    frequency: str
    risk_caps: Mapping[str, Any]
    required_data: Sequence[str]
    feature_hints: Sequence[str]
    label_hints: Sequence[str]
    risk_notes: Sequence[str]
    metrics: Sequence[str]
    backend_hint: str | None
    holding_period: str | None
    code_refs: Sequence[Mapping[str, Any]]
    confidence: float
    access_scope: Sequence[str]
    license_scope: str
    author: str
    created_at: str


class ProductionStrategySpecDistiller:
    """Distill schema-valid StrategySpec payloads from research-note markdown."""

    def __init__(
        self,
        *,
        source_dirs: Sequence[str | Path] | None = None,
        created_by: str = "Codex",
        created_at: datetime | str | None = None,
    ) -> None:
        self.source_dirs = tuple(_resolve_source_dirs(source_dirs))
        self.created_by = _require_text(created_by, "created_by")
        self.created_at = _iso_timestamp(created_at) if created_at is not None else None
        self._converter = StrategySpecConversionService(created_by=self.created_by)

    def distill(self, source_record_id: str) -> dict[str, Any]:
        """Return a schema-valid StrategySpec payload ready for registry admission."""

        result = self.distill_result(source_record_id)
        payload = result.strategy_spec.to_dict()
        validate_strategy_spec_payload(payload)
        return payload

    def distill_registry_payload(self, source_record_id: str) -> dict[str, Any]:
        """Return the registry service create payload for the distilled StrategySpec."""

        return dict(self.distill_result(source_record_id).registry_payload)

    def distill_result(self, source_record_id: str) -> StrategySpecConversionResult:
        note = _parse_research_note(self._resolve_markdown(source_record_id), created_at=self.created_at)
        source_record = _source_record_from_note(note)
        evidence_item = _evidence_item_from_note(note)
        evidence_bundle = _evidence_bundle_from_note(note, created_by=self.created_by)
        try:
            return self._converter.convert_source_material(
                evidence_bundle,
                source_records=[source_record],
                evidence_items=[evidence_item],
                title=note.title,
                created_at=note.created_at,
                metadata={
                    "task_id": "STRAT-V2-001",
                    "production_distillation": True,
                    "distillation_schema_version": "production_distillation.v1",
                    "source_record_id": note.source_record_id,
                    "source_markdown_sha256": _sha256(note.markdown),
                    "symbols": list(note.symbols),
                    "frequency": note.frequency,
                    "risk_caps": dict(note.risk_caps),
                    "metrics": list(note.metrics),
                    "note_content_ref": note.content_ref,
                },
            )
        except StrategySpecConversionError as exc:
            raise ValidationError(f"StrategySpec conversion failed for {note.source_record_id}: {exc}") from exc

    def _resolve_markdown(self, source_record_id: str) -> tuple[str, Path]:
        requested = _require_text(source_record_id, "source_record_id")
        direct_path = Path(requested)
        if direct_path.exists():
            return direct_path.read_text(encoding="utf-8"), direct_path

        for source_dir in self.source_dirs:
            if not source_dir.exists():
                continue
            for path in sorted(source_dir.rglob("*.md")):
                markdown = path.read_text(encoding="utf-8")
                ids = _candidate_source_ids(markdown, path)
                if requested in ids:
                    return markdown, path
        searched = ", ".join(str(path) for path in self.source_dirs) or "<none>"
        raise ValidationError(f"SourceRecord not found for source_record_id={requested!r}; searched: {searched}")


def distill(source_record_id: str) -> dict[str, Any]:
    """Distill ``source_record_id`` with the default production source resolver."""

    return ProductionStrategySpecDistiller().distill(source_record_id)


def distill_registry_payload(source_record_id: str) -> dict[str, Any]:
    """Build a registry-service create payload for ``source_record_id``."""

    return ProductionStrategySpecDistiller().distill_registry_payload(source_record_id)


def _parse_research_note(source: tuple[str, Path], *, created_at: str | None = None) -> ParsedResearchNote:
    markdown, path = source
    if not markdown.strip():
        raise ValidationError(f"source markdown is empty: {path}")

    sections = _markdown_sections(markdown)
    front_matter = _document_metadata(sections)
    seed = _section_key_values(sections, ("strategy_seed", "strategy"))

    source_record_id = (
        front_matter.get("source_record_id")
        or front_matter.get("source_id")
        or front_matter.get("record_id")
        or path.stem
    )
    title = _title(markdown, front_matter, path)
    hypothesis = _section_value(
        sections,
        seed,
        section_names=("hypothesis", "thesis"),
        keys=("hypothesis", "thesis", "strategy_hypothesis"),
        required_name="hypothesis",
    )
    universe = _extract_universe(sections, seed)
    frequency = _extract_frequency(sections, seed)
    risk_caps = _extract_risk_caps(sections)
    code_refs = _extract_code_refs(sections)
    required_data = _section_list(
        sections,
        seed,
        section_names=("data_requirements", "required_data", "data"),
        keys=("required_data", "data_dependencies", "dataset_refs", "market_data_fields"),
        fallback=("governed source evidence",),
    )
    feature_hints = _section_list(
        sections,
        seed,
        section_names=("feature_hints", "features", "signals", "factors"),
        keys=("feature_hints", "features", "signals", "factors"),
        fallback=(),
    )
    label_hints = _section_list(
        sections,
        seed,
        section_names=("label_hints", "labels", "targets"),
        keys=("label_hints", "labels", "target", "targets", "objective_label"),
        fallback=(),
    )
    risk_notes = _section_list(
        sections,
        seed,
        section_names=("risk_notes", "risks"),
        keys=("risk_notes", "risks", "risk_constraints"),
        fallback=tuple(f"risk_cap:{key}={value}" for key, value in risk_caps.items()),
    )
    metrics = _section_list(
        sections,
        seed,
        section_names=("evaluation", "metrics", "evaluation_plan"),
        keys=("metrics", "evaluation_metrics"),
        fallback=("sharpe_ratio", "max_drawdown"),
    )
    backend_hint = _first_text(seed.get("backend_hint"), seed.get("backend"), seed.get("framework"))
    holding_period = _first_text(seed.get("holding_period"), seed.get("horizon"), seed.get("target_horizon"))

    return ParsedResearchNote(
        source_record_id=_require_text(source_record_id, "source_record_id"),
        title=title,
        content_ref=_content_ref(path),
        markdown=markdown,
        hypothesis=hypothesis,
        symbols=universe["symbols"],
        venues=universe["venues"],
        asset_classes=universe["asset_classes"],
        frequency=frequency,
        risk_caps=risk_caps,
        required_data=required_data,
        feature_hints=feature_hints,
        label_hints=label_hints,
        risk_notes=risk_notes,
        metrics=metrics,
        backend_hint=backend_hint,
        holding_period=holding_period,
        code_refs=code_refs,
        confidence=_confidence(front_matter),
        access_scope=_split_list(front_matter.get("access") or front_matter.get("access_scope") or "research"),
        license_scope=_first_text(front_matter.get("license"), front_matter.get("license_scope")) or "internal_research",
        author=_first_text(front_matter.get("author"), front_matter.get("owner")) or "research-ops",
        created_at=created_at or _note_timestamp(front_matter.get("date") or front_matter.get("created_at")),
    )


def _source_record_from_note(note: ParsedResearchNote) -> SourceRecord:
    return SourceRecord(
        source_id=note.source_record_id,
        connector_id="internal-research-note",
        source_type="internal_note",
        title=note.title,
        content_ref=note.content_ref,
        status="normalized",
        trace_id=f"trace-strat-v2-001-{_short_hash([note.source_record_id])}",
        metadata={
            "task_id": "STRAT-V2-001",
            "body": note.markdown,
            "summary": note.hypothesis,
            "strategy_seed": {
                "hypothesis": note.hypothesis,
                "asset_class": list(note.asset_classes),
                "market_scope": list(note.venues),
                "holding_period": note.holding_period,
                "required_data": list(note.required_data),
                "backend_hint": note.backend_hint,
                "feature_hints": list(note.feature_hints),
                "label_hints": list(note.label_hints),
                "risk_notes": list(note.risk_notes),
            },
            "symbols": list(note.symbols),
            "frequency": note.frequency,
            "risk_caps": dict(note.risk_caps),
            "metrics": list(note.metrics),
            "code_refs": [dict(ref) for ref in note.code_refs],
            "license_scope": note.license_scope,
            "access_scope": list(note.access_scope),
            "governance": {
                "direct_execution_allowed": False,
                "registry_write_authority": "registry_service_only",
            },
        },
    )


def _evidence_item_from_note(note: ParsedResearchNote) -> EvidenceItem:
    return EvidenceItem(
        evidence_item_id=f"evi-{_slugify(note.source_record_id)}",
        source_id=note.source_record_id,
        item_type="internal_research_note",
        content_ref=f"{note.content_ref}#strategy-distillation",
        citation_label=f"{note.source_record_id}#strategy-distillation",
        body=note.hypothesis,
        confidence=note.confidence,
        access_scope=note.access_scope,
        trace_refs=[f"trace-strat-v2-001-{_short_hash([note.source_record_id])}"],
        metadata={
            "risk_caps": dict(note.risk_caps),
            "frequency": note.frequency,
            "code_refs": [dict(ref) for ref in note.code_refs],
        },
    )


def _evidence_bundle_from_note(note: ParsedResearchNote, *, created_by: str) -> EvidenceBundle:
    evidence_item_id = f"evi-{_slugify(note.source_record_id)}"
    return EvidenceBundle(
        evidence_bundle_id=f"evbundle-{_short_hash([note.source_record_id, note.title, note.hypothesis])}",
        source_ids=[note.source_record_id],
        evidence_item_ids=[evidence_item_id],
        summary=note.hypothesis,
        citation_refs=[f"{note.source_record_id}#strategy-distillation"],
        confidence=note.confidence,
        license_scope=note.license_scope,
        access_scope=note.access_scope,
        created_by=created_by,
        created_at=note.created_at,
        trace_refs=[f"trace-strat-v2-001-{_short_hash([note.source_record_id])}"],
        metadata={
            "task_id": "STRAT-V2-001",
            "source_markdown_sha256": _sha256(note.markdown),
            "research_only": True,
        },
    )


def _markdown_sections(markdown: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {"": []}
    current = ""
    for line in markdown.splitlines():
        match = _HEADING_RE.match(line)
        if match:
            current = _normalize_key(match.group(2))
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)
    return sections


def _candidate_source_ids(markdown: str, path: Path) -> set[str]:
    front = _document_metadata(_markdown_sections(markdown))
    return {
        item
        for item in (
            front.get("source_record_id"),
            front.get("source_id"),
            front.get("record_id"),
            path.stem,
        )
        if item
    }


def _front_matter(lines: Sequence[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in lines:
        match = _KEY_VALUE_RE.match(line)
        if not match:
            continue
        values[_normalize_key(match.group(1))] = match.group(2).strip()
    return values


def _document_metadata(sections: Mapping[str, Sequence[str]]) -> dict[str, str]:
    lines: list[str] = list(sections.get("", ()))
    for name, section_lines in sections.items():
        if name and name not in _STRUCTURED_SECTION_NAMES:
            lines.extend(section_lines)
            break
    return _front_matter(lines)


def _section_key_values(sections: Mapping[str, Sequence[str]], section_names: Sequence[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for name in section_names:
        for key, value in _parse_key_values(sections.get(name, ())).items():
            values.setdefault(key, value)
    return values


def _parse_key_values(lines: Sequence[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    current_key: str | None = None
    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            continue
        match = _KEY_VALUE_RE.match(line)
        if match:
            current_key = _normalize_key(match.group(1))
            values[current_key] = match.group(2).strip()
            continue
        if current_key and raw[:1].isspace():
            values[current_key] = f"{values[current_key]} {line.strip()}".strip()
    return values


def _section_value(
    sections: Mapping[str, Sequence[str]],
    seed: Mapping[str, str],
    *,
    section_names: Sequence[str],
    keys: Sequence[str],
    required_name: str,
) -> str:
    value = _first_text(*(seed.get(_normalize_key(key)) for key in keys))
    if value:
        return value
    for name in section_names:
        text = _section_text(sections.get(name, ()))
        if text:
            return text
    raise ValidationError(f"source markdown missing required section or key: {required_name}")


def _section_list(
    sections: Mapping[str, Sequence[str]],
    seed: Mapping[str, str],
    *,
    section_names: Sequence[str],
    keys: Sequence[str],
    fallback: Sequence[str],
) -> tuple[str, ...]:
    values: list[str] = []
    for key in keys:
        values.extend(_split_list(seed.get(_normalize_key(key))))
    if values:
        return _unique(values)
    for name in section_names:
        section = sections.get(name, ())
        kv = _parse_key_values(section)
        for key in keys:
            values.extend(_split_list(kv.get(_normalize_key(key))))
        if not values:
            values.extend(_bullet_values(section))
        if values:
            return _unique(values)
    return _unique(fallback)


def _extract_universe(
    sections: Mapping[str, Sequence[str]],
    seed: Mapping[str, str],
) -> dict[str, tuple[str, ...]]:
    universe_kv: dict[str, str] = {}
    for name in ("universe", "market_scope", "market"):
        universe_kv.update(_parse_key_values(sections.get(name, ())))
    explicit_symbols = _split_list(
        _first_text(
            universe_kv.get("symbols"),
            universe_kv.get("tickers"),
            universe_kv.get("instrument_universe"),
            seed.get("symbols"),
        )
    )
    market_values = _split_list(
        _first_text(
            universe_kv.get("universe"),
            universe_kv.get("market_scope"),
            universe_kv.get("markets"),
            universe_kv.get("venues"),
            seed.get("market_scope"),
            seed.get("markets"),
            seed.get("venues"),
        )
    )
    symbols = tuple(explicit_symbols)
    if not symbols and market_values:
        symbols = (f"universe:{_slugify('-'.join(market_values))}",)
    if not symbols:
        raise ValidationError("source markdown missing required section or key: universe")
    venues = _unique(_split_list(_first_text(universe_kv.get("venues"), universe_kv.get("exchanges"))) or market_values)
    asset_classes = _unique(
        _split_list(
            _first_text(
                universe_kv.get("asset_class"),
                universe_kv.get("asset_classes"),
                seed.get("asset_class"),
                seed.get("asset_classes"),
            )
        )
        or ("unspecified",)
    )
    return {"symbols": tuple(symbols), "venues": venues, "asset_classes": asset_classes}


def _extract_frequency(sections: Mapping[str, Sequence[str]], seed: Mapping[str, str]) -> str:
    frequency_kv = _parse_key_values(sections.get("frequency", ()))
    value = _first_text(
        frequency_kv.get("frequency"),
        frequency_kv.get("data_frequency"),
        seed.get("frequency"),
        seed.get("bar_frequency"),
        _section_text(sections.get("frequency", ())),
    )
    if not value:
        raise ValidationError("source markdown missing required section or key: frequency")
    return _normalize_frequency(value)


def _extract_risk_caps(sections: Mapping[str, Sequence[str]]) -> dict[str, Any]:
    lines = sections.get("risk_caps", ()) or sections.get("risk_limits", ())
    if not lines:
        raise ValidationError("source markdown missing required section: risk_caps")
    caps = {_normalize_key(key): _parse_scalar(value) for key, value in _parse_key_values(lines).items()}
    if not caps:
        raise ValidationError("source markdown risk_caps section must contain key/value caps")
    return caps


def _extract_code_refs(sections: Mapping[str, Sequence[str]]) -> tuple[Mapping[str, Any], ...]:
    lines = sections.get("code_refs", ()) or sections.get("code_references", ())
    refs: list[dict[str, Any]] = []
    for raw in lines:
        line = raw.strip()
        if not line or not line.startswith(("-", "*")):
            continue
        payload = _parse_inline_mapping(line[1:].strip())
        if payload:
            refs.append(payload)
    if not refs:
        raise ValidationError("source markdown missing required section or entries: code_refs")
    for ref in refs:
        if not ref.get("repo_ref") or not ref.get("path"):
            raise ValidationError("code_refs entries require repo_ref and path")
    return tuple(refs)


def _parse_inline_mapping(value: str) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for part in value.split(";"):
        if ":" not in part:
            continue
        key, raw = part.split(":", 1)
        payload[_normalize_key(key)] = _parse_scalar(raw.strip())
    return payload


def _bullet_values(lines: Sequence[str]) -> list[str]:
    values: list[str] = []
    for raw in lines:
        line = raw.strip()
        if line.startswith(("-", "*")):
            value = line[1:].strip()
            if ":" in value:
                _, value = value.split(":", 1)
            values.extend(_split_list(value))
    return values


def _section_text(lines: Sequence[str] | None) -> str:
    if not lines:
        return ""
    text = " ".join(line.strip() for line in lines if line.strip())
    return re.sub(r"\s+", " ", text).strip()


def _title(markdown: str, front_matter: Mapping[str, str], path: Path) -> str:
    explicit = _first_text(front_matter.get("title"))
    if explicit:
        return explicit
    for line in markdown.splitlines():
        match = _HEADING_RE.match(line)
        if match and len(match.group(1)) == 1:
            return _require_text(match.group(2).removeprefix("Internal Research Note:").strip(), "title")
    return path.stem.replace("_", " ").replace("-", " ").title()


def _content_ref(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _resolve_source_dirs(source_dirs: Sequence[str | Path] | None) -> tuple[Path, ...]:
    if source_dirs is not None:
        return tuple(Path(item) for item in source_dirs)
    raw = os.getenv(SOURCE_DIRS_ENV, "").strip()
    if raw:
        return tuple(Path(item) for item in raw.split(os.pathsep) if item.strip())
    return DEFAULT_SOURCE_DIRS


def _confidence(front_matter: Mapping[str, str]) -> float:
    raw = _first_text(front_matter.get("confidence"), front_matter.get("trust_score"))
    if not raw:
        return 0.8
    try:
        value = float(str(raw).rstrip("%"))
        if "%" in str(raw):
            value = value / 100.0
    except ValueError as exc:
        raise ValidationError(f"confidence must be numeric: {raw}") from exc
    if value < 0.0 or value > 1.0:
        raise ValidationError("confidence must be between 0.0 and 1.0")
    return value


def _note_timestamp(value: str | None) -> str:
    if not value:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    raw = value.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return f"{raw}T00:00:00Z"
    return _iso_timestamp(raw)


def _iso_timestamp(value: datetime | str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    raw = value.strip()
    if raw.endswith("Z"):
        datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return raw
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_frequency(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"1d", "daily", "day", "1 day", "one day"}:
        return "1d"
    if normalized in {"1h", "hourly", "hour", "1 hour"}:
        return "1h"
    if normalized in {"1m", "1min", "1 min", "minute", "intraday"}:
        return "1m"
    if normalized in {"1w", "weekly", "week", "1 week"}:
        return "1w"
    if normalized in {"1mo", "monthly", "month", "1 month"}:
        return "1mo"
    return value.strip()


def _parse_scalar(value: str) -> Any:
    raw = value.strip()
    lowered = raw.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    percent = raw.endswith("%")
    numeric = raw[:-1] if percent else raw
    try:
        parsed = float(numeric)
    except ValueError:
        return raw
    if percent:
        return round(parsed / 100.0, 8)
    if parsed.is_integer() and "." not in numeric:
        return int(parsed)
    return parsed


def _split_list(value: Any) -> tuple[str, ...]:
    if value in (None, "", [], ()):
        return ()
    if isinstance(value, str):
        pieces = re.split(r"[,;\n]", value)
    elif isinstance(value, Sequence):
        pieces = [str(item) for item in value]
    else:
        pieces = [str(value)]
    return _unique(piece.strip() for piece in pieces if piece.strip())


def _unique(values: Sequence[Any] | Any) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in seen:
            seen.add(text)
            ordered.append(text)
    return tuple(ordered)


def _first_text(*values: Any) -> str | None:
    for value in values:
        if value in (None, "", [], ()):
            continue
        if isinstance(value, Sequence) and not isinstance(value, str):
            for item in value:
                text = str(item).strip()
                if text:
                    return text
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")


def _require_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValidationError(f"{field_name} is required")
    return text


def _slugify(value: str, fallback: str = "item") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return slug or fallback


def _sha256(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _short_hash(values: Sequence[str]) -> str:
    payload = json.dumps(list(values), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _sample_run_payload(source_record_id: str, *, source_dirs: Sequence[str | Path]) -> dict[str, Any]:
    distiller = ProductionStrategySpecDistiller(
        source_dirs=source_dirs,
        created_at="2026-05-17T18:45:00Z",
    )
    result = distiller.distill_result(source_record_id)
    return {
        "task_id": "STRAT-V2-001",
        "source_record_id": source_record_id,
        "strategy_spec": result.strategy_spec.to_dict(),
        "registry_payload": dict(result.registry_payload),
        "candidate_advance_request": dict(result.candidate_advance_request),
        "seed_promotion_lineage_edge": dict(result.seed_promotion_lineage_edge),
        "evidence_code_lineage_edge": dict(result.evidence_code_lineage_edge),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Distill internal research-note markdown into StrategySpec JSON.")
    parser.add_argument("source_record_id")
    parser.add_argument("--source-dir", action="append", default=[], help="Directory containing markdown source notes.")
    parser.add_argument("--registry-payload", action="store_true", help="Print registry create payload instead of StrategySpec.")
    parser.add_argument("--sample-run", action="store_true", help="Print a sample run packet with lineage edges.")
    parser.add_argument("--output", type=Path, help="Write JSON output to a file instead of stdout.")
    args = parser.parse_args(argv)

    source_dirs = tuple(Path(item) for item in args.source_dir) or _resolve_source_dirs(None)
    if args.sample_run:
        payload = _sample_run_payload(args.source_record_id, source_dirs=source_dirs)
    else:
        distiller = ProductionStrategySpecDistiller(source_dirs=source_dirs)
        payload = (
            distiller.distill_registry_payload(args.source_record_id)
            if args.registry_payload
            else distiller.distill(args.source_record_id)
        )
    rendered = json.dumps(payload, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


__all__ = [
    "ProductionStrategySpecDistiller",
    "ValidationError",
    "distill",
    "distill_registry_payload",
]


if __name__ == "__main__":
    raise SystemExit(main())
